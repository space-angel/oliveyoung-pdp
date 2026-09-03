"""
제품 카탈로그 레이어 (PER-171 / PRD §3-3).

제품 동일성은 파이프라인의 책임이 아니라 이 레이어의 책임이다.
파이프라인은 리뷰 행의 `productKey` 문자열을 신뢰하지 않고, `goodsNo`를
카탈로그에 물어 정규화 제품 ID(`productId`)를 받는다.

핵심 규칙: **미등록 `goodsNo`는 조용히 폴백하지 않고 에러다.**
폴백을 허용하면 같은 제품이 두 개로 쪼개지고, 그 사고는 집계 수치가
틀어진 뒤에야 발견된다.

카탈로그 파일은 `pipeline/build_product_catalog.py`가 생성한다.
새 SKU가 등장하면 그 스크립트를 다시 돌려 카탈로그를 갱신한다 —
파이프라인 코드에 매핑을 상수로 넣지 않는다.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CATALOG_PATH = Path(__file__).parents[1] / "data/input/product_catalog.json"
SCHEMA_VERSION = "v5-1"
PRODUCT_ID_PATTERN = re.compile(r"^p\d{3}$")
GOODS_NO_PATTERN = re.compile(r"^A\d{12}$")
VALID_SOURCES = {"crawl_request", "observed_variant", "legacy_v4"}


class CatalogError(Exception):
    """카탈로그 자체가 계약을 위반했다 (로드 시점 검증 실패)."""


class UnknownGoodsNoError(CatalogError):
    """입력에 카탈로그가 모르는 상품 ID가 있다. 조용한 폴백 금지."""


class UnknownProductError(CatalogError):
    """카탈로그에 없는 productId / displayName 조회."""


@dataclass(frozen=True)
class Product:
    product_id: str
    display_name: str
    category: str
    requested_goods_no: str
    goods_nos: tuple[str, ...]
    renewal_policy: str | None = None
    notes: tuple[str, ...] = ()


class ProductCatalog:
    def __init__(self, products: list[Product], meta: dict):
        self.meta = meta
        self._by_id = {p.product_id: p for p in products}
        self._by_goods_no: dict[str, str] = {}
        self._by_display_name: dict[str, str] = {}
        for p in products:
            for g in p.goods_nos:
                self._by_goods_no[g] = p.product_id
            self._by_display_name[p.display_name] = p.product_id

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
        seen_goods: dict[str, str] = {}
        for entry in raw.get("products", []):
            pid = entry.get("productId", "")
            if not PRODUCT_ID_PATTERN.match(pid):
                raise CatalogError(f"productId 형식 위반: {pid!r} (기대: p001)")
            display = (entry.get("displayName") or "").strip()
            category = (entry.get("category") or "").strip()
            if not display or not category:
                raise CatalogError(f"{pid}: displayName·category 는 비울 수 없다")

            goods = tuple(g["goodsNo"] for g in entry.get("goodsNos", []))
            if not goods:
                raise CatalogError(f"{pid}: goodsNos 가 비었다")
            for g, src in ((g["goodsNo"], g.get("source")) for g in entry["goodsNos"]):
                if not GOODS_NO_PATTERN.match(g):
                    raise CatalogError(f"{pid}: goodsNo 형식 위반 {g!r}")
                if src not in VALID_SOURCES:
                    raise CatalogError(f"{pid}/{g}: 알 수 없는 source {src!r}")
                if g in seen_goods:
                    raise CatalogError(
                        f"goodsNo {g} 가 두 제품에 걸쳐 있다: {seen_goods[g]} vs {pid}. "
                        "제품 동일성이 모호하므로 사람이 정해야 한다"
                    )
                seen_goods[g] = pid

            requested = entry.get("requestedGoodsNo")
            if requested is not None and requested not in goods:
                raise CatalogError(f"{pid}: requestedGoodsNo {requested} 가 goodsNos 에 없다")

            products.append(
                Product(
                    product_id=pid,
                    display_name=display,
                    category=category,
                    requested_goods_no=requested,
                    goods_nos=goods,
                    renewal_policy=entry.get("renewalPolicy"),
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

        return cls(products, meta)

    # --- 조회 ---

    def resolve_goods_no(self, goods_no: str) -> str:
        """`goodsNo` → 정규화 제품 ID. 미등록이면 에러 (폴백 없음)."""
        try:
            return self._by_goods_no[goods_no]
        except KeyError:
            raise UnknownGoodsNoError(
                f"카탈로그에 없는 goodsNo: {goods_no}\n"
                "  → 새 SKU 이거나 다른 제품의 리뷰다. 어느 제품인지 정한 뒤\n"
                "     pipeline/build_product_catalog.py 를 다시 돌려 카탈로그에 등록한다.\n"
                "     (임의 폴백은 같은 제품을 두 개로 쪼갠다)"
            ) from None

    def product(self, product_id: str) -> Product:
        try:
            return self._by_id[product_id]
        except KeyError:
            raise UnknownProductError(f"카탈로그에 없는 productId: {product_id}") from None

    def product_of_goods_no(self, goods_no: str) -> Product:
        return self.product(self.resolve_goods_no(goods_no))

    def by_display_name(self, display_name: str) -> Product:
        """레거시 호환용 — v4 산출물·골든셋이 한글 제품명을 키로 쓴다."""
        try:
            return self._by_id[self._by_display_name[display_name]]
        except KeyError:
            raise UnknownProductError(
                f"카탈로그에 없는 displayName: {display_name!r}"
            ) from None

    @property
    def products(self) -> list[Product]:
        return list(self._by_id.values())

    def __len__(self) -> int:
        return len(self._by_id)


def load_catalog(path: Path | str = DEFAULT_CATALOG_PATH) -> ProductCatalog:
    return ProductCatalog.load(path)
