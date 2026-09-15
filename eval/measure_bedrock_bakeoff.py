"""Bedrock 후보 태거 200건 벤치 (PER-175).

Haiku 4.5 · Sonnet 5 를 잰 것과 **같은 조건**으로 Bedrock 모델을 잰다 —
같은 표본 200건, 같은 프롬프트 v1, 같은 청크 20, 같은 채점기(`eval/validate_tags.py`),
같은 정답셋 v2(432태그). 조건이 하나라도 다르면 한 표에 올리면 안 된다.

품질 수치는 커밋된 per-model 리포트(`eval/reports/v5_tag_bench_*.json`)에서 읽는다.
토큰 실측은 실행 매니페스트(gitignore 대상)에서 나오므로 **여기 표로 박아 둔다** —
근거가 재생성 불가 산출물에만 있으면 리포트가 고아가 된다.

  .venv/bin/python3 eval/measure_bedrock_bakeoff.py
  .venv/bin/python3 eval/measure_bedrock_bakeoff.py --check
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
REPORT_PATH = ROOT / "eval/reports/v5_tag_bedrock_bakeoff.json"
MD_PATH = ROOT / "eval/reports/v5_tag_bedrock_bakeoff.md"

CORPUS_REVIEWS = 25_000
SAMPLE_REVIEWS = 200

# us-east-1 Standard, $/MTok. Flex·Batch 는 50%.
# 출처: https://aws.amazon.com/bedrock/pricing/ (2026-09-15 확인)
PRICES = {
    "zai.glm-4.7": (0.60, 2.20),
    "zai.glm-4.7-flash": (0.07, 0.40),
    "minimax.minimax-m2.5": (0.30, 1.20),
    "qwen.qwen3-next-80b-a3b": (0.15, 1.20),
    "openai.gpt-oss-120b-1:0": (0.15, 0.62),
    "openai.gpt-oss-20b-1:0": (0.07, 0.20),
    "moonshotai.kimi-k2.5": (0.60, 2.50),
    # 참조선 (Anthropic API 배치로 측정된 기존 파일럿)
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5": (3.00, 15.00),
}

# 실행 매니페스트에서 옮긴 실측치. 200건 · 청크 20 · 프롬프트 v1 · service_tier=flex.
MEASURED: dict[str, dict] = {
    "zai.glm-4.7": {"label": "bench_glm47", "in": 40609, "out": 16832, "cacheRead": 2016,
                    "reviewsTagged": 200, "truncated": 0, "failedChunks": 0},
    "minimax.minimax-m2.5": {"label": "bench_minimaxm25", "in": 30349, "out": 42903,
                             "cacheRead": 2400, "reviewsTagged": 200, "truncated": 0,
                             "failedChunks": 0},
    "qwen.qwen3-next-80b-a3b": {"label": "bench_qwen3next80b", "in": 35699, "out": 21893,
                                "cacheRead": 0, "reviewsTagged": 200, "truncated": 0,
                                "failedChunks": 0},
    "zai.glm-4.7-flash": {"label": "bench_glm47flash", "in": 40545, "out": 24509,
                          "cacheRead": 4992, "reviewsTagged": 200, "truncated": 0,
                          "failedChunks": 0},
    "openai.gpt-oss-120b-1:0": {"label": "bench_gptoss120b", "in": 32351, "out": 59882,
                                "cacheRead": 0, "reviewsTagged": 180, "truncated": 1,
                                "failedChunks": 1},
    "openai.gpt-oss-20b-1:0": {"label": "bench_gptoss20b", "in": 32351, "out": 67413,
                               "cacheRead": 0, "reviewsTagged": 120, "truncated": 4,
                               "failedChunks": 4},
}

# 기존 파일럿 (Anthropic API 경로). 토큰 실측을 같은 방식으로 안 모았으므로 비용은 내지 않는다.
BASELINES = {"claude-haiku-4-5": "pilot_haiku45", "claude-sonnet-5": "pilot_sonnet5"}


def row_from_report(model: str, label: str) -> dict:
    path = ROOT / f"eval/reports/v5_tag_{label}.json"
    if not path.exists():
        raise SystemExit(f"리포트가 없다: {path.relative_to(ROOT)} — validate_tags 를 먼저 돌린다")
    rep = json.loads(path.read_text())
    if rep["tagger"] != model:
        raise SystemExit(
            f"{path.relative_to(ROOT)} 의 tagger 가 {rep['tagger']!r} 인데 {model!r} 로 집계하려 한다.\n"
            "  라벨이 섞였다 — 재개가 남의 결과를 주웠을 수 있다 (tag_compat 재개 가드 참조)"
        )
    c = rep["comparison"]
    return {
        "model": model,
        "label": label,
        "precision": c["precision"],
        "recall": c["recall"],
        "polarityAgreement": c["polarityAgreementOnOverlap"],
        "tagsPassed": c["candidatePairs"],
        "goldTags": c["goldPairs"],
        "overlap": c["overlap"],
        "reviewsWithTag": rep["profile"]["coverage"]["reviewsWithTag"],
    }


def build() -> dict:
    rows = []
    for model, m in MEASURED.items():
        row = row_from_report(model, m["label"])
        pi, po = PRICES[model]
        scale = CORPUS_REVIEWS / SAMPLE_REVIEWS
        std = (m["in"] * scale / 1e6) * pi + (m["out"] * scale / 1e6) * po
        row |= {
            "tokens": {k: m[k] for k in ("in", "out", "cacheRead")},
            "tokensPerReview": {"in": round(m["in"] / SAMPLE_REVIEWS),
                                "out": round(m["out"] / SAMPLE_REVIEWS)},
            "reviewsTagged": m["reviewsTagged"],
            "truncatedChunks": m["truncated"],
            "failedChunks": m["failedChunks"],
            "price": {"inPerMTok": pi, "outPerMTok": po},
            "cost25kStandard": round(std, 2),
            "cost25kFlex": round(std / 2, 2),
        }
        rows.append(row)

    for model, label in BASELINES.items():
        rows.append(row_from_report(model, label) | {"note": "Anthropic API 경로 기존 파일럿 (비용 미측정)"})

    rows.sort(key=lambda r: -r["recall"])
    best = max(rows, key=lambda r: r["recall"])
    return {
        "issue": "PER-175",
        "conditions": {
            "sample": "eval/gold/v5_tag_pilot_sample.jsonl (200건, 시드 고정 층화)",
            "gold": "eval/gold/v5_tags_pilot_gold.jsonl (432태그, v2)",
            "prompt": "pipeline/prompts/tag/v1.md",
            "chunkSize": 20,
            "serviceTier": "flex",
            "region": "us-east-1",
            "note": "Haiku·Sonnet 과 같은 조건이어야 한 표에 올라간다. 청크·프롬프트가 다르면 분리한다",
        },
        "priceSource": "https://aws.amazon.com/bedrock/pricing/ (2026-09-15 확인)",
        "limitation": (
            "표본 200건이다. 정답셋은 Opus 5 가 만들었고(eval/reports/v5_tag_gold_v2.md), "
            "재현율의 상한은 정답셋의 품질이다 — 100% 는 의미 없는 목표다"
        ),
        "models": rows,
        "finding": {
            "bestRecall": {"model": best["model"], "recall": best["recall"],
                           "precision": best["precision"]},
            "reviewCoverageFailures": [
                {"model": r["model"], "reviewsTagged": r["reviewsTagged"],
                 "truncatedChunks": r["truncatedChunks"]}
                for r in rows if r.get("reviewsTagged", SAMPLE_REVIEWS) < SAMPLE_REVIEWS
            ],
        },
    }


def to_md(rep: dict) -> str:
    c = rep["conditions"]
    lines = [
        "# Bedrock 후보 태거 벤치 — 200건 (PER-175)",
        "",
        f"표본 {c['sample']} · 정답셋 {c['gold']}",
        f"프롬프트 `{c['prompt']}` · 청크 {c['chunkSize']} · `service_tier={c['serviceTier']}` · {c['region']}",
        "",
        f"> {rep['limitation']}",
        "",
        "| 모델 | 정밀도 | 재현율 | 방향 일치 | 통과 태그 | 리뷰 | 잘림 | in/out 토큰·리뷰 | 25K Flex |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rep["models"]:
        tpr = r.get("tokensPerReview")
        tok = f"{tpr['in']}/{tpr['out']}" if tpr else "—"
        cost = f"${r['cost25kFlex']:.2f}" if "cost25kFlex" in r else "—"
        rev = f"{r['reviewsTagged']}/200" if "reviewsTagged" in r else "200/200"
        trunc = r.get("truncatedChunks", "—")
        lines.append(
            f"| `{r['model']}` | {r['precision']} | **{r['recall']}** | {r['polarityAgreement']} | "
            f"{r['tagsPassed']} | {rev} | {trunc} | {tok} | {cost} |"
        )
    f = rep["finding"]
    lines += [
        "",
        f"**최고 재현율**: `{f['bestRecall']['model']}` — 재현율 {f['bestRecall']['recall']} · "
        f"정밀도 {f['bestRecall']['precision']}",
        "",
        "**리뷰를 다 못 읽은 모델** (출력 상한에 걸려 청크가 잘렸다):",
    ]
    for r in f["reviewCoverageFailures"]:
        lines.append(f"- `{r['model']}` — {r['reviewsTagged']}/200건, 잘린 청크 {r['truncatedChunks']}개")
    lines += ["", f"단가 출처: {rep['priceSource']}", ""]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="Bedrock 태거 벤치 집계 (PER-175)")
    ap.add_argument("--check", action="store_true", help="리포트가 재현되는지만 확인")
    args = ap.parse_args()

    rep = build()
    payload = json.dumps(rep, ensure_ascii=False, indent=2) + "\n"
    md = to_md(rep)

    if args.check:
        for path, want in ((REPORT_PATH, payload), (MD_PATH, md)):
            if not path.exists():
                raise SystemExit(f"FAIL: 리포트가 없다 ({path.relative_to(ROOT)})")
            if path.read_text() != want:
                raise SystemExit(
                    f"FAIL: 벤치 리포트가 재현되지 않는다 ({path.relative_to(ROOT)})\n"
                    "  → 채점 규칙이나 정답셋이 바뀌었다면 다시 생성해 함께 커밋한다"
                )
        print(f"OK: 벤치 리포트 재현 일치 ({REPORT_PATH.relative_to(ROOT)})")
        return

    REPORT_PATH.write_text(payload)
    MD_PATH.write_text(md)
    print(md)
    print(f"→ {REPORT_PATH.relative_to(ROOT)} · {MD_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
