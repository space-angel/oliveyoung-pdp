"""
v4 골든셋 15문항이 v5 규격으로 옮겨지는가 (PER-178 완료 조건 마지막 항목).

v4 골든셋(`legacy/v4/eval/golden_set.json`)은 {question, productKey, category, supportingReviewIds,
rationale} 다. v5 라벨(`pipeline/golden_contract.py`)은 answer · condition · direction ·
evidence[{reviewId, quote, stance}] · failureReasons 를 요구한다. 옮길 수 있는지는 두 가지로 갈린다.

  구조   productKey → productId 가 카탈로그로 풀리는가, supportingReviewIds 가 v5 코퍼스에 있는가,
         리센시 컷을 통과하는가 (파이프라인이 근거로 쓸 수 있는가)
  내용   answer · condition · direction · quote 는 v4 에 **없다.** 원문을 다시 읽지 않으면 채울 수 없다

이 스크립트는 구조 쪽을 **측정**한다. 내용 쪽은 측정할 것이 없다 — 필드가 없다.

  입력  legacy/v4/eval/golden_set.json · data/intermediate/v5_reviews.jsonl · data/input/product_catalog.json
  출력  eval/reports/v4_golden_migration_per178.json   (커밋)

사용:
  python3 eval/measure_v4_golden_migration.py
  python3 eval/measure_v4_golden_migration.py --check   # 입력 해시·결과가 리포트와 같은지
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

from catalog import UnknownProductError, load_catalog  # noqa: E402
from golden_contract import LABEL_FIELDS  # noqa: E402
from policy import RECENCY_CUTOFF_MONTH, SUFFICIENCY_N_MIN, recency_gate  # noqa: E402

V4_GOLDEN_PATH = ROOT / "legacy/v4/eval/golden_set.json"
REVIEWS_PATH = ROOT / "data/intermediate/v5_reviews.jsonl"
CATALOG_PATH = ROOT / "data/input/product_catalog.json"
REPORT_PATH = ROOT / "eval/reports/v4_golden_migration_per178.json"

# v4 필드 → v5 필드. None 은 v5 에 대응 필드가 없다는 뜻이다.
FIELD_MAP = {
    "goldenId": "labelId",
    "productKey": "productId (카탈로그 displayName 으로 해석)",
    "category": None,  # v4 의 실사용/리스크/적합성 분류. v5 는 aspect(14종) + direction 으로 표현
    "question": "question",
    "supportingReviewIds": "evidence[].reviewId (quote · stance 는 없음)",
    "rationale": "notes",
}
V5_ONLY = sorted(set(LABEL_FIELDS) - {"labelId", "productId", "question", "notes", "bundleId"} - {"evidence"})


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def measure() -> dict:
    golden = json.loads(V4_GOLDEN_PATH.read_text())["goldenSet"]
    if not REVIEWS_PATH.exists():
        raise SystemExit(f"입수 결과가 없다 ({REVIEWS_PATH.relative_to(ROOT)}). 먼저 pipeline/ingest.py 를 돌려라")
    reviews = {}
    for line in REVIEWS_PATH.read_text().splitlines():
        if line:
            r = json.loads(line)
            reviews[r["reviewId"]] = r
    catalog = load_catalog(CATALOG_PATH)

    items = []
    for g in golden:
        try:
            product = catalog.by_display_name(g["productKey"])
            product_id = product.product_id
            lineage = [p.product_id for p in catalog.lineage_of(product_id)]
        except UnknownProductError:
            product_id, lineage = None, []
        ids = g["supportingReviewIds"]
        in_corpus = [i for i in ids if i in reviews]
        recent = [i for i in in_corpus if recency_gate(reviews[i]["raw"]["reviewDate"]).passed]
        same_product = [i for i in in_corpus if reviews[i]["productId"] in lineage]
        authors = {reviews[i]["derived"]["authorKey"] for i in recent}
        usable = [i for i in recent if reviews[i]["productId"] in lineage]
        items.append(
            {
                "goldenId": g["goldenId"],
                "productKey": g["productKey"],
                "productId": product_id,
                "lineage": lineage,
                "category_v4": g["category"],
                "supportingReviewIds": len(ids),
                "inV5Corpus": len(in_corpus),
                "recencyPassed": len(recent),
                "sameLineage": len(same_product),
                "usableEvidence": len(usable),
                "usableUniqueAuthors": len(authors),
                "meetsNmin": len(authors) >= SUFFICIENCY_N_MIN,
            }
        )

    total_ids = sum(i["supportingReviewIds"] for i in items)
    return {
        "issue": "PER-178",
        "question": "v4 골든셋 15문항을 v5 라벨 규격(concern-golden-v1)으로 마이그레이션할 수 있는가",
        "source": {
            "v4Golden": {"path": str(V4_GOLDEN_PATH.relative_to(ROOT)), "sha256": sha256(V4_GOLDEN_PATH)},
            "reviews": {"path": str(REVIEWS_PATH.relative_to(ROOT)), "sha256": sha256(REVIEWS_PATH)},
            "catalog": {"path": str(CATALOG_PATH.relative_to(ROOT)), "sha256": sha256(CATALOG_PATH)},
        },
        "recencyCutoffMonth": RECENCY_CUTOFF_MONTH,
        "nMin": SUFFICIENCY_N_MIN,
        "structure": {
            "items": len(items),
            "productResolved": sum(1 for i in items if i["productId"]),
            "supportingReviewIds": total_ids,
            "inV5Corpus": sum(i["inV5Corpus"] for i in items),
            "recencyPassed": sum(i["recencyPassed"] for i in items),
            "usableEvidence": sum(i["usableEvidence"] for i in items),
            "itemsWithZeroUsable": sum(1 for i in items if i["usableEvidence"] == 0),
            "itemsMeetingNmin": sum(1 for i in items if i["meetsNmin"]),
        },
        "content": {
            "fieldMap": FIELD_MAP,
            "v5FieldsAbsentInV4": V5_ONLY,
            "note": "answer · condition · direction · evidence.quote · evidence.stance · failureReasons 는 v4 에 없다. 원문을 다시 읽어야 채워지므로 '변환'이 아니라 '재라벨'이다",
        },
        "perItem": items,
        "verdict": (
            "구조 마이그레이션은 가능하지만(제품 15/15 해석) 근거는 살아남지 않는다 — "
            f"근거 ID {total_ids}개 중 v5 코퍼스에 {sum(i['inV5Corpus'] for i in items)}개, "
            f"리센시 통과 {sum(i['recencyPassed'] for i in items)}개, N_min 충족 문항 {sum(1 for i in items if i['meetsNmin'])}/15. "
            "내용 필드(answer·condition·direction·quote)는 v4 에 없어 변환할 것이 없다. "
            "→ v4 15문항은 v5 골든셋으로 옮기지 않고 v4 대비 비교(PER-201)의 v4 쪽 기준으로만 남긴다. "
            "v4 5제품은 블라인드가 깨져 v5 표본에서도 뺐다 (sample_concern_golden.py)"
        ),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    report = measure()
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.check:
        if not REPORT_PATH.exists() or REPORT_PATH.read_text() != payload:
            raise SystemExit(f"FAIL: {REPORT_PATH.relative_to(ROOT)} 가 재실행 결과와 다르다. 다시 생성하라")
        print(f"OK: v4 골든셋 마이그레이션 리포트 재현 확인 ({report['structure']['items']}문항)")
        return
    REPORT_PATH.write_text(payload)
    s = report["structure"]
    print(f"[v4 골든셋] {s['items']}문항 · 제품 해석 {s['productResolved']} · 근거 ID {s['supportingReviewIds']} → v5 코퍼스 {s['inV5Corpus']} → 리센시 {s['recencyPassed']} → 같은 계보 {s['usableEvidence']}")
    print(f"           근거 0개 문항 {s['itemsWithZeroUsable']} · N_min({report['nMin']}) 충족 문항 {s['itemsMeetingNmin']}")
    print(f"           v4 에 없는 v5 필드: {report['content']['v5FieldsAbsentInV4']}")
    print(f"→ {REPORT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
