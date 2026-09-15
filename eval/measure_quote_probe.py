"""콘솔 프로브 — Bedrock 후보 모델 4종의 인용 원문성 (PER-175).

**이 프로브는 재현 실행이 아니다.** 입력은 사람이 Bedrock 콘솔 플레이그라운드에
프롬프트 v1 을 붙여 넣고 받은 응답을 그대로 옮긴 것이다 (2026-09-15). API 키가
없어 파일럿 200건을 못 돌리는 동안, **어떤 실패가 나는지**만 먼저 본 것이다.

  표본 2건 · 정답 태그 7개. **모델 순위를 정하는 데이터가 아니다.**
  결정 근거는 파일럿 200건 + 정답셋 v2(432태그)다 (eval/validate_tags.py).

여기서 고정하는 것은 하나다 — **4개 모델 중 3개가 인용을 "고쳐서" 떨어졌고,
고친 방식이 조사·맞춤법·오타로 각각 달랐다.** 이 관찰이 프롬프트 v2 의 근거다
(pipeline/prompts/tag/v2.md 규칙 4).

  .venv/bin/python3 eval/measure_quote_probe.py
  .venv/bin/python3 eval/measure_quote_probe.py --check
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

from tag import load_reviews, partition_by_contract  # noqa: E402
from tag_contract import prompt_version  # noqa: E402

REPORT_PATH = ROOT / "eval/reports/v5_tag_quote_probe.json"
GOLD_PATH = ROOT / "eval/gold/v5_tags_pilot_gold.jsonl"
REVIEW_IDS = [4119503, 8653077]

# 콘솔에서 받은 응답 원문. 손으로 옮긴 것이라 여기가 정본이다 — 고치지 않는다.
RESPONSES: dict[str, dict] = {
    "minimax.minimax-m2.5": {"results": [{"reviewId": 4119503, "aspects": [{"aspect": "보습감", "polarity": "positive", "snippet": "촉촉하네요", "skinTypeHint": "A02"}, {"aspect": "보습감", "polarity": "negative", "snippet": "속보습은 못 느꼈어요", "skinTypeHint": "A02"}, {"aspect": "지속성", "polarity": "negative", "snippet": "금새 날라가는 느낌이에요", "skinTypeHint": "A02"}]}, {"reviewId": 8653077, "aspects": [{"aspect": "밀착력", "polarity": "positive", "snippet": "용기가 눈가에 바르기 좋게 양조절을 쉽게할 수 있어", "skinTypeHint": None}, {"aspect": "발림감/텍스처", "polarity": "positive", "snippet": "발림성은 부드럽고", "skinTypeHint": None}, {"aspect": "흡수력", "polarity": "positive", "snippet": "흡수가 빠른편이네요", "skinTypeHint": None}]}]},
    "openai.gpt-oss-safeguard-120b": {"results": [{"reviewId": 4119503, "aspects": [{"aspect": "보습감", "polarity": "positive", "snippet": "순하고 촉촉하네요.", "skinTypeHint": "A02"}, {"aspect": "지속성", "polarity": "negative", "snippet": "금새 날라가는 느낌이에요.", "skinTypeHint": "A02"}]}, {"reviewId": 8653077, "aspects": [{"aspect": "탄력/탱탱함", "polarity": "positive", "snippet": "주름개선 효과가 좋다는", "skinTypeHint": None}, {"aspect": "발림감/텍스처", "polarity": "positive", "snippet": "발림성은 부드럽고", "skinTypeHint": None}, {"aspect": "흡수력", "polarity": "positive", "snippet": "흡수도 빠른편이네요", "skinTypeHint": None}, {"aspect": "트러블/자극", "polarity": "positive", "snippet": "자극도 없습니다", "skinTypeHint": None}, {"aspect": "향", "polarity": "negative", "snippet": "향.. 몇번을 ㅁ써도 익숙해 지지가 않네요", "skinTypeHint": None}]}]},
    "openai.gpt-oss-20b": {"results": [{"reviewId": 4119503, "aspects": [{"aspect": "보습감", "polarity": "positive", "snippet": "순하고 촉촉하네요", "skinTypeHint": None}, {"aspect": "지속성", "polarity": "negative", "snippet": "금세 날라가는 느낌", "skinTypeHint": None}]}, {"reviewId": 8653077, "aspects": [{"aspect": "보습감", "polarity": "positive", "snippet": "유수분감은 아주 충만", "skinTypeHint": None}, {"aspect": "발림감/텍스처", "polarity": "positive", "snippet": "발림성은 부드럽고", "skinTypeHint": None}, {"aspect": "흡수력", "polarity": "positive", "snippet": "흡수도 빠른편이네요", "skinTypeHint": None}, {"aspect": "트러블/자극", "polarity": "positive", "snippet": "자극도 없습니다", "skinTypeHint": None}, {"aspect": "향", "polarity": "negative", "snippet": "향.. 몇번을 ㅁ써도 익숙해", "skinTypeHint": None}]}]},
    "openai.gpt-oss-120b": {"results": [{"reviewId": 4119503, "aspects": [{"aspect": "보습감", "polarity": "positive", "snippet": "촉촉하네요", "skinTypeHint": "A02"}, {"aspect": "지속성", "polarity": "negative", "snippet": "금새 날라가는 느낌이에요", "skinTypeHint": "A02"}]}, {"reviewId": 8653077, "aspects": [{"aspect": "탄력/탱탱함", "polarity": "positive", "snippet": "주름개선 효과가 좋다는", "skinTypeHint": None}, {"aspect": "보습감", "polarity": "positive", "snippet": "유수분감은 아주 충만하고", "skinTypeHint": None}, {"aspect": "트러블/자극", "polarity": "positive", "snippet": "자극도 없습니다", "skinTypeHint": None}, {"aspect": "발림감/텍스처", "polarity": "positive", "snippet": "발림성은 부드럽고 흡수도 빠른편이네요", "skinTypeHint": None}, {"aspect": "향", "polarity": "negative", "snippet": "향.. 몇번을 써도 익숙해 지지가 않네요", "skinTypeHint": None}]}]},
}


def score(name: str, payload: dict, reviews: dict, gold: list[dict]) -> dict:
    tags = [{"reviewId": r["reviewId"], **a} for r in payload["results"] for a in r["aspects"]]
    kept, violations = partition_by_contract(tags, reviews)

    gold_keys = {(g["reviewId"], g["aspect"]) for g in gold}
    gold_dir = {(g["reviewId"], g["aspect"]): g["polarity"] for g in gold}
    keys = {(t["reviewId"], t["aspect"]) for t in kept}
    hit = keys & gold_keys

    # 4119503 은 본문에 "전 건성이라" 가 있다 — 정답셋은 A02 를 매겼다.
    # 힌트를 라벨이 아니라 코드로 받는 것이 v4 카테고리 분포 FAIL 의 수습이다.
    hinted = [t.get("skinTypeHint") for t in kept if t["reviewId"] == 4119503]

    return {
        "model": name,
        "tagsEmitted": len(tags),
        "tagsPassed": len(kept),
        "contractViolations": len(violations),
        "violations": [
            {
                "reviewId": v["reviewId"],
                "aspect": v["aspect"],
                "snippet": v["snippet"],
                "violation": v["violation"],
            }
            for v in violations
        ],
        "goldOverlap": len(hit),
        "goldTags": len(gold_keys),
        "polarityAgreement": sum(
            1 for t in kept if (t["reviewId"], t["aspect"]) in gold_dir
            and t["polarity"] == gold_dir[(t["reviewId"], t["aspect"])]
        ),
        "skinTypeHintA02": f"{sum(1 for h in hinted if h == 'A02')}/{len(hinted)}",
        "falsePositiveAspects": sorted(a for _, a in keys - gold_keys),
        "missedAspects": sorted(a for _, a in gold_keys - keys),
    }


def build() -> dict:
    reviews = {r["reviewId"]: r for r in load_reviews(pilot=True) if r["reviewId"] in REVIEW_IDS}
    missing = [i for i in REVIEW_IDS if i not in reviews]
    if missing:
        raise SystemExit(f"표본에 없는 reviewId: {missing} — 표본이 바뀌었다면 프로브도 무의미하다")
    gold = [
        json.loads(l)
        for l in GOLD_PATH.read_text().splitlines()
        if l.strip() and json.loads(l)["reviewId"] in REVIEW_IDS
    ]

    scored = [score(name, payload, reviews, gold) for name, payload in RESPONSES.items()]
    quote_violations = [
        v for s in scored for v in s["violations"] if "부분문자열이 아니다" in v["violation"]
    ]

    return {
        "issue": "PER-175",
        "provenance": {
            "how": "Bedrock 콘솔 플레이그라운드에 프롬프트를 붙여 넣고 받은 응답을 손으로 옮겼다",
            "reproducible": False,
            "date": "2026-09-15",
            "prompt": prompt_version(),
            "settings": {"temperature": 0, "maxTokens": 8000, "serviceTier": "flex"},
        },
        "limitation": (
            "표본 2건 · 정답 태그 7개다. 모델 순위를 정하는 데이터가 아니고, "
            "결정 근거는 파일럿 200건 + 정답셋 v2(432태그)다"
        ),
        "reviewIds": REVIEW_IDS,
        "models": scored,
        "finding": {
            "modelsProbed": len(scored),
            "modelsWithQuoteViolation": len({v["snippet"] for v in quote_violations} and
                                            [s["model"] for s in scored
                                             if any("부분문자열이 아니다" in v["violation"]
                                                    for v in s["violations"])]),
            "quoteViolations": quote_violations,
            "note": (
                "프롬프트 v1 규칙 4 가 이미 '맞춤법 교정 금지' 를 명시했는데도 고쳤다. "
                "규칙을 어긴 것이 아니라 '원문이 틀렸을 때' 를 프롬프트가 말해주지 않았다. "
                "B1 코드 복원으로는 못 건진다 — 줄바꿈·공백이 아니라 글자가 다르다"
            ),
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="콘솔 프로브 채점 (PER-175)")
    ap.add_argument("--check", action="store_true", help="리포트가 재현되는지만 확인")
    args = ap.parse_args()

    report = build()
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"

    if args.check:
        if not REPORT_PATH.exists():
            raise SystemExit(f"FAIL: 리포트가 없다 ({REPORT_PATH.relative_to(ROOT)})")
        if REPORT_PATH.read_text() != payload:
            raise SystemExit(
                f"FAIL: 프로브 리포트가 재현되지 않는다 ({REPORT_PATH.relative_to(ROOT)})\n"
                "  → 계약 규칙이나 정답셋이 바뀌었다면 리포트를 다시 생성해 함께 커밋한다"
            )
        print(f"OK: 프로브 리포트 재현 일치 ({REPORT_PATH.relative_to(ROOT)})")
        return

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(payload)

    print(f"[프로브] 리뷰 {len(REVIEW_IDS)}건 · 정답 태그 {report['models'][0]['goldTags']}개 "
          f"— 순위가 아니라 실패 유형을 본다")
    for m in report["models"]:
        print(f"  {m['model']:<32} 낸 {m['tagsEmitted']} · 통과 {m['tagsPassed']} · "
              f"위반 {m['contractViolations']} · 정답겹침 {m['goldOverlap']}/{m['goldTags']} · "
              f"힌트 {m['skinTypeHintA02']}")
    f = report["finding"]
    print(f"  → {len(f['quoteViolations'])}건의 인용 변조 (모델 {f['modelsProbed']}개 중 "
          f"{f['modelsWithQuoteViolation']}개):")
    for v in f["quoteViolations"]:
        print(f"     {v['snippet']!r}")
    print(f"→ {REPORT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
