"""
전수 태깅 비용 재계산 (PER-175).

이슈의 비용표는 **v4 실측 리뷰당 1.65클레임**을 깔고 있다. 파일럿 정답셋에서
코퍼스 가중 추정이 리뷰당 2.06태그로 나왔으므로 출력 토큰이 그만큼 늘어난다.

측정된 값과 추정한 값을 섞지 않는다:
  measured  PER-175 count_tokens 실측 (입력 7.12M · 배치 20건당 5,693 · 시스템 829)
  measured  파일럿 정답셋의 리뷰당 태그 수 (eval/reports/v5_tag_pilot.json)
  estimated 프롬프트 v1 이 v4 대비 길어진 배수 → 시스템 토큰 (문자수 비례, count_tokens 미실행)
  estimated 태그 1개당 출력 토큰 (v4 산식을 그대로 승계)

단가는 claude-api 스킬의 표 (2026-06-24 캐시본): Haiku 4.5 $1/$5, Sonnet 5 $2/$10 per 1M.
Batch API 50%, 캐시 읽기 0.1x.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
PILOT = ROOT / "eval/reports/v5_tag_pilot.json"
OUT = ROOT / "eval/reports/v5_tag_cost_estimate.json"

REVIEWS = 25_000
BATCH_SIZE = 20
INPUT_TOKENS_PER_BATCH = 5_693      # PER-175 count_tokens 실측
SYSTEM_TOKENS_V4 = 829              # 같음 (고정 프리픽스)
CLAIMS_PER_REVIEW_V4 = 1.65         # v4 실측 — 이슈 비용표의 전제
OUTPUT_TOKENS_V4 = 2_620_000        # 위 전제에서 나온 출력 총량

PRICES = {"haiku-4-5": (1.0, 5.0), "sonnet-5": (2.0, 10.0)}
BATCH_DISCOUNT = 0.5
CACHE_READ_MULTIPLIER = 0.1


def main() -> None:
    pilot = json.loads(PILOT.read_text())
    tags_per_review = pilot["corpusEstimate"]["estTagsPerReview"]

    # 프롬프트가 길어진 만큼 고정 프리픽스가 늘어난다 (문자수 비례 — 실측 아님)
    v5_chars = len((ROOT / "pipeline/prompts/tag/v1.md").read_bytes().decode())
    v4_chars = len((ROOT / "legacy/v4/pipeline/prompts/step1/v1.md").read_bytes().decode())
    system_tokens = round(SYSTEM_TOKENS_V4 * v5_chars / v4_chars)

    batches = REVIEWS // BATCH_SIZE
    input_tokens = batches * (INPUT_TOKENS_PER_BATCH - SYSTEM_TOKENS_V4 + system_tokens)
    cached_tokens = batches * system_tokens          # 첫 배치 제외는 무시 (0.08%)
    output_tokens = round(OUTPUT_TOKENS_V4 * tags_per_review / CLAIMS_PER_REVIEW_V4)

    rows = {}
    for model, (in_price, out_price) in PRICES.items():
        plain_in = input_tokens / 1e6 * in_price
        cached_in = ((input_tokens - cached_tokens) / 1e6 * in_price
                     + cached_tokens / 1e6 * in_price * CACHE_READ_MULTIPLIER)
        out = output_tokens / 1e6 * out_price
        rows[model] = {
            "standard": round(plain_in + out, 2),
            "batch": round((plain_in + out) * BATCH_DISCOUNT, 2),
            "cached": round(cached_in + out, 2),
            "batchAndCached": round((cached_in + out) * BATCH_DISCOUNT, 2),
        }

    report = {
        "issue": "PER-175",
        "reviews": REVIEWS,
        "batches": batches,
        "measured": {
            "inputTokensPerBatch": INPUT_TOKENS_PER_BATCH,
            "systemTokensV4": SYSTEM_TOKENS_V4,
            "tagsPerReviewPilotCorpusWeighted": tags_per_review,
            "source": "eval/reports/v5_tag_pilot.json · PER-175 count_tokens",
        },
        "estimated": {
            "systemTokensV5": system_tokens,
            "basis": f"프롬프트 문자수 {v4_chars}→{v5_chars} 비례. count_tokens 미실행 (API 키 없음)",
            "inputTokens": input_tokens,
            "outputTokens": output_tokens,
            "outputVsIssueAssumption": f"{tags_per_review / CLAIMS_PER_REVIEW_V4:.2f}배 (1.65 → {tags_per_review})",
        },
        "usd": rows,
        "prices": {"note": "claude-api 스킬 표 (캐시 2026-06-24), Batch 50%, 캐시 읽기 0.1x", **{k: v for k, v in PRICES.items()}},
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"태그/리뷰 {tags_per_review} (이슈 전제 {CLAIMS_PER_REVIEW_V4}) → 출력 {output_tokens:,} 토큰")
    print(f"시스템 프롬프트 {SYSTEM_TOKENS_V4} → {system_tokens} 토큰 (추정)")
    for m, r in rows.items():
        print(f"  {m:10s} 표준 ${r['standard']:6.2f} · Batch ${r['batch']:6.2f} · 캐싱 ${r['cached']:6.2f} · 둘다 ${r['batchAndCached']:6.2f}")
    print(f"→ {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
