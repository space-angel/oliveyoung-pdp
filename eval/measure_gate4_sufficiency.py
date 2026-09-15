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
그래서 U·D 를 **층별 가중치로 되돌린 값**을 함께 낸다. 셀 번들(B02~B04)은 모집단이
40 이하라 가중치가 1.0 인 전수이고, 제품 번들(B01·B05)만 배율이 붙는다.

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
from gates import IdentityScope, run_duplicate_gate, run_identity_gate  # noqa: E402
from golden_contract import validate_labels  # noqa: E402
from policy import (  # noqa: E402
    RECENCY_CUTOFF_MONTH,
    SNAPSHOT_LATEST_MONTH,
    SufficiencyPolicy,
    sufficiency_gate,
)
from sufficiency import (  # noqa: E402
    SUFFICIENCY_REJECT_LABELS,
    Claim,
    ClaimSupport,
    EvidenceCell,
    DEFAULT_SUFFICIENCY,
    cell_sufficient,
    run_sufficiency_gate,
)
from trust import score_all  # noqa: E402

INPUT_PATH = ROOT / "data/input/reviews_50products.json"
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
    """입수(PER-173)와 같은 경로로 레코드를 만든다 — 중간 산출물이 gitignore 라서."""
    catalog = load_catalog()
    records = []
    for row in json.loads(INPUT_PATH.read_text()):
        product_id = catalog.resolve_goods_no(row["goodsNo"], row["reviewDate"])
        records.append(build_record(row, product_id).to_dict())
    priors = score_all(records)
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
        "sensitivity": sensitivity(rows),
        "minorityPolicy": minority_profile(rows),
        "goldenLabels": golden_rows(rows),
        "limits": [
            "전수 태깅(PER-175)이 없어 코퍼스 단위 D 를 세지 못한다. 이 리포트의 U·D 는 "
            "사람이 만든 골든셋 라벨 26건에서 온 것이고, 코퍼스 커버리지는 PER-199·PER-211 이 잰다",
            "골든셋에서는 R_min 이 결속하지 않는다 — 전건 U/D ≥ 0.6 이다. 라벨러가 주장의 "
            "방향을 지지하는 근거를 인용하기 때문이고, 비율 조건이 결속하는 구간은 D 가 큰 "
            "전수 셀이다 (예: 언급 100명 중 8명)",
            "번들의 S 는 리뷰 40건 표본의 고유 작성자 수라 셀 크기가 아니다. 모집단이 40 이하인 "
            "셀 번들(B02~B04)만 전수이고, 제품 번들(B01·B05)의 S 는 층별 가중치로 되돌린 값을 함께 낸다",
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
    print(f"→ {REPORT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
