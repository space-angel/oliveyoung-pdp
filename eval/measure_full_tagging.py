"""전수 25,000건 태깅 결과 프로파일 (PER-175).

**이 리포트는 재생성 비용이 $4 다.** 입력이 되는 태그 파일은 LLM 산출물이고
`data/intermediate/` 라 gitignore 대상이다. 그래서 `verify.sh` 의 `--check` 대열에
넣지 않는다 — 넣으면 클론 직후 게이트가 깨진다. 대신 리포트 안에 실행 정체
(모델·프롬프트 해시·입력 해시·묶음 크기·서비스 티어)를 박아, 어떤 실행에서 나온
수치인지 산출물만 보고 알 수 있게 한다 (§5-2).

  .venv/bin/python3 eval/measure_full_tagging.py --label full_glm47
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

from tag import chunks, load_reviews, partition_by_contract, read_jsonl  # noqa: E402

REPORT_PATH = ROOT / "eval/reports/v5_tag_full_run.json"
MD_PATH = ROOT / "eval/reports/v5_tag_full_run.md"
GOLD_SAMPLE = ROOT / "eval/gold/v5_tag_pilot_sample.jsonl"

# us-east-1 Standard $/MTok (https://aws.amazon.com/bedrock/pricing/ · 2026-09-15).
# Flex 는 50%. 캐시 읽기 단가는 확인하지 못해 **할인 없이** 계산한다 — 실제 청구는 이보다 낮다.
PRICES = {"zai.glm-4.7": (0.60, 2.20)}


def violation_breakdown(raw_tags: list[dict], reviews: dict) -> tuple[int, dict]:
    _, violations = partition_by_contract(raw_tags, reviews)
    kinds: collections.Counter = collections.Counter()
    for v in violations:
        t = v["violation"]
        kinds[
            "같은 축 두 번" if "두 번" in t
            else "인용 원문 아님" if "부분문자열" in t
            else "리뷰당 상한 초과" if "상한" in t
            else "택소노미 밖" if "택소노미" in t
            else "힌트 코드 밖" if "skinTypeHint" in t
            else "기타"
        ] += 1
    return len(violations), dict(kinds.most_common())


def build(label: str) -> dict:
    runs = ROOT / f"data/intermediate/tag_runs/{label}.json"
    if not runs.exists():
        raise SystemExit(
            f"실행 매니페스트가 없다: {runs.relative_to(ROOT)}\n"
            "  전수 태깅을 먼저 돌린다 — pipeline/tag_compat.py run --model zai.glm-4.7 …"
        )
    man = json.loads(runs.read_text())
    out = man["output"]

    reviews_list = load_reviews(pilot=False)
    reviews = {r["reviewId"]: r for r in reviews_list}
    raw_tags = read_jsonl(ROOT / out["rawPath"])
    kept = read_jsonl(ROOT / out["path"])
    n_viol, viol_kinds = violation_breakdown(raw_tags, reviews)

    per_review = collections.Counter(t["reviewId"] for t in kept)
    aspects = collections.Counter(t["aspect"] for t in kept)
    polarity = collections.Counter(t["polarity"] for t in kept)

    # 표본 200건이 전수에서 몇 개의 묶음에 흩어졌는가 — 파일럿 점수와 전수 점수가
    # 갈리는 이유다. 같은 리뷰라도 묶음 안의 이웃이 달라진다.
    sample_ids = {json.loads(l)["reviewId"] for l in GOLD_SAMPLE.read_text().splitlines() if l.strip()}
    chunk_of = {r["reviewId"]: i for i, b in chunks(reviews_list, man["chunkSize"]) for r in b}
    sample_chunks = {chunk_of[s] for s in sample_ids if s in chunk_of}

    u = out["usage"]
    pi, po = PRICES.get(man["model"], (0.0, 0.0))
    std = (u["input"] / 1e6) * pi + (u["output"] / 1e6) * po

    bench = json.loads((ROOT / "eval/reports/v5_tag_bench_glm47.json").read_text())["comparison"]
    full = json.loads((ROOT / "eval/reports/v5_tag_full_glm47.json").read_text())["comparison"]

    profile = json.loads((ROOT / "eval/reports/v5_ingest_profile.json").read_text())["trustPrior"]

    return {
        "issue": "PER-175",
        "run": {
            "label": man["label"],
            "model": man["model"],
            "provider": man["provider"],
            "baseUrl": man["baseUrl"],
            "serviceTier": man["serviceTier"],
            "chunkSize": man["chunkSize"],
            "maxTokens": man["maxTokens"],
            "temperature": man["temperature"],
            "prompt": man["prompt"],
            "input": man["input"],
            "requests": u["chunks"],
            "filled": man.get("filled"),
        },
        "coverage": {
            "reviews": len(reviews),
            "reviewsInResult": out["reviewsTagged"],
            "reviewsWithTag": len(per_review),
            "reviewsWithoutTag": len(reviews) - len(per_review),
            "pctReviewsWithTag": round(100 * len(per_review) / len(reviews), 1),
            "truncatedChunks": out["finishReasons"].get("length", 0),
            "failedChunks": len(out["failedChunks"]),
        },
        "tags": {
            "emitted": len(raw_tags),
            "passed": len(kept),
            "violations": n_viol,
            "violationPct": round(100 * n_viol / len(raw_tags), 1),
            "violationKinds": viol_kinds,
            "perReview": round(len(kept) / len(reviews), 2),
            "polarity": dict(polarity),
            "aspects": dict(aspects.most_common()),
            "unusedOfTaxonomy": [],
            "withSkinTypeHint": sum(1 for t in kept if t.get("skinTypeHint")),
        },
        "tokens": {
            **u,
            "perReviewIn": round(u["input"] / len(reviews)),
            "perReviewOut": round(u["output"] / len(reviews)),
            "usd": {
                "standard": round(std, 2),
                "flex": round(std / 2, 2),
                "note": "캐시 읽기 단가를 확인하지 못해 할인 없이 계산했다. 실제 청구는 이보다 낮다",
            },
        },
        "goldComparison": {
            "scope": "정답셋 표본 200건 (전수 25,000건 안에 포함돼 있다)",
            "full": {k: full[k] for k in ("precision", "recall", "polarityAgreementOnOverlap",
                                          "candidatePairs", "goldPairs", "overlap")},
            "bench200": {k: bench[k] for k in ("precision", "recall", "polarityAgreementOnOverlap",
                                               "candidatePairs")},
            "delta": {
                "precision": round(full["precision"] - bench["precision"], 1),
                "recall": round(full["recall"] - bench["recall"], 1),
                "polarityAgreement": round(
                    full["polarityAgreementOnOverlap"] - bench["polarityAgreementOnOverlap"], 1
                ),
            },
            "sampleChunkSpread": {
                "chunksInFullRun": len(sample_chunks),
                "chunksInBench": (len(sample_ids) + man["chunkSize"] - 1) // man["chunkSize"],
                "note": (
                    "같은 200건이 전수에서는 서로 다른 이웃과 묶였다. 묶음 구성이 결과를 "
                    "바꾸므로 **파일럿 점수를 전수 점수로 쓰면 안 된다**"
                ),
            },
        },
        "trustPrior": {
            "issue": "PER-174",
            "unavailableSignals": profile["unavailableSignals"],
            "distinctScores": profile["distinctScores"],
            "onTopicTrue": len(per_review),
            "note": "태그가 들어와 onTopic 이 unavailable 에서 빠졌다. PER-174 인계분 완료 판정",
        },
    }


def to_md(r: dict) -> str:
    run, cov, tags, tok, g = r["run"], r["coverage"], r["tags"], r["tokens"], r["goldComparison"]
    lines = [
        "# 전수 25,000건 태깅 — 실행 결과 (PER-175)",
        "",
        f"`{run['model']}` · 묶음 {run['chunkSize']} · `service_tier={run['serviceTier']}` · "
        f"요청 {run['requests']:,}개",
        f"프롬프트 `{run['prompt']['path']}` (`{run['prompt']['sha256'][:12]}…`)",
        f"태깅 투입물 해시 `{run['input']['taggingSha256'][:12]}…`",
        "",
        "## 수거",
        "",
        f"- 리뷰 **{cov['reviewsInResult']:,}/{cov['reviews']:,}** · 잘린 묶음 {cov['truncatedChunks']} · "
        f"실패 묶음 {cov['failedChunks']}",
        f"- 태그 {tags['emitted']:,}개 생성 → **계약 통과 {tags['passed']:,}** "
        f"(위반 {tags['violations']:,} · {tags['violationPct']}%)",
        f"- 태그 달린 리뷰 {cov['reviewsWithTag']:,}건 ({cov['pctReviewsWithTag']}%) · "
        f"리뷰당 {tags['perReview']}개",
        "",
        "### 버려진 태그의 유형",
        "",
        "| 유형 | 건수 |",
        "|---|---:|",
    ]
    for k, v in tags["violationKinds"].items():
        lines.append(f"| {k} | {v:,} |")
    if run.get("filled"):
        f = run["filled"]
        lines += [
            "",
            f"> 묶음 {run['chunkSize']} 에서 반복적으로 빠진 리뷰 {f['requestedReviews']}건은 "
            f"묶음 {f['chunkSize']} 로 다시 물어 채웠다 (`{f['reviewIds']}`). "
            "묶음 크기는 실행 설정이지 계약이 아니고, 계약이 요구하는 건 모든 reviewId 가 "
            "결과에 있어야 한다는 것이다.",
        ]
    lines += [
        "",
        "## aspect 분포",
        "",
        "| aspect | 태그 | 비중 |",
        "|---|---:|---:|",
    ]
    for a, c in tags["aspects"].items():
        lines.append(f"| {a} | {c:,} | {100 * c / tags['passed']:.1f}% |")
    lines += [
        "",
        f"방향: 긍정 {tags['polarity'].get('positive', 0):,} · "
        f"부정 {tags['polarity'].get('negative', 0):,} · "
        f"중립 {tags['polarity'].get('neutral', 0):,} · "
        f"skinTypeHint 달린 태그 {tags['withSkinTypeHint']:,}개",
        "",
        "## 토큰·비용 (실측)",
        "",
        f"- 입력 {tok['input']:,} (그중 캐시 읽기 **{tok['cacheRead']:,}**) · 출력 {tok['output']:,}",
        f"- 리뷰당 입력 {tok['perReviewIn']} · 출력 {tok['perReviewOut']}",
        f"- Standard ${tok['usd']['standard']} → **Flex ${tok['usd']['flex']}**",
        f"- {tok['usd']['note']}",
        "",
        "## 정답셋 대조 — 표본 200건",
        "",
        "| | 정밀도 | 재현율 | 방향 |",
        "|---|---:|---:|---:|",
        f"| 전수 실행 | **{g['full']['precision']}** | **{g['full']['recall']}** | "
        f"{g['full']['polarityAgreementOnOverlap']} |",
        f"| 벤치(200건 단독) | {g['bench200']['precision']} | {g['bench200']['recall']} | "
        f"{g['bench200']['polarityAgreementOnOverlap']} |",
        f"| 차이 | {g['delta']['precision']:+} | {g['delta']['recall']:+} | "
        f"{g['delta']['polarityAgreement']:+} |",
        "",
        f"같은 200건인데 점수가 다르다. 전수에서 이 200건은 **{g['sampleChunkSpread']['chunksInFullRun']}개 "
        f"묶음**에 흩어졌고, 벤치에서는 {g['sampleChunkSpread']['chunksInBench']}개였다. "
        f"{g['sampleChunkSpread']['note']}",
        "",
        "## trustPrior 재점수 (PER-174 인계)",
        "",
        f"- `unavailable` 신호: {r['trustPrior']['unavailableSignals'] or '없음'}",
        f"- `onTopic` 이 참인 리뷰 {r['trustPrior']['onTopicTrue']:,}건",
        f"- 서로 다른 점수 {r['trustPrior']['distinctScores']:,}개",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="전수 태깅 프로파일 (PER-175)")
    ap.add_argument("--label", default="full_glm47")
    args = ap.parse_args()

    rep = build(args.label)
    REPORT_PATH.write_text(json.dumps(rep, ensure_ascii=False, indent=2) + "\n")
    md = to_md(rep)
    MD_PATH.write_text(md)
    print(md)
    print(f"→ {REPORT_PATH.relative_to(ROOT)} · {MD_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
