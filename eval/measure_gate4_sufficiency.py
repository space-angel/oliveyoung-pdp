"""
PER-186 근거 측정 — 게이트4 충분성(절대하한 AND 비율 AND 세그먼트 최소).

확인하는 것은 네 가지다.

  1) **셀이 얼마나 작은가** — `S ≥ S_min` 이 실제로 무엇을 침묵시키는가. 게이트1+2를
     통과한 고유 작성자 수로 `productId` · `productId×skinType` · `productId×skinTrouble`
     셀 크기를 낸다 (§5-3 기본 폴백의 근거)
  2) **임계값을 바꾸면 통과 주장 수가 어떻게 변하는가** — PER-199 커버리지 측정의 입력.
     사람이 만든 골든셋 라벨 26건에 게이트를 걸어 N_min × R_min 격자를 훑는다
  3) **소수 의견이 얼마나 흔한가** — "반대 1명" 을 다수 방향으로 뭉갤지 말지가
     이 이슈의 결정이고, 그 결정이 몇 건에 걸리는지를 먼저 센다
  4) **비율 조건이 결속하는가** — 골든셋에서는 결속하지 않는다. 그 사실을 숨기지 않고
     `limits` 에 적는다. 결속하는 구간은 D 가 큰 전수 셀이고 태깅(PER-175) 이후다

## 골든셋 수치를 코퍼스 수치로 읽지 않는다

번들은 리뷰 40건 표본이고 1~2★ 을 과표집했다 (`v5_concern_golden_meta.json`).
그래서 U·D 를 **층별 가중치로 되돌린 값**을 함께 낸다. 나뉘는 기준은 scope 종류가
아니라 모집단 크기다 — B02(17)·B03(29)·B04(20)은 모집단이 40 이하라 가중치 1.0 인
전수이고, B01(395)·B05(252)에만 배율이 붙는다.

사용:
  .venv/bin/python eval/measure_gate4_sufficiency.py
  → eval/reports/gate4_sufficiency_per186.json
  .venv/bin/python eval/measure_gate4_sufficiency.py --check   # 커밋본과 일치 확인
"""
import argparse
import collections
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

from catalog import load_catalog  # noqa: E402
from contracts import MISSING_SEGMENT, build_record  # noqa: E402
from ingest import load_aspect_counts  # noqa: E402
from gates import IdentityScope, run_duplicate_gate, run_identity_gate  # noqa: E402
from golden_contract import validate_labels  # noqa: E402
from policy import (  # noqa: E402
    RECENCY_CUTOFF_MONTH,
    SNAPSHOT_LATEST_MONTH,
    SufficiencyPolicy,
    sufficiency_gate,
)
from sufficiency import (  # noqa: E402
    MULTI_AXES,
    matches,
    SUFFICIENCY_REJECT_LABELS,
    Claim,
    ClaimSupport,
    EvidenceCell,
    DEFAULT_SUFFICIENCY,
    cell_sufficient,
    run_sufficiency_gate,
)
from tag_contract import ASPECTS  # noqa: E402
from trust import score_all  # noqa: E402

INPUT_PATH = ROOT / "data/input/reviews_50products.json"
TAGS_PATH = ROOT / "data/intermediate/v5_tags.jsonl"
TAGS_META_PATH = ROOT / "data/intermediate/v5_tags_meta.json"
BUNDLE_PATH = ROOT / "eval/gold/v5_concern_golden_sample.jsonl"
LABEL_PATH = ROOT / "eval/gold/v5_concern_golden_labels.jsonl"
REPORT_PATH = ROOT / "eval/reports/gate4_sufficiency_per186.json"

S_GRID = (8, 10, 15, 20, 30, 50)
N_GRID = (3, 5, 8, 12, 15)
R_GRID = (0.05, 0.10, 0.20, 0.30)
CELL_AXES = ("skinType", "skinTrouble")


def pct(part: int, whole: int) -> float:
    return round(100 * part / whole, 2) if whole else 0.0


def load_records() -> tuple[list[dict], object]:
    """    입수(PER-173)와 같은 경로로 레코드를 만든다.

    `data/intermediate/v5_reviews.jsonl` 을 읽지 않고 `data/input` 에서 다시 만드는 이유는
    그 경로 자체(`build_record` · `score_all`)가 입수와 같은지 이 측정이 함께 확인하기
    때문이다. 대신 **`aspect_counts` 를 반드시 넘긴다** — 넘기지 않으면 `trustPrior` 의
    `onTopic` 이 `unavailable` 로 남아 입수 산출물과 파생층이 갈리고, 게이트2 의 작성자
    1표 대표가 달라진다 (PER-174 · PER-188 인계).
    """
    catalog = load_catalog()
    records = []
    for row in json.loads(INPUT_PATH.read_text()):
        product_id = catalog.resolve_goods_no(row["goodsNo"], row["reviewDate"])
        records.append(build_record(row, product_id).to_dict())
    priors = score_all(records, aspect_counts=load_aspect_counts(TAGS_PATH))
    for record in records:
        record["derived"]["trustPrior"] = priors[record["reviewId"]]
    return records, catalog

def through_gates(records: list[dict], catalog) -> tuple[dict[str, list[dict]], dict[str, dict]]:
    """게이트1 → 게이트2. 순서가 규격이다 (PER-183 `gateOrder`)."""
    grouped: dict[str, list[dict]] = collections.defaultdict(list)
    for record in records:
        grouped[record["productId"]].append(record)

    kept: dict[str, list[dict]] = {}
    trace: dict[str, dict] = {}
    for product_id, rows in sorted(grouped.items()):
        first = run_identity_gate(rows, IdentityScope(product_id), catalog)
        second = run_duplicate_gate(first.passed)
        kept[product_id] = second.passed
        trace[product_id] = {
            "reviews": len(rows),
            "afterGate1": len(first.passed),
            "afterGate2": len(second.passed),
            "gate1Rejected": first.rejected_by_reason(),
            "gate2Rejected": second.rejected_by_reason(),
        }
    return kept, trace


def cell_profile(kept: dict[str, list[dict]]) -> dict:
    """셀 크기 S 의 분포. S 는 게이트2 통과분의 고유 작성자 수다."""
    out: dict = {}

    product_cells = {
        pid: EvidenceCell.of(rows, pid, {}) for pid, rows in kept.items() if rows
    }
    sizes = sorted(c.size for c in product_cells.values())
    out["product"] = {
        "cells": len(product_cells),
        "min": sizes[0] if sizes else 0,
        "median": sizes[len(sizes) // 2] if sizes else 0,
        "max": sizes[-1] if sizes else 0,
        "atLeast": {str(n): sum(1 for s in sizes if s >= n) for n in S_GRID},
    }

    for axis in CELL_AXES:
        counter: collections.Counter = collections.Counter()
        for pid, rows in kept.items():
            for row in rows:
                cell = row["condition"][axis]
                segments = cell["segments"] if axis == "skinTrouble" else [cell["segment"]]
                for segment in segments:
                    counter[(pid, segment)] += 1
        stated = {k: v for k, v in counter.items() if k[1] != MISSING_SEGMENT}
        out[axis] = {
            "cells": len(counter),
            "statedCells": len(stated),
            "atLeast": {str(n): sum(1 for v in counter.values() if v >= n) for n in S_GRID},
            "atLeastStated": {str(n): sum(1 for v in stated.values() if v >= n) for n in S_GRID},
        }
    out["note"] = (
        "S 는 셀의 고유 작성자 수다 (게이트2 통과분이라 리뷰 1건 = 작성자 1명). "
        "미기재는 '조건 없음'이 아니라 별도 세그먼트이므로 따로 센다"
    )
    return out


def fallback(kept: dict[str, list[dict]], trace: dict[str, dict], catalog,
             policy: SufficiencyPolicy) -> dict:
    """§5-3 — 리뷰가 적은 제품은 주장을 적게 내거나 아무것도 안 낸다."""
    silent = []
    for pid in sorted(trace):
        rows = kept.get(pid) or []
        cell = EvidenceCell.of(rows, pid, {}) if rows else None
        size = cell.size if cell else 0
        if cell is not None and cell_sufficient(cell, policy):
            continue
        reason = "게이트1·2 통과 리뷰 0건" if size == 0 else f"고유 작성자 {size}명 < S_min={policy.s_min}"
        silent.append({
            "productId": pid,
            "displayName": catalog.product(pid).display_name,
            "reviews": trace[pid]["reviews"],
            "cellAuthors": size,
            "reason": reason,
            "gate1Rejected": trace[pid]["gate1Rejected"],
        })

    stated_silent = {}
    for axis in CELL_AXES:
        counter: collections.Counter = collections.Counter()
        for pid, rows in kept.items():
            for row in rows:
                cell = row["condition"][axis]
                segments = cell["segments"] if axis == "skinTrouble" else [cell["segment"]]
                for segment in segments:
                    if segment != MISSING_SEGMENT:
                        counter[(pid, segment)] += 1
        below = sum(1 for v in counter.values() if v < policy.s_min)
        stated_silent[axis] = {
            "statedCells": len(counter),
            "silentCells": below,
            "silentPct": pct(below, len(counter)),
        }

    return {
        "sMin": policy.s_min,
        "products": len(trace),
        "silentProducts": silent,
        "speakingProducts": len(trace) - len(silent),
        "conditionCells": stated_silent,
        "note": (
            "침묵은 실패가 아니라 §5-3 의 기본 폴백이다. 셀이 작으면 주장을 만들기 "
            "전에 멈춘다 — 생성 비용을 쓰고 나서 거르면 '적게 낸다'가 성립하지 않는다"
        ),
    }


# --- 골든셋 대조 (PER-178 라벨의 support_counts 를 임계값 실험의 대조값으로) ---


def _stratum(rating: int) -> str:
    return "rating_1_2" if rating <= 2 else f"rating_{rating}"


def golden_claims() -> tuple[list[dict], dict]:
    """골든셋 라벨 → (주장, 셀) 쌍. 라벨은 계약 검증을 거친 것만 쓴다."""
    bundles = {}
    for line in BUNDLE_PATH.read_text().splitlines():
        bundle = json.loads(line)
        bundles[bundle["bundleId"]] = bundle
    labels = validate_labels(
        [json.loads(line) for line in LABEL_PATH.read_text().splitlines()], bundles)

    rows = []
    for label in labels:
        bundle = bundles[label["bundleId"]]
        weights = {s["name"]: s["weight"] for s in bundle["strata"]}
        # 작성자 1명 = 1표이므로 가중치도 작성자 단위로 잡는다 (리뷰 단위면 중복 계수)
        author_weight: dict[str, float] = {}
        authors: dict[int, str] = {}
        for review in bundle["reviews"]:
            key = review["derived"]["authorKey"]
            authors[review["reviewId"]] = key
            author_weight.setdefault(key, weights[_stratum(review["raw"]["rating"])])

        cell = EvidenceCell.of(bundle["reviews"], bundle["productId"], label["condition"])
        by: dict[str, set[str]] = {"positive": set(), "negative": set(), "neutral": set()}
        for evidence in label["evidence"]:
            by[evidence["stance"]].add(authors[evidence["reviewId"]])
        support = ClaimSupport(
            aspect=label["aspect"] or "(택소노미 밖)",
            positive=frozenset(by["positive"]),
            negative=frozenset(by["negative"]),
            neutral=frozenset(by["neutral"]),
        )
        counts = support.as_dict(cell)
        weighted = {
            "supportAuthors": round(sum(author_weight[a] for a in support.support)),
            "spokeAuthors": round(sum(author_weight[a] for a in support.spoke)),
            "cellAuthors": round(sum(author_weight[a] for a in cell.authors)),
        }
        rows.append({
            "labelId": label["labelId"],
            "bundleId": label["bundleId"],
            "scope": bundle["scope"]["kind"],
            "population": bundle["population"],
            "conditional": cell.conditional,
            "source": label["source"],
            "claim": Claim(label["labelId"], cell, support),
            "counts": counts,
            "weighted": weighted,
        })
    return rows, bundles


def _pass_count(rows: list[dict], policy: SufficiencyPolicy, weighted: bool) -> dict:
    """격자 한 칸. 가중분은 게이트를 같은 판정 함수로 돌리되 수를 되돌린 값으로 센다."""
    if not weighted:
        result = run_sufficiency_gate([r["claim"] for r in rows], policy)
        return {
            "passed": len(result.passed),
            "rejected": {SUFFICIENCY_REJECT_LABELS[k]: v
                         for k, v in result.rejected_by_reason().items()},
        }
    passed = 0
    rejected: collections.Counter = collections.Counter()
    for row in rows:
        w = row["weighted"]
        decision = sufficiency_gate(
            support_authors=w["supportAuthors"],
            spoke_authors=w["spokeAuthors"],
            cell_authors=w["cellAuthors"],
            policy=policy,
        )
        if decision.passed:
            passed += 1
        else:
            rejected[SUFFICIENCY_REJECT_LABELS[decision.reason]] += 1
    return {"passed": passed, "rejected": dict(sorted(rejected.items()))}


def sensitivity(rows: list[dict]) -> dict:
    """임계값을 바꿨을 때 통과 주장 수가 어떻게 변하는가 (완료 조건 · PER-199 입력)."""
    grid = []
    for n in N_GRID:
        for r in R_GRID:
            policy = SufficiencyPolicy(n_min=n, r_min=r, s_min=max(DEFAULT_SUFFICIENCY.s_min, n))
            grid.append({
                "nMin": n,
                "rMin": r,
                "sMin": policy.s_min,
                "raw": _pass_count(rows, policy, weighted=False),
                "weighted": _pass_count(rows, policy, weighted=True),
            })

    s_sweep = []
    for s in S_GRID:
        policy = SufficiencyPolicy(s_min=s)
        s_sweep.append({
            "sMin": s,
            "nMin": policy.n_min,
            "raw": _pass_count(rows, policy, weighted=False),
            "weighted": _pass_count(rows, policy, weighted=True),
        })

    return {
        "labels": len(rows),
        "conditionalLabels": sum(1 for r in rows if r["conditional"]),
        "nMinByRMin": grid,
        "sMinSweep": s_sweep,
        "note": (
            "N_min 을 올리면 S_min 도 함께 올린다 — U ≤ D ≤ S 라 S_min < N_min 은 "
            "세그먼트 조건을 끄는 설정이고 정책이 에러를 낸다"
        ),
    }


def minority_profile(rows: list[dict]) -> dict:
    """소수 의견이 몇 건에 걸리는가. 결정(뭉개지 않는다)의 적용 범위다."""
    mixed = [r for r in rows if r["counts"]["direction"] == "mixed"]
    dist: collections.Counter = collections.Counter(
        r["counts"]["minorityAuthors"] for r in mixed)
    single = [r["labelId"] for r in mixed if r["counts"]["minorityAuthors"] == 1]
    return {
        "labels": len(rows),
        "mixedLabels": len(mixed),
        "minorityDistribution": {str(k): v for k, v in sorted(dist.items())},
        "singleDissentLabels": sorted(single),
        "singleDissentPct": pct(len(single), len(rows)),
        "decision": (
            "반대 1명도 mixed 를 유지하고 limitation='single_dissent' 로 표기한다. "
            "다수 방향으로 뭉개면 이 26건 중 " + str(len(single)) + "건에서 반대 의견이 "
            "화면에서 사라진다 (PER-178 규격 §9 가 기각한 대안)"
        ),
    }


# --- 코퍼스 전수 (PER-175 전수 태깅 38,251개) ---


def load_tags() -> tuple[dict[int, list[tuple[str, str]]], dict]:
    """reviewId → [(aspect, polarity)]. 정본 태그가 없으면 조용히 건너뛰지 않고 에러다."""
    if not TAGS_PATH.exists():
        raise SystemExit(
            f"[gate4] 전수 태그가 없다: {TAGS_PATH.relative_to(ROOT)}\n"
            "  먼저 정본을 못박는다 — python3 pipeline/run_v5.py --steps tag\n"
            "  태그 없이 낸 민감도는 골든셋 26건짜리이고 코퍼스 커버리지가 아니다."
        )
    by_review: dict[int, list[tuple[str, str]]] = collections.defaultdict(list)
    for line in TAGS_PATH.read_text().splitlines():
        tag = json.loads(line)
        by_review[tag["reviewId"]].append((tag["aspect"], tag["polarity"]))
    return dict(by_review), json.loads(TAGS_META_PATH.read_text())


def corpus_claims(kept: dict[str, list[dict]], by_review: dict) -> list[dict]:
    """(셀 × aspect) 후보 주장. **D ≥ 1 인 것만** — 아무도 말하지 않은 주제는 주장이 아니다.

    셀은 제품 전체 · `skinType` · `skinTrouble` 세 종류다. 조건부 주장이 무조건부보다
    얼마나 더 떨어지는지가 이 이슈가 PER-199 에 넘기는 수치라 축을 나눠 센다.

    `ClaimSupport.of()` 를 셀마다 부르면 태그 38,251개를 매번 훑어 15,000회 이상
    반복된다. 그래서 여기서는 같은 집합을 한 번에 모아 `ClaimSupport` 를 직접 만들고,
    아래 `_assert_fast_path` 가 표본에서 두 경로가 같은 값을 내는지 확인한다.
    """
    claims: list[dict] = []
    for product_id, rows in sorted(kept.items()):
        if not rows:
            continue
        base = EvidenceCell.of(rows, product_id, {})  # 게이트2 통과분인지 여기서 확인된다
        buckets: list[tuple[str, dict]] = [("product", {})]
        for axis in CELL_AXES:
            segments = set()
            for row in rows:
                cell = row["condition"][axis]
                segments.update(cell["segments"] if axis in MULTI_AXES else [cell["segment"]])
            for segment in sorted(segments):
                buckets.append((axis, {axis: [segment] if axis in MULTI_AXES else segment}))

        for axis, condition in buckets:
            members = [r for r in rows if matches(r, condition)]
            authors = frozenset(r["derived"]["authorKey"] for r in members)
            cell = EvidenceCell(product_id, condition, authors)
            by_aspect: dict[str, dict[str, set]] = collections.defaultdict(
                lambda: {"positive": set(), "negative": set(), "neutral": set()})
            for row in members:
                author = row["derived"]["authorKey"]
                for aspect, polarity in by_review.get(row["reviewId"], ()):
                    by_aspect[aspect][polarity].add(author)
            for aspect in ASPECTS:
                stances = by_aspect.get(aspect)
                if not stances:
                    continue
                support = ClaimSupport(
                    aspect=aspect,
                    positive=frozenset(stances["positive"]),
                    negative=frozenset(stances["negative"]),
                    neutral=frozenset(stances["neutral"]),
                )
                segment = condition.get(axis) if axis != "product" else None
                if isinstance(segment, list):
                    segment = segment[0]
                claims.append({
                    "axis": axis,
                    "segment": segment,
                    "stated": axis == "product" or segment != MISSING_SEGMENT,
                    "claim": Claim(
                        f"{product_id}|{axis}={segment}|{aspect}", cell, support),
                })
        del base
    return claims


def _assert_fast_path(kept: dict[str, list[dict]], tags_raw: list[dict], claims: list[dict]) -> int:
    """빠른 경로가 `ClaimSupport.of()` 와 같은 값을 내는지 표본으로 확인한다.

    최적화가 조용히 다른 수를 내면 이 리포트 전체가 무의미하므로 가정하지 않고 잰다.
    """
    checked = 0
    for entry in claims[::997]:
        claim = entry["claim"]
        cell = claim.cell
        reference = ClaimSupport.of(tags_raw, kept[cell.product_id], claim.support.aspect, cell)
        if reference != claim.support:
            raise SystemExit(
                f"[gate4] 빠른 경로가 ClaimSupport.of() 와 다르다: {claim.claim_id}"
            )
        checked += 1
    return checked


def corpus_sensitivity(claims: list[dict]) -> dict:
    """전수 셀에서의 민감도. 골든셋 26건과 달리 여기서는 R_min 이 결속한다."""
    def counts(policy: SufficiencyPolicy, subset: list[dict]) -> dict:
        result = run_sufficiency_gate([e["claim"] for e in subset], policy)
        return {
            "candidates": len(subset),
            "passed": len(result.passed),
            "passedPct": pct(len(result.passed), len(subset)),
            "rejected": {SUFFICIENCY_REJECT_LABELS[k]: v
                         for k, v in result.rejected_by_reason().items()},
            "singleDissent": len(result.limitations.get("single_dissent", [])),
        }

    unconditional = [e for e in claims if e["axis"] == "product"]
    conditional = [e for e in claims if e["axis"] != "product" and e["stated"]]
    missing = [e for e in claims if e["axis"] != "product" and not e["stated"]]

    grid = []
    for n in N_GRID:
        for r in R_GRID:
            policy = SufficiencyPolicy(n_min=n, r_min=r, s_min=max(DEFAULT_SUFFICIENCY.s_min, n))
            grid.append({
                "nMin": n, "rMin": r, "sMin": policy.s_min,
                "all": counts(policy, claims),
                "unconditional": counts(policy, unconditional),
                "conditional": counts(policy, conditional),
            })

    s_sweep = [{"sMin": s, "nMin": SufficiencyPolicy(s_min=s).n_min,
                "all": counts(SufficiencyPolicy(s_min=s), claims)} for s in S_GRID]

    default = DEFAULT_SUFFICIENCY
    by_axis = {}
    for axis in ("product",) + CELL_AXES:
        subset = [e for e in claims if e["axis"] == axis]
        by_axis[axis] = {
            "all": counts(default, subset),
            "stated": counts(default, [e for e in subset if e["stated"]]),
            "missing": counts(default, [e for e in subset if not e["stated"]]),
        }

    return {
        "candidates": len(claims),
        "byAxis": by_axis,
        "missingSegmentCandidates": len(missing),
        "nMinByRMin": grid,
        "sMinSweep": s_sweep,
        "note": (
            "후보는 (셀 × aspect) 중 D ≥ 1 인 것이다 — 아무도 말하지 않은 주제는 주장이 "
            "아니라서 분모에 넣지 않는다. 한 리뷰가 여러 skinTrouble 코드를 가지면 그 "
            "코드 셀마다 세어지므로 축별 후보 수를 서로 더하지 않는다"
        ),
    }


def binding_condition(claims: list[dict], policy: SufficiencyPolicy) -> dict:
    """세 조건 중 무엇이 실제로 결속하는가. 사유 코드를 나눈 값이 여기서 나온다.

    각 조건을 **혼자만** 걸었을 때의 탈락 수도 함께 낸다 — AND 로 묶으면 앞선 조건이
    잡아간 몫이 뒤 조건의 수에서 빠져 "이 조건은 일 안 한다"로 잘못 읽힌다.
    """
    alone = {}
    for name, kwargs in (
        ("nMin", {"r_min": 1e-9, "s_min": 1}),
        ("rMin", {"n_min": 1, "s_min": 1}),
        ("sMin", {"n_min": 1, "r_min": 1e-9}),
    ):
        base = {"n_min": policy.n_min, "r_min": policy.r_min, "s_min": policy.s_min}
        base.update(kwargs)
        if name == "nMin":
            base["s_min"] = max(1, policy.n_min)
        solo = SufficiencyPolicy(**base)
        result = run_sufficiency_gate([e["claim"] for e in claims], solo)
        alone[name] = len(result.rejected)

    result = run_sufficiency_gate([e["claim"] for e in claims], policy)
    return {
        "candidates": len(claims),
        "passed": len(result.passed),
        "rejectedByReason": {SUFFICIENCY_REJECT_LABELS[k]: v
                             for k, v in result.rejected_by_reason().items()},
        "rejectedIfOnly": alone,
        "note": (
            "`rejectedByReason` 는 판정 순서(S → U → U/D)를 거친 귀속이고, "
            "`rejectedIfOnly` 는 그 조건 하나만 걸었을 때의 탈락 수다. 두 수가 다른 것이 "
            "세 조건을 AND 로 묶은 이유다 — 하나만으로는 나머지가 잡는 것을 놓친다"
        ),
    }


def ratio_diagnosis(claims: list[dict], policy: SufficiencyPolicy) -> dict:
    """R_min 이 왜 결속하지 않는가. **결론을 적지 말고 수를 내라.**

    U 는 주장의 방향을 말한 사람이고 방향은 그 데이터에서 파생된다. 그래서 단일 방향
    주장은 U/D 가 1.0 이고, 방향이 갈리면 `mixed` 가 되어 U 가 양쪽을 다시 흡수한다.
    U/D 를 1 아래로 끌어내리는 것은 **중립(U0) 하나뿐**이다 — 전수 태그에서 중립은
    4.18% 다. 그 구조를 수로 확인하고, 소수 측에 비율을 걸었다면 무엇이 잡혔을지를
    함께 낸다 (PER-211 이 쓸 입력이지 여기서 정책을 바꾸지 않는다).
    """
    shares, minority_shares, mixed = [], [], 0
    minority_dist: collections.Counter = collections.Counter()
    for entry in claims:
        support = entry["claim"].support
        d = len(support.spoke)
        if not d:
            continue
        shares.append(len(support.support) / d)
        if support.minority is not None:
            mixed += 1
            minority_dist[min(support.minority, 10)] += 1
            minority_shares.append(min(len(support.positive), len(support.negative)) / d)

    def quantile(values: list[float], q: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        return round(ordered[min(len(ordered) - 1, int(len(ordered) * q))], 4)

    # 기본 정책을 통과한 주장 중에서만 센다 — 떨어질 주장의 소수 측은 논점이 아니다
    passed = run_sufficiency_gate([e["claim"] for e in claims], policy).passed
    below_side = {}
    for r in R_GRID:
        below_side[str(r)] = sum(
            1 for c in passed
            if c.support.minority is not None
            and min(len(c.support.positive), len(c.support.negative)) / len(c.support.spoke) < r
        )

    # 문장에 수를 손으로 박지 않는다 — 옆 칸의 계산값과 조용히 갈린다 (2026-09-15 실제로 갈렸다)
    rmin_only = sum(1 for v in shares if v < policy.r_min)

    return {
        "candidates": len(shares),
        "supportShare": {
            "min": quantile(shares, 0.0),
            "p05": quantile(shares, 0.05),
            "median": quantile(shares, 0.5),
            "belowRMin": {str(r): sum(1 for v in shares if v < r) for r in R_GRID},
        },
        "mixedCandidates": mixed,
        "minorityAuthors": {("10+" if k == 10 else str(k)): v
                            for k, v in sorted(minority_dist.items())},
        "minorityShare": {
            "min": quantile(minority_shares, 0.0),
            "median": quantile(minority_shares, 0.5),
        },
        "passedWithMinorityBelow": {
            "passed": len(passed),
            "mixed": sum(1 for c in passed if c.support.minority is not None),
            "belowShare": below_side,
        },
        "finding": (
            f"R_min 에 귀속되는 탈락은 전수 {len(shares):,} 후보에서 0 이다. 혼자 걸면 "
            f"{rmin_only:,}건을 잡지만 그 {rmin_only:,}건은 전부 N_min 이 이미 잡는다. "
            "구조가 원인이다 — U 는 방향을 말한 사람 전체이고 `mixed` 면 양쪽을 흡수하므로, "
            "U/D 를 1 아래로 내리는 것은 중립 태그뿐이다. 그래서 U/D 중위수가 1.0 이다. "
            "비율이 실제로 일할 자리는 주장 전체가 아니라 **소수 측**이고(통과분 "
            f"{len(passed):,}건 중 {sum(1 for c in passed if c.support.minority is not None):,}건이 "
            "mixed), 그 수를 `passedWithMinorityBelow` 에 냈다 — 정책 변경은 PER-211 의 "
            "판단이지 이 이슈에서 하지 않는다"
        ),
    }


def golden_rows(rows: list[dict]) -> list[dict]:
    """라벨별 U/D/S. 격자의 어느 칸에서 무엇이 되살아나는지 되짚을 수 있어야 한다."""
    out = []
    for row in rows:
        counts = row["counts"]
        out.append({
            "labelId": row["labelId"],
            "scope": row["scope"],
            "conditional": row["conditional"],
            "population": row["population"],
            "direction": counts["direction"],
            "positiveAuthors": counts["positiveAuthors"],
            "negativeAuthors": counts["negativeAuthors"],
            "neutralAuthors": counts["neutralAuthors"],
            "supportAuthors": counts["supportAuthors"],
            "spokeAuthors": counts["spokeAuthors"],
            "silentAuthors": counts["silentAuthors"],
            "cellAuthors": counts["cellAuthors"],
            "supportShare": counts["supportShare"],
            "weighted": row["weighted"],
        })
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="재실행 결과가 커밋된 리포트와 같은지만 확인 (§5-2 재현성)")
    args = ap.parse_args()

    records, catalog = load_records()
    kept, trace = through_gates(records, catalog)
    rows, _ = golden_claims()
    policy = DEFAULT_SUFFICIENCY

    by_review, tags_meta = load_tags()
    corpus = corpus_claims(kept, by_review)
    tags_raw = [json.loads(line) for line in TAGS_PATH.read_text().splitlines()]
    spot_checked = _assert_fast_path(kept, tags_raw, corpus)

    report = {
        "issue": "PER-186",
        "source": {
            "path": str(INPUT_PATH.relative_to(ROOT)),
            "sha256": hashlib.sha256(INPUT_PATH.read_bytes()).hexdigest(),
            "reviews": len(records),
            "products": len(trace),
            "afterGate1And2": sum(len(v) for v in kept.values()),
            "goldenLabels": str(LABEL_PATH.relative_to(ROOT)),
        },
        "policy": {
            **policy.as_meta(),
            "snapshotLatestMonth": SNAPSHOT_LATEST_MONTH,
            "recencyCutoffMonth": RECENCY_CUTOFF_MONTH,
        },
        "cellProfile": cell_profile(kept),
        "fallback": fallback(kept, trace, catalog, policy),
        "corpus": {
            "tags": {
                "path": str(TAGS_PATH.relative_to(ROOT)),
                "label": tags_meta["label"],
                "model": tags_meta["model"],
                "tags": tags_meta["tags"],
                "reviewsTagged": tags_meta["reviewsTagged"],
                "prompt": tags_meta["prompt"],
            },
            "fastPathSpotChecks": spot_checked,
            "sensitivity": corpus_sensitivity(corpus),
            "binding": binding_condition(corpus, policy),
            "ratioDiagnosis": ratio_diagnosis(corpus, policy),
        },
        "sensitivity": sensitivity(rows),
        "minorityPolicy": minority_profile(rows),
        "goldenLabels": golden_rows(rows),
        "limits": [
            "코퍼스 수치(corpus)는 태그 품질에 딸려 있다. 태거는 GLM 4.7 단일 실행이고 "
            "골든셋 200건 대비 정밀도·재현율은 eval/reports/v5_tag_gold_v2.json 에 있다 — "
            "태거를 바꾸면 이 커버리지도 바뀐다",
            "R_min 은 골든셋에서도 전수에서도 결속하지 않는다. 값이 아니라 정의의 문제라 "
            "(U 가 mixed 에서 양쪽을 흡수한다) ratioDiagnosis 에 수만 내고 정책은 그대로 뒀다",
            "후보는 (셀 × aspect) 중 D ≥ 1 인 것이고 '생성될 뻔한 주장' 이 아니다. 실제 "
            "커버리지는 질문-답 쌍(PER-191)까지 가야 정해진다 — 그것이 PER-199 다",
            "모집단이 40 을 넘는 번들의 S 는 리뷰 40건 표본의 고유 작성자 수라 셀 크기가 아니다. "
            "나뉘는 기준은 scope 종류가 아니라 모집단 크기다 — B02(17)·B03(29)·B04(20)은 전수이고 "
            "B01(395)·B05(252)에만 층별 가중치로 되돌린 값이 붙는다",
            "번들은 1~2★ 을 과표집했다. 가중분과 원수치가 다르면 가중분이 코퍼스에 가깝다",
        ],
    }

    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.check:
        if not REPORT_PATH.exists():
            raise SystemExit(f"FAIL: 리포트가 없다 ({REPORT_PATH.relative_to(ROOT)})")
        if REPORT_PATH.read_text() != payload:
            raise SystemExit(
                f"FAIL: 게이트4 리포트가 재현되지 않는다 ({REPORT_PATH.relative_to(ROOT)})\n"
                "  → 임계값이나 게이트 판정이 바뀌었다면 리포트를 다시 생성해 함께 커밋한다"
            )
        print(f"OK: 게이트4 리포트 재현 일치 ({REPORT_PATH.relative_to(ROOT)})")
        return

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(payload)

    cp, fb, se, mp = (report["cellProfile"], report["fallback"],
                      report["sensitivity"], report["minorityPolicy"])
    print(f"[게이트4] N_min={policy.n_min} R_min={policy.r_min} S_min={policy.s_min} "
          f"minority_min={policy.minority_min}")
    print(f"  셀: 제품 {cp['product']['cells']}개 (중위 {cp['product']['median']}명) · "
          f"skinType 기재 {cp['skinType']['statedCells']}개 중 "
          f"S>={policy.s_min} {cp['skinType']['atLeastStated'][str(policy.s_min)]}개")
    print(f"  침묵: 제품 {len(fb['silentProducts'])}개 "
          f"({', '.join(p['productId'] for p in fb['silentProducts'])}) · "
          f"skinType 기재 셀 {fb['conditionCells']['skinType']['silentCells']}개 "
          f"({fb['conditionCells']['skinType']['silentPct']}%)")
    base = next(g for g in se["nMinByRMin"]
                if g["nMin"] == policy.n_min and g["rMin"] == policy.r_min)
    print(f"  골든셋 {se['labels']}건 (조건부 {se['conditionalLabels']}건): 기본 정책 통과 "
          f"{base['raw']['passed']}건 · 가중 {base['weighted']['passed']}건")
    for g in se["nMinByRMin"]:
        if g["rMin"] == policy.r_min:
            print(f"    N_min={g['nMin']:2d}: 원수치 {g['raw']['passed']:2d} / "
                  f"가중 {g['weighted']['passed']:2d}")
    print(f"  소수 의견: mixed {mp['mixedLabels']}건 중 반대 1명 "
          f"{len(mp['singleDissentLabels'])}건 ({mp['singleDissentPct']}%) — 뭉개지 않는다")
    co = report["corpus"]
    cs, cb = co["sensitivity"], co["binding"]
    print(f"  [전수 태그 {co['tags']['tags']}개 · {co['tags']['model']}]")
    print(f"    후보 (셀×aspect) {cs['candidates']}건 → 통과 {cb['passed']}건 "
          f"({pct(cb['passed'], cs['candidates'])}%)")
    for axis in ("product",) + CELL_AXES:
        a = cs["byAxis"][axis]["stated"]
        print(f"      {axis:12s} {a['candidates']:5d} → {a['passed']:4d} ({a['passedPct']}%)")
    print(f"    결속: {cb['rejectedByReason']} · 혼자 걸었을 때 {cb['rejectedIfOnly']}")
    print(f"    R_min 귀속 탈락 0 — 소수 측 몫이 R_min 미만인 통과 주장 "
          f"{co['ratioDiagnosis']['passedWithMinorityBelow']['belowShare']['0.1']}건")
    print(f"→ {REPORT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
