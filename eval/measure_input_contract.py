"""
PER-176 근거 측정 — 입력 계약.

계약 문서 docs/INPUT_CONTRACT.md 의 표와 1:1 대응한다. 세 가지를 잰다.

  1. 집계 단위      goodsNo / productId / 행의 productKey 를 같은 잣대로 비교
  2. 입력 프로파일  유효성·식별자·조건축 도메인 (계약이 무엇을 보증하는지)
  3. 강제 커버리지  위반 클래스마다 실제로 에러가 나는지 **호출해서** 확인

3번이 이 스크립트의 요점이다. "조용한 폴백 금지"는 주장이 아니라 측정 대상이어야 한다 —
위반 케이스를 실제로 넣어보고, 통과해버린 게 하나라도 있으면 종료 코드 1로 실패한다.

사용:
  .venv/bin/python eval/measure_input_contract.py
  → eval/reports/input_contract_per176.json
"""
import argparse
import collections
import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

from catalog import UnknownGoodsNoError, load_catalog  # noqa: E402
from codebook import load_codebook  # noqa: E402
from contracts import (  # noqa: E402
    KNOWN_FIELDS,
    MISSING_SEGMENT,
    RAW_FIELDS,
    ContractError,
    assert_row_schema,
    author_key,
    build_record,
)
from policy import SUFFICIENCY_N_MIN, recency_gate  # noqa: E402

DEFAULT_INPUT = ROOT / "data/input/reviews_50products.json"
DEFAULT_OUTPUT = ROOT / "eval/reports/input_contract_per176.json"

DATE_PATTERN = re.compile(r"^\d{4}\.\d{2}\.\d{2}$")


def pct(part: int, whole: int) -> float:
    return round(100 * part / whole, 2) if whole else 0.0


# --- 1. 집계 단위 ---


def measure_units(rows: list[dict], catalog) -> dict:
    """세 후보 단위를 같은 잣대로 비교한다.

    잣대는 두 개다.
      파편화   한 제품의 근거가 몇 갈래로 갈리는가 (주장이 몇 번 중복 생성되는가)
      사장     단위 자체가 N_min 을 못 넘어 **어떤 주장도 만들 수 없는** 리뷰가 몇 건인가

    충분성 셀 수는 단위마다 세는 대상이 달라(SKU×세그먼트 vs 제품×세그먼트) 크기를
    직접 비교하면 안 된다. 그래서 셀 수는 참고로만 싣고 판단 근거로 쓰지 않는다.
    """
    keys = {
        "goodsNo": lambda r: r["goodsNo"],
        "productId": lambda r: catalog.resolve_goods_no(r["goodsNo"], r["reviewDate"]),
        "productKeyRowString": lambda r: r["productKey"],
    }
    out = {}
    for name, keyf in keys.items():
        sizes = collections.Counter(keyf(r) for r in rows)
        thin = {k for k, v in sizes.items() if v < SUFFICIENCY_N_MIN}
        cells = collections.defaultdict(set)
        for r in rows:
            seg = (r.get("skinType") or "").strip() or MISSING_SEGMENT
            cells[(keyf(r), seg)].add(author_key(r["userName"]))
        out[name] = {
            "units": len(sizes),
            "reviewsPerUnit": {
                "min": min(sizes.values()),
                "median": sorted(sizes.values())[len(sizes) // 2],
                "max": max(sizes.values()),
            },
            "unitsBelowNMin": len(thin),
            "reviewsStrandedBelowNMin": sum(sizes[k] for k in thin),
            "cellsWithSkinType": len(cells),
            "cellsAtLeastNMinAfterAuthorDedup": sum(
                1 for v in cells.values() if len(v) >= SUFFICIENCY_N_MIN
            ),
        }

    # 파편화: 한 제품이 몇 개의 goodsNo 로 갈리는가
    by_product = collections.defaultdict(set)
    for r in rows:
        by_product[keys["productId"](r)].add(r["goodsNo"])
    fan = collections.Counter(len(v) for v in by_product.values())
    out["fragmentation"] = {
        "productsWithMultipleGoodsNos": sum(1 for v in by_product.values() if len(v) > 1),
        "products": len(by_product),
        "maxGoodsNosPerProduct": max(len(v) for v in by_product.values()),
        "goodsNosPerProductHistogram": {str(k): fan[k] for k in sorted(fan)},
    }

    # 행의 productKey 는 리뉴얼 세대를 모른다 — 이름이 키로 못 쓰이는 이유 (PER-172)
    generations = collections.defaultdict(set)
    for r in rows:
        generations[r["productKey"]].add(keys["productId"](r))
    split = {k: sorted(v) for k, v in generations.items() if len(v) > 1}
    out["productKeyRowString"]["lineagesHidingMultipleGenerations"] = len(split)
    out["productKeyRowString"]["example"] = (
        {"productKey": next(iter(split)), "productIds": split[next(iter(split))]} if split else None
    )

    # 크롤 목표(500건) 미달 SKU — 문서에 인용돼 온 '139' 의 실제 정의
    sizes = collections.Counter(r["goodsNo"] for r in rows)
    out["goodsNo"]["belowCrawlTarget500"] = sum(1 for v in sizes.values() if v < 500)
    return out


# --- 2. 입력 프로파일 ---


def measure_profile(rows: list[dict], catalog) -> dict:
    n = len(rows)
    codebook = load_codebook()
    ids = [r["reviewId"] for r in rows]
    field_sets = {frozenset(r) for r in rows}

    nulls = collections.Counter()
    for r in rows:
        for k, v in r.items():
            if v is None:
                nulls[k] += 1
            elif isinstance(v, str) and not v.strip():
                nulls[k + " (공백)"] += 1

    stated = {}
    out_of_domain = collections.Counter()
    # skinTone 은 조건축이 아니지만(§4) 코드 도메인은 검증한다 — 승격 시 이미 검증된 값이어야 한다
    for axis in ("skinType", "skinTrouble", "skinTone"):
        domain = codebook.domain(axis)
        count = 0
        for r in rows:
            values = r.get(axis) if axis == "skinTrouble" else [r.get(axis)]
            values = [v for v in (values or []) if (v or "").strip()]
            if values:
                count += 1
            for v in values:
                if v not in domain:
                    out_of_domain[axis] += 1
        stated[axis] = count
    stated["option"] = sum(1 for r in rows if (r.get("option") or "").strip())

    recency_kept = sum(1 for r in rows if recency_gate(r["reviewDate"]).passed)

    return {
        "reviews": n,
        "schema": {
            "fieldsPerRow": sorted(next(iter(field_sets))),
            "distinctFieldSets": len(field_sets),
            "matchesContract": field_sets == {frozenset(KNOWN_FIELDS)},
            "rawFields": len(RAW_FIELDS),
        },
        "identifiers": {
            "reviewIdDuplicates": n - len(set(ids)),
            "reviewIdNonPositiveInt": sum(
                1 for i in ids if isinstance(i, bool) or not isinstance(i, int) or i <= 0
            ),
            "reviewIdsSpanningMultipleGoodsNos": len(
                {k for k, v in _by_review_id(rows).items() if len(v) > 1}
            ),
            "unresolvedGoodsNos": sorted(
                {r["goodsNo"] for r in rows if not _resolves(catalog, r)}
            ),
        },
        "requiredFieldMissing": {
            f: sum(1 for r in rows if not str(r.get(f) or "").strip())
            for f in ("content", "reviewDate", "userName")
        },
        "nullOrBlankByField": dict(sorted(nulls.items())),
        "rating": {
            "distribution": dict(sorted(collections.Counter(r["rating"] for r in rows).items())),
            "outOfRange": sum(1 for r in rows if not 1 <= r["rating"] <= 5),
        },
        "reviewDate": {
            "formatViolations": sum(1 for r in rows if not DATE_PATTERN.match(str(r["reviewDate"]))),
            "earliest": min(r["reviewDate"] for r in rows),
            "latest": max(r["reviewDate"] for r in rows),
            "insideRecencyWindow": recency_kept,
            "insideRecencyWindowPct": pct(recency_kept, n),
        },
        "conditionAxes": {
            "statedPct": {k: pct(v, n) for k, v in stated.items()},
            "outOfCodebookDomain": dict(out_of_domain),
            "codebookCodes": {a: len(codebook.domain(a)) for a in codebook.axes},
            "optionDistinctValues": len({(r.get("option") or "").strip() for r in rows} - {""}),
        },
        "authorKey": {
            "distinct": len({author_key(r["userName"]) for r in rows}),
            "nonNfcNames": sum(
                1 for r in rows if r["userName"] != unicodedata.normalize("NFC", r["userName"])
            ),
        },
    }


def _by_review_id(rows: list[dict]) -> dict:
    """같은 reviewId 가 여러 SKU 에 걸치면 식별자가 제품에 종속된다는 뜻이다 (현 스냅샷 0)."""
    out = collections.defaultdict(set)
    for r in rows:
        out[r["reviewId"]].add(r["goodsNo"])
    return out


def _resolves(catalog, row: dict) -> bool:
    try:
        catalog.resolve_goods_no(row["goodsNo"], row["reviewDate"])
        return True
    except UnknownGoodsNoError:
        return False


# --- 3. 강제 커버리지 ---

GOOD_ROW = {
    "reviewId": 1, "content": "보습은 좋은데 향이 강해요", "rating": 4,
    "reviewDate": "2026.07.19", "userName": "돌핀러", "goodsNo": "A000000211119",
    "requestedGoodsNo": "A000000211119", "productName": "테스트 상품 40ml",
    "option": "40+40ml", "skinType": "A04", "skinTone": "B03", "skinTrouble": ["C05"],
    "reviewType": "NORMAL", "isRepurchase": False, "isMonthUseReview": False,
    "isMonthOverReview": False, "hasPhoto": False, "usefulPoint": 0, "recommendCount": 0,
    "productKey": "믿으면 안 되는 값", "category": "믿으면 안 되는 값",
    "profileImageUrl": "", "reviewImages": [], "reviewerRank": None, "isTopReviewer": False,
}

# 위반 클래스 → 그 클래스를 대표하는 입력. 계약 문서의 §와 대응한다.
VIOLATIONS = {
    "필수 필드 결측": {"content": None},
    "필수 필드 공백": {"userName": "   "},
    "reviewId 타입": {"reviewId": "1"},
    "reviewId 비양수": {"reviewId": 0},
    "rating 범위 밖": {"rating": 7},
    "rating 타입": {"rating": "4"},
    "날짜 파싱 불가": {"reviewDate": "어제"},
    "조건 코드 도메인 밖": {"skinType": "A99"},
    "조건 코드가 라벨": {"skinType": "건성"},
    "조건축 혼용": {"skinType": "B03"},
    "다중 조건축이 문자열": {"skinTrouble": "C05"},
    "모르는 필드": {"usagePeriod": "3개월"},
}


def measure_enforcement() -> dict:
    """위반 케이스를 실제로 넣어본다. 통과해버린 것이 곧 계약의 구멍이다."""
    results = {}
    for name, patch in VIOLATIONS.items():
        row = dict(GOOD_ROW)
        row.update(patch)
        try:
            assert_row_schema(row)
            build_record(row, "p031")
        except ContractError as e:
            results[name] = {"raises": True, "error": str(e)[:160]}
        else:
            results[name] = {"raises": False, "error": None}
    leaks = [k for k, v in results.items() if not v["raises"]]
    return {
        "cases": len(results),
        "raising": sum(1 for v in results.values() if v["raises"]),
        "silentlyAccepted": leaks,
        "byCase": results,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    args = ap.parse_args()

    rows = json.loads(args.input.read_text())
    catalog = load_catalog()
    report = {
        "issue": "PER-176",
        "doc": "docs/INPUT_CONTRACT.md",
        "source": {"path": str(args.input.relative_to(ROOT)), "reviews": len(rows)},
        "sufficiencyNMin": SUFFICIENCY_N_MIN,
        "aggregationUnit": measure_units(rows, catalog),
        "inputProfile": measure_profile(rows, catalog),
        "enforcement": measure_enforcement(),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    u = report["aggregationUnit"]
    print(f"[집계 단위]  N_min={SUFFICIENCY_N_MIN}")
    for name in ("goodsNo", "productId", "productKeyRowString"):
        v = u[name]
        print(f"  {name:20s} 단위 {v['units']:4d} | N<{SUFFICIENCY_N_MIN} 단위 {v['unitsBelowNMin']:3d}"
              f" (리뷰 {v['reviewsStrandedBelowNMin']:4d}건 사장) | 셀 {v['cellsWithSkinType']:4d}"
              f" → 통과 {v['cellsAtLeastNMinAfterAuthorDedup']}")
    f = u["fragmentation"]
    print(f"  파편화: 제품 {f['products']}개 중 {f['productsWithMultipleGoodsNos']}개가 "
          f"복수 goodsNo (최대 {f['maxGoodsNosPerProduct']}개)")
    print(f"  행의 productKey 가 세대를 못 가르는 계보: "
          f"{u['productKeyRowString']['lineagesHidingMultipleGenerations']}개")

    p = report["inputProfile"]
    print(f"\n[입력 프로파일] 스키마 일치 {p['schema']['matchesContract']} | "
          f"reviewId 중복 {p['identifiers']['reviewIdDuplicates']} | "
          f"미등록 goodsNo {len(p['identifiers']['unresolvedGoodsNos'])} | "
          f"rating 범위 밖 {p['rating']['outOfRange']} | "
          f"날짜 형식 위반 {p['reviewDate']['formatViolations']} | "
          f"도메인 밖 코드 {sum(p['conditionAxes']['outOfCodebookDomain'].values())}")

    e = report["enforcement"]
    print(f"\n[강제 커버리지] {e['raising']}/{e['cases']} 위반 클래스가 에러")
    print(f"→ {args.out.relative_to(ROOT)}")
    if e["silentlyAccepted"]:
        print(f"FAIL: 조용히 통과한 위반 {e['silentlyAccepted']}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
