"""
PER-188 근거 측정 — 통합 `rejected[]` 원장과 골든셋 역추적.

확인하는 것은 네 가지다.

  1) **사유 어휘가 한 곳에 있는가** — 레지스트리 전문을 리포트에 싣는다. 사유가 코드
     여기저기 흩어져 있으면 리포트만 보고는 어휘를 확인할 수 없다
  2) **입력이 통과와 탈락으로 남김없이 설명되는가** — 리뷰 25,000건이 게이트별 통과분과
     원장 행으로 정확히 나뉜다. 안 맞으면 어딘가에서 조용히 사라진 것이다
  3) **골든셋이 잡은 주장 중 파이프라인이 못 낸 것을 되짚을 수 있는가** — 이 이슈의
     핵심이다. 라벨 하나하나를 게이트 1~4 에 통과시켜 보고, 못 냈으면 **어느 게이트의
     어느 사유에서 멈췄는지**를 낸다. v4 에서 손으로 하던 일
  4) **임계값을 바꾸면 무엇이 되살아나는가** — 탈락 행에 수치가 붙어 있으므로 리포트를
     다시 돌리지 않고 읽을 수 있어야 한다 (PER-199 커버리지 실험의 입력)

## 골든셋 26건을 커버리지로 읽지 않는다

라벨은 5개 번들(제품 4개)에서 나온 **파일럿 26건**이고 최종 100건이 아니다 (PER-180).
그리고 라벨은 번들 40건을 보고 만들었는데 역추적은 **전수 코퍼스**에 건다 — 분모가
다르므로 방향(`direction`)이 번들에서는 `negative` 인데 전수에서는 `mixed` 가 되는 일이
생긴다. 그건 오류가 아니라 표본 크기의 차이이고, 수를 숨기지 않고 따로 낸다.

## 입력 기반은 다른 measure 스크립트와 같다

`data/input/reviews_50products.json` 에서 레코드를 다시 만든다 — 입수 산출물
(`data/intermediate/v5_reviews.jsonl`)은 gitignore 라 클론 직후에 없고, 그래야
`eval/reports/gate1_identity_per182.json` · `gate4_sufficiency_per186.json` 과 같은
기반 위에서 수를 비교할 수 있다. **이 기반은 실제 파이프라인과 미세하게 다르다** —
`limits` 에 그 차이를 적었다.

사용:
  .venv/bin/python eval/measure_rejected_ledger.py
  → eval/reports/rejected_ledger_per188.json
  .venv/bin/python eval/measure_rejected_ledger.py --check   # 커밋본과 일치 확인
"""
import argparse
import collections
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

import reject_registry as registry  # noqa: E402
from catalog import load_catalog  # noqa: E402
from contracts import build_record  # noqa: E402
from golden_contract import validate_labels  # noqa: E402
from ledger import (  # noqa: E402
    OUTCOME_PRODUCED,
    OUTCOME_UNTRACEABLE,
    ClaimTarget,
    recall_summary,
    run_gates,
    trace_all,
)
from option_norm import OptionIndex  # noqa: E402
from policy import (  # noqa: E402
    DEFAULT_SUFFICIENCY,
    RECENCY_CUTOFF_MONTH,
    SNAPSHOT_LATEST_MONTH,
    SufficiencyPolicy,
)
from reject_registry import GATE_SUFFICIENCY, UNIT_CLAIM, UNIT_REVIEW  # noqa: E402
from trust import score_all  # noqa: E402

INPUT_PATH = ROOT / "data/input/reviews_50products.json"
TAGS_PATH = ROOT / "data/intermediate/v5_tags.jsonl"
TAGS_META_PATH = ROOT / "data/intermediate/v5_tags_meta.json"
BUNDLE_PATH = ROOT / "eval/gold/v5_concern_golden_sample.jsonl"
LABEL_PATH = ROOT / "eval/gold/v5_concern_golden_labels.jsonl"
GATE4_REPORT_PATH = ROOT / "eval/reports/gate4_sufficiency_per186.json"
REPORT_PATH = ROOT / "eval/reports/rejected_ledger_per188.json"

# 임계값을 내렸을 때 무엇이 되살아나는가 (PER-186 의 격자와 같은 값)
N_GRID = (3, 5, 8, 12, 15)


def pct(part: int, whole: int) -> float:
    return round(100 * part / whole, 2) if whole else 0.0


def load_records() -> tuple[list[dict], object]:
    """다른 measure 스크립트와 같은 경로로 레코드를 만든다 (모듈 문서 참조)."""
    catalog = load_catalog()
    records = []
    for row in json.loads(INPUT_PATH.read_text()):
        product_id = catalog.resolve_goods_no(row["goodsNo"], row["reviewDate"])
        records.append(build_record(row, product_id).to_dict())
    priors = score_all(records)
    for record in records:
        record["derived"]["trustPrior"] = priors[record["reviewId"]]
    return records, catalog


def load_tags() -> tuple[list[dict], dict[int, list[tuple[str, str]]], dict]:
    """전수 태그. 없으면 조용히 건너뛰지 않고 멈춘다 — 태그 없이는 게이트4 후보가 0이다."""
    if not TAGS_PATH.exists():
        raise SystemExit(
            f"[per188] 전수 태그가 없다: {TAGS_PATH.relative_to(ROOT)}\n"
            "  먼저 정본을 못박는다 — python3 pipeline/run_v5.py --steps tag"
        )
    tags = [json.loads(line) for line in TAGS_PATH.read_text().splitlines() if line.strip()]
    by_review: dict[int, list[tuple[str, str]]] = collections.defaultdict(list)
    for tag in tags:
        by_review[tag["reviewId"]].append((tag["aspect"], tag["polarity"]))
    return tags, dict(by_review), json.loads(TAGS_META_PATH.read_text())


# --- 1. 원장 ---


def ledger_profile(run, records: list[dict]) -> dict:
    """전수 원장. **입력이 통과와 탈락으로 남김없이 설명되는지**를 함께 확인한다."""
    ledger = run.ledger
    review_rows = ledger.of_unit(UNIT_REVIEW)
    claim_rows = ledger.of_unit(UNIT_CLAIM)

    kept = sum(len(v) for v in run.kept.values())
    if kept + len(review_rows) != len(records):
        # 조용히 두면 "탈락 N건"이 입력의 일부만 설명한다
        raise SystemExit(
            f"[per188] 리뷰 {len(records)}건이 통과 {kept} + 탈락 {len(review_rows)} 로 "
            "설명되지 않는다 — 어딘가에서 사유 없이 사라졌다"
        )
    if len(run.passed_claims) + len(claim_rows) != len(run.claims):
        raise SystemExit(
            f"[per188] 주장 후보 {len(run.claims)}건이 통과 {len(run.passed_claims)} + "
            f"탈락 {len(claim_rows)} 로 설명되지 않는다"
        )

    by_gate_reason = {
        gate: {
            registry.label_of(code): {
                "reason": code,
                "count": n,
                "pct": pct(n, len(records) if registry.reason(code).unit == UNIT_REVIEW
                           else len(run.claims)),
            }
            for code, n in reasons.items()
        }
        for gate, reasons in ledger.by_gate_reason().items()
    }
    return {
        "reviewsIn": len(records),
        "afterGate1": sum(t["afterGate1"] for t in run.trace.values()),
        "afterGate2": kept,
        "claimCandidates": len(run.claims),
        "claimsPassed": len(run.passed_claims),
        "rows": len(ledger.rows),
        "reviewRows": len(review_rows),
        "claimRows": len(claim_rows),
        "byGateReason": by_gate_reason,
        "limitations": run.limitations,
        "accounted": {
            "reviews": f"{kept} 통과 + {len(review_rows)} 탈락 = {len(records)}",
            "claims": (
                f"{len(run.passed_claims)} 통과 + {len(claim_rows)} 탈락 = {len(run.claims)}"),
        },
        "polarityRows": len([r for r in ledger.rows if r.gate == "polarity"]),
        "note": (
            "게이트3 행은 0이다 — 부정 근거는 틀린 근거가 아니다 (PER-185 §1). 리뷰 행과 "
            "주장 행은 단위가 달라 서로 더하지 않는다"
        ),
    }


def cross_check(run) -> dict:
    """커밋된 PER-186 리포트와 같은 수가 나오는가. **가정하지 않고 대조한다.**

    같은 입력 기반에서 원장이 게이트4 의 수를 그대로 재현하지 못하면, 이 이슈가 만든
    실행 경로가 기존 측정과 다른 파이프라인이라는 뜻이다.
    """
    committed = json.loads(GATE4_REPORT_PATH.read_text())["corpus"]["binding"]
    ours = {
        "candidates": len(run.claims),
        "passed": len(run.passed_claims),
        "rejectedByReason": {
            registry.label_of(code): n
            for code, n in run.ledger.by_gate_reason().get(GATE_SUFFICIENCY, {}).items()
        },
    }
    matches = (
        ours["candidates"] == committed["candidates"]
        and ours["passed"] == committed["passed"]
        and ours["rejectedByReason"] == committed["rejectedByReason"]
    )
    if not matches:
        raise SystemExit(
            "[per188] 원장이 커밋된 게이트4 리포트와 다른 수를 낸다\n"
            f"  원장  {ours}\n  PER-186 {committed}\n"
            "  → 게이트 판정이 바뀌었다면 양쪽 리포트를 함께 다시 낸다"
        )
    return {
        "against": str(GATE4_REPORT_PATH.relative_to(ROOT)),
        "ledger": ours,
        "committed": committed,
        "matches": matches,
        "note": (
            "같은 입력 기반에서 원장이 PER-186 의 커밋 수치를 그대로 낸다. 이 이슈는 "
            "판정을 바꾸지 않고 사유를 한곳에 모으고 역추적 경로를 뚫었을 뿐이다"
        ),
    }


# --- 2. 골든셋 역추적 ---


def golden_targets() -> tuple[list[ClaimTarget], list[dict], dict]:
    """골든셋 라벨 → 되짚을 대상. 계약 검증을 거친 라벨만 쓴다.

    조건축과 옵션을 나눠 싣는다 — 옵션 동일성은 게이트1 이 소유하므로 셀 조건이 아니라
    `IdentityScope` 로 내려간다 (PER-182).
    """
    bundles = {}
    for line in BUNDLE_PATH.read_text().splitlines():
        bundle = json.loads(line)
        bundles[bundle["bundleId"]] = bundle
    labels = validate_labels(
        [json.loads(line) for line in LABEL_PATH.read_text().splitlines()], bundles)

    targets = []
    for label in labels:
        condition = {
            axis: label["condition"][axis]
            for axis in ("skinType", "skinTrouble")
            if label["condition"].get(axis) is not None
        }
        targets.append(ClaimTarget(
            key=label["labelId"],
            product_id=label["productId"],
            aspect=label["aspect"],
            condition=condition,
            option_scope=label["condition"].get("option"),
            evidence_review_ids=tuple(e["reviewId"] for e in label["evidence"]),
            direction=label["direction"],
        ))
    return targets, labels, bundles


def traceback_rows(traces: list, labels: list[dict], bundles: dict) -> list[dict]:
    """라벨별 역추적 한 줄. **미스 하나하나가 어느 행에 걸리는지** 읽을 수 있어야 한다."""
    by_label = {label["labelId"]: label for label in labels}
    rows = []
    for trace in traces:
        label = by_label[trace.key]
        bundle = bundles[label["bundleId"]]
        row = trace.as_dict()
        rows.append({
            "labelId": trace.key,
            "bundleId": label["bundleId"],
            "productId": label["productId"],
            "aspect": label["aspect"],
            "condition": label["condition"],
            "bundlePopulation": bundle["population"],
            "source": label["source"],
            "outcome": row["outcome"],
            "gate": row["gate"],
            "reason": row["reason"],
            "label": row["label"],
            "detail": row["detail"],
            "claimId": row["claimId"],
            "goldenDirection": row["goldenDirection"],
            "pipelineDirection": row["pipelineDirection"],
            "directionMatch": row["directionMatch"],
            "support": row["metrics"],
            "evidenceReviews": row["evidenceReviews"],
            "evidenceLost": row["evidenceLost"],
            "evidence": row["evidence"],
            "limitations": row["limitations"],
        })
    return rows


def evidence_profile(traces: list) -> dict:
    """근거 리뷰가 어느 게이트에서 빠졌나. 통과한 주장의 근거도 빠질 수 있다."""
    fates: collections.Counter = collections.Counter()
    total = 0
    for trace in traces:
        for fate in trace.evidence:
            total += 1
            fates["통과" if fate.passed else f"{fate.gate}/{fate.label}"] += 1
    lost = [
        {"labelId": t.key, "lost": t.evidence_lost, "of": len(t.evidence),
         "outcome": t.outcome}
        for t in traces if t.evidence_lost
    ]
    return {
        "evidenceReviews": total,
        "byFate": dict(sorted(fates.items())),
        "labelsWithLostEvidence": lost,
        "note": (
            "골든셋 근거 인용이 가리키는 리뷰가 게이트에서 빠지면 그 claim 의 근거가 "
            "화면에서 줄어든다. 통과한 주장에서도 일어나므로 결과와 무관하게 센다"
        ),
    }


def direction_profile(traces: list) -> dict:
    """방향이 어긋난 통과 주장. **오류가 아니라 분모 차이일 수 있다.**

    골든셋 방향은 번들 40건에서, 파이프라인 방향은 전수 셀에서 계산된다. 소수 반대가
    전수에서 나타나면 `negative` → `mixed` 가 된다 (반대 1명도 mixed, PER-178 §9).
    판정하지 않고 쌍으로 센다.
    """
    produced = [t for t in traces if t.outcome == OUTCOME_PRODUCED]
    pairs: collections.Counter = collections.Counter(
        (t.golden_direction, t.pipeline_direction) for t in produced)
    mismatched = [t for t in produced if t.direction_match is False]

    def kind(trace) -> str:
        """두 종류를 섞지 않는다 — 벌어진 것과 뒤집힌 것은 뜻이 다르다.

        `widened_to_mixed` 는 전수에서 소수 반대가 나타난 것이라 표본 차이로 설명된다.
        `flipped` 는 번들과 전수의 방향이 **서로 반대**라 그 설명이 안 통한다.
        """
        if trace.pipeline_direction == "mixed":
            return "widened_to_mixed"
        return "flipped"

    kinds: collections.Counter = collections.Counter(kind(t) for t in mismatched)
    return {
        "produced": len(produced),
        "match": sum(1 for t in produced if t.direction_match),
        "mismatch": len(mismatched),
        "mismatchPct": pct(len(mismatched), len(produced)),
        "mismatchKind": dict(sorted(kinds.items())),
        "pairs": {f"{g}→{p}": n for (g, p), n in sorted(pairs.items())},
        "mismatchedLabels": [
            {"labelId": t.key, "golden": t.golden_direction,
             "pipeline": t.pipeline_direction,
             "kind": kind(t),
             "positiveAuthors": t.metrics["positiveAuthors"],
             "negativeAuthors": t.metrics["negativeAuthors"],
             "neutralAuthors": t.metrics["neutralAuthors"],
             "cellAuthors": t.metrics["cellAuthors"]}
            for t in mismatched
        ],
        "note": (
            "골든셋 방향은 번들 40건, 파이프라인 방향은 전수 셀에서 나온다. 전수에서 "
            "소수 반대가 나타나면 단일 방향이 mixed 가 된다 — 방향 정의는 양쪽이 같다 "
            "(golden_contract.derive_direction · ClaimSupport.direction). "
            "`widened_to_mixed` 는 그 표본 차이로 설명되지만 `flipped` 는 설명되지 "
            "않는다 — 태거 방향 오류(정답셋 6.5%)나 라벨 오류의 후보다"
        ),
    }


def revival(targets: list[ClaimTarget], records: list[dict], tags: list[dict],
            catalog, option_index: OptionIndex) -> dict:
    """N_min 을 바꾸면 어느 미스가 되살아나는가 (PER-199 입력).

    탈락 행에 수치가 붙어 있다는 것의 값어치가 여기서 나온다 — 임계값을 바꿨을 때
    되살아나는 주장을 리포트를 다시 읽는 것만으로 알 수 있어야 한다.
    """
    grid = []
    for n_min in N_GRID:
        policy = SufficiencyPolicy(
            n_min=n_min, r_min=DEFAULT_SUFFICIENCY.r_min,
            s_min=max(DEFAULT_SUFFICIENCY.s_min, n_min))
        traces = trace_all(targets, records, tags, catalog,
                           option_index=option_index, policy=policy)
        summary = recall_summary(traces)
        grid.append({
            "nMin": n_min,
            "sMin": policy.s_min,
            "produced": summary["produced"],
            "missed": summary["missed"],
            "traceable": summary["traceable"],
            "recall": summary["recall"],
            "missByGateReason": summary["missByGateReason"],
            "producedLabels": sorted(
                t.key for t in traces if t.outcome == OUTCOME_PRODUCED),
        })
    return {
        "nMinGrid": grid,
        "note": (
            "S_min 은 N_min 과 함께 올린다 — U ≤ D ≤ S 라 S_min < N_min 은 세그먼트 "
            "조건을 끄는 설정이고 정책이 에러를 낸다 (PER-186)"
        ),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="재실행 결과가 커밋된 리포트와 같은지만 확인 (§5-2 재현성)")
    args = ap.parse_args()

    records, catalog = load_records()
    tags, tags_by_review, tags_meta = load_tags()
    option_index = OptionIndex.from_records(records)

    run = run_gates(records, tags_by_review, catalog)
    targets, labels, bundles = golden_targets()
    traces = trace_all(targets, records, tags, catalog, option_index=option_index)
    summary = recall_summary(traces)

    report = {
        "issue": "PER-188",
        "source": {
            "path": str(INPUT_PATH.relative_to(ROOT)),
            "sha256": hashlib.sha256(INPUT_PATH.read_bytes()).hexdigest(),
            "reviews": len(records),
            "products": len(run.trace),
            "tags": str(TAGS_PATH.relative_to(ROOT)),
            "tagsSha256": hashlib.sha256(TAGS_PATH.read_bytes()).hexdigest(),
            "tagsLabel": tags_meta["label"],
            "tagsModel": tags_meta["model"],
            "goldenLabels": str(LABEL_PATH.relative_to(ROOT)),
            "goldenLabelsSha256": hashlib.sha256(LABEL_PATH.read_bytes()).hexdigest(),
            "goldenLabelCount": len(labels),
            "goldenBundles": sorted({label["bundleId"] for label in labels}),
            "note": (
                "이 리포트는 전수 태그 data/intermediate/v5_tags.jsonl 에 의존한다. "
                "그 파일은 gitignore 이므로 클론 직후에는 없고, verify.sh 의 --check 가 "
                "거기서 멈춘다 — 먼저 python3 pipeline/run_v5.py --steps tag 로 정본을 "
                "못박아야 한다. verify.sh 에 넣은 것은 게이트4"
                "(eval/measure_gate4_sufficiency.py)의 선례를 따른 것이고, 같은 의존을 "
                "가진 eval/measure_gate3_polarity.py 는 반대로 verify.sh 에서 빠져 있다. "
                "그 불일치는 정책 결정이라 이 이슈에서 건드리지 않았다"
            ),
        },
        "policy": {
            **DEFAULT_SUFFICIENCY.as_meta(),
            "snapshotLatestMonth": SNAPSHOT_LATEST_MONTH,
            "recencyCutoffMonth": RECENCY_CUTOFF_MONTH,
        },
        "registry": registry.as_dict(),
        "ledger": ledger_profile(run, records),
        "crossCheck": cross_check(run),
        "traceback": {
            **summary,
            "labels": traceback_rows(traces, labels, bundles),
        },
        "evidence": evidence_profile(traces),
        "direction": direction_profile(traces),
        "revival": revival(targets, records, tags, catalog, option_index),
        "limits": [
            "골든셋 라벨은 파일럿 26건(번들 5개 · 제품 4개)이고 최종 100건이 아니다 "
            "(PER-180). 재현율 수치를 코퍼스 커버리지로 읽지 않는다 — 실제 커버리지는 "
            "질문-답 쌍(PER-191)까지 가야 정해지고 그것이 PER-199 다",
            "라벨은 번들 40건을 보고 만들었는데 역추적은 전수 코퍼스에 건다. 분모가 달라 "
            "번들에서 단일 방향인 주장이 전수에서 mixed 가 될 수 있다 — direction 절에 "
            "쌍으로 냈고, 그건 오류가 아니라 표본 크기의 차이다",
            "aspect 가 null 인 라벨 4건은 (셀 × aspect) 키가 없어 되짚을 수 없다 "
            "(`untraceable`). 분모에서 빼되 수를 남겼다 — 택소노미 14종이 동결이라 "
            "(PER-175) 이 4건은 임계값을 바꿔도 되살아나지 않는다",
            "게이트2 의 `의미중복`(PER-184)은 미적용이다. 지금 원장의 중복 사유는 본문 "
            "완전일치와 동일작성자 두 축뿐이고, 의미가 같은 다른 문장은 독립 근거로 남아 "
            "있다. 레지스트리에 코드를 reserved 로 열어 뒀을 뿐 탈락은 0건이다",
            "v4 의 Golden Set 미스 2건은 이 원장으로 되짚지 못한다 — v4 골든셋 15문항을 "
            "v5 로 옮기지 않기로 했기 때문이다 (PER-178, "
            "eval/reports/v4_golden_migration_per178.json). 이 이슈가 자동화한 것은 "
            "그 2건의 사후 추적이 아니라 **앞으로의 미스 추적 경로**다",
            "입력 기반이 실제 파이프라인과 미세하게 다르다. 이 스크립트는 다른 measure "
            "스크립트와 같이 data/input 에서 레코드를 다시 만들고, 그 경로에는 태그가 "
            "없어 trustPrior 의 onTopic 신호가 unavailable 이다. 입수 산출물"
            "(v5_reviews.jsonl)로 돌리면 게이트2 대표가 달라져 후보 7,689 · 통과 2,420 "
            "이 된다 (python3 pipeline/run_v5.py --steps gates). 게이트1·2 통과 수는 "
            "양쪽 17,672 로 같고 달라지는 것은 사유 귀속과 대표 선택이다",
        ],
    }

    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.check:
        if not REPORT_PATH.exists():
            raise SystemExit(f"FAIL: 리포트가 없다 ({REPORT_PATH.relative_to(ROOT)})")
        if REPORT_PATH.read_text() != payload:
            raise SystemExit(
                f"FAIL: 원장 리포트가 재현되지 않는다 ({REPORT_PATH.relative_to(ROOT)})\n"
                "  → 사유 어휘·게이트 판정·골든셋 라벨이 바뀌었다면 리포트를 다시 생성해 "
                "함께 커밋한다"
            )
        print(f"OK: 원장 리포트 재현 일치 ({REPORT_PATH.relative_to(ROOT)})")
        return

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(payload)

    led, tb, ev, di = (report["ledger"], report["traceback"],
                       report["evidence"], report["direction"])
    print(f"[PER-188] 리뷰 {led['reviewsIn']} → 게이트1 {led['afterGate1']} "
          f"→ 게이트2 {led['afterGate2']} · 주장 후보 {led['claimCandidates']} "
          f"→ 통과 {led['claimsPassed']}")
    for gate, reasons in led["byGateReason"].items():
        detail = " · ".join(f"{k} {v['count']} ({v['pct']}%)" for k, v in reasons.items())
        print(f"  {gate:12s} {detail}")
    print(f"  {'polarity':12s} 탈락 0 — 근거를 버리지 않는다 (PER-185 §1)")
    print(f"  원장 {led['rows']}행 = 리뷰 {led['reviewRows']} + 주장 {led['claimRows']}")
    print(f"  설명: {led['accounted']['reviews']} · 주장 {led['accounted']['claims']}")
    print(f"  교차검증: PER-186 커밋 수치와 일치 = {report['crossCheck']['matches']}")
    print(f"[역추적] 골든셋 {tb['targets']}건 → 되짚을 수 있는 {tb['traceable']}건 중 "
          f"파이프라인이 낸 것 {tb['produced']} (재현율 {tb['recall']})")
    print(f"  미스 {tb['missed']}건: {tb['missByGateReason']} "
          f"(전부 사유가 붙었나: {tb['missedAllTraced']})")
    print(f"  되짚을 수 없음 {tb['byOutcome'].get(OUTCOME_UNTRACEABLE, 0)}건 "
          "(aspect 가 택소노미 밖)")
    print(f"  근거 리뷰 {ev['evidenceReviews']}건의 운명: {ev['byFate']}")
    print(f"  방향: 통과 {di['produced']}건 중 일치 {di['match']} · "
          f"불일치 {di['mismatch']} ({di['mismatchPct']}%) {di['mismatchKind']}")
    print(f"        쌍 {di['pairs']}")
    for row in report["revival"]["nMinGrid"]:
        print(f"    N_min={row['nMin']:2d}: 낸 주장 {row['produced']:2d}/"
              f"{row['traceable']} (재현율 {row['recall']})")
    print(f"→ {REPORT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
