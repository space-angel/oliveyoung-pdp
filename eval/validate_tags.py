"""
태깅 결과 검증 + 프로파일 (PER-175).

계약 위반은 `pipeline/tag_contract.validate_tags` 가 세운다. 이 스크립트는 그 위에서
**태거를 판단하는 데 쓰는 수치**를 낸다. 파일럿(구독 토큰, 사람이 채점할 정답셋)과
배치(API 전수) 둘 다 같은 잣대로 본다.

  python3 eval/validate_tags.py --tags eval/gold/v5_tags_pilot_gold.jsonl \
                                --sample data/intermediate/v5_tag_pilot_sample.jsonl \
                                --label pilot_gold --out eval/reports/v5_tag_pilot.json

배치 결과가 나오면 --against 로 정답셋을 주고 (리뷰×aspect) 단위 일치율을 낸다.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

from tag_contract import (  # noqa: E402
    ASPECTS,
    POLARITIES,
    is_verbatim,
    is_verbatim_loose,
    prompt_version,
    validate_tags,
)


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def pct(part: int, whole: int) -> float:
    return round(100 * part / whole, 1) if whole else 0.0


def profile(tags: list[dict], reviews: dict[int, dict], strata: dict[int, str]) -> dict:
    by_review = collections.defaultdict(list)
    for t in tags:
        by_review[t["reviewId"]].append(t)

    verbatim_strict = sum(1 for t in tags if is_verbatim(t["snippet"], reviews[t["reviewId"]]["raw"]["content"]))
    verbatim_loose = sum(
        1 for t in tags if is_verbatim_loose(t["snippet"], reviews[t["reviewId"]]["raw"]["content"])
    )

    # 별점과 방향의 교차 검증 (게이트3 근거). 불일치는 오류가 아니라 플래그다 —
    # 5점 리뷰 안의 불만은 정상이고, 그걸 잡는 게 (리뷰 × 주제) 단위의 존재 이유다.
    disagree = [
        t
        for t in tags
        if (reviews[t["reviewId"]]["raw"]["rating"] >= 4 and t["polarity"] == "negative")
        or (reviews[t["reviewId"]]["raw"]["rating"] <= 2 and t["polarity"] == "positive")
    ]

    # 한 리뷰 안에서 방향이 갈린 비율 — 리뷰 단위 감성으로는 표현 못 하는 몫
    mixed = [rid for rid, ts in by_review.items() if len({t["polarity"] for t in ts}) > 1]

    aspect_counts = collections.Counter(t["aspect"] for t in tags)
    top6 = sum(c for _, c in aspect_counts.most_common(6))

    per_stratum = {}
    for name in sorted(set(strata.values())):
        rids = [rid for rid, s in strata.items() if s == name]
        st = [t for t in tags if strata[t["reviewId"]] == name]
        pol = collections.Counter(t["polarity"] for t in st)
        per_stratum[name] = {
            "reviews": len(rids),
            "tagged": sum(1 for rid in rids if by_review[rid]),
            "tags": len(st),
            "tagsPerReview": round(len(st) / len(rids), 2) if rids else 0,
            "polarity": {p: pol.get(p, 0) for p in POLARITIES},
        }

    hints = [t for t in tags if t["skinTypeHint"]]
    hint_reviews = {t["reviewId"] for t in hints}
    hint_on_missing = {
        rid for rid in hint_reviews if not reviews[rid]["condition"]["skinType"]["stated"]
    }
    hint_conflict = [
        {
            "reviewId": rid,
            "condition": reviews[rid]["condition"]["skinType"]["code"],
            "hint": next(t["skinTypeHint"] for t in tags if t["reviewId"] == rid and t["skinTypeHint"]),
        }
        for rid in hint_reviews
        if reviews[rid]["condition"]["skinType"]["stated"]
        and next(t["skinTypeHint"] for t in tags if t["reviewId"] == rid and t["skinTypeHint"])
        != reviews[rid]["condition"]["skinType"]["code"]
    ]

    return {
        "reviews": len(reviews),
        "tags": len(tags),
        "tagsPerReview": round(len(tags) / len(reviews), 2),
        "coverage": {
            "reviewsWithTag": len([r for r in by_review.values() if r]),
            "pctReviewsWithTag": pct(len([r for r in by_review.values() if r]), len(reviews)),
            "silentReviews": len(reviews) - len([r for r in by_review.values() if r]),
        },
        "snippet": {
            "verbatimStrict": verbatim_strict,
            "pctVerbatimStrict": pct(verbatim_strict, len(tags)),
            "verbatimAfterWhitespaceFold": verbatim_loose,
            "maxLen": max((len(t["snippet"]) for t in tags), default=0),
            "note": "v4 는 88.8% 였다. 계약은 100% 를 요구한다 — 미달이면 validate_tags 가 이미 세운다",
        },
        "polarity": {p: sum(1 for t in tags if t["polarity"] == p) for p in POLARITIES},
        "ratingDisagreement": {
            "count": len(disagree),
            "pct": pct(len(disagree), len(tags)),
            "note": "오류가 아니라 게이트3 플래그. 5점 리뷰의 불만을 잡았다는 뜻",
        },
        "mixedDirectionReviews": {
            "count": len(mixed),
            "pct": pct(len(mixed), len(by_review)),
            "note": "리뷰 단위 감성으로는 표현 불가능한 몫. (리뷰 × 주제) 단위의 근거",
        },
        "aspects": {
            "distinct": len(aspect_counts),
            "unusedOfTaxonomy": [a for a in ASPECTS if a not in aspect_counts],
            "top6Share": pct(top6, len(tags)),
            "counts": dict(aspect_counts.most_common()),
        },
        "skinTypeHint": {
            "tagsWithHint": len(hints),
            "reviewsWithHint": len(hint_reviews),
            "onMissingCondition": len(hint_on_missing),
            "conflictWithCondition": hint_conflict,
            "note": "onMissingCondition 이 조건축을 실제로 늘린 몫이다 (v4 는 이 값을 집계에서 잃었다)",
        },
        "perStratum": per_stratum,
    }


def corpus_estimate(tags: list[dict], strata: dict[int, str], sample_meta: dict) -> dict:
    """층별 가중치로 코퍼스(25K) 규모를 되돌린다.

    표본은 1~2점을 6.8배, 5점을 356배 축소해 뽑았다. 표본에서 센 부정 비율을
    그대로 코퍼스 수치로 읽으면 부정을 크게 과대평가한다.
    """
    weight = {s["name"]: s["weight"] for s in sample_meta["strata"]}
    est_tags = est_neg = 0.0
    per = {}
    for name, w in weight.items():
        st = [t for t in tags if strata[t["reviewId"]] == name]
        neg = sum(1 for t in st if t["polarity"] == "negative")
        est_tags += len(st) * w
        est_neg += neg * w
        per[name] = {"sampleTags": len(st), "weight": w, "estCorpusTags": round(len(st) * w)}
    corpus = sample_meta["corpus"]
    return {
        "corpusReviews": corpus,
        "estTags": round(est_tags),
        "estTagsPerReview": round(est_tags / corpus, 2),
        "estNegativeShare": round(100 * est_neg / est_tags, 1) if est_tags else 0.0,
        "sampleNegativeShare": pct(sum(1 for t in tags if t["polarity"] == "negative"), len(tags)),
        "perStratum": per,
        "note": "표본은 희소 클래스를 과표집했다. 코퍼스 수치는 가중치로 되돌린 추정이고 표본 수치와 다르다",
    }


def compare(tags: list[dict], gold: list[dict]) -> dict:
    """(리뷰 × aspect) 단위로 정답셋과 대조한다."""
    a = {(t["reviewId"], t["aspect"]): t for t in tags}
    g = {(t["reviewId"], t["aspect"]): t for t in gold}
    both = set(a) & set(g)
    agree = [k for k in both if a[k]["polarity"] == g[k]["polarity"]]
    return {
        "goldPairs": len(g),
        "candidatePairs": len(a),
        "overlap": len(both),
        "precision": pct(len(both), len(a)),
        "recall": pct(len(both), len(g)),
        "polarityAgreementOnOverlap": pct(len(agree), len(both)),
        "missedByCandidate": sorted(f"{k[0]}:{k[1]}" for k in set(g) - set(a))[:50],
        "extraInCandidate": sorted(f"{k[0]}:{k[1]}" for k in set(a) - set(g))[:50],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tags", required=True)
    ap.add_argument("--sample", default="eval/gold/v5_tag_pilot_sample.jsonl")
    ap.add_argument("--meta", default="eval/gold/v5_tag_pilot_meta.json")
    ap.add_argument("--label", required=True, help="누가 만든 태그인지 (예: pilot_gold, batch_haiku45)")
    ap.add_argument("--tagger", default="", help="모델 ID 또는 실행 주체. meta 에 그대로 남는다")
    ap.add_argument("--against", help="정답셋 jsonl. 주면 일치율을 낸다")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    sample = read_jsonl(ROOT / args.sample)
    reviews = {r["reviewId"]: r for r in sample}
    sample_meta = json.loads((ROOT / args.meta).read_text())
    strata_of = {}
    for r in sample:
        rating = r["raw"]["rating"]
        strata_of[r["reviewId"]] = (
            "rating_1_2" if rating <= 2 else "rating_3" if rating == 3 else f"rating_{rating}"
        )

    raw_tags = read_jsonl(ROOT / args.tags)
    tags = validate_tags(raw_tags, reviews)  # 위반이 있으면 여기서 선다

    report = {
        "issue": "PER-175",
        "label": args.label,
        "tagger": args.tagger,
        "prompt": prompt_version(),
        "sample": {
            "path": args.sample,
            "sha256": sample_meta["source"]["sha256"],
            "seed": sample_meta["seed"],
            "strata": sample_meta["strata"],
        },
        "profile": profile(tags, reviews, strata_of),
        "corpusEstimate": corpus_estimate(tags, strata_of, sample_meta),
    }
    if args.against:
        report["comparison"] = compare(tags, read_jsonl(ROOT / args.against))

    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    p = report["profile"]
    print(f"[{args.label}] 태그 {p['tags']}개 / 리뷰 {p['reviews']}건  (리뷰당 {p['tagsPerReview']})")
    print(f"  인용 원문성 {p['snippet']['pctVerbatimStrict']}%  ·  태그 달린 리뷰 {p['coverage']['pctReviewsWithTag']}%")
    print(f"  방향 갈린 리뷰 {p['mixedDirectionReviews']['pct']}%  ·  별점 불일치 {p['ratingDisagreement']['pct']}%")
    c = report["corpusEstimate"]
    print(f"  코퍼스 추정: 태그 {c['estTags']:,}개 (리뷰당 {c['estTagsPerReview']}) · 부정 {c['estNegativeShare']}% "
          f"[표본에선 {c['sampleNegativeShare']}%]")
    print(f"→ {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
