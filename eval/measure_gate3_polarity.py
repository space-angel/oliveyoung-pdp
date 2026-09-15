"""
PER-185 근거 측정 — 게이트3 방향성(별점 교차검증 · 혼재 처리).

**이 리포트는 태그 파일이 있어야 돈다.** 태그는 LLM 산출물이고 `data/intermediate/`
라 gitignore 대상이다. 그래서 `verify.sh` 의 `--check` 대열에 넣지 않는다 — 넣으면
클론 직후 게이트가 깨진다 (`measure_full_tagging.py` 와 같은 규칙). 대신 리포트 안에
실행 정체(모델·프롬프트 해시·입력 해시)를 박아 어떤 실행에서 나온 수치인지 남긴다.

확인하는 것은 다섯 가지다.

  1) **혼재가 실재하는가** — `(제품 × aspect)` 묶음 중 방향이 갈리는 비율. 갈림을
     태거 잡음과 구별하는 모수(`TAGGER_FLIP_RATE`)에 결과가 얼마나 민감한지 함께 낸다
  2) **별점은 방향의 대리값이 될 수 없는가** — 별점과 태그 방향의 불일치율과 층화.
     `minority_aspect`(좋은데 X는 별로)가 다수라면 별점으로 방향을 정하는 순간
     그만큼의 부정 근거가 사라진다는 뜻이다
  3) **태거 방향 오류 모수가 아직 맞는가** — 정답셋에서 다시 재고 상수와 어긋나면
     **에러다.** 태거를 바꾸면(PER-213) 이 모수가 조용히 낡는다
  4) **리뷰 안 갈림이 어디로 갔는가** — 태깅 계약이 같은 축 중복을 떨어뜨렸다.
     그 결과 방향이 **출력 순서로 정해진** `(리뷰 × aspect)` 가 몇 건인지 센다
  5) **v4 의 polarity 불일치 2건을 이 게이트가 잡는가** — 완료 조건 4

사용:
  .venv/bin/python eval/measure_gate3_polarity.py
  → eval/reports/gate3_polarity_per185.json
  → eval/reports/gate3_rating_conflicts.jsonl   (#3 평가셋 후보 풀)

`order_chosen` 집합(모수 4)은 gitignore 대상이라 없으면 **자동으로 다시 만든다** —
결정론적 로컬 파싱이고 API 를 부르지 않는다. `--build-order-chosen` 은 그걸 따로
돌리고 싶을 때만 쓴다.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

from catalog import load_catalog  # noqa: E402
from gates import IdentityScope, run_duplicate_gate, run_identity_gate  # noqa: E402
from polarity import (  # noqa: E402
    CONFLICT_LABELS,
    LIMIT_ORDER_CHOSEN_DIRECTION,
    LIMIT_MINORITY_WITHIN_NOISE,
    NOISE_ALPHA,
    TAGGER_FLIP_RATE,
    TAGGER_FLIP_RATE_SOURCE,
    VERDICT_LABELS,
    DIRECTION_MIXED,
    VERDICT_MIXED,
    aspect_support,
    binom_sf,
    rating_conflicts,
)
from policy import SUFFICIENCY_N_MIN  # noqa: E402
from tag_contract import ASPECTS  # noqa: E402

REVIEWS_PATH = ROOT / "data/intermediate/v5_reviews.jsonl"
TAGS_PATH = ROOT / "data/intermediate/v5_tags.jsonl"
TAGS_META_PATH = ROOT / "data/intermediate/v5_tags_meta.json"
GOLD_PATH = ROOT / "eval/gold/v5_tags_pilot_gold.jsonl"
REPORT_PATH = ROOT / "eval/reports/gate3_polarity_per185.json"
CONFLICTS_PATH = ROOT / "eval/reports/gate3_rating_conflicts.jsonl"
ORDER_CHOSEN_PATH = ROOT / "data/intermediate/v5_tags_order_chosen.json"

# 모수를 다시 쟀을 때 상수와 이만큼 넘게 벌어지면 에러. 정답셋이 조금 손질돼도
# 흔들리지 않되, 태거가 바뀌어 모수가 달라지면 잡히는 폭이다.
FLIP_RATE_TOLERANCE = 0.01

# v4 평가에서 polarity 불일치로 찍힌 2건 (`eval/reports/eval_report_v4.json`).
# 둘 다 **질문이 실제 여론과 반대 방향을 암시한** 유형이다. 게이트3 이 그 방향을
# 어떻게 판정하는지가 완료 조건 4 다.
V4_POLARITY_FAILURES = (
    {
        "concernId": "파티온 _05",
        "productNameFragment": "파티온 노스카나인 트러블 세럼",
        "question": "제형이 너무 물 같아서 양 조절이 어렵지 않을까요?",
        "presupposes": "negative",
        "aspects": ["발림감/텍스처", "흡수력"],
    },
    {
        "concernId": "힌스 로_02",
        "productNameFragment": "힌스 로 글로우 젤 틴트",
        "question": "광택감이 오래 유지되지 않는 편인가요?",
        "presupposes": "negative",
        # v4 판정문이 센 '긍정 32건 대 부정 4건' 은 광택 축이다. 지속성도 함께 본다 —
        # 질문이 두 축("광택감"이 "오래 유지")을 한 문장에 묶었기 때문이다
        "aspects": ["광택/윤기", "지속성"],
    },
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def pct(part: int, whole: int) -> float:
    return round(100 * part / whole, 2) if whole else 0.0


def require(path: Path, how: str) -> None:
    if not path.exists():
        raise SystemExit(f"입력이 없다: {path.relative_to(ROOT)}\n  {how}")


def gate12(records: list[dict], catalog) -> dict[str, list[dict]]:
    """게이트1 → 게이트2 를 제품별로 건다. 게이트3 의 입력은 그 통과분이다.

    옵션 범위는 걸지 않는다 — 옵션은 질문이 요구할 때만 거는 컷이고(PER-182),
    여기서는 제품 전체의 방향 분포를 본다.
    """
    grouped: dict[str, list[dict]] = collections.defaultdict(list)
    for record in records:
        grouped[record["productId"]].append(record)
    passed: dict[str, list[dict]] = {}
    for pid, rows in sorted(grouped.items()):
        first = run_identity_gate(rows, IdentityScope(pid), catalog)
        passed[pid] = run_duplicate_gate(first.passed).passed
    return passed


def measure_flip_rate(tags: list[dict]) -> dict:
    """태거가 방향을 **반대로** 뒤집는 비율을 정답셋에서 다시 잰다 (모수 3).

    3종 일치율(6.5%)이 아니라 방향쌍만 본다. 방향↔`neutral` 오류는 가짜 반대
    방향을 만들지 않고 분모에서 빠질 뿐이라 혼재 판정을 위협하지 않는다.
    """
    gold = read_jsonl(GOLD_PATH)
    g = {(t["reviewId"], t["aspect"]): t for t in gold}
    gold_ids = {t["reviewId"] for t in gold}
    a = {(t["reviewId"], t["aspect"]): t for t in tags if t["reviewId"] in gold_ids}
    both = set(a) & set(g)

    directional = [k for k in both
                   if a[k]["polarity"] in ("positive", "negative")
                   and g[k]["polarity"] in ("positive", "negative")]
    opposed = [k for k in directional if a[k]["polarity"] != g[k]["polarity"]]
    agree3 = sum(1 for k in both if a[k]["polarity"] == g[k]["polarity"])

    confusion: collections.Counter = collections.Counter(
        (g[k]["polarity"], a[k]["polarity"]) for k in both)
    return {
        "goldPairs": len(g),
        "overlap": len(both),
        "polarityAgreementAll3Pct": pct(agree3, len(both)),
        "directionalPairs": len(directional),
        "opposedPairs": len(opposed),
        "opposedPct": pct(len(opposed), len(directional)),
        "upperBound95": TAGGER_FLIP_RATE,
        "confusion": {f"gold:{k[0]}→tag:{k[1]}": v for k, v in sorted(confusion.items())},
        "note": (
            "3종 일치율과 방향 뒤집힘은 다르다 — 불일치의 대부분이 방향↔neutral 이고 "
            "그건 가짜 반대 방향을 만들지 않는다. 혼재 판정 모수로는 뒤집힘만 쓴다"
        ),
    }


def assert_flip_rate_current(measured: dict) -> None:
    """모수가 낡았으면 멈춘다. 태거를 바꾸면(PER-213) 조용히 낡는 종류의 상수다."""
    observed = measured["opposedPct"] / 100
    if observed > TAGGER_FLIP_RATE + FLIP_RATE_TOLERANCE:
        raise SystemExit(
            f"[중단] 태거 방향 뒤집힘이 {measured['opposedPct']}% 로 "
            f"모수 {TAGGER_FLIP_RATE * 100:.1f}% 를 넘는다.\n"
            "  → 태거가 바뀌었다. pipeline/polarity.py 의 TAGGER_FLIP_RATE 를 다시 정하고\n"
            "     혼재 판정을 재측정하라. 그냥 두면 잡음을 '혼재'로 부른다."
        )


def load_order_chosen() -> tuple[set[tuple[int, str]], dict]:
    """방향이 **출력 순서로 정해진** `(reviewId, aspect)` 집합 (모수 4).

    태깅 계약은 "같은 리뷰에 같은 aspect 를 두 번 넣지 않는다"이고, 위반 시 **먼저
    나온 태그만 남는다.** 그래서 태거가 한 축에 긍·부정을 둘 다 뱉은 리뷰는 축이
    사라지는 게 아니라 **방향이 출력 순서로 굳는다.** 그 사실이 판정의 한계로
    따라나가야 한다.

    파일이 없으면 **원문 응답에서 다시 만든다.** 결정론적이고 API 를 부르지 않는
    로컬 파싱이라 그래도 된다 — 그리고 그래야 한다. 이 집합은 gitignore 대상인데
    커밋된 리포트가 여기 의존하므로, 자동으로 만들지 않으면 같은 태그를 가진
    다른 워크트리에서 `--check` 가 **한계가 빠진 리포트로 조용히 실패한다.**

    원문 응답조차 없으면 그때는 빈 집합을 돌려주되 **그 사실을 리포트에 적는다** —
    한계가 조용히 빠지는 것과 한계가 없는 것은 다르다.
    """
    if not ORDER_CHOSEN_PATH.exists():
        manifest = ROOT / "data/intermediate/tag_runs/full_glm47.json"
        if not manifest.exists() or not (ROOT / json.loads(manifest.read_text())["raw"]).exists():
            return set(), {
                "available": False,
                "note": (
                    f"{ORDER_CHOSEN_PATH.relative_to(ROOT)} 도 태깅 원문 응답도 없다 — "
                    "`order_chosen` 한계를 재지 못했다. 이 리포트는 그만큼 낙관적이다"
                ),
            }
        print(f"[order_chosen] {ORDER_CHOSEN_PATH.relative_to(ROOT)} 가 없어 원문에서 다시 만든다")
        build_order_chosen()
    payload = json.loads(ORDER_CHOSEN_PATH.read_text())
    pairs = {(int(r["reviewId"]), r["aspect"]) for r in payload["pairs"]}
    return pairs, {"available": True, **{k: v for k, v in payload.items() if k != "pairs"}}


def build_order_chosen() -> dict:
    """원문 청크를 다시 파싱해 `order_chosen` 집합을 만든다.

    태깅 실행의 매니페스트는 위반 **표본**만 들고 있어서(50건) 전수 집합은 원문
    응답에서 다시 세야 한다. 비싸지 않지만(로컬 파싱) 매번 할 일은 아니라 파일로 남긴다.
    """
    from tag import load_reviews, parse_text, partition_by_contract, read_jsonl as rj

    manifest_path = ROOT / "data/intermediate/tag_runs/full_glm47.json"
    require(manifest_path, "전수 태깅 실행 매니페스트가 필요하다")
    manifest = json.loads(manifest_path.read_text())
    raw_path = ROOT / manifest["raw"]
    require(raw_path, "전수 태깅 원문 응답이 필요하다")

    reviews = {r["reviewId"]: r for r in load_reviews(pilot=False)}
    emitted: list[dict] = []
    for chunk in rj(raw_path):
        try:
            parsed = parse_text(chunk["text"])
        except Exception:  # noqa: BLE001 — 깨진 청크는 태깅 층이 이미 셌다
            continue
        for row in parsed.get("results", []):
            for a in row.get("aspects", []):
                emitted.append({"reviewId": row["reviewId"], **a})

    kept, violations = partition_by_contract(emitted, reviews)
    kept_polarity = {(t["reviewId"], t["aspect"]): t["polarity"] for t in kept}

    full: dict[tuple[int, str], list[str]] = {}
    for v in violations:
        if "두 번" not in v["violation"]:
            continue
        key = (v["reviewId"], v["aspect"])
        if key not in full:
            full[key] = [kept_polarity[key]] if key in kept_polarity else []
        full[key].append(v["polarity"])

    opposed = sorted(k for k, ps in full.items()
                     if "positive" in ps and "negative" in ps)
    # 중복이 곧 갈림은 아니다 — 같은 방향을 두 번 뱉은 쪽이 훨씬 많다. 그 분포를
    # 남겨야 "1,644건의 혼재 신호가 버려졌다"는 오독을 수치로 막을 수 있다
    combos = collections.Counter(" + ".join(sorted(ps)) for ps in full.values())
    payload = {
        "issue": "PER-185",
        "source": {"manifest": manifest["label"], "raw": manifest["raw"]},
        "emittedTags": len(emitted),
        "keptTags": len(kept),
        "duplicateAspectViolations": sum(1 for v in violations if "두 번" in v["violation"]),
        "distinctPairsWithDuplicate": len(full),
        "emittedDirectionCombos": dict(combos.most_common()),
        "pairsWithOpposedDirections": len(opposed),
        "reviewsAffected": len({rid for rid, _ in opposed}),
        "byAspect": dict(collections.Counter(a for _, a in opposed).most_common()),
        "note": (
            "태깅 계약 위반 시 **먼저 나온 태그만 남는다** — 축이 사라지는 게 아니라 "
            "방향이 출력 순서로 굳는다. 이 목록의 방향은 판정된 것이 아니다"
        ),
        "pairs": [{"reviewId": rid, "aspect": a} for rid, a in opposed],
    }
    ORDER_CHOSEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    ORDER_CHOSEN_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return payload


def cell_profile(passed: dict[str, list[dict]], tags_by_product: dict, catalog,
                 order_chosen: set) -> tuple[dict, list]:
    """`(제품 × aspect)` 묶음마다 방향을 판정한다. 판정 1건이 곧 화면의 한 줄이다."""
    verdicts: collections.Counter = collections.Counter()
    cells = []
    for pid, records in passed.items():
        tags = tags_by_product.get(pid, [])
        for aspect in ASPECTS:
            support = aspect_support(records, tags, aspect, order_chosen=order_chosen)
            verdicts[support.verdict] += 1
            if support.directional_authors:
                cells.append({
                    "productId": pid,
                    "displayName": catalog.product(pid).display_name,
                    "aspect": aspect,
                    "direction": support.direction,
                    "verdict": support.verdict,
                    "label": VERDICT_LABELS[support.verdict],
                    "positiveAuthors": support.positive_authors,
                    "negativeAuthors": support.negative_authors,
                    "neutralAuthors": support.neutral_authors,
                    "silentAuthors": support.silent_authors,
                    "directionalAuthors": support.directional_authors,
                    "negativeRatio": support.negative_ratio,
                    "minorityAuthors": support.minority_authors,
                    "minorityBeyondNoise": support.minority_beyond_noise,
                    "limitations": sorted(support.limitations),
                })
    sized = [c for c in cells if c["directionalAuthors"] >= SUFFICIENCY_N_MIN]
    mixed_sized = [c for c in sized if c["direction"] == DIRECTION_MIXED]
    beyond = [c for c in mixed_sized if c["minorityBeyondNoise"]]
    ratios = sorted(c["negativeRatio"] for c in sized)

    def q(p: float) -> float:
        return round(ratios[min(len(ratios) - 1, int(len(ratios) * p))], 4) if ratios else 0.0

    return {
        "cellUnit": "productId × aspect (조건축은 호출부가 자른다)",
        "cellsEvaluated": len(ASPECTS) * len(passed),
        "verdicts": {v: {"cells": n, "label": VERDICT_LABELS[v]}
                     for v, n in sorted(verdicts.items())},
        "withDirectionalEvidence": len(cells),
        f"withDirectionalAtLeast{SUFFICIENCY_N_MIN}": len(sized),
        # 정본 정의 — 반대가 1명이어도 mixed 다 (PER-178 §9)
        "mixedCells": len(mixed_sized),
        "mixedPctOfSized": pct(len(mixed_sized), len(sized)),
        # 그 중 소수가 태거 잡음으로 설명되지 않는 것. **판정이 아니라 주석이다**
        "mixedBeyondNoiseCells": len(beyond),
        "mixedBeyondNoisePctOfSized": pct(len(beyond), len(sized)),
        "mixedWithinNoiseCells": len(mixed_sized) - len(beyond),
        "negativeRatioQuantiles": {
            "min": q(0.0), "p25": q(0.25), "median": q(0.5),
            "p75": q(0.75), "p90": q(0.9), "max": round(ratios[-1], 4) if ratios else 0.0,
        },
        "cellsWithOrderChosenLimitation": sum(
            1 for c in sized if LIMIT_ORDER_CHOSEN_DIRECTION in c["limitations"]),
        "cellsWithMinorityWithinNoise": sum(
            1 for c in sized if LIMIT_MINORITY_WITHIN_NOISE in c["limitations"]),
        "note": (
            f"N>={SUFFICIENCY_N_MIN} 은 충분성 게이트(PER-186)의 절대하한이다. "
            "게이트3 은 컷하지 않고 같은 잣대로 읽기 위해 쓴다"
        ),
    }, cells


def noise_sensitivity(cells: list) -> dict:
    """잡음 주석이 모수에 얼마나 민감한가.

    **혼재 판정의 민감도가 아니다** — 혼재는 반대 1명이어도 혼재이므로 모수와 무관하다.
    여기서 흔들리는 건 "이 갈림을 태거 잡음으로 설명할 수 있는가" 라는 주석뿐이다.
    """
    sized = [c for c in cells if c["directionalAuthors"] >= SUFFICIENCY_N_MIN]
    split = [c for c in sized if c["direction"] == DIRECTION_MIXED]
    rows = {}
    for p in (0.013, TAGGER_FLIP_RATE, 0.05, 0.065, 0.08):
        n = sum(1 for c in split
                if binom_sf(min(c["positiveAuthors"], c["negativeAuthors"]),
                            c["directionalAuthors"], p) < NOISE_ALPHA)
        rows[f"{p:.3f}"] = {"beyondNoiseCells": n, "pctOfSplit": pct(n, len(split))}
    floors = {}
    for n in (8, 10, 20, 50, 100, 200, 400):
        for k in range(n + 1):
            if binom_sf(k, n, TAGGER_FLIP_RATE) < NOISE_ALPHA:
                floors[str(n)] = {"minMinority": k, "minMinorityPct": round(100 * k / n, 1)}
                break
    return {
        "sizedCells": len(sized),
        "splitCells": len(split),
        "byFlipRate": rows,
        "minorityFloorBySampleSize": floors,
        "note": (
            "주석의 문턱은 고정 비율이 아니라 표본 크기를 따라 움직인다. 점추정(1.3%)을 "
            "쓰면 잡음 밖이 늘고 3종 불일치율(6.5%)을 쓰면 준다 — 상한 3.3% 는 그 사이의 "
            "보수적 선택이다. 어느 값을 써도 **혼재 판정 자체는 바뀌지 않는다**"
        ),
    }


def conflict_profile(passed: dict[str, list[dict]], tags_by_product: dict) -> tuple[dict, list]:
    """별점 교차검증 (완료 조건 2). 판정을 바꾸지 않고 모으기만 한다."""
    conflicts = []
    directional_tags = 0
    rated_directional = 0
    for pid, records in passed.items():
        tags = tags_by_product.get(pid, [])
        conflicts.extend((pid, c) for c in rating_conflicts(records, tags))
        by_id = {r["reviewId"]: r for r in records}
        for t in tags:
            if t["polarity"] not in ("positive", "negative"):
                continue
            directional_tags += 1
            if by_id[t["reviewId"]]["derived"]["sentimentPrior"] in ("positive", "negative"):
                rated_directional += 1

    kinds: collections.Counter = collections.Counter(c.kind for _, c in conflicts)
    by_aspect: collections.Counter = collections.Counter(c.aspect for _, c in conflicts)
    by_rating: collections.Counter = collections.Counter(c.rating for _, c in conflicts)
    direction: collections.Counter = collections.Counter(
        f"별점{c.rating_sentiment}↔태그{c.tag_polarity}" for _, c in conflicts)
    return {
        "directionalTags": directional_tags,
        "comparable": rated_directional,
        "notComparableRating3": directional_tags - rated_directional,
        "conflicts": len(conflicts),
        "conflictPct": pct(len(conflicts), rated_directional),
        "byKind": {
            k: {"tags": n, "pct": pct(n, len(conflicts)), "label": CONFLICT_LABELS[k]}
            for k, n in sorted(kinds.items())
        },
        "byDirection": dict(direction.most_common()),
        "byRating": {str(k): v for k, v in sorted(by_rating.items())},
        "topAspects": dict(by_aspect.most_common(6)),
        "note": (
            "`minority_aspect` 가 다수라면 그건 오류가 아니라 정상이다 — 한 리뷰가 "
            "축마다 다른 방향을 말한다는 증거이고, **별점으로 방향을 정하면 그만큼의 "
            "부정 근거가 사라진다**는 뜻이다. 라벨 가치는 `sole_aspect` 가 가장 높다"
        ),
    }, conflicts


def v4_recheck(passed: dict[str, list[dict]], tags_by_product: dict, catalog,
               order_chosen: set) -> dict:
    """완료 조건 4 — v4 polarity 불일치 2건을 이 게이트가 잡는가.

    "잡는다"의 뜻: 질문이 암시한 방향을 게이트의 판정이 **뒷받침하지 않는다**는 것이
    산출물에 드러나는가. 게이트는 질문을 검사하지 않는다 — 방향과 비율을 먼저 확정해
    한쪽만 보여주는 주장이 애초에 나올 수 없게 만든다.
    """
    out = []
    for case in V4_POLARITY_FAILURES:
        pid = next((p for p in passed
                    if case["productNameFragment"] in catalog.product(p).display_name), None)
        if pid is None:
            out.append({**{k: v for k, v in case.items() if k != "aspects"},
                        "found": False,
                        "note": "이 제품이 v5 스냅샷에 없다"})
            continue
        rows = []
        for aspect in case["aspects"]:
            sup = aspect_support(passed[pid], tags_by_product.get(pid, []), aspect,
                                 order_chosen=order_chosen)
            ratio = sup.negative_ratio
            # 전제된 방향의 몫. 이 수가 작으면 질문이 근거를 앞질러 간 것이다
            share = (ratio if case["presupposes"] == "negative"
                     else (1 - ratio) if ratio is not None else None)
            rows.append({
                "aspect": aspect,
                "direction": sup.direction,
                "label": VERDICT_LABELS[sup.verdict],
                "positiveAuthors": sup.positive_authors,
                "negativeAuthors": sup.negative_authors,
                "silentAuthors": sup.silent_authors,
                "negativeRatio": ratio,
                "minorityBeyondNoise": sup.minority_beyond_noise,
                "presupposedShare": round(share, 4) if share is not None else None,
                # 전제된 방향이 **유일한** 방향인가 (한쪽만 보여줘도 되는 경우)
                "presupposedIsSoleDirection": sup.direction == case["presupposes"],
                # 전제된 방향이 잡음 밖으로 실재하는가. `mixed` 라도 소수가 잡음
                # 범위면 그 방향을 단정하는 질문은 근거를 앞지른 것이다
                "presupposedSurvivesNoise": (
                    sup.direction == case["presupposes"]
                    or (sup.mixed and share is not None
                        and (sup.minority_beyond_noise or share >= 0.5))
                ),
            })
        # "잡았다" = v4 판정문이 근거로 든 축에서, 전제된 방향이 근거를 앞질렀다는 게
        # 게이트 산출물에 드러난다. 첫 축이 그 축이다 (V4_POLARITY_FAILURES 주석 참조)
        cited = rows[0]
        out.append({
            "concernId": case["concernId"],
            "productId": pid,
            "displayName": catalog.product(pid).display_name,
            "question": case["question"],
            "presupposes": case["presupposes"],
            "found": True,
            "citedAspect": cited["aspect"],
            "aspects": rows,
            "caught": not cited["presupposedSurvivesNoise"],
        })
    return {
        "cases": out,
        "caught": sum(1 for c in out if c.get("caught")),
        "total": len(out),
        "criterion": (
            "v4 판정문이 근거로 든 축에서 전제된 방향의 몫(presupposedShare)이 태거 "
            "잡음 밖으로 실재하지 않으면 '잡았다'. 방향 판정만으로는 못 가른다 — "
            "반대 1명도 mixed 라 거의 모든 셀이 mixed 이기 때문이다"
        ),
        "note": (
            "게이트3 은 질문을 검사하지 않는다. 방향과 비율을 **주장보다 먼저** 확정해 "
            "근거가 뒷받침하지 않는 방향을 전제한 주장이 나올 수 없게 만든다. "
            "`혼재` 도 '질문이 옳았다'가 아니라 '양쪽을 함께 보여야 한다'는 판정이고, "
            "그래서 negativeRatio 가 주장에 함께 실린다"
        ),
    }


def build() -> tuple[dict, list]:
    require(REVIEWS_PATH, "먼저 입수를 돌린다 — python3 pipeline/run_v5.py --steps ingest")
    require(TAGS_PATH, "전수 태그가 필요하다 — python3 pipeline/run_v5.py --steps tag")
    require(TAGS_META_PATH, "전수 태그 정본 메타가 필요하다")

    records = read_jsonl(REVIEWS_PATH)
    tags = read_jsonl(TAGS_PATH)
    tags_meta = json.loads(TAGS_META_PATH.read_text())
    catalog = load_catalog()

    flip = measure_flip_rate(tags)
    assert_flip_rate_current(flip)

    order_chosen, order_meta = load_order_chosen()
    passed = gate12(records, catalog)
    kept_ids = {r["reviewId"] for rows in passed.values() for r in rows}

    tags_by_product: dict[str, list[dict]] = collections.defaultdict(list)
    product_of = {r["reviewId"]: r["productId"] for rows in passed.values() for r in rows}
    for t in tags:
        if t["reviewId"] in kept_ids:
            tags_by_product[product_of[t["reviewId"]]].append(t)

    cells_summary, cells = cell_profile(passed, tags_by_product, catalog, order_chosen)
    conflicts_summary, conflicts = conflict_profile(passed, tags_by_product)

    report = {
        "issue": "PER-185",
        "source": {
            "reviews": {
                "path": str(REVIEWS_PATH.relative_to(ROOT)),
                "sha256": sha256(REVIEWS_PATH),
                "records": len(records),
            },
            "tags": {
                "path": str(TAGS_PATH.relative_to(ROOT)),
                "sha256": sha256(TAGS_PATH),
                "tags": len(tags),
                "run": tags_meta.get("label"),
                "model": tags_meta.get("model"),
                "prompt": tags_meta.get("prompt"),
                "taggingInputSha256": (tags_meta.get("input") or {}).get("taggingSha256"),
            },
            "note": (
                "태그는 LLM 산출물이라 재생성 비용이 $4 다. verify.sh --check 대열에 "
                "넣지 않고 실행 정체를 리포트에 박는다 (measure_full_tagging.py 와 같은 규칙)"
            ),
        },
        "policy": {
            "tagUnit": "(리뷰 × aspect) — 리뷰 1건에 방향 1개가 아니다 (PER-175)",
            "countUnit": "고유 작성자 수 (게이트2 통과분이라 리뷰 1건 = 작성자 1명)",
            "gateRejects": False,
            "gateRejectsNote": "게이트3 은 반대 근거를 버리지 않는다 — rejected[] 가 없다",
            "negativeRatioDenominator": "positive + negative (neutral·silent 제외, PER-178)",
            "taggerFlipRate": TAGGER_FLIP_RATE,
            "taggerFlipRateSource": TAGGER_FLIP_RATE_SOURCE,
            "directionRule": (
                "golden_contract.derive_direction (PER-178) 와 같은 규칙 — 반대 1명도 mixed. "
                "소수 처리는 게이트4(PER-186)"
            ),
            "noiseAlpha": NOISE_ALPHA,
            "sufficiencyNMin": SUFFICIENCY_N_MIN,
        },
        "gateInput": {
            "records": len(records),
            "afterGate1And2": len(kept_ids),
            "products": len(passed),
            "tagsAfterGates": sum(len(v) for v in tags_by_product.values()),
            "tagsDroppedWithReviews": len(tags) - sum(len(v) for v in tags_by_product.values()),
            "note": (
                "게이트2 에서 빠진 리뷰의 태그도 함께 빠진다 — 같은 작성자의 두 번째 "
                "리뷰가 방향에서 두 표가 되면 안 된다"
            ),
        },
        "taggerDirectionError": flip,
        "withinReviewSplit": order_meta,
        "cells": cells_summary,
        "noiseSensitivity": noise_sensitivity(cells),
        "ratingCrossCheck": conflicts_summary,
        "v4PolarityFailures": v4_recheck(passed, tags_by_product, catalog, order_chosen),
    }
    return report, conflicts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build-order-chosen", action="store_true",
                    help="원문 응답을 다시 파싱해 order_chosen 집합을 만든다 (느림)")
    ap.add_argument("--check", action="store_true",
                    help="재실행 결과가 커밋된 리포트와 같은지만 확인")
    args = ap.parse_args()

    if args.build_order_chosen:
        payload = build_order_chosen()
        print(f"[order_chosen] 같은 축 중복 {payload['duplicateAspectViolations']}건 → "
              f"방향이 갈린 (리뷰×aspect) {payload['pairsWithOpposedDirections']}건 "
              f"(리뷰 {payload['reviewsAffected']}건)")
        print(f"→ {ORDER_CHOSEN_PATH.relative_to(ROOT)}")
        return

    report, conflicts = build()
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    conflict_lines = "".join(
        json.dumps({"productId": pid, **c.as_dict()}, ensure_ascii=False) + "\n"
        for pid, c in conflicts)

    if args.check:
        for path, want in ((REPORT_PATH, payload), (CONFLICTS_PATH, conflict_lines)):
            if not path.exists():
                raise SystemExit(f"FAIL: 산출물이 없다 ({path.relative_to(ROOT)})")
            if path.read_text() != want:
                raise SystemExit(
                    f"FAIL: 게이트3 산출물이 재현되지 않는다 ({path.relative_to(ROOT)})\n"
                    "  → 방향 판정 규칙이나 태그 정본이 바뀌었다면 다시 생성해 함께 커밋한다")
        print(f"OK: 게이트3 리포트 재현 일치 ({REPORT_PATH.relative_to(ROOT)})")
        return

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(payload)
    CONFLICTS_PATH.write_text(conflict_lines)

    c, m, r = report["cells"], report["noiseSensitivity"], report["ratingCrossCheck"]
    g = report["gateInput"]
    print(f"[게이트3] 게이트1·2 통과 {g['afterGate1And2']}건 · 태그 {g['tagsAfterGates']}개 "
          f"→ 판정 {c['withDirectionalEvidence']}셀")
    print(f"  혼재(정본·반대1명도): N>={SUFFICIENCY_N_MIN} 셀 {m['sizedCells']}개 중 "
          f"{c['mixedCells']}개 ({c['mixedPctOfSized']}%) · 부정비율 중위 "
          f"{c['negativeRatioQuantiles']['median']}")
    print(f"    그 중 소수가 잡음 밖 {c['mixedBeyondNoiseCells']}개 "
          f"({c['mixedBeyondNoisePctOfSized']}%) · 잡음 범위 {c['mixedWithinNoiseCells']}개")
    print(f"  태거 방향 뒤집힘 {report['taggerDirectionError']['opposedPct']}% "
          f"(방향쌍 {report['taggerDirectionError']['directionalPairs']}개) — 모수 {TAGGER_FLIP_RATE}")
    print(f"  별점 불일치 {r['conflicts']}건 / 비교가능 {r['comparable']}건 ({r['conflictPct']}%)")
    for kind, v in r["byKind"].items():
        print(f"    {v['label']:8s} {v['tags']:5d}건 ({v['pct']}%)")
    w = report["withinReviewSplit"]
    if w.get("available"):
        print(f"  리뷰 안 갈림: 방향이 출력 순서로 정해진 (리뷰×aspect) "
              f"{w['pairsWithOpposedDirections']}건 · 영향 셀 {c['cellsWithOrderChosenLimitation']}개")
    else:
        print(f"  리뷰 안 갈림: 재지 못했다 — {w['note']}")
    v4 = report["v4PolarityFailures"]
    print(f"  v4 polarity 불일치 재확인: {v4['caught']}/{v4['total']} 잡음")
    for case in v4["cases"]:
        for row in case.get("aspects", []):
            mark = "←판정근거" if row["aspect"] == case.get("citedAspect") else ""
            print(f"    {case['concernId']:10s} {row['aspect']:12s} "
                  f"{row['label']:4s} 부정비율 {row['negativeRatio']} "
                  f"(pos {row['positiveAuthors']} / neg {row['negativeAuthors']}) "
                  f"잡음밖={row['minorityBeyondNoise']} {mark}")
    print(f"→ {REPORT_PATH.relative_to(ROOT)}")
    print(f"→ {CONFLICTS_PATH.relative_to(ROOT)}  ({len(conflicts)}건 — #3 평가셋 후보 풀)")


if __name__ == "__main__":
    main()
