"""발표 데모 슬라이드에 쓸 실제 claim 을 고른다 (PER-191).

발표 자료의 데모는 지금까지 **손으로 만든 목업**이었다. 그걸 진짜 산출물로 바꾼다.
고르는 규칙을 코드에 두는 이유는, 발표할 때마다 "인상적인 걸 골랐다"가 되지 않게
하려는 것이다 — 규칙은 아래 하나뿐이고 결과는 결정론적이다.

한 제품에서 셋을 고른다.

  1. 조건이 **기재된** 적합성 주장 — "건성인데 …" 가 진짜 건성 셀에서 나온 것
  2. 리스크 주장 — 사람들이 유튜브에서 "[제품명] 단점" 을 찾는 자리
  3. **만들지 않은 주장** — 게이트4 에서 근거 부족으로 탈락한 실물

3번이 데모의 핵심이다. "답이 없다" 를 목업으로 지어내지 않고 **원장에 남은 실제 탈락
행**을 쓴다. 근거가 모자라면 말하지 않는다는 것이 이 파이프라인의 기본값이기 때문이다.

사용:
  .venv/bin/python eval/share/deck_demo_data.py --product p044
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / "pipeline"))

from catalog import load_catalog  # noqa: E402
from codebook import load_codebook  # noqa: E402
from contracts import MISSING_SEGMENT  # noqa: E402
from ledger import load_inputs, run_gates  # noqa: E402
from tag_contract import fold_invisible  # noqa: E402

CLAIMS = ROOT / "data/output/claims_v5.jsonl"
OUT = Path(__file__).parent / "deck_demo.json"


def stated(claim: dict) -> str | None:
    for axis in ("skinType", "skinTrouble"):
        v = claim["condition"].get(axis)
        if v and MISSING_SEGMENT not in v:
            return axis
    return None


def shape(c: dict, reviews: dict, book) -> dict:
    s = c["support"]
    axis = stated(c)
    label = None
    if axis:
        label = " · ".join(book.label(axis, code) for code in c["condition"][axis])
    return {
        "claimId": c["claimId"],
        "decisionAxis": c["decisionAxis"],
        "aspect": c["aspect"],
        "conditionLabel": label,
        "question": c["question"],
        "verdict": c["verdict"],
        "answer": c["answer"],
        "positive": s["positiveAuthors"],
        "negative": s["negativeAuthors"],
        "spoke": s["spokeAuthors"],
        "silent": s["silentAuthors"],
        "cell": s["cellAuthors"],
        "evidence": [
            {"reviewId": e["reviewId"], "stance": e["stance"], "quote": e["quote"],
             "full": reviews.get(e["reviewId"], ""),
             "at": fold_invisible(reviews.get(e["reviewId"], "")).find(fold_invisible(e["quote"])),
             "len": len(fold_invisible(e["quote"]))}
            for e in c["evidence"][:3]
        ],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--product", required=True)
    args = ap.parse_args()

    catalog, book = load_catalog(), load_codebook()
    product = catalog.product(args.product)
    claims = [json.loads(l) for l in CLAIMS.read_text().splitlines() if l.strip()]
    mine = [c for c in claims if c["productId"] == args.product]
    if not mine:
        raise SystemExit(f"FAIL: {args.product} 의 claim 이 없다")

    records, tags, cat = load_inputs()
    reviews = {r["reviewId"]: r["raw"]["content"] for r in records}

    def best(pred):
        rows = sorted((c for c in mine if pred(c)),
                      key=lambda c: (-c["support"]["spokeAuthors"], c["claimId"]))
        return rows[0] if rows else None

    picked, used = [], set()
    for want in (lambda c: c["decisionAxis"] == "적합성" and stated(c),
                 lambda c: c["decisionAxis"] == "리스크",
                 lambda c: c["decisionAxis"] == "실사용맥락",
                 lambda c: True):
        c = best(lambda x: want(x) and x["claimId"] not in used)
        if c:
            used.add(c["claimId"])
            picked.append(shape(c, reviews, book))
        if len(picked) >= 2:
            break

    # 만들지 않은 주장 — 원장의 실제 탈락 행에서. 지어내지 않는다.
    run = run_gates(records, tags, cat)
    rejected = [r for r in run.ledger.rows
                if r.unit == "claim" and r.subject.startswith(args.product + "|")]
    withheld = None
    if rejected:
        # 근거가 가장 적은 것 = "왜 말하지 않는가" 가 가장 선명한 것
        row = sorted(rejected, key=lambda r: ((r.metrics or {}).get("supportAuthors", 0), r.subject))[0]
        withheld = {"claimId": row.subject, "reason": row.reason,
                    "gate": row.gate, "detail": row.detail,
                    "metrics": row.metrics or {}}

    payload = {
        "product": {"productId": product.product_id, "name": product.display_name,
                    "category": product.category},
        "claims": picked,
        "withheld": withheld,
        "totals": {"claimsForProduct": len(mine),
                   "quotes": sum(len(c["evidence"]) for c in mine)},
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(f"{product.display_name} · claim {len(mine)}건 중 {len(picked)}건 선정")
    for c in picked:
        print(f"  [{c['decisionAxis']}] {c['question']}")
        print(f"      → {c['verdict']}")
    if withheld:
        print(f"  [만들지 않음] {withheld['claimId']} — {withheld['reason']} {withheld['metrics']}")
    print(f"→ {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
