"""
PER-174 근거 측정 — 신뢰도 사전 점수의 6개 신호.

PRD §3-4 는 신호 6개와 각각의 방향(↑/↓)을 제시한다. 그 방향을 그대로 가중치로 박으면
"PRD 가 그렇게 썼으니까"밖에 근거가 없다. 이 스크립트는 각 신호가 **실제로 근거의 양을
예측하는지** 같은 스냅샷에서 잰다.

## 무엇을 정답으로 놓는가

"신뢰도"는 직접 관측되지 않는다. 대리 지표로 **골든셋 200건의 aspect 태그 수**를 쓴다
(`eval/gold/v5_tags_pilot_gold.jsonl`, PER-175). 근거로 쓸 수 있는 문장이 몇 개나 들어
있는지를 사람이 손으로 센 값이고, 이 파이프라인이 리뷰에서 실제로 뽑아 쓰는 것이 그것이다.

  한계 — 표본이 200건이고 평점 층화 표본이라 코퍼스 비율이 아니다. 그래서 이 스크립트는
  **효과의 부호와 신뢰구간만** 읽고, 효과 크기를 코퍼스로 외삽하지 않는다.
  구간이 0을 포함하면 "효과 없음"이 아니라 **"이 표본으로는 0과 구별 못 함"** 이고,
  그 경우 가중치를 0.0 으로 두되 신호는 계속 계산해 남긴다.

## 측정 항목

  1. 코퍼스 수준 신호 규모 · `usefulPoint` 가 좋아요 수가 아님을 보이는 3개 증거
  2. 신호별 Δ평균 aspect 태그 수 + 부트스트랩 95% 신뢰구간 (골든셋 200건)
  3. 교란 통제 — 길이 층 안에서 좋아요 신호가 살아남는지
  4. 길이 램프 모양 비교 (linear / sqrt / log)
  5. "배송·포장만 언급" 어휘 규칙의 정밀도·재현율
  6. 채택된 가중치로 매긴 점수의 분포

사용:
  .venv/bin/python eval/measure_trust_signals.py
  → eval/reports/trust_signals_per174.json
"""
import argparse
import collections
import json
import math
import random
import re
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

from trust import RAMPS, SIGNALS, ScoringContext, TrustWeights, trust_prior  # noqa: E402

DEFAULT_INPUT = ROOT / "data/input/reviews_50products.json"
RECORDS_PATH = ROOT / "data/intermediate/v5_reviews.jsonl"
SAMPLE_PATH = ROOT / "eval/gold/v5_tag_pilot_sample.jsonl"
GOLD_PATH = ROOT / "eval/gold/v5_tags_pilot_gold.jsonl"
DEFAULT_OUTPUT = ROOT / "eval/reports/trust_signals_per174.json"

SEED = 20260904
BOOTSTRAP = 4000

# "배송·포장만 언급" 후보 어휘. 채택하지 않기로 한 근거를 남기려고 리포트에 함께 싣는다.
DELIVERY_PATTERN = r"배송|택배|포장|박스|파손|누락|배달"


def pct(part: int, whole: int) -> float:
    return round(100 * part / whole, 2) if whole else 0.0


def quantiles(values: list[float]) -> dict:
    v = sorted(values)
    n = len(v)
    q = lambda p: v[min(n - 1, int(n * p))]  # noqa: E731
    return {"min": v[0], "p25": q(0.25), "median": q(0.5), "p75": q(0.75), "p95": q(0.95), "max": v[-1]}


def spearman(xs, ys) -> float:
    def ranked(x):
        order = sorted(range(len(x)), key=lambda i: x[i])
        out = [0] * len(x)
        for pos, i in enumerate(order):
            out[i] = pos
        return out

    a, b = ranked(xs), ranked(ys)
    ma, mb = st.mean(a), st.mean(b)
    cov = sum((p - ma) * (q - mb) for p, q in zip(a, b))
    sa = math.sqrt(sum((p - ma) ** 2 for p in a))
    sb = math.sqrt(sum((q - mb) ** 2 for q in b))
    return round(cov / (sa * sb), 3) if sa and sb else 0.0


def pearson(xs, ys) -> float:
    ma, mb = st.mean(xs), st.mean(ys)
    cov = sum((p - ma) * (q - mb) for p, q in zip(xs, ys))
    sa = math.sqrt(sum((p - ma) ** 2 for p in xs))
    sb = math.sqrt(sum((q - mb) ** 2 for q in ys))
    return round(cov / (sa * sb), 3) if sa and sb else 0.0


def bootstrap_diff(a: list[float], b: list[float], rng: random.Random) -> dict:
    """두 집단의 평균 차와 부트스트랩 95% 구간. 구간이 0을 포함하면 방향을 주장하지 않는다."""
    if not a or not b:
        return {"n_true": len(a), "n_false": len(b), "note": "표본 부족"}
    diffs = sorted(
        st.mean([rng.choice(a) for _ in a]) - st.mean([rng.choice(b) for _ in b])
        for _ in range(BOOTSTRAP)
    )
    lo, hi = diffs[int(BOOTSTRAP * 0.025)], diffs[int(BOOTSTRAP * 0.975) - 1]
    return {
        "n_true": len(a),
        "n_false": len(b),
        "meanTrue": round(st.mean(a), 3),
        "meanFalse": round(st.mean(b), 3),
        "delta": round(st.mean(a) - st.mean(b), 3),
        "ci95": [round(lo, 3), round(hi, 3)],
        "excludesZero": bool(lo > 0 or hi < 0),
    }


# --- 1. 코퍼스 수준 ---


def corpus_signals(rows: list[dict]) -> dict:
    n = len(rows)
    content = collections.Counter(r["content"].strip() for r in rows)
    dup_groups = {k: v for k, v in content.items() if v > 1}
    lengths = [len(r["content"].strip()) for r in rows]

    # `usefulPoint` 가 좋아요 수가 아니라는 세 가지 증거
    by_goods = collections.defaultdict(list)
    for r in rows:
        by_goods[r["goodsNo"]].append(r["usefulPoint"])
    monotone = sum(
        1 for v in by_goods.values() if all(v[i] >= v[i + 1] for i in range(len(v) - 1))
    )
    medians = sorted(st.median(v) for v in by_goods.values())

    return {
        "reviews": n,
        "hasPhoto": {"count": sum(1 for r in rows if r["hasPhoto"]), "pct": pct(sum(1 for r in rows if r["hasPhoto"]), n)},
        "isMonthOverReview": {"count": sum(1 for r in rows if r["isMonthOverReview"]), "pct": pct(sum(1 for r in rows if r["isMonthOverReview"]), n)},
        "isMonthUseReview": {"count": sum(1 for r in rows if r["isMonthUseReview"]), "pct": pct(sum(1 for r in rows if r["isMonthUseReview"]), n)},
        "recommendCountPositive": {"count": sum(1 for r in rows if r["recommendCount"] > 0), "pct": pct(sum(1 for r in rows if r["recommendCount"] > 0), n)},
        "contentLength": quantiles(lengths),
        "identicalContent": {
            "groups": len(dup_groups),
            "reviewsInGroups": sum(dup_groups.values()),
            "pct": pct(sum(dup_groups.values()), n),
            "largestGroup": max(dup_groups.values()) if dup_groups else 0,
        },
        "usefulPointIsNotLikes": {
            "verdict": "쓰지 않는다 — 올리브영의 정렬 점수이지 좋아요 수가 아니다",
            "spearmanWithRecommendCount": spearman([r["usefulPoint"] for r in rows], [r["recommendCount"] for r in rows]),
            "goodsNoGroupsMonotoneInFileOrder": f"{monotone}/{len(by_goods)}",
            "monotoneMeaning": "크롤러가 usefulPoint 내림차순으로 수집했다 — 표본이 이 변수 위에서 잘려 있다 (제품당 상위 500건)",
            "perGoodsNoMedian": {"min": medians[0], "median": medians[len(medians) // 2], "max": medians[-1]},
            "scaleMeaning": "제품별 중앙값이 50배 차이라 제품 간 비교가 성립하지 않는다",
        },
        "recommendCountIsAgeConfounded": {
            "spearmanWithReviewDate": spearman([r["recommendCount"] for r in rows], [r["reviewDate"] for r in rows]),
            "zeroPctByYear": {
                y: pct(sum(1 for r in v if r["recommendCount"] == 0), len(v))
                for y, v in sorted(_by_year(rows).items())
            },
            "remedy": "제품 안 백분위로 정규화한다. 원값을 쓰면 리센시 컷(PER-172)으로 남긴 최신 리뷰를 바로 뒤로 민다",
        },
        "rankSignalsAreOneSignal": {
            "isTopReviewer": sum(1 for r in rows if r["isTopReviewer"]),
            "reviewerRankPresent": sum(1 for r in rows if r["reviewerRank"] is not None),
            "both": sum(1 for r in rows if r["isTopReviewer"] and r["reviewerRank"] is not None),
            "topWithoutRank": sum(1 for r in rows if r["isTopReviewer"] and r["reviewerRank"] is None),
            "meaning": "두 필드는 사실상 같은 신호다. 둘을 별개 신호로 세면 등급을 두 번 센다",
        },
    }


def _by_year(rows: list[dict]) -> dict:
    out = collections.defaultdict(list)
    for r in rows:
        out[r["reviewDate"][:4]].append(r)
    return out


# --- 2~4. 골든셋 대비 신호 효과 ---


def gold_effects(sample: list[dict], gold: list[dict], rows_by_id: dict, rng: random.Random) -> dict:
    tags = collections.Counter(g["reviewId"] for g in gold)
    y = {s["reviewId"]: tags[s["reviewId"]] for s in sample}
    length = {s["reviewId"]: len(s["raw"]["content"].strip()) for s in sample}

    def split(predicate):
        a = [y[s["reviewId"]] for s in sample if predicate(s)]
        b = [y[s["reviewId"]] for s in sample if not predicate(s)]
        return bootstrap_diff(a, b, rng)

    def zero_rate(predicate):
        a = [s for s in sample if predicate(s)]
        b = [s for s in sample if not predicate(s)]
        return {
            "truePct": pct(sum(1 for s in a if y[s["reviewId"]] == 0), len(a)),
            "falsePct": pct(sum(1 for s in b if y[s["reviewId"]] == 0), len(b)),
        }

    def rank_of(s):
        return rows_by_id[s["reviewId"]]

    effects = {
        "contentLength>=100": {**split(lambda s: length[s["reviewId"]] >= 100), "zeroTagRate": zero_rate(lambda s: length[s["reviewId"]] >= 100)},
        "contentLength>=200": split(lambda s: length[s["reviewId"]] >= 200),
        "hasPhoto": {**split(lambda s: s["raw"]["hasPhoto"]), "zeroTagRate": zero_rate(lambda s: s["raw"]["hasPhoto"])},
        "isMonthOverReview": {**split(lambda s: s["raw"]["isMonthOverReview"]), "zeroTagRate": zero_rate(lambda s: s["raw"]["isMonthOverReview"])},
        "isRepurchase": split(lambda s: s["raw"]["isRepurchase"]),
        "isTopReviewer": split(lambda s: rank_of(s)["isTopReviewer"]),
        "recommendCount>0": {**split(lambda s: rank_of(s)["recommendCount"] > 0), "zeroTagRate": zero_rate(lambda s: rank_of(s)["recommendCount"] > 0)},
    }

    # 교란 통제 — 좋아요는 길이의 대리값인가
    controlled = {}
    for lo, hi in ((0, 100), (100, 10**9)):
        band = [s for s in sample if lo <= length[s["reviewId"]] < hi]
        a = [y[s["reviewId"]] for s in band if rank_of(s)["recommendCount"] > 0]
        b = [y[s["reviewId"]] for s in band if rank_of(s)["recommendCount"] == 0]
        controlled[f"length_{lo}_{'inf' if hi > 10**8 else hi}"] = bootstrap_diff(a, b, rng)

    # 길이 램프 모양
    weights = TrustWeights.load()
    span = weights.cap_chars - weights.floor_chars
    ys = [y[s["reviewId"]] for s in sample]
    ramp_fit = {
        name: pearson(
            [fn(max(0.0, min(1.0, (length[s["reviewId"]] - weights.floor_chars) / span))) for s in sample], ys
        )
        for name, fn in RAMPS.items()
    }
    ramp_fit["rawLength"] = pearson([length[s["reviewId"]] for s in sample], ys)

    # 길이 구간별 태그 수
    buckets = []
    for lo, hi in ((0, 50), (50, 100), (100, 200), (200, 400), (400, 10**9)):
        band = [s for s in sample if lo <= length[s["reviewId"]] < hi]
        if not band:
            continue
        counts = [y[s["reviewId"]] for s in band]
        buckets.append(
            {
                "range": f"{lo}-{'inf' if hi > 10**8 else hi}",
                "n": len(band),
                "meanTags": round(st.mean(counts), 2),
                "zeroTagPct": pct(sum(1 for c in counts if c == 0), len(band)),
            }
        )

    # "배송·포장만 언급" 어휘 규칙
    pattern = re.compile(DELIVERY_PATTERN)
    zero = [s for s in sample if y[s["reviewId"]] == 0]
    nonzero = [s for s in sample if y[s["reviewId"]] > 0]
    hit_zero = sum(1 for s in zero if pattern.search(s["raw"]["content"]))
    hit_nonzero = sum(1 for s in nonzero if pattern.search(s["raw"]["content"]))

    return {
        "goldSample": len(sample),
        "goldTags": len(gold),
        "zeroTagReviews": {"count": len(zero), "pct": pct(len(zero), len(sample))},
        "target": "골든셋 aspect 태그 수 (사람이 센 근거 문장 수)",
        "bootstrapIterations": BOOTSTRAP,
        "effects": effects,
        "likesControlledForLength": controlled,
        "lengthRampFit": ramp_fit,
        "lengthBuckets": buckets,
        "deliveryLexicon": {
            "pattern": DELIVERY_PATTERN,
            "verdict": "감점 신호로 채택하지 않는다 — 태깅 이후 aspectCount==0 으로 판정한다",
            "precision": pct(hit_zero, hit_zero + hit_nonzero),
            "recall": pct(hit_zero, len(zero)),
            "hitsInZeroTag": hit_zero,
            "hitsInTagged": hit_nonzero,
            "note": "무태그 24건의 실제 내용은 배송 얘기가 아니라 '괜찮아요' 류의 근거 없는 호평이 대부분이다",
        },
    }


# --- 6. 채택 가중치로 매긴 점수 분포 ---


def adopted_score_profile(records: list[dict], weights: TrustWeights) -> dict:
    context = ScoringContext.from_records(records)
    priors = [trust_prior(r, context, weights) for r in records]
    scores = [p["score"] for p in priors]
    return {
        **quantiles(scores),
        "distinctScores": len(set(scores)),
        "unavailableSignals": sorted({s for p in priors for s in p.get("unavailable", [])}),
        "signalMeans": {
            name: round(st.mean([p["signals"][name] for p in priors]), 4)
            for name in SIGNALS
            if name in priors[0]["signals"]
        },
    }


def measure(rows: list[dict], records: list[dict], sample: list[dict], gold: list[dict]) -> dict:
    rng = random.Random(SEED)
    weights = TrustWeights.load()
    return {
        "issue": "PER-174",
        "purpose": "PRD §3-4 6개 신호의 방향과 크기를 실측해 가중치의 근거로 삼는다",
        "seed": SEED,
        "corpus": corpus_signals(rows),
        "goldEffects": gold_effects(sample, gold, {r["reviewId"]: r for r in rows}, rng),
        "adoptedWeights": weights.as_dict(),
        "adoptedScoreProfile": adopted_score_profile(records, weights),
        "verdicts": {
            "contentLength": "채택 0.6 — 유일하게 신뢰구간이 0을 배제한다",
            "uniqueContent": "채택 0.4 — 결정론적 신호. 측정이 필요 없다",
            "onTopic": "채택 0.5, 입수 시점 미가용 — aspect 태깅(PER-175) 후에만 판정된다",
            "hasPhoto": "0.0 — 구간이 0을 포함한다. 신호는 계속 계산해 남긴다",
            "usagePeriod": "0.0 — 구간이 0을 포함하고 점추정 부호가 PRD 주장과 반대다",
            "likes": "0.0 — 길이를 통제하면 효과가 사라진다. usefulPoint 는 아예 쓰지 않는다",
            "reviewerRank/isTopReviewer": "신호로 쓰지 않는다 — 두 필드가 사실상 같고 효과 구간이 0을 포함한다. PER-173 의 드롭을 유지한다",
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    ap.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = ap.parse_args()

    rows = json.loads(args.input.read_text())
    if not RECORDS_PATH.exists():
        raise SystemExit(f"입수 결과가 없다 ({RECORDS_PATH.relative_to(ROOT)}). 먼저 pipeline/ingest.py 를 돌려라")
    records = [json.loads(line) for line in RECORDS_PATH.read_text().splitlines() if line]
    sample = [json.loads(line) for line in SAMPLE_PATH.read_text().splitlines() if line]
    gold = [json.loads(line) for line in GOLD_PATH.read_text().splitlines() if line]

    report = measure(rows, records, sample, gold)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    g = report["goldEffects"]
    print(f"[PER-174] 골든셋 {g['goldSample']}건 / 태그 {g['goldTags']}개 · 무태그 {g['zeroTagReviews']['count']}건 ({g['zeroTagReviews']['pct']}%)")
    for name, e in g["effects"].items():
        if "delta" not in e:
            continue
        mark = "채택" if e["excludesZero"] else "0 포함"
        print(f"  {name:22s} Δ{e['delta']:+.2f} 95%CI [{e['ci95'][0]:+.2f},{e['ci95'][1]:+.2f}]  {mark}")
    print(f"  길이 통제 후 좋아요: " + " / ".join(
        f"{k} Δ{v.get('delta', 0):+.2f}" for k, v in g["likesControlledForLength"].items()))
    print(f"  램프 적합도: " + " ".join(f"{k} {v:+.3f}" for k, v in g["lengthRampFit"].items()))
    d = g["deliveryLexicon"]
    print(f"  배송 어휘 규칙: 정밀도 {d['precision']}% 재현율 {d['recall']}% → 채택 안 함")
    p = report["adoptedScoreProfile"]
    print(f"  채택 점수 분포: min {p['min']} / 중앙 {p['median']} / max {p['max']} · 서로 다른 점수 {p['distinctScores']}개")
    print(f"→ {args.output.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
