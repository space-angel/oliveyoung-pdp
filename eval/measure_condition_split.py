"""
PER-192 근거 측정 — 조건 정규화. **같은 aspect 인데 조건별로 방향이 갈리는 셀을 찾는다.**

PRD 의 핵심 주장은 *"제품에 대한 답은 조건 없이 성립하지 않는다"* 이고, 이 측정이
그 주장의 반증 가능한 형태다. 갈리는 셀이 하나도 없으면 조건 정규화는 값이 없다 —
그 경우도 이 리포트가 그대로 말하게 되어 있다.

확인하는 것은 넷이다.

  1) **기재율** — 조건축에 값이 있는 리뷰의 몫. 이슈 설명문의 57.1 / 53.9 / 68.1 을
     그대로 옮기지 않고 다시 잰다. 기재율이 낮다는 것이 조건부 주장의 상한이다
  2) **무조건 주장이 실제로 무조건인가** — 게이트4를 통과한 `(제품 × aspect)` 주장마다
     같은 축의 기재된·충분한 세그먼트끼리 부정 몫을 견준다 (`condition_render`)
  3) **갈린 셀의 목록과 크기** — 몇 개인지, 가장 크게 갈리는 게 무엇인지
  4) **무조건 서술이었다면 무엇이 지워졌을지** — 소수 방향 세그먼트의 작성자 수.
     "조건을 안 붙이면 N 명이 화면에서 사라진다" 가 이 이슈가 PER-191 에 넘기는 수다

## 다중비교를 숨기지 않는다

`(제품 × aspect × 축)` 마다 세그먼트 쌍을 전부 검정하므로 검정이 수천 건이다.
α=0.05 를 그대로 쓰면 우연히 갈린 쌍이 섞인다. 그래서 **보정 전과 후를 함께** 낸다 —
Bonferroni · Holm 은 축이 겹치는 `skinTrouble` 에서 보수적이지만(한 리뷰가 C01 과
C05 를 함께 가지면 두 검정이 독립이 아니다), 보정 후에도 살아남는 쌍이 있으면
"갈림이 존재한다" 는 주장은 다중비교로 설명되지 않는다.

사용:
  python3 eval/measure_condition_split.py
  → eval/reports/condition_split_per192.json
  python3 eval/measure_condition_split.py --check   # 커밋본과 일치 확인
"""
import argparse
import collections
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

import random  # noqa: E402

from catalog import load_catalog  # noqa: E402
from condition_render import (  # noqa: E402
    ARROW,
    FAILURE_MISSING_CONDITION,
    SPLIT_ALPHA,
    UNCONDITIONAL_SUBJECT,
    SegmentDirection,
    check_unconditional,
    describe,
    fisher_two_sided,
    render,
    render_axis,
)
from contracts import CONDITION_AXES, MISSING_SEGMENT, build_record  # noqa: E402
from gates import IdentityScope, run_duplicate_gate, run_identity_gate  # noqa: E402
from ingest import assert_matches_ingest, load_aspect_counts  # noqa: E402
from polarity import aspect_support  # noqa: E402
from policy import DEFAULT_SUFFICIENCY, sufficiency_gate  # noqa: E402
from sufficiency import MULTI_AXES  # noqa: E402
from tag_contract import ASPECTS  # noqa: E402
from trust import score_all  # noqa: E402

INPUT_PATH = ROOT / "data/input/reviews_50products.json"
TAGS_PATH = ROOT / "data/intermediate/v5_tags.jsonl"
TAGS_META_PATH = ROOT / "data/intermediate/v5_tags_meta.json"
REPORT_PATH = ROOT / "eval/reports/condition_split_per192.json"

CELL_AXES = ("skinType", "skinTrouble")
TOP_N = 20

# 순열 귀무분포. **씨앗과 횟수를 리포트에 박는다** — 재현되지 않으면 수가 아니라 인상이다.
PERMUTATIONS = 200
SEED = 192

# 순열 경로가 쓰는 정수 부호. 침묵은 태그가 없는 것이지 중립이 아니다 (PER-178).
POLARITY_INDEX = {"positive": 0, "negative": 1, "neutral": 2}
SILENT = 3


def pct(part: int, whole: int) -> float:
    return round(100 * part / whole, 2) if whole else 0.0


def load_records() -> tuple[list[dict], object]:
    """입수(PER-173)와 같은 경로로 레코드를 만들고 산출물과 같은지 대조한다.

    `v5_reviews.jsonl` 을 그냥 읽지 않는 이유는 게이트4 측정과 같다 — 리포트가
    재현된다고 기반이 같은 것은 아니다.
    """
    catalog = load_catalog()
    records = []
    for row in json.loads(INPUT_PATH.read_text()):
        product_id = catalog.resolve_goods_no(row["goodsNo"], row["reviewDate"])
        records.append(build_record(row, product_id).to_dict())
    priors = score_all(records, aspect_counts=load_aspect_counts(TAGS_PATH))
    for record in records:
        record["derived"]["trustPrior"] = priors[record["reviewId"]]
    assert_matches_ingest(records, who="PER-192 조건 정규화 측정")
    return records, catalog


def through_gates(records: list[dict], catalog) -> dict[str, list[dict]]:
    """게이트1 → 게이트2. 순서가 규격이다 (PER-183 `gateOrder`)."""
    grouped: dict[str, list[dict]] = collections.defaultdict(list)
    for record in records:
        grouped[record["productId"]].append(record)
    kept: dict[str, list[dict]] = {}
    for product_id, rows in sorted(grouped.items()):
        first = run_identity_gate(rows, IdentityScope(product_id), catalog)
        kept[product_id] = run_duplicate_gate(first.passed).passed
    return kept


def stated_rates(records: list[dict], kept: dict[str, list[dict]]) -> dict:
    """조건축 기재율. **이슈 설명문의 수치를 옮기지 않고 다시 잰다.**

    두 분모를 함께 낸다 — 전건(25,000)과 게이트1·2 통과분. 기재율은 조건부 주장의
    상한이고, 게이트를 지난 뒤의 값이 실제로 쓸 수 있는 몫이다.
    """
    def count(rows: list[dict]) -> dict:
        out = {}
        for axis in CONDITION_AXES:
            stated = sum(1 for r in rows if r["condition"][axis]["stated"])
            out[axis] = {"reviews": len(rows), "stated": stated,
                         "statedPct": pct(stated, len(rows))}
        return out

    passed = [r for rows in kept.values() for r in rows]
    both = sum(1 for r in records
               if r["condition"]["skinType"]["stated"]
               and r["condition"]["skinTrouble"]["stated"])
    neither = sum(1 for r in records
                  if not r["condition"]["skinType"]["stated"]
                  and not r["condition"]["skinTrouble"]["stated"])
    return {
        "allReviews": count(records),
        "afterGate1And2": count(passed),
        "profileBoth": {"reviews": both, "pct": pct(both, len(records))},
        "profileNeither": {"reviews": neither, "pct": pct(neither, len(records))},
        "note": (
            "기재율이 조건부 주장의 상한이다. 미기재는 '조건 없음' 이 아니라 별도 "
            f"세그먼트이고(`{MISSING_SEGMENT}`), '{UNCONDITIONAL_SUBJECT}' 와 같은 "
            "것으로 표기하지 않는다 (PER-177 §3 · PER-178)"
        ),
    }


def _segments_of(row: dict, axis: str) -> list[str]:
    cell = row["condition"][axis]
    return list(cell["segments"]) if axis in MULTI_AXES else [cell["segment"]]


def scan(kept: dict[str, list[dict]], tags_by_product: dict[str, list[dict]],
         catalog) -> tuple[list[dict], dict]:
    """게이트4를 통과한 무조건 주장마다 조건별 갈림을 본다.

    후보는 `(제품 × aspect)` 중 **무조건 주장이 충분성을 통과하는 것**이다 — 애초에
    말하지 못할 주장은 "조건을 빠뜨렸다" 고 할 대상이 아니다.
    """
    policy = DEFAULT_SUFFICIENCY
    rows: list[dict] = []
    totals = collections.Counter()
    for product_id, records in sorted(kept.items()):
        if not records:
            continue
        ids = {r["reviewId"] for r in records}
        tags = [t for t in tags_by_product.get(product_id, []) if t["reviewId"] in ids]
        by_review: dict[int, list[dict]] = collections.defaultdict(list)
        for tag in tags:
            by_review[tag["reviewId"]].append(tag)

        for aspect in ASPECTS:
            whole = aspect_support(records, tags, aspect)
            totals["productAspects"] += 1
            u = (whole.positive_authors + whole.negative_authors
                 if whole.positive_authors and whole.negative_authors
                 else whole.positive_authors or whole.negative_authors or 0)
            decision = sufficiency_gate(
                support_authors=u,
                spoke_authors=whole.mentioned_authors,
                cell_authors=len(records),
                policy=policy,
            )
            if not decision.passed:
                totals["insufficientUnconditional"] += 1
                continue
            totals["unconditionalClaims"] += 1

            segments: list[SegmentDirection] = []
            for axis in CELL_AXES:
                members: dict[str, list[dict]] = collections.defaultdict(list)
                for row in records:
                    for segment in _segments_of(row, axis):
                        members[segment].append(row)
                for segment, rows_in in sorted(members.items()):
                    seg_tags = [t for r in rows_in for t in by_review.get(r["reviewId"], [])]
                    segments.append(SegmentDirection(
                        axis, segment, aspect_support(rows_in, seg_tags, aspect)))

            verdict = check_unconditional(aspect, segments, policy=policy, alpha=SPLIT_ALPHA)
            totals["comparedPairs"] += verdict.compared
            if verdict.holds:
                totals["holds"] += 1
                continue
            totals["split"] += 1
            totals["splitPairs"] += len(verdict.pairs)
            totals["majorityFlips"] += sum(1 for p in verdict.pairs if p.flips)
            totals["contradictingAuthors"] += verdict.contradicting_authors
            rows.append({
                "productId": product_id,
                "displayName": catalog.product(product_id).display_name,
                "aspect": aspect,
                "unconditional": {
                    "sentence": render(None, f"{aspect} — {whole.verdict}"),
                    **whole.as_dict()["support"],
                    "direction": whole.direction,
                    "cellAuthors": len(records),
                },
                **verdict.as_dict(),
            })
    return rows, dict(totals)


def multiplicity(rows: list[dict], compared: int, alpha: float = SPLIT_ALPHA) -> dict:
    """보정 전/후를 함께 낸다. 보정 후에도 남으면 갈림은 우연으로 설명되지 않는다.

    Holm 은 검정이 독립이라고 보지만 `skinTrouble` 은 한 리뷰가 여러 코드를 가져
    독립이 아니다 — 그 방향은 **보수적**이라 "살아남았다" 는 결론을 약화시키지 않는다.
    """
    p_values = sorted(pair["pValue"] for row in rows for pair in row["splitPairs"])
    bonferroni = sum(1 for p in p_values if p < alpha / compared) if compared else 0
    holm = 0
    for index, p in enumerate(p_values):
        if p < alpha / (compared - index):
            holm = index + 1
        else:
            break
    return {
        "tests": compared,
        "alpha": alpha,
        "significantRaw": len(p_values),
        "significantRawPct": pct(len(p_values), compared),
        "significantBonferroni": bonferroni,
        "significantHolm": holm,
        "minPValue": p_values[0] if p_values else None,
        "note": (
            "보정 전 수는 우연히 갈린 쌍을 포함한다. 보정 후에도 남는 쌍이 있으면 "
            "'조건별로 방향이 갈린다' 는 다중비교로 설명되지 않는다. skinTrouble 은 "
            "한 리뷰가 여러 코드를 가져 검정이 독립이 아니다 — Holm 은 그 경우 보수적이다"
        ),
    }


def headline(rows: list[dict]) -> list[dict]:
    """가장 크게 갈린 쌍. 수만 내지 말고 **문장으로도** 낸다 — 이게 PRD 의 근거다."""
    pairs = []
    for row in rows:
        for pair in row["splitPairs"]:
            pairs.append({
                "productId": row["productId"],
                "displayName": row["displayName"],
                "aspect": row["aspect"],
                "axis": pair["axis"],
                "pValue": pair["pValue"],
                "negativeShareGap": pair["negativeShareGap"],
                "majorityFlips": pair["majorityFlips"],
                "left": {k: pair["left"][k] for k in
                         ("segment", "phrase", "positiveAuthors", "negativeAuthors",
                          "negativeShare", "cellAuthors")},
                "right": {k: pair["right"][k] for k in
                          ("segment", "phrase", "positiveAuthors", "negativeAuthors",
                           "negativeShare", "cellAuthors")},
                "sentence": (
                    f"{pair['left']['phrase']} {ARROW} 부정 "
                    f"{pair['left']['negativeShare']} · "
                    f"{pair['right']['phrase']} {ARROW} 부정 "
                    f"{pair['right']['negativeShare']} ({row['aspect']}, {row['productId']})"
                ),
            })
    pairs.sort(key=lambda p: (p["pValue"], -p["negativeShareGap"]))
    return pairs[:TOP_N]


def erased(rows: list[dict]) -> dict:
    """무조건 서술이었다면 무엇이 지워졌을지. **작성자 수로 센다** (PER-170)."""
    per_claim = sorted(
        ({"productId": r["productId"], "aspect": r["aspect"],
          "contradictingAuthors": r["contradictingAuthors"],
          "unconditionalDirection": r["unconditional"]["direction"],
          "negativeRatio": r["unconditional"]["negativeRatio"]}
         for r in rows if r["contradictingAuthors"]),
        key=lambda x: -x["contradictingAuthors"])
    total = sum(x["contradictingAuthors"] for x in per_claim)
    return {
        "claimsWithContradiction": len(per_claim),
        "authors": total,
        "top": per_claim[:TOP_N],
        "note": (
            "갈린 쌍에서 **소수 쪽 세그먼트**의 방향 근거를 (축, 세그먼트) 단위로 중복 "
            "없이 센 수다. 무조건으로 서술하면 이 사람들의 방향이 다수 방향에 덮인다 — "
            "컷이 아니라 표기의 문제이고, 그래서 이 이슈의 산출은 `missing_condition` "
            "**후보**이지 claim 수정이 아니다"
        ),
    }


def build_units(kept: dict[str, list[dict]],
                tags_by_product: dict[str, list[dict]]) -> list[tuple]:
    """순열용 자료. `(제품 × aspect)` 마다 방향 벡터와 세그먼트 색인만 남긴다.

    계약 경로(`scan`)를 순열마다 다시 도는 것은 너무 비싸다. 대신 같은 판정을 수로만
    다시 쓰고, **관측값이 계약 경로와 같은지 `main()` 이 대조한다** — 다르면 귀무분포가
    다른 통계량의 것이 되므로 조용히 넘기지 않고 에러다 (게이트4 측정의 `_assert_fast_path`
    와 같은 규칙).
    """
    units: list[tuple] = []
    for product_id, records in sorted(kept.items()):
        if not records:
            continue
        ids = {r["reviewId"] for r in records}
        by_aspect: dict[str, dict[int, int]] = collections.defaultdict(dict)
        for tag in tags_by_product.get(product_id, []):
            if tag["reviewId"] in ids:
                by_aspect[tag["aspect"]][tag["reviewId"]] = POLARITY_INDEX[tag["polarity"]]
        axes = []
        for axis in CELL_AXES:
            index: dict[str, int] = {}
            per_record = []
            for row in records:
                per_record.append([index.setdefault(s, len(index))
                                   for s in _segments_of(row, axis)])
            names = [s for s, _ in sorted(index.items(), key=lambda kv: kv[1])]
            axes.append((per_record, names))
        for aspect in ASPECTS:  # 순서를 고정한다 — 순열 씨앗이 같은 값을 내야 한다
            marks = by_aspect.get(aspect)
            if not marks:
                continue
            vector = [marks.get(r["reviewId"], SILENT) for r in records]
            units.append((vector, axes, len(records)))
    return units


def _statistic(units: list[tuple], rng: random.Random | None = None) -> tuple[int, int, float]:
    """(유의한 쌍 수, 검정 수, 최소 p). `rng` 를 주면 **방향만** 섞는다.

    귀무가설은 *"조건과 방향은 무관하다"* 다. 방향 벡터를 제품 안에서 섞으면 세그먼트
    크기·기재 구조·긍부정 총수는 그대로 두고 **조건과 방향의 짝만** 끊긴다. 그래서 이
    분포가 곧 "우연이면 이만큼 갈려 보인다" 다.
    """
    policy = DEFAULT_SUFFICIENCY
    significant = tests = 0
    min_p = 1.0
    for vector, axes, cell_authors in units:
        values = vector
        if rng is not None:
            values = vector[:]
            rng.shuffle(values)
        pos = sum(1 for v in values if v == 0)
        neg = sum(1 for v in values if v == 1)
        neu = sum(1 for v in values if v == 2)
        u = (pos + neg) if (pos and neg) else (pos or neg or 0)
        if not sufficiency_gate(u, pos + neg + neu, cell_authors, policy=policy).passed:
            continue
        for per_record, names in axes:
            counts = [[0, 0, 0, 0] for _ in names]
            for i, segments in enumerate(per_record):
                value = values[i]
                for s in segments:
                    counts[s][value] += 1
            usable = []
            for s, name in enumerate(names):
                if name == MISSING_SEGMENT:
                    continue
                p, n, t, silent = counts[s]
                if not (p + n):
                    continue
                support = (p + n) if (p and n) else (p or n)
                if sufficiency_gate(support, p + n + t, p + n + t + silent, policy=policy).passed:
                    usable.append((p, n))
            for i in range(len(usable)):
                for j in range(i + 1, len(usable)):
                    (p1, n1), (p2, n2) = usable[i], usable[j]
                    tests += 1
                    value = fisher_two_sided(n1, p1, n2, p2)
                    if value < SPLIT_ALPHA:
                        significant += 1
                    min_p = min(min_p, value)
    return significant, tests, min_p


def permutation_arm(label: str, description: str, kept: dict[str, list[dict]],
                    tags_by_product: dict[str, list[dict]],
                    permutations: int = PERMUTATIONS) -> dict:
    """게이트 단계 하나에 대한 관측값 + 순열 귀무분포.

    **왜 게이트 단계별로 재나.** 중복 작성자를 지우지 않으면 같은 사람의 여러 리뷰가
    같은 조건·같은 의견으로 여러 번 세어져 조건과 방향이 실제보다 붙어 보인다
    (PER-170: 카운트가 19.7% 부푼다). 그 편향이 이 측정에 얼마나 들어오는지는 짐작이
    아니라 단계별 수로 확인한다.
    """
    units = build_units(kept, tags_by_product)
    observed = _statistic(units)
    rng = random.Random(SEED)
    null = [_statistic(units, rng) for _ in range(permutations)]
    counts = [n[0] for n in null]
    min_ps = [n[2] for n in null]
    ordered = sorted(counts)
    return {
        "arm": label,
        "description": description,
        "reviews": sum(len(v) for v in kept.values()),
        "observed": {
            "significantPairs": observed[0],
            "tests": observed[1],
            "minPValue": observed[2],
        },
        "null": {
            "permutations": permutations,
            "seed": SEED,
            "significantPairsMedian": ordered[len(ordered) // 2],
            "significantPairsP05": ordered[int(0.05 * len(ordered))],
            "significantPairsP95": ordered[int(0.95 * len(ordered))],
            "minPValueMedian": sorted(min_ps)[len(min_ps) // 2],
        },
        "permutationP": {
            "significantPairs": round(
                sum(1 for c in counts if c >= observed[0]) / permutations, 4),
            "minPValue": round(
                sum(1 for m in min_ps if m <= observed[2]) / permutations, 4),
        },
        "note": (
            "`permutationP` 가 작아야 '조건과 방향이 무관하다' 를 기각한다. 크면 관측된 "
            "갈림이 우연으로 설명된다는 뜻이고, 그 사실을 숨기지 않는다"
        ),
    }


def _finding(arms: list[dict], multi: dict, totals: dict) -> dict:
    """결론 문장. **수를 손으로 박지 않는다** — 옆 칸의 계산값과 조용히 갈린다."""
    by_arm = {a["arm"]: a for a in arms}
    raw, gated = by_arm["all"], by_arm["gate1+2"]
    dedup = by_arm["gate2"]
    return {
        "splitCellsFound": totals.get("split", 0),
        "splitPairsFound": totals.get("splitPairs", 0),
        "survivesMultiplicity": multi["significantHolm"] > 0,
        "survivesPermutation": gated["permutationP"]["significantPairs"] < 0.05,
        "text": (
            f"개별 사례는 있다 — 게이트1·2 통과분에서 조건별로 갈린 셀 "
            f"{totals.get('split', 0)}개 · 쌍 {totals.get('splitPairs', 0)}개이고 "
            f"가장 작은 p 는 {multi['minPValue']} 다. 그러나 **전체로는 우연과 구별되지 "
            f"않는다**: 검정 {multi['tests']:,}건에서 Holm 보정 후 남는 쌍이 "
            f"{multi['significantHolm']}개이고, 방향을 제품 안에서 섞은 순열 "
            f"{gated['null']['permutations']}회의 귀무분포에서 유의 쌍 중위수가 "
            f"{gated['null']['significantPairsMedian']}개다 (관측 "
            f"{gated['observed']['significantPairs']}개, 순열 p="
            f"{gated['permutationP']['significantPairs']}). "
            f"게이트 이전 전건에서는 유의 쌍이 {raw['observed']['significantPairs']}개로 "
            f"귀무 중위수 {raw['null']['significantPairsMedian']}개를 크게 넘고 순열 p="
            f"{raw['permutationP']['significantPairs']} 지만, **중복 게이트만 걸어도** "
            f"{dedup['observed']['significantPairs']}개(귀무 중위수 "
            f"{dedup['null']['significantPairsMedian']}, 순열 p="
            f"{dedup['permutationP']['significantPairs']})로 내려앉는다 — 게이트 이전의 "
            "갈림은 상당 부분 **같은 작성자를 여러 번 센 것**이다 (PER-170). "
            "조건 정규화는 유지한다: 표기가 '이 조건의 리뷰들은' 이어야 한다는 것과 "
            "'조건별로 방향이 갈린다' 를 이 스냅샷이 입증하느냐는 별개 문제이고, "
            "무조건 서술 검증은 갈림이 실제로 관측될 때 그것을 지우지 않기 위한 장치다"
        ),
    }


def rendering_samples() -> list[dict]:
    """표기 견본. 저장은 코드, 표기에서 라벨 — 두 값을 나란히 낸다."""
    samples = [
        ({}, "지속력이 좋다는 쪽이다"),
        ({"skinType": ["A02"]}, "건조하다는 리뷰가 많다"),
        ({"skinTrouble": ["C09"]}, "모공에 낀다는 리뷰가 있다"),
        ({"skinType": ["A01"], "skinTrouble": ["C05", "C07"]}, "번들거린다는 쪽이다"),
        ({"skinType": [MISSING_SEGMENT]}, "판단을 미룬다"),
        ({"option": "40+40ml"}, "용량 대비 낫다는 쪽이다"),
    ]
    return [{"stored": condition, "rendered": render(condition, result),
             "describe": describe(condition)} for condition, result in samples]


def main() -> None:
    ap = argparse.ArgumentParser(description="PER-192 조건별 방향 갈림 측정")
    ap.add_argument("--check", action="store_true",
                    help="재실행 결과가 커밋된 리포트와 같은지만 확인 (§5-2 재현성)")
    args = ap.parse_args()

    if not TAGS_PATH.exists():
        raise SystemExit(
            f"[PER-192] 전수 태그가 없다: {TAGS_PATH.relative_to(ROOT)}\n"
            "  → python3 pipeline/run_v5.py --steps tag (LLM 태깅 · 약 $4)"
        )
    records, catalog = load_records()
    kept = through_gates(records, catalog)

    tags_by_product: dict[str, list[dict]] = collections.defaultdict(list)
    product_of = {r["reviewId"]: r["productId"] for r in records}
    tags_all = [json.loads(line) for line in TAGS_PATH.read_text().splitlines() if line.strip()]
    for tag in tags_all:
        tags_by_product[product_of[tag["reviewId"]]].append(tag)

    rows, totals = scan(kept, tags_by_product, catalog)
    multi = multiplicity(rows, totals.get("comparedPairs", 0))

    # 게이트 단계별 관측값 + 순열 귀무분포. 파이프라인 단계(gate1+2)를 먼저 돌려
    # 계약 경로(`scan`)와 같은 통계량인지 대조한다 — 다르면 귀무분포가 무의미하다.
    grouped: dict[str, list[dict]] = collections.defaultdict(list)
    for record in records:
        grouped[record["productId"]].append(record)
    gate1_only = {pid: run_identity_gate(rs, IdentityScope(pid), catalog).passed
                  for pid, rs in sorted(grouped.items())}
    gate2_only = {pid: run_duplicate_gate(rs).passed for pid, rs in sorted(grouped.items())}

    pipeline_arm = permutation_arm(
        "gate1+2", "파이프라인 실제 입력 — 동일성(PER-182) → 중복(PER-183)", kept, tags_by_product)
    if (pipeline_arm["observed"]["significantPairs"] != totals.get("splitPairs", 0)
            or pipeline_arm["observed"]["tests"] != totals.get("comparedPairs", 0)):
        raise SystemExit(
            "[PER-192] 순열 경로가 계약 경로(condition_render.check_unconditional)와 "
            f"다른 통계량을 낸다: 순열 {pipeline_arm['observed']} vs 계약 "
            f"{totals.get('splitPairs')}/{totals.get('comparedPairs')}"
        )
    arms = [
        permutation_arm("all", "게이트 이전 전건 — 중복 작성자가 남아 있다",
                        dict(grouped), tags_by_product),
        permutation_arm("gate1", "동일성 게이트만 (옵션·리센시). 중복 작성자는 남아 있다",
                        gate1_only, tags_by_product),
        permutation_arm("gate2", "중복 게이트만 — 작성자 1명 = 1표 (PER-170)",
                        gate2_only, tags_by_product),
        pipeline_arm,
    ]

    report = {
        "issue": "PER-192",
        "source": {
            "path": str(INPUT_PATH.relative_to(ROOT)),
            "sha256": hashlib.sha256(INPUT_PATH.read_bytes()).hexdigest(),
            "reviews": len(records),
            "products": len(kept),
            "afterGate1And2": sum(len(v) for v in kept.values()),
            "tags": {
                "path": str(TAGS_PATH.relative_to(ROOT)),
                "sha256": hashlib.sha256(TAGS_PATH.read_bytes()).hexdigest(),
                **{k: json.loads(TAGS_META_PATH.read_text())[k]
                   for k in ("label", "model", "tags", "reviewsTagged")},
            },
        },
        "policy": {
            **DEFAULT_SUFFICIENCY.as_meta(),
            "splitAlpha": SPLIT_ALPHA,
            "test": "Fisher 정확검정 (양측) — 행은 세그먼트, 열은 (부정, 긍정)",
            "failureCandidate": FAILURE_MISSING_CONDITION,
        },
        "statedRates": stated_rates(records, kept),
        "rendering": rendering_samples(),
        "unconditional": {
            "productAspectPairs": totals.get("productAspects", 0),
            "sufficientUnconditionalClaims": totals.get("unconditionalClaims", 0),
            "insufficient": totals.get("insufficientUnconditional", 0),
            "holds": totals.get("holds", 0),
            "split": totals.get("split", 0),
            "splitPct": pct(totals.get("split", 0), totals.get("unconditionalClaims", 0)),
            "splitPairs": totals.get("splitPairs", 0),
            "majorityFlips": totals.get("majorityFlips", 0),
            "note": (
                "후보는 게이트4를 통과한 무조건 주장이다 — 애초에 말하지 못할 주장은 "
                "'조건을 빠뜨렸다' 고 할 대상이 아니다. 비교는 기재된·충분한 세그먼트끼리만 "
                "한다 (미기재는 독자가 자기에게 적용할 수 있는 조건이 아니다)"
            ),
        },
        "multiplicity": multi,
        "permutationByGate": arms,
        "finding": _finding(arms, multi, totals),
        "topSplits": headline(rows),
        "erasedByUnconditional": erased(rows),
        "splitClaims": rows,
        "limits": [
            "태그 품질에 딸려 있다. 태거는 GLM 4.7 단일 실행이고 방향이 반대로 뒤집히는 "
            "비율이 정답셋 233쌍 중 3쌍(1.29%)이다 — 갈림의 일부는 태거 오류일 수 있다 "
            "(eval/reports/v5_tag_gold_v2.json · polarity.TAGGER_FLIP_RATE)",
            "기재율이 상한이다. 프로필을 안 밝힌 리뷰는 어느 조건 세그먼트에도 들어가지 "
            "않으므로, 여기서 못 본 갈림이 없다는 뜻이 아니다",
            "세그먼트는 자기 선택이다. '모공 고민' 이라고 적은 사람이 모공을 더 본다 — "
            "조건이 결과를 바꾼 것인지 그 조건의 사람이 다르게 본 것인지 이 데이터로는 "
            "가르지 못한다. 표기가 '이 조건의 리뷰들은' 이어야 하는 이유다",
            "skinTrouble 은 한 리뷰가 여러 코드를 가지므로 세그먼트가 서로 겹친다. "
            "검정이 독립이 아니고 Bonferroni·Holm 은 그 경우 보수적이다",
            "이 측정은 claim 을 고치지 않는다. 산출은 `missing_condition` 후보이고 "
            "확정은 judge(PER-196)의 몫이다",
            "순열 귀무분포는 '조건과 방향이 무관하다' 를 제품 안에서 방향 벡터를 섞어 "
            "만든다. 섞기는 제품 단위라 제품 간 차이는 귀무에도 그대로 남아 있고, "
            "태거 오류도 관측·귀무 양쪽에 같이 들어간다",
            "검정력이 낮다. 게이트1·2 통과분에서 비교 가능한 쌍이 줄어 (같은 코퍼스의 "
            "게이트 이전 대비) 작은 차이는 잡히지 않는다 — '갈리지 않았다' 가 아니라 "
            "'이 표본으로는 갈렸다고 말할 수 없다' 다",
        ],
    }

    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.check:
        if not REPORT_PATH.exists():
            raise SystemExit(f"FAIL: 리포트가 없다 ({REPORT_PATH.relative_to(ROOT)})")
        if REPORT_PATH.read_text() != payload:
            raise SystemExit(
                f"FAIL: 조건 갈림 리포트가 재현되지 않는다 ({REPORT_PATH.relative_to(ROOT)})\n"
                "  → 표기 규칙·충분성 임계값·태그가 바뀌었다면 다시 생성해 함께 커밋한다"
            )
        print(f"OK: 조건 갈림 리포트 재현 일치 ({REPORT_PATH.relative_to(ROOT)})")
        return

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(payload)

    sr, un = report["statedRates"]["allReviews"], report["unconditional"]
    print(f"[PER-192] 기재율 (전건 {len(records):,}): " + " · ".join(
        f"{axis} {sr[axis]['statedPct']}%" for axis in CONDITION_AXES))
    print(f"  무조건 주장 {un['sufficientUnconditionalClaims']}건 중 "
          f"조건별로 갈린 것 {un['split']}건 ({un['splitPct']}%) · "
          f"갈린 쌍 {un['splitPairs']}개 (다수 방향 뒤집힘 {un['majorityFlips']}개)")
    print(f"  검정 {multi['tests']:,}건 — 보정 전 {multi['significantRaw']} · "
          f"Holm {multi['significantHolm']} · Bonferroni {multi['significantBonferroni']}")
    for pair in report["topSplits"][:5]:
        print(f"    p={pair['pValue']:.2e} {pair['sentence']}")
    for arm in arms:
        print(f"  [순열 {arm['arm']:7s}] 리뷰 {arm['reviews']:6,} · 유의 쌍 "
              f"{arm['observed']['significantPairs']:3d} / 검정 {arm['observed']['tests']:5,} "
              f"· 귀무 중위수 {arm['null']['significantPairsMedian']:3d} "
              f"· p={arm['permutationP']['significantPairs']}")
    print(f"  무조건 서술이었다면 덮였을 작성자 "
          f"{report['erasedByUnconditional']['authors']}명 "
          f"({report['erasedByUnconditional']['claimsWithContradiction']}개 주장)")
    print(f"→ {REPORT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
