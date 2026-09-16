"""생성된 claim 전건을 사람이 쓰는 두 모양으로 내보낸다 (PER-191).

1. `data/output/claims_v5.csv`      — 표 도구용. claim 1건 = 1행
2. `eval/share/claims_data.json`    — 탐색 페이지용. 인용까지 들고 있다

**수치를 새로 만들지 않는다.** `claims_v5.jsonl` 과 카탈로그·코드북에서만 읽는다.

사용:
  .venv/bin/python eval/share/export_claims.py
"""
import collections
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / "pipeline"))

from catalog import load_catalog  # noqa: E402
from codebook import load_codebook  # noqa: E402
from condition_render import render_subject  # noqa: E402
from tag_contract import fold_invisible  # noqa: E402

CLAIMS = ROOT / "data/output/claims_v5.jsonl"
META = ROOT / "data/output/claims_v5_meta.json"
CSV_OUT = ROOT / "data/output/claims_v5.csv"
JSON_OUT = Path(__file__).parent / "claims_data.json"

HEDGE = {
    "direction_mixed": "의견이 갈린다",
    "silence_dominates": "말하지 않은 사람이 많다",
    "few_authors": "말한 사람이 적다",
    "gate_limitation": "게이트가 한계를 남겼다",
}
LIMIT = {
    "tagger_direction_error": "태거 방향 오류 가능",
    "order_chosen_direction": "방향이 출력 순서로 정해짐",
    "minority_within_tagger_noise": "소수가 잡음 범위",
    "single_dissent": "반대 1명",
    "renewal_unobserved": "리뉴얼 세대 미확정",
}
DIRECTION = {"mixed": "혼재", "positive": "긍정", "negative": "부정", "neutral": "중립"}


def condition_phrase(condition: dict) -> str:
    """조건을 사람이 읽는 구로. 표기에서만 라벨을 붙인다 (PER-192).

    조건만 필요하므로 `render_subject` 를 쓴다 — `render` 는 결과 문장을 요구한다.
    """
    return render_subject(condition)


def main() -> None:
    if not CLAIMS.exists():
        raise SystemExit(f"FAIL: 산출물이 없다 ({CLAIMS.relative_to(ROOT)})")
    rows = [json.loads(l) for l in CLAIMS.read_text().splitlines() if l.strip()]
    meta = json.loads(META.read_text())
    catalog = load_catalog()
    name = {}
    for c in rows:
        if c["productId"] not in name:
            name[c["productId"]] = catalog.product(c["productId"]).display_name

    shaped = []
    for c in rows:
        s = c["support"]
        shaped.append({
            "claimId": c["claimId"],
            "productId": c["productId"],
            "product": name[c["productId"]],
            "aspect": c["aspect"],
            "claimType": c["claimType"],
            "condition": condition_phrase(c["condition"]),
            "direction": c["direction"],
            "directionLabel": DIRECTION.get(c["direction"], c["direction"]),
            "question": c["question"],
            "answer": c["answer"],
            "positive": s["positiveAuthors"],
            "negative": s["negativeAuthors"],
            "neutral": s["neutralAuthors"],
            "spoke": s["spokeAuthors"],
            "silent": s["silentAuthors"],
            "cell": s["cellAuthors"],
            "confidence": c["confidence"]["band"],
            "hedgeReasons": [HEDGE.get(r, r) for r in c["confidence"]["reasons"]],
            "limitations": [LIMIT.get(l, l) for l in c["limitations"]],
            "evidence": [{"reviewId": e["reviewId"], "stance": e["stance"], "quote": e["quote"]}
                         for e in c["evidence"]],
        })
    shaped.sort(key=lambda r: (r["product"], r["aspect"], r["condition"], r["claimId"]))

    # --- 인용을 원문 안에서 보여주기 위한 재료 (하이라이트)
    #
    # 인용만 떼어 보여주면 읽는 사람이 그게 정말 그 리뷰의 문장인지 확인할 수 없다.
    # 원문을 통째로 싣고 인용 구간을 표시한다.
    #
    # 오프셋은 **접은 본문 기준**이다. `fold_invisible` 이 CRLF 를 \n 으로 합치고
    # 폭 없는 문자를 지우므로 원본 문자열의 인덱스와 다르다 — 접은 본문을 그대로
    # 실어서 인덱스가 어긋날 자리를 없앤다. 접기는 글자·띄어쓰기 수를 바꾸지 않으므로
    # 읽는 사람이 보는 문장은 원문 그대로다 (PER-175 · PER-190).
    cited = {e["reviewId"] for r in shaped for e in r["evidence"]}
    body = {}
    for line in (ROOT / "data/intermediate/v5_reviews.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec["reviewId"] in cited:
            body[rec["reviewId"]] = {
                "text": fold_invisible(rec["raw"]["content"]),
                "rating": rec["raw"]["rating"],
                "date": rec["raw"]["reviewDate"],
            }
    unresolved = 0
    for r in shaped:
        for e in r["evidence"]:
            text = body[e["reviewId"]]["text"]
            at = text.find(fold_invisible(e["quote"]))
            if at < 0:                       # 생성 게이트가 막으므로 여기 오면 안 된다
                unresolved += 1
                continue
            e["at"] = at
            e["len"] = len(fold_invisible(e["quote"]))
    if unresolved:
        raise SystemExit(
            f"FAIL: 인용 {unresolved}개를 원문에서 찾지 못했다 — 생성 게이트(PER-190)가 "
            "통과시킨 인용은 전부 원문 부분문자열이어야 한다")

    # --- CSV: 인용은 줄바꿈으로 이어 붙인다. 표 도구에서 셀 안 줄바꿈으로 읽힌다
    CSV_OUT.parent.mkdir(parents=True, exist_ok=True)
    cols = ["claimId", "product", "aspect", "condition", "claimType", "directionLabel",
            "question", "answer", "positive", "negative", "neutral", "spoke", "silent",
            "cell", "confidence", "hedgeReasons", "limitations", "evidenceCount", "quotes"]
    with CSV_OUT.open("w", newline="", encoding="utf-8-sig") as f:   # BOM — 엑셀 한글
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in shaped:
            w.writerow({
                **{k: r[k] for k in cols if k in r and k not in
                   ("hedgeReasons", "limitations", "evidenceCount", "quotes")},
                "hedgeReasons": " · ".join(r["hedgeReasons"]),
                "limitations": " · ".join(r["limitations"]),
                "evidenceCount": len(r["evidence"]),
                "quotes": "\n".join(f'[{e["stance"]}] {e["quote"]} (리뷰 {e["reviewId"]})'
                                    for e in r["evidence"]),
            })

    payload = {
        "issue": "PER-191",
        "model": meta.get("modelId"),
        "promptVersion": (meta.get("promptVersion") or {}).get("version"),
        "run": {k: meta["run"][k] for k in ("targets", "produced", "skipped", "producedPct")},
        "facets": {
            "products": sorted({r["product"] for r in shaped}),
            "aspects": sorted({r["aspect"] for r in shaped}),
            "conditions": sorted({r["condition"] for r in shaped}),
            "directions": ["혼재", "긍정", "부정"],
        },
        "totals": {
            "claims": len(shaped),
            "quotes": sum(len(r["evidence"]) for r in shaped),
            "conditional": sum(1 for r in shaped if r["claimType"] == "conditional"),
            "unconditional": sum(1 for r in shaped if r["claimType"] == "unconditional"),
            "byDirection": dict(collections.Counter(r["directionLabel"] for r in shaped)),
        },
        "claims": shaped,
        "reviews": body,
    }
    JSON_OUT.write_text(json.dumps(payload, ensure_ascii=False) + "\n")

    print(f"claim {len(shaped):,}건 · 인용 {payload['totals']['quotes']:,}개 · "
          f"제품 {len(payload['facets']['products'])} · 주제 {len(payload['facets']['aspects'])}")
    print(f"원문 {len(body):,}건 실음 · 인용 위치 전건 확인")
    print(f"→ {CSV_OUT.relative_to(ROOT)}  ({CSV_OUT.stat().st_size:,} bytes)")
    print(f"→ {JSON_OUT.relative_to(ROOT)}  ({JSON_OUT.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
