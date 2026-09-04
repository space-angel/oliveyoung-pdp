"""
제품 카탈로그 생성기 (PER-171 / PER-172).

`data/input/product_catalog.json` 을 만든다. 매핑을 손으로 타이핑하지 않고
세 소스에서 유도하되, **추측이 필요한 지점에서는 에러를 내고 멈춘다.**

소스와 역할
  1. `crawler/products_50.json`        정본 제품 50개 (요청 goodsNo · 카테고리 · 표시명)
  2. `data/input/reviews_50products.json`  관측된 변형 SKU (requestedGoodsNo 로 제품에 붙는다)
  3. `data/input/product_canonical_map.json` + v4 입력 2개   v4 시절 goodsNo (레거시 재현용)

productId 는 한 번 붙으면 고정한다(append-only). 표시명 오타를 고쳐도 집계 키가
바뀌지 않게 하려는 것이다 — v4 맵은 이름을 키로 써서 파일 간 이름 드리프트가
20건, 브랜드 오기가 1건 발생했다. `lineageId` 와 `renewalPolicy`(PER-172) 도 같은
이유로 커밋본 값을 승계한다 — **리뉴얼 취급은 생성기가 추론하지 않는다.**

사용:
  .venv/bin/python pipeline/build_product_catalog.py            # 생성/갱신
  .venv/bin/python pipeline/build_product_catalog.py --check    # 커밋본과 일치 확인 (CI/재현성)
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from catalog import SCHEMA_VERSION, ProductCatalog  # noqa: E402

ROOT = Path(__file__).parents[1]
SEEDS_PATH = ROOT / "crawler/products_50.json"
REVIEWS_PATH = ROOT / "data/input/reviews_50products.json"
LEGACY_MAP_PATH = ROOT / "data/input/product_canonical_map.json"
LEGACY_INPUTS = [
    ROOT / "data/input/reviews_200_normalized.json",
    ROOT / "data/input/v4_reviews_500.json",
]
CATALOG_PATH = ROOT / "data/input/product_catalog.json"
COVERAGE_PATH = ROOT / "eval/reports/product_catalog_coverage.json"

# v4 맵의 표시명이 정본과 다르고 **브랜드까지 다른** 경우는 자동 병합하지 않는다.
# 사람이 확인한 건만 여기에 근거와 함께 등록한다. 등록되지 않은 브랜드 불일치는 에러다.
RESOLVED_BRAND_CONFLICTS = {
    # v4 맵 표시명 → (정본 표시명, 근거)
    "닥터자르트 레드 블레미쉬 클리어 수딩 크림": (
        "닥터지 레드 블레미쉬 클리어 수딩크림",
        "A000000166641 의 리뷰 productName 은 '레드 블레미쉬 클리어 수딩 크림'이고 "
        "이 제품은 닥터지(Dr.G) 상품이다. v4 맵의 '닥터자르트' 표기가 오기다",
    ),
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def brand_token(name: str) -> str:
    return name.split()[0] if name.split() else ""


def build() -> tuple[dict, dict]:
    seeds = json.loads(SEEDS_PATH.read_text())
    reviews = json.loads(REVIEWS_PATH.read_text())
    legacy_map = json.loads(LEGACY_MAP_PATH.read_text())["mapping"]

    # --- 1. 정본 제품: 크롤 요청 목록 ---
    products: dict[str, dict] = {}  # displayName → entry
    for s in seeds:
        name = s["productKey"]
        if name in products:
            raise SystemExit(f"products_50.json 에 표시명 중복: {name}")
        products[name] = {
            "displayName": name,
            "category": s["category"],
            "requestedGoodsNo": s["goodsNo"],
            "goodsNos": {s["goodsNo"]: "crawl_request"},
            "notes": [],
        }

    # --- 2. 관측된 변형 SKU: requestedGoodsNo 로 제품을 찾는다 ---
    by_requested = {p["requestedGoodsNo"]: name for name, p in products.items()}
    reviews_per_goods = collections.Counter(r["goodsNo"] for r in reviews)
    label_mismatch = []
    for r in reviews:
        name = by_requested.get(r["requestedGoodsNo"])
        if name is None:
            raise SystemExit(
                f"리뷰의 requestedGoodsNo {r['requestedGoodsNo']} 가 products_50.json 에 없다. "
                "크롤 목록과 스냅샷이 어긋났다"
            )
        # 스냅샷의 productKey 는 크롤 목록에서 주입된 값이다. 어긋나면 스냅샷을 신뢰할 수 없다.
        if r["productKey"] != name:
            label_mismatch.append({"reviewId": r["reviewId"], "row": r["productKey"], "seed": name})
        products[name]["goodsNos"].setdefault(r["goodsNo"], "observed_variant")
    if label_mismatch:
        raise SystemExit(
            f"스냅샷 productKey 와 크롤 목록이 불일치: {len(label_mismatch)}건 "
            f"(예: {label_mismatch[:3]})"
        )

    # --- 3. 레거시 v4 goodsNo: 이름 일치 → goodsNo 겹침 순으로 제품을 찾는다 ---
    goods_owner = {g: name for name, p in products.items() for g in p["goodsNos"]}
    reconciliation, orphans = [], []
    for legacy_name, legacy_goods in legacy_map.items():
        if legacy_name in products:
            target = legacy_name
        else:
            overlap = collections.Counter(
                goods_owner[g] for g in legacy_goods if g in goods_owner
            )
            if not overlap:
                orphans.append({"legacyName": legacy_name, "goodsNos": sorted(legacy_goods)})
                continue
            if len(overlap) > 1:
                raise SystemExit(
                    f"v4 맵 '{legacy_name}' 의 goodsNo 가 여러 제품에 걸친다: {dict(overlap)}. "
                    "사람이 정해야 한다"
                )
            target = next(iter(overlap))
            same_brand = brand_token(legacy_name) == brand_token(target)
            note = None
            if not same_brand:
                resolved = RESOLVED_BRAND_CONFLICTS.get(legacy_name)
                if not resolved or resolved[0] != target:
                    raise SystemExit(
                        f"v4 맵 '{legacy_name}' 이 '{target}' 과 goodsNo 를 공유하지만 브랜드가 다르다. "
                        "자동 병합하지 않는다 — 확인 후 RESOLVED_BRAND_CONFLICTS 에 근거와 함께 등록하라"
                    )
                note = f"v4 맵 표시명 '{legacy_name}' 정정: {resolved[1]}"
                products[target]["notes"].append(note)
            reconciliation.append(
                {
                    "legacyName": legacy_name,
                    "resolvedTo": target,
                    "overlapGoodsNos": sum(1 for g in legacy_goods if g in goods_owner),
                    "brandMatch": same_brand,
                    "note": note,
                }
            )
        for g in legacy_goods:
            products[target]["goodsNos"].setdefault(g, "legacy_v4")

    # v4 입력 파일에만 있는 goodsNo 도 붙인다 (레거시 베이스라인 재현용)
    legacy_input_goods = collections.Counter()
    for path in LEGACY_INPUTS:
        for r in json.loads(path.read_text()):
            legacy_input_goods[r["goodsNo"]] += 1
    unresolved_legacy = sorted(g for g in legacy_input_goods if g not in goods_owner
                               and not any(g in p["goodsNos"] for p in products.values()))
    if unresolved_legacy:
        raise SystemExit(
            f"v4 입력의 goodsNo {unresolved_legacy} 를 어느 제품에도 붙일 수 없다"
        )

    # --- 4. productId / lineageId 부여 (append-only) ---
    # lineageId 는 리뉴얼 계보 키다 (PER-172). 세대를 나누면 새 productId 가 생기지만
    # lineageId 는 조상 것을 물려받으므로, 여기서도 커밋본 값을 그대로 승계한다.
    existing: dict[str, str] = {}
    existing_lineage: dict[str, str] = {}
    existing_renewal: dict[str, dict] = {}
    if CATALOG_PATH.exists():
        for e in json.loads(CATALOG_PATH.read_text()).get("products", []):
            existing[e["displayName"]] = e["productId"]
            if e.get("lineageId"):
                existing_lineage[e["displayName"]] = e["lineageId"]
            if isinstance(e.get("renewalPolicy"), dict):
                existing_renewal[e["displayName"]] = e["renewalPolicy"]
    ordered = sorted(products.values(), key=lambda p: (p["category"], p["displayName"]))
    next_num = max((int(pid[1:]) for pid in existing.values()), default=0) + 1
    for entry in ordered:
        pid = existing.get(entry["displayName"])
        if pid is None:
            pid = f"p{next_num:03d}"
            next_num += 1
        entry["productId"] = pid
        # 계보를 새로 시작하는 제품은 자기 productId 번호를 계보 번호로 쓴다.
        entry["lineageId"] = existing_lineage.get(entry["displayName"], f"L{pid[1:]}")
        # 리뉴얼 취급은 생성기가 추론하지 않는다 — 사람이 근거와 함께 카탈로그에 적고,
        # 재생성 시 그 값을 승계한다. 아직 정하지 않았으면 'unobserved' 로 명시한다.
        entry["renewalPolicy"] = existing_renewal.get(
            entry["displayName"], {"policy": "unobserved", "fromMonth": None, "toMonth": None, "evidence": None}
        )

    catalog = {
        "_meta": {
            "schemaVersion": SCHEMA_VERSION,
            "issue": "PER-171 / PER-172",
            "description": (
                "제품 동일성(goodsNo → productId)의 단일 정본. 파이프라인은 리뷰 행의 "
                "productKey 문자열을 신뢰하지 않고 goodsNo 를 이 파일에 물어 제품을 식별한다."
            ),
            "rules": [
                "집계 단위는 productId 다. goodsNo 는 변형 SKU 단위이므로 그룹핑 키로 쓰지 않는다.",
                "미등록 goodsNo 는 조용히 폴백하지 않고 에러다 (pipeline/catalog.py).",
                "productId 는 한 번 부여하면 고정한다. 표시명이 바뀌어도 집계 키는 유지된다.",
                "리뉴얼은 별개 제품이다 (PER-172). 세대는 별개 productId 를 갖고 lineageId 로 묶인다.",
                "세대 경계는 goodsNo 가 아니라 날짜다 — 한 goodsNo 가 여러 세대에 걸치면 review_date 로 가른다.",
                "renewalPolicy 는 null 을 허용하지 않는다. 정하지 않았으면 'unobserved' 로 명시한다.",
                "renewalPolicy 는 생성기가 추론하지 않는다. 사람이 근거와 함께 적고 재생성 시 승계된다.",
            ],
            "generatedBy": "pipeline/build_product_catalog.py",
            "supersedes": "data/input/product_canonical_map.json (v4, 표시명 키 기반)",
            "sources": [
                {"path": str(p.relative_to(ROOT)), "sha256": sha256(p), "role": role}
                for p, role in [
                    (SEEDS_PATH, "정본 제품 목록"),
                    (REVIEWS_PATH, "관측된 변형 SKU"),
                    (LEGACY_MAP_PATH, "v4 매핑"),
                    *[(p, "v4 입력") for p in LEGACY_INPUTS],
                ]
            ],
        },
        "products": [
            {
                "productId": e["productId"],
                "displayName": e["displayName"],
                "category": e["category"],
                "requestedGoodsNo": e["requestedGoodsNo"],
                "lineageId": e["lineageId"],
                "renewalPolicy": e["renewalPolicy"],
                "notes": e["notes"],
                "goodsNos": [
                    {"goodsNo": g, "source": src}
                    for g, src in sorted(e["goodsNos"].items())
                ],
            }
            for e in sorted(ordered, key=lambda p: p["productId"])
        ],
    }

    # --- 5. 커버리지 리포트 ---
    owner = {g["goodsNo"]: p["productId"] for p in catalog["products"] for g in p["goodsNos"]}
    source_counts = collections.Counter(
        g["source"] for p in catalog["products"] for g in p["goodsNos"]
    )
    unresolved_25k = sorted({r["goodsNo"] for r in reviews} - set(owner))
    unresolved_v4 = sorted(set(legacy_input_goods) - set(owner))
    renewal_counts = collections.Counter(
        p["renewalPolicy"]["policy"] for p in catalog["products"]
    )
    coverage = {
        "issue": "PER-171 / PER-172",
        "lineages": len({p["lineageId"] for p in catalog["products"]}),
        "renewalPolicyCounts": dict(sorted(renewal_counts.items())),
        "sources": catalog["_meta"]["sources"],
        "products": len(catalog["products"]),
        "goodsNosTotal": len(owner),
        "goodsNosBySource": dict(source_counts),
        "snapshot25k": {
            "reviews": len(reviews),
            "goodsNos": len(reviews_per_goods),
            "unresolvedGoodsNos": unresolved_25k,
            "unresolvedReviews": sum(reviews_per_goods[g] for g in unresolved_25k),
        },
        "legacyV4Inputs": {
            "reviews": sum(legacy_input_goods.values()),
            "goodsNos": len(legacy_input_goods),
            "unresolvedGoodsNos": unresolved_v4,
        },
        "legacyMapReconciliation": {
            "entries": len(legacy_map),
            "nameDrift": reconciliation,
            "orphans": orphans,
        },
        "perProduct": [
            {
                "productId": p["productId"],
                "displayName": p["displayName"],
                "category": p["category"],
                "goodsNos": len(p["goodsNos"]),
                "reviews25k": sum(reviews_per_goods[g["goodsNo"]] for g in p["goodsNos"]),
            }
            for p in catalog["products"]
        ],
    }
    return catalog, coverage


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="커밋본과 일치하는지만 확인한다")
    args = ap.parse_args()

    catalog, coverage = build()
    payload = json.dumps(catalog, ensure_ascii=False, indent=2) + "\n"

    if args.check:
        current = CATALOG_PATH.read_text() if CATALOG_PATH.exists() else ""
        if current != payload:
            print("FAIL: 카탈로그가 소스와 어긋난다. 재생성 후 커밋하라.", file=sys.stderr)
            sys.exit(1)
        ProductCatalog.load(CATALOG_PATH)
        print(f"OK: 카탈로그 {len(catalog['products'])}제품 / goodsNo {coverage['goodsNosTotal']}개 일치")
        return

    CATALOG_PATH.write_text(payload)
    COVERAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    COVERAGE_PATH.write_text(json.dumps(coverage, ensure_ascii=False, indent=2) + "\n")
    loaded = ProductCatalog.load(CATALOG_PATH)  # 생성 직후 계약 검증

    print(f"[카탈로그] 제품 {len(loaded)}개 / goodsNo {coverage['goodsNosTotal']}개")
    for src, n in sorted(coverage["goodsNosBySource"].items()):
        print(f"  {src:18s} {n}")
    print(f"  25K 미해결 goodsNo: {len(coverage['snapshot25k']['unresolvedGoodsNos'])}")
    print(f"  v4 입력 미해결 goodsNo: {len(coverage['legacyV4Inputs']['unresolvedGoodsNos'])}")
    print(f"  v4 맵 이름 드리프트: {len(coverage['legacyMapReconciliation']['nameDrift'])}"
          f" / 고아 엔트리: {len(coverage['legacyMapReconciliation']['orphans'])}")
    print(f"→ {CATALOG_PATH.relative_to(ROOT)}, {COVERAGE_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
