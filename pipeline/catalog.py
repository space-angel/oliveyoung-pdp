"""
제품 카탈로그 레이어 (PER-171 / PER-172 / PRD §3-3).

제품 동일성은 파이프라인의 책임이 아니라 이 레이어의 책임이다.
파이프라인은 리뷰 행의 `productKey` 문자열을 신뢰하지 않고, `goodsNo`를
카탈로그에 물어 정규화 제품 ID(`productId`)를 받는다.

핵심 규칙: **미등록 `goodsNo`는 조용히 폴백하지 않고 에러다.**
폴백을 허용하면 같은 제품이 두 개로 쪼개지고, 그 사고는 집계 수치가
틀어진 뒤에야 발견된다.

## 리뉴얼 세대 (PER-172)

리뉴얼은 별개 제품이다 — 제형이 바뀌면 리뷰의 주장도 무효다. 그런데 세대 경계가
SKU 코드 **안쪽**에 있는 제품이 실재하므로(goodsNo 1개로 3년 치 리뷰), 세대 식별의
키는 `goodsNo` 가 아니라 **(goodsNo, reviewDate)** 다. 그래서:

  - 세대는 각자 별개의 `productId` 를 갖고, `lineageId` 로 같은 계보임을 표시한다
  - 한 `goodsNo` 가 여러 `productId` 에 걸치는 것은 **같은 계보 안에서만** 허용된다
  - 그 경우 `resolve_goods_no()` 는 `review_date` 를 요구한다. 날짜 없이 부르면 에러다

`renewalPolicy` 는 3상태이고 null 을 허용하지 않는다 — 정하지 않았다는 사실 자체를
`unobserved` 로 명시한다. 어휘와 컷 판정은 `pipeline/policy.py` 가 소유한다.

카탈로그 파일은 `pipeline/build_product_catalog.py`가 생성한다. 새 SKU가 등장하면
그 스크립트를 다시 돌려 카탈로그를 갱신한다 — 파이프라인 코드에 매핑을 상수로 넣지 않는다.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from policy import (  # noqa: E402
    MONTH_PATTERN,
    RENEWAL_POLICIES,
    RENEWAL_SEPARATE,
    month_of,
)

DEFAULT_CATALOG_PATH = Path(__file__).parents[1] / "data/input/product_catalog.json"
# v5-2: lineageId · renewalPolicy 객체 추가 (PER-172)
SCHEMA_VERSION = "v5-2"
PRODUCT_ID_PATTERN = re.compile(r"^p\d{3}$")
LINEAGE_ID_PATTERN = re.compile(r"^L\d{3}$")
GOODS_NO_PATTERN = re.compile(r"^A\d{12}$")
VALID_SOURCES = {"crawl_request", "observed_variant", "legacy_v4"}


class CatalogError(Exception):
    """카탈로그 자체가 계약을 위반했다 (로드 시점 검증 실패)."""


class UnknownGoodsNoError(CatalogError):
    """입력에 카탈로그가 모르는 상품 ID가 있다. 조용한 폴백 금지."""


class UnknownProductError(CatalogError):
    """카탈로그에 없는 productId / displayName 조회."""


class AmbiguousGenerationError(CatalogError):
    """`goodsNo` 가 여러 세대에 걸치는데 어느 세대인지 정할 수 없다 (PER-172)."""


@dataclass(frozen=True)
class Product:
    product_id: str
    display_name: str
    category: str
    requested_goods_no: str
    goods_nos: tuple[str, ...]
    lineage_id: str
    renewal_policy: str
    renewal_from_month: str | None = None
    renewal_to_month: str | None = None
    renewal_evidence: str | None = None
    notes: tuple[str, ...] = ()

    def covers_month(self, month: str) -> bool:
        """이 세대의 구간이 해당 월을 덮는가. 구간이 열려 있으면(None) 그쪽은 무한이다."""
        if self.renewal_from_month is not None and month < self.renewal_from_month:
            return False
        if self.renewal_to_month is not None and month > self.renewal_to_month:
            return False
        return True


def _parse_renewal(pid: str, raw) -> dict:
    """`renewalPolicy` 검증. null 은 허용하지 않는다 — 'unobserved' 로 명시해야 한다."""
    if not isinstance(raw, dict):
        raise CatalogError(
            f"{pid}: renewalPolicy 는 객체여야 한다 (받은 값 {raw!r}). "
            f"정하지 않았다면 {{\"policy\": \"unobserved\"}} 로 명시한다 (PER-172)"
        )
    policy = raw.get("policy")
    if policy not in RENEWAL_POLICIES:
        raise CatalogError(
            f"{pid}: 알 수 없는 renewalPolicy.policy {policy!r} (기대: {RENEWAL_POLICIES})"
        )
    from_month, to_month = raw.get("fromMonth"), raw.get("toMonth")
    evidence = (raw.get("evidence") or "").strip() or None

    if policy != RENEWAL_SEPARATE:
        if from_month is not None or to_month is not None:
            raise CatalogError(
                f"{pid}: policy={policy!r} 인데 세대 구간(fromMonth/toMonth)이 있다. "
                "구간은 separate 에서만 의미가 있다"
            )
        if evidence is not None:
            raise CatalogError(
                f"{pid}: policy={policy!r} 인데 evidence 가 있다. "
                "근거는 세대를 나눴을 때만 붙인다"
            )
        return {"policy": policy, "fromMonth": None, "toMonth": None, "evidence": None}

    # separate — 어디서 자르는지와 왜 잘랐는지가 없으면 결정이 아니다
    if evidence is None:
        raise CatalogError(
            f"{pid}: policy='separate' 인데 evidence 가 비었다. "
            "리뉴얼 시점을 무엇으로 확인했는지 남겨야 한다"
        )
    if from_month is None and to_month is None:
        raise CatalogError(
            f"{pid}: policy='separate' 인데 세대 구간이 양쪽 다 열려 있다. "
            "어디서 자를지 정하지 않은 것이므로 분할이 성립하지 않는다"
        )
    for label, value in (("fromMonth", from_month), ("toMonth", to_month)):
        if value is not None and not MONTH_PATTERN.match(value):
            raise CatalogError(f"{pid}: {label} 형식 위반 {value!r} (기대: 2025-07)")
    if from_month is not None and to_month is not None and from_month > to_month:
        raise CatalogError(f"{pid}: 세대 구간이 뒤집혔다 ({from_month} > {to_month})")
    return {"policy": policy, "fromMonth": from_month, "toMonth": to_month, "evidence": evidence}


class ProductCatalog:
    def __init__(self, products: list[Product], meta: dict):
        self.meta = meta
        self._by_id = {p.product_id: p for p in products}
        self._by_goods_no: dict[str, list[str]] = {}
        self._by_display_name: dict[str, str] = {}
        self._by_lineage: dict[str, list[str]] = {}
        for p in products:
            for g in p.goods_nos:
                self._by_goods_no.setdefault(g, []).append(p.product_id)
            self._by_display_name[p.display_name] = p.product_id
            self._by_lineage.setdefault(p.lineage_id, []).append(p.product_id)

    # --- 로드 & 검증 ---

    @classmethod
    def load(cls, path: Path | str = DEFAULT_CATALOG_PATH) -> "ProductCatalog":
        path = Path(path)
        if not path.exists():
            raise CatalogError(
                f"카탈로그 파일이 없다: {path}\n"
                "  → .venv/bin/python pipeline/build_product_catalog.py 로 생성한다"
            )
        raw = json.loads(path.read_text())
        meta = raw.get("_meta", {})
        version = meta.get("schemaVersion")
        if version != SCHEMA_VERSION:
            raise CatalogError(
                f"카탈로그 schemaVersion 불일치: 파일 {version!r} != 코드 {SCHEMA_VERSION!r}"
            )

        products: list[Product] = []
        goods_owner: dict[str, list[str]] = {}
        lineage_of: dict[str, str] = {}
        for entry in raw.get("products", []):
            pid = entry.get("productId", "")
            if not PRODUCT_ID_PATTERN.match(pid):
                raise CatalogError(f"productId 형식 위반: {pid!r} (기대: p001)")
            display = (entry.get("displayName") or "").strip()
            category = (entry.get("category") or "").strip()
            if not display or not category:
                raise CatalogError(f"{pid}: displayName·category 는 비울 수 없다")
            lineage = entry.get("lineageId") or ""
            if not LINEAGE_ID_PATTERN.match(lineage):
                raise CatalogError(f"{pid}: lineageId 형식 위반 {lineage!r} (기대: L001)")
            renewal = _parse_renewal(pid, entry.get("renewalPolicy"))

            goods = tuple(g["goodsNo"] for g in entry.get("goodsNos", []))
            if not goods:
                raise CatalogError(f"{pid}: goodsNos 가 비었다")
            for g, src in ((g["goodsNo"], g.get("source")) for g in entry["goodsNos"]):
                if not GOODS_NO_PATTERN.match(g):
                    raise CatalogError(f"{pid}: goodsNo 형식 위반 {g!r}")
                if src not in VALID_SOURCES:
                    raise CatalogError(f"{pid}/{g}: 알 수 없는 source {src!r}")
                goods_owner.setdefault(g, []).append(pid)

            requested = entry.get("requestedGoodsNo")
            if requested is not None and requested not in goods:
                raise CatalogError(f"{pid}: requestedGoodsNo {requested} 가 goodsNos 에 없다")

            lineage_of[pid] = lineage
            products.append(
                Product(
                    product_id=pid,
                    display_name=display,
                    category=category,
                    requested_goods_no=requested,
                    goods_nos=goods,
                    lineage_id=lineage,
                    renewal_policy=renewal["policy"],
                    renewal_from_month=renewal["fromMonth"],
                    renewal_to_month=renewal["toMonth"],
                    renewal_evidence=renewal["evidence"],
                    notes=tuple(entry.get("notes", [])),
                )
            )

        if not products:
            raise CatalogError(f"카탈로그가 비었다: {path}")
        ids = [p.product_id for p in products]
        if len(set(ids)) != len(ids):
            raise CatalogError("productId 중복")
        names = [p.display_name for p in products]
        if len(set(names)) != len(names):
            dupes = sorted({n for n in names if names.count(n) > 1})
            raise CatalogError(f"displayName 중복: {dupes}")

        cls._validate_lineages(products)

        # goodsNo 공유는 **같은 계보 안에서만** 허용한다. 계보를 넘으면 제품 동일성이
        # 모호해지고, 그 판단은 사람이 해야 한다.
        for g, owners in goods_owner.items():
            if len(owners) == 1:
                continue
            lineages = {lineage_of[pid] for pid in owners}
            if len(lineages) > 1:
                raise CatalogError(
                    f"goodsNo {g} 가 서로 다른 계보에 걸쳐 있다: "
                    f"{sorted(set(owners))} (lineage {sorted(lineages)}). "
                    "제품 동일성이 모호하므로 사람이 정해야 한다"
                )

        return cls(products, meta)

    @staticmethod
    def _validate_lineages(products: list[Product]) -> None:
        """계보 단위 검증 — 세대 구간이 겹치거나 현행 세대가 두 개면 날짜로 못 가른다."""
        by_lineage: dict[str, list[Product]] = {}
        for p in products:
            by_lineage.setdefault(p.lineage_id, []).append(p)

        for lineage, members in sorted(by_lineage.items()):
            if len(members) == 1:
                continue
            non_separate = [p.product_id for p in members if p.renewal_policy != RENEWAL_SEPARATE]
            if non_separate:
                raise CatalogError(
                    f"계보 {lineage} 에 제품이 {len(members)}개인데 "
                    f"{non_separate} 의 policy 가 'separate' 가 아니다. "
                    "계보를 나눴다면 세대는 별개 제품이어야 한다"
                )
            open_ended = [p.product_id for p in members if p.renewal_to_month is None]
            if len(open_ended) != 1:
                raise CatalogError(
                    f"계보 {lineage}: 현행 세대(toMonth=null)가 {len(open_ended)}개다 "
                    f"({open_ended}). 정확히 하나여야 한다"
                )
            ordered = sorted(members, key=lambda p: (p.renewal_from_month or ""))
            for earlier, later in zip(ordered, ordered[1:]):
                if earlier.renewal_to_month is None or (
                    later.renewal_from_month is not None
                    and earlier.renewal_to_month >= later.renewal_from_month
                ):
                    raise CatalogError(
                        f"계보 {lineage}: 세대 구간이 겹친다 "
                        f"({earlier.product_id} …{earlier.renewal_to_month} / "
                        f"{later.product_id} {later.renewal_from_month}…). "
                        "겹치면 리뷰를 날짜로 가를 수 없다"
                    )

    # --- 조회 ---

    def resolve_goods_no(self, goods_no: str, review_date: str | None = None) -> str:
        """`goodsNo` → 정규화 제품 ID. 미등록이면 에러 (폴백 없음).

        `goodsNo` 가 여러 세대에 걸치면 `review_date` 로 세대를 고른다. 날짜 없이 부르면
        에러다 — 임의로 한 세대를 고르면 구·신 세대의 근거가 섞인다.
        """
        owners = self._by_goods_no.get(goods_no)
        if not owners:
            raise UnknownGoodsNoError(
                f"카탈로그에 없는 goodsNo: {goods_no}\n"
                "  → 새 SKU 이거나 다른 제품의 리뷰다. 어느 제품인지 정한 뒤\n"
                "     pipeline/build_product_catalog.py 를 다시 돌려 카탈로그에 등록한다.\n"
                "     (임의 폴백은 같은 제품을 두 개로 쪼갠다)"
            )
        if len(owners) == 1:
            return owners[0]

        if review_date is None:
            raise AmbiguousGenerationError(
                f"goodsNo {goods_no} 는 세대 {sorted(owners)} 에 걸쳐 있다. "
                "세대를 가르려면 review_date 가 필요하다 (PER-172)"
            )
        month = month_of(review_date)
        matched = [pid for pid in owners if self._by_id[pid].covers_month(month)]
        if len(matched) == 1:
            return matched[0]
        raise AmbiguousGenerationError(
            f"goodsNo {goods_no} / {month}: 덮는 세대가 {len(matched)}개다 ({sorted(matched)}). "
            f"후보 {sorted(owners)} 의 세대 구간을 다시 정해야 한다"
        )

    def product(self, product_id: str) -> Product:
        try:
            return self._by_id[product_id]
        except KeyError:
            raise UnknownProductError(f"카탈로그에 없는 productId: {product_id}") from None

    def product_of_goods_no(self, goods_no: str, review_date: str | None = None) -> Product:
        return self.product(self.resolve_goods_no(goods_no, review_date))

    def by_display_name(self, display_name: str) -> Product:
        """레거시 호환용 — v4 산출물·골든셋이 한글 제품명을 키로 쓴다."""
        try:
            return self._by_id[self._by_display_name[display_name]]
        except KeyError:
            raise UnknownProductError(
                f"카탈로그에 없는 displayName: {display_name!r}"
            ) from None

    def lineage(self, lineage_id: str) -> tuple[Product, ...]:
        """한 계보의 세대들을 오래된 순으로. 근거는 섞지 않되 계보는 따라갈 수 있어야 한다."""
        pids = self._by_lineage.get(lineage_id)
        if not pids:
            raise UnknownProductError(f"카탈로그에 없는 lineageId: {lineage_id!r}")
        return tuple(sorted((self._by_id[pid] for pid in pids),
                            key=lambda p: (p.renewal_from_month or "", p.product_id)))

    def lineage_of(self, product_id: str) -> tuple[Product, ...]:
        return self.lineage(self.product(product_id).lineage_id)

    @property
    def products(self) -> list[Product]:
        return list(self._by_id.values())

    @property
    def lineages(self) -> list[str]:
        return sorted(self._by_lineage)

    def __len__(self) -> int:
        return len(self._by_id)


def load_catalog(path: Path | str = DEFAULT_CATALOG_PATH) -> ProductCatalog:
    return ProductCatalog.load(path)
