"""
통합 `rejected[]` 원장 + 골든셋 역추적 (PER-188 / PRD §3-2 · §6 · §11).

게이트 1~4 는 각자 탈락분을 낸다. **흩어져 있으면 재현율을 못 잰다** — 어느 리뷰가
어느 검문소에서 멈췄는지, 골든셋이 잡은 주장을 파이프라인이 왜 못 냈는지를 한 곳에서
되짚을 수 없기 때문이다. 이 모듈이 그 한 곳이다.

  원장   게이트 1~4 의 탈락을 리뷰/주장 단위로 한 목록에 모은다. 행마다
         `gate` · `reason` · `label` · `detail` · 수치가 붙는다
  역추적 골든셋 claim 을 받아 파이프라인이 냈는지, 못 냈으면 **어느 게이트의 어느
         사유에서 멈췄는지**를 돌려준다. v4 에서 손으로 하던 일이다

## 왜 이게 재현율 계산 방식인가

통과분만 남기면 정밀도는 측정되지만 재현율은 영영 측정되지 않는다 (PRD §6). v4 의
Golden Set 미스 2건은 원인 추적이 **수동**이었다 — 리뷰를 눈으로 따라가며 어디서
빠졌는지 찾았다. 표본이 100건, 코퍼스가 25,000건이 되면 그 방법은 성립하지 않는다.

그래서 탈락을 지우지 않고 사유와 함께 쌓고, 골든셋 claim 을 그 목록으로 되짚는다.
**골든셋이 잡았는데 파이프라인이 못 낸 주장이 원장의 어느 행에 걸리는가** — 그 대응이
성립해야 "재현율 N%" 라는 말에 근거가 생긴다.

## 리뷰는 한 번만 멈춘다

게이트1 에서 탈락한 리뷰는 게이트2 에 들어가지 않는다. 그래서 원장에서 한 리뷰는
**최대 한 행**이고, 두 번째 행을 넣으려 하면 에러다 (`LedgerError`). 두 행이 생기면
"어느 게이트에서 멈췄나"에 답이 둘이 되고, 사유별 합이 입력 수를 넘는다.

주장(게이트4)은 단위가 달라 `claimId` 로 센다. **두 수를 서로 더하지 않는다** —
사유마다 `unit` 이 붙어 있는 것이 그 때문이다.

## 게이트3 은 원장에 탈락을 넣지 않는다

`polarity.py` 에 `rejected[]` 가 없는 것과 같은 이유다 (PER-185 §1). 이 모듈은
게이트3 을 **거치되** 그 산출을 판정(방향)과 한계로 받아 통과분에 얹는다. 레지스트리가
게이트3 사유를 0개로 등록해 두어, 실수로 넣으려 하면 `RejectRegistryError` 가 난다.

## 실행 순서가 규격이다

  1 동일성   이 제품·이 세대·이 시점·(옵션 범위를 걸었다면) 이 옵션의 리뷰인가
  2 중복     같은 사람·같은 글을 두 번 세고 있지 않은가 → 남은 수가 독립 근거 수다
  3 방향성   남은 근거가 한 방향인가 — **탈락 없음**. 판정과 한계만 낸다
  4 충분성   이 근거로 주장을 세울 수 있는가 — 주장 단위 탈락

순서를 바꾸면 통과 수가 달라진다. 게이트1 을 건너뛰면 컷될 리뷰가 게이트2 의 대표로
남고, 게이트2 를 건너뛰면 셀 크기 S 가 부푼다.

사용:
  .venv/bin/python pipeline/run_v5.py --steps gates
  .venv/bin/python pipeline/ledger.py --check     # 원장이 현재 입력과 맞는지
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

ROOT = Path(__file__).parents[1]

from catalog import load_catalog  # noqa: E402
from contracts import MISSING_SEGMENT  # noqa: E402
from gates import (  # noqa: E402
    GateResult,
    IdentityScope,
    RejectedRow,
    run_duplicate_gate,
    run_identity_gate,
)
from option_norm import OptionIndex, UnknownOptionScopeError  # noqa: E402
from polarity import aspect_support  # noqa: E402
from policy import DEFAULT_SUFFICIENCY, SufficiencyPolicy  # noqa: E402
from reject_registry import (  # noqa: E402
    GATE_DUPLICATE,
    GATE_IDENTITY,
    GATE_ORDER,
    GATE_POLARITY,
    GATE_SUFFICIENCY,
    UNIT_CLAIM,
    UNIT_REVIEW,
    assert_rejectable,
    label_of,
)
from sufficiency import (  # noqa: E402
    MULTI_AXES,
    Claim,
    ClaimSupport,
    EvidenceCell,
    SufficiencyResult,
    matches,
    run_sufficiency_gate,
)
from tag_contract import ASPECTS  # noqa: E402

REVIEWS_PATH = ROOT / "data/intermediate/v5_reviews.jsonl"
TAGS_PATH = ROOT / "data/intermediate/v5_tags.jsonl"
TAGS_META_PATH = ROOT / "data/intermediate/v5_tags_meta.json"
ORDER_CHOSEN_PATH = ROOT / "data/intermediate/v5_tags_order_chosen.json"
LEDGER_PATH = ROOT / "data/intermediate/v5_rejected.jsonl"
SUMMARY_PATH = ROOT / "data/intermediate/v5_gate_ledger.json"

CELL_AXES = ("skinType", "skinTrouble")


class LedgerError(ValueError):
    """원장이 계약을 위반했다 — 같은 대상이 두 번 멈췄거나, 사유 없는 행이 들어왔다."""


# =============================================================================
# 원장
# =============================================================================


@dataclass(frozen=True)
class LedgerRow:
    """원장 한 줄. **버린 것 하나 = 사유 하나 = 이 행 하나.**

    `subject` 는 리뷰면 `reviewId`, 주장이면 `claimId` 다. 단위를 필드로 들고 다니는
    이유는 두 수를 더하지 못하게 하려는 것이다 — "탈락 8,000건"이 리뷰와 주장의 합이면
    아무 뜻도 없는 수다.
    """
    gate: str
    reason: str
    unit: str
    subject: str
    detail: str | None = None
    metrics: dict | None = None

    def __post_init__(self) -> None:
        entry = assert_rejectable(self.gate, self.reason)
        if entry.unit != self.unit:
            raise LedgerError(
                f"사유 {self.reason!r} 의 단위는 {entry.unit!r} 인데 {self.unit!r} 로 "
                "기록하려 했다 — 리뷰 탈락과 주장 탈락을 한 수로 더하게 된다"
            )
        if not str(self.subject).strip():
            raise LedgerError(f"[{self.gate}/{self.reason}] 무엇이 탈락했는지가 비어 있다")

    @property
    def label(self) -> str:
        return label_of(self.reason)

    @property
    def gate_order(self) -> int:
        return GATE_ORDER[self.gate]

    def as_dict(self) -> dict:
        row = {
            "gate": self.gate,
            "gateOrder": self.gate_order,
            "reason": self.reason,
            "label": self.label,
            "unit": self.unit,
            "subject": self.subject,
            "detail": self.detail,
        }
        if self.metrics is not None:
            row["metrics"] = self.metrics
        return row


@dataclass
class RejectedLedger:
    """게이트 1~4 의 탈락을 한 목록으로 모은 원장.

    같은 대상을 두 번 넣으면 에러다. 게이트1 에서 멈춘 리뷰는 게이트2 에 들어가지
    않으므로, 두 행이 생겼다는 건 게이트 순서가 깨졌거나 같은 리뷰를 두 번 먹였다는
    뜻이다 — 조용히 두면 사유별 합이 입력 수를 넘는다.
    """
    rows: list[LedgerRow] = field(default_factory=list)
    _index: dict[tuple[str, str], LedgerRow] = field(default_factory=dict, repr=False)

    def add(self, row: LedgerRow) -> LedgerRow:
        key = (row.unit, str(row.subject))
        previous = self._index.get(key)
        if previous is not None:
            raise LedgerError(
                f"{row.unit} {row.subject} 가 이미 {previous.gate}/{previous.reason} 로 "
                f"멈춰 있는데 {row.gate}/{row.reason} 로 또 기록하려 했다. "
                "한 대상은 한 번만 멈춘다 — 두 행이면 '어느 게이트에서 멈췄나'의 답이 둘이 된다"
            )
        self._index[key] = row
        self.rows.append(row)
        return row

    def add_review_rejections(self, result: GateResult) -> None:
        """게이트1·2 의 `GateResult` 를 그대로 흡수한다."""
        for rejected in result.rejected:
            self.add(LedgerRow(
                gate=rejected.gate,
                reason=rejected.reason,
                unit=UNIT_REVIEW,
                subject=str(rejected.review_id),
                detail=rejected.detail,
            ))

    def add_claim_rejections(self, result: SufficiencyResult) -> None:
        """게이트4 의 `SufficiencyResult` 를 흡수한다. 수치(`support`)를 함께 남긴다."""
        for rejected in result.rejected:
            self.add(LedgerRow(
                gate=GATE_SUFFICIENCY,
                reason=rejected.reason,
                unit=UNIT_CLAIM,
                subject=rejected.claim_id,
                detail=rejected.detail,
                metrics=rejected.support,
            ))

    # --- 되짚기 ---

    def stopped_at(self, subject: object, unit: str = UNIT_REVIEW) -> LedgerRow | None:
        """이 리뷰/주장이 어느 게이트에서 멈췄나. 통과했으면 `None`.

        **이 한 줄이 이 이슈의 완료 조건이다** — "버린 것을 왜 버렸는지"를 대상 하나로
        물을 수 있어야 골든셋 미스를 자동으로 되짚을 수 있다.
        """
        return self._index.get((unit, str(subject)))

    def by_gate(self) -> dict[str, int]:
        counts: collections.Counter = collections.Counter(r.gate for r in self.rows)
        return {g: counts[g] for g in sorted(counts, key=lambda g: GATE_ORDER[g])}

    def by_reason(self) -> dict[str, int]:
        counts: collections.Counter = collections.Counter(r.reason for r in self.rows)
        return dict(sorted(counts.items()))

    def by_gate_reason(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for row in self.rows:
            out.setdefault(row.gate, {})
            out[row.gate][row.reason] = out[row.gate].get(row.reason, 0) + 1
        return {
            gate: dict(sorted(reasons.items()))
            for gate, reasons in sorted(out.items(), key=lambda kv: GATE_ORDER[kv[0]])
        }

    def of_unit(self, unit: str) -> list[LedgerRow]:
        return [r for r in self.rows if r.unit == unit]

    def as_dict(self) -> dict:
        return {
            "issue": "PER-188",
            "rows": len(self.rows),
            "reviewRows": len(self.of_unit(UNIT_REVIEW)),
            "claimRows": len(self.of_unit(UNIT_CLAIM)),
            "byGate": self.by_gate(),
            "byGateReason": {
                gate: {label_of(code): n for code, n in reasons.items()}
                for gate, reasons in self.by_gate_reason().items()
            },
            "note": (
                "리뷰 행과 주장 행은 단위가 달라 서로 더하지 않는다. 게이트3 은 근거를 "
                "버리지 않으므로 이 원장에 행이 없다 (PER-185 §1)"
            ),
        }

    def write_jsonl(self, path: Path) -> int:
        """원장 전문. 정렬은 게이트 순 → 사유 → 대상이다 — 재실행하면 바이트가 같아야 한다."""
        rows = sorted(
            (r.as_dict() for r in self.rows),
            key=lambda r: (r["gateOrder"], r["reason"], r["subject"]),
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows))
        return len(rows)


# =============================================================================
# 4게이트 실행 경로
# =============================================================================


@dataclass
class GateRun:
    """4게이트를 순서대로 건 결과. 통과분과 원장을 **함께** 낸다."""
    ledger: RejectedLedger = field(default_factory=RejectedLedger)
    kept: dict[str, list[dict]] = field(default_factory=dict)
    claims: list[Claim] = field(default_factory=list)
    passed_claims: list[Claim] = field(default_factory=list)
    limitations: dict[str, dict] = field(default_factory=dict)
    policy: SufficiencyPolicy = DEFAULT_SUFFICIENCY
    trace: dict[str, dict] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "issue": "PER-188",
            "policy": self.policy.as_meta(),
            "products": len(self.trace),
            "reviewsIn": sum(t["reviews"] for t in self.trace.values()),
            "afterGate1": sum(t["afterGate1"] for t in self.trace.values()),
            "afterGate2": sum(t["afterGate2"] for t in self.trace.values()),
            "claimCandidates": len(self.claims),
            "claimsPassed": len(self.passed_claims),
            "ledger": self.ledger.as_dict(),
            "limitations": dict(sorted(self.limitations.items())),
        }


def run_review_gates(
    records: list[dict],
    scope: IdentityScope,
    catalog,
    ledger: RejectedLedger,
    option_index: OptionIndex | None = None,
) -> tuple[list[dict], set[str], dict[str, int]]:
    """게이트1 → 게이트2. 탈락분은 원장으로 가고 통과분·한계·단계별 수를 돌려준다.

    **순서를 바꾸면 안 된다.** 게이트1 이 먼저 걸려야 컷된 리뷰가 게이트2 의 대표로
    남지 않는다 (`test_gates.GateOrder` 가 고정).

    단계별 수를 원장에서 역산하지 않고 게이트가 낸 값을 그대로 쓴다 — 역산하면
    원장이 비어 있을 때도 그럴듯한 수가 나온다.
    """
    first = run_identity_gate(records, scope, catalog, option_index)
    ledger.add_review_rejections(first)
    second = run_duplicate_gate(first.passed)
    ledger.add_review_rejections(second)
    counts = {
        "reviews": len(records),
        "afterGate1": len(first.passed),
        "afterGate2": len(second.passed),
    }
    return second.passed, set(first.limitations) | set(second.limitations), counts


def claim_id(product_id: str, axis: str, segment: str | None, aspect: str) -> str:
    """주장 식별자. `rejected[]` 가 되짚을 키이므로 사람이 읽고 재구성할 수 있어야 한다."""
    return f"{product_id}|{axis}={segment}|{aspect}"


def cell_conditions(records: list[dict], axes: tuple[str, ...] = CELL_AXES) -> list[tuple[str, str | None, dict]]:
    """이 묶음이 만들 수 있는 셀 목록 — 제품 전체 + 조건축 세그먼트별.

    미기재도 세그먼트다 ('조건 없음'이 아니다, PER-173). 빼면 프로필을 안 밝힌
    사람들의 주장이 통째로 사라진다.
    """
    out: list[tuple[str, str | None, dict]] = [("product", None, {})]
    for axis in axes:
        segments: set[str] = set()
        for record in records:
            cell = record["condition"][axis]
            segments.update(cell["segments"] if axis in MULTI_AXES else [cell["segment"]])
        for segment in sorted(segments):
            condition = {axis: [segment] if axis in MULTI_AXES else segment}
            out.append((axis, segment, condition))
    return out


def build_claims(
    kept: list[dict],
    product_id: str,
    tags_by_review: dict[int, list[tuple[str, str]]],
    axes: tuple[str, ...] = CELL_AXES,
) -> list[Claim]:
    """(셀 × aspect) 후보 주장. **D ≥ 1 인 것만** — 아무도 말하지 않은 주제는 주장이 아니다.

    `kept` 는 게이트2 통과분이어야 한다. `EvidenceCell.of` 가 그 전제를 확인한다.
    """
    if not kept:
        return []
    EvidenceCell.of(kept, product_id, {})  # 게이트2 통과분인지 확인 (아니면 GateError)
    claims: list[Claim] = []
    for axis, segment, condition in cell_conditions(kept, axes):
        members = [r for r in kept if matches(r, condition)]
        if not members:
            continue
        cell = EvidenceCell(
            product_id, condition,
            frozenset(r["derived"]["authorKey"] for r in members))
        by_aspect: dict[str, dict[str, set]] = collections.defaultdict(
            lambda: {"positive": set(), "negative": set(), "neutral": set()})
        for record in members:
            author = record["derived"]["authorKey"]
            for aspect, polarity in tags_by_review.get(record["reviewId"], ()):
                by_aspect[aspect][polarity].add(author)
        for aspect in ASPECTS:
            stances = by_aspect.get(aspect)
            if not stances:
                continue
            claims.append(Claim(
                claim_id(product_id, axis, segment, aspect),
                cell,
                ClaimSupport(
                    aspect=aspect,
                    positive=frozenset(stances["positive"]),
                    negative=frozenset(stances["negative"]),
                    neutral=frozenset(stances["neutral"]),
                ),
            ))
    return claims


def run_gates(
    records: list[dict],
    tags_by_review: dict[int, list[tuple[str, str]]],
    catalog,
    *,
    policy: SufficiencyPolicy = DEFAULT_SUFFICIENCY,
    axes: tuple[str, ...] = CELL_AXES,
) -> GateRun:
    """게이트 1~4 를 순서대로 걸고 통합 원장을 낸다 (`run_v5.py --steps gates`).

    게이트3 은 탈락을 내지 않으므로 여기서 **판정을 위해** 돌리지 않는다 — 방향은
    게이트4 의 `ClaimSupport.direction` 이 같은 규칙으로 계산하고, 별점 교차검증은
    `eval/measure_gate3_polarity.py` 가 소유한다. 이 함수가 게이트3 에 대해 하는 일은
    **한계를 통과분에 얹는 것**뿐이다.
    """
    run = GateRun(policy=policy)
    grouped: dict[str, list[dict]] = collections.defaultdict(list)
    for record in records:
        grouped[record["productId"]].append(record)

    # 한계는 **단위와 함께** 센다. `renewal_unobserved` 는 리뷰에, `single_dissent` 는
    # 주장에 붙는다 — 한 수로 더하면 무엇이 몇 건인지 알 수 없다 (사유와 같은 규칙)
    review_limits: collections.Counter = collections.Counter()
    claim_limits: collections.Counter = collections.Counter()
    for product_id, rows in sorted(grouped.items()):
        kept, limits, counts = run_review_gates(
            rows, IdentityScope(product_id), catalog, run.ledger)
        run.kept[product_id] = kept
        for code in limits:
            review_limits[code] += len(kept)
        run.trace[product_id] = counts
        run.claims.extend(build_claims(kept, product_id, tags_by_review, axes))

    result = run_sufficiency_gate(run.claims, policy)
    run.ledger.add_claim_rejections(result)
    run.passed_claims = result.passed
    for code, claim_ids in result.limitations.items():
        claim_limits[code] += len(claim_ids)
    run.limitations = {
        code: {"unit": unit, "count": n}
        for unit, counter in ((UNIT_REVIEW, review_limits), (UNIT_CLAIM, claim_limits))
        for code, n in sorted(counter.items())
    }
    return run


# =============================================================================
# 역추적 — 골든셋 claim → `rejected[]`
# =============================================================================

# 역추적 결과 어휘. **탈락 사유가 아니다** — 파이프라인이 그 주장을 냈는가에 대한
# 답이고, 냈다/못 냈다의 이유가 원장의 어느 행인지를 가리킨다.
OUTCOME_PRODUCED = "produced"            # 게이트4 통과 — 파이프라인이 낸다
OUTCOME_REJECTED = "rejected"            # 게이트4 탈락 — 원장에 사유가 있다
OUTCOME_NO_CANDIDATE = "no_candidate"    # 후보조차 못 됐다 (D=0 등) — 근거가 게이트1·2 에서 빠졌다
OUTCOME_UNTRACEABLE = "untraceable"      # 파이프라인의 주장 단위로 표현되지 않는다 (aspect 없음 등)
OUTCOMES = (OUTCOME_PRODUCED, OUTCOME_REJECTED, OUTCOME_NO_CANDIDATE, OUTCOME_UNTRACEABLE)

# 후보조차 못 된 이유 / 되짚을 수 없는 이유. 원장의 사유 코드와 **어휘를 섞지 않는다** —
# 이건 게이트의 판정이 아니라 대조의 결과다.
MISS_NO_ASPECT = "aspect_out_of_taxonomy"   # 라벨의 aspect 가 null 또는 동결 14종 밖
MISS_NO_MENTION = "no_tagged_mention"       # 셀에 남은 리뷰 중 이 주제를 태깅한 사람이 0명
MISS_EMPTY_CELL = "empty_cell"              # 게이트1·2 를 통과한 리뷰가 셀에 0건
MISS_UNKNOWN_OPTION = "option_scope_unknown"  # 옵션 범위를 이 제품의 색상으로 읽을 수 없다


@dataclass(frozen=True)
class ClaimTarget:
    """되짚을 대상 1건 — 보통 골든셋 라벨 하나.

    `condition` 은 조건축 셀(`{"skinType": "A04"}`)이고, **옵션은 따로 받는다** —
    옵션 동일성은 게이트4 의 셀이 아니라 게이트1 이 소유하기 때문이다 (PER-182).
    """
    key: str
    product_id: str
    aspect: str | None
    condition: dict = field(default_factory=dict)
    option_scope: str | None = None
    evidence_review_ids: tuple[int, ...] = ()
    direction: str | None = None


@dataclass(frozen=True)
class EvidenceFate:
    """근거 리뷰 1건의 운명. 통과했으면 `gate` 가 `None` 이다."""
    review_id: int
    passed: bool
    gate: str | None = None
    reason: str | None = None
    label: str | None = None

    def as_dict(self) -> dict:
        return {
            "reviewId": self.review_id,
            "passed": self.passed,
            "gate": self.gate,
            "reason": self.reason,
            "label": self.label,
        }


@dataclass(frozen=True)
class Trace:
    """역추적 1건. **골든셋 claim 하나가 원장의 어디에 걸리는가.**"""
    key: str
    outcome: str
    gate: str | None = None
    reason: str | None = None
    label: str | None = None
    detail: str | None = None
    metrics: dict | None = None
    claim_id: str | None = None
    pipeline_direction: str | None = None
    golden_direction: str | None = None
    evidence: tuple[EvidenceFate, ...] = ()
    limitations: tuple[str, ...] = ()

    @property
    def direction_match(self) -> bool | None:
        if self.pipeline_direction is None or self.golden_direction is None:
            return None
        return self.pipeline_direction == self.golden_direction

    @property
    def evidence_lost(self) -> int:
        return sum(1 for e in self.evidence if not e.passed)

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "outcome": self.outcome,
            "gate": self.gate,
            "reason": self.reason,
            "label": self.label,
            "detail": self.detail,
            "metrics": self.metrics,
            "claimId": self.claim_id,
            "pipelineDirection": self.pipeline_direction,
            "goldenDirection": self.golden_direction,
            "directionMatch": self.direction_match,
            "evidenceReviews": len(self.evidence),
            "evidenceLost": self.evidence_lost,
            "evidence": [e.as_dict() for e in self.evidence],
            "limitations": list(self.limitations),
        }


def trace_target(
    target: ClaimTarget,
    records: list[dict],
    tags: list[dict],
    catalog,
    *,
    option_index: OptionIndex | None = None,
    policy: SufficiencyPolicy = DEFAULT_SUFFICIENCY,
) -> Trace:
    """claim 하나를 게이트 1~4 에 통과시켜 보고, 못 냈으면 **어디서 멈췄는지**를 낸다.

    `records` 는 그 제품의 코퍼스 전부다 (게이트 이전). 번들 40건이 아니라 전수를
    넣어야 "파이프라인이 그 주장을 냈을까"의 답이 실제 파이프라인과 같아진다.

    되짚는 순서는 **뒤에서 앞으로**다 — 게이트4 에서 탈락했으면 거기가 답이고,
    후보조차 못 됐으면 그 원인이 게이트1·2 에 있다. 근거 리뷰의 운명(`evidence`)은
    결과와 무관하게 항상 낸다: 통과한 주장이라도 근거 절반이 중복으로 빠졌다면
    그 사실이 표기(PER-190)에 걸린다.
    """
    ledger = RejectedLedger()

    scope = IdentityScope(target.product_id)
    if target.option_scope is not None:
        if option_index is None:
            raise LedgerError(
                f"[{target.key}] 옵션 범위 {target.option_scope!r} 를 걸었는데 "
                "OptionIndex 가 없다 — 색상 판정은 코퍼스 어휘가 필요하다 (PER-182)"
            )
        try:
            scope = IdentityScope(
                target.product_id,
                option_index.resolve_scope(target.product_id, target.option_scope))
        except UnknownOptionScopeError as exc:
            return Trace(
                key=target.key,
                outcome=OUTCOME_UNTRACEABLE,
                gate=GATE_IDENTITY,
                reason=MISS_UNKNOWN_OPTION,
                detail=str(exc).splitlines()[0],
                golden_direction=target.direction,
            )

    in_product = [r for r in records if r["productId"] == target.product_id]
    kept, limits, _ = run_review_gates(in_product, scope, catalog, ledger, option_index)

    fates = tuple(
        _fate(review_id, ledger, {r["reviewId"] for r in kept})
        for review_id in target.evidence_review_ids
    )

    if target.aspect is None or target.aspect not in ASPECTS:
        # 동결 14종 밖이면 파이프라인의 주장 단위로 표현되지 않는다. 원장으로 되짚을
        # 수 없다는 사실 자체가 커버리지의 한계이므로 숨기지 않고 이 결과로 낸다
        return Trace(
            key=target.key,
            outcome=OUTCOME_UNTRACEABLE,
            reason=MISS_NO_ASPECT,
            detail=f"aspect={target.aspect!r} — 동결 택소노미 14종 밖이라 (셀 × aspect) 키가 없다",
            golden_direction=target.direction,
            evidence=fates,
        )

    cell = EvidenceCell.of(kept, target.product_id, dict(target.condition))
    axis, segment = _axis_of(target.condition)
    key = claim_id(target.product_id, axis, segment, target.aspect)

    if not cell.authors:
        return Trace(
            key=target.key, outcome=OUTCOME_NO_CANDIDATE, gate=GATE_IDENTITY,
            reason=MISS_EMPTY_CELL, claim_id=key,
            detail="게이트1·2 를 통과한 리뷰가 이 셀에 0건이다",
            golden_direction=target.direction, evidence=fates,
        )

    cell_records = [
        r for r in kept
        if r["derived"]["authorKey"] in cell.authors and matches(r, dict(target.condition))
    ]
    cell_ids = {r["reviewId"] for r in cell_records}
    cell_tags = [t for t in tags if t.get("reviewId") in cell_ids]

    support = ClaimSupport.of(cell_tags, cell_records, target.aspect, cell)
    # 게이트3 — 판정을 바꾸지 않고 방향과 한계만 받는다 (PER-185 §1)
    polarity = aspect_support(cell_records, cell_tags, target.aspect)
    limitations = tuple(sorted(set(limits) | set(polarity.limitations)))

    if not support.spoke:
        return Trace(
            key=target.key, outcome=OUTCOME_NO_CANDIDATE, gate=GATE_POLARITY,
            reason=MISS_NO_MENTION, claim_id=key,
            detail=(
                f"셀 작성자 {cell.size}명 중 {target.aspect!r} 를 말한 사람이 0명이다 "
                f"(태깅 누락이거나 실제 침묵 — 침묵은 근거가 아니다, PER-178)"
            ),
            pipeline_direction=polarity.direction,
            golden_direction=target.direction, evidence=fates, limitations=limitations,
        )

    result = run_sufficiency_gate([Claim(key, cell, support)], policy)
    metrics = support.as_dict(cell)
    if result.passed:
        return Trace(
            key=target.key, outcome=OUTCOME_PRODUCED, claim_id=key, metrics=metrics,
            pipeline_direction=support.direction, golden_direction=target.direction,
            evidence=fates,
            limitations=tuple(sorted(set(limitations) | set(result.limitations))),
        )

    rejected = result.rejected[0]
    return Trace(
        key=target.key, outcome=OUTCOME_REJECTED, gate=GATE_SUFFICIENCY,
        reason=rejected.reason, label=rejected.label, detail=rejected.detail,
        metrics=metrics, claim_id=key,
        pipeline_direction=support.direction, golden_direction=target.direction,
        evidence=fates, limitations=limitations,
    )


def _axis_of(condition: dict) -> tuple[str, str | None]:
    """조건 딕트 → (축 이름, 세그먼트). 조건이 없으면 제품 전체 셀이다."""
    stated = {k: v for k, v in condition.items() if v is not None}
    if not stated:
        return "product", None
    if len(stated) > 1:
        raise LedgerError(
            f"조건축을 두 개 이상 건 셀은 되짚지 않는다: {sorted(stated)} — "
            "축을 나눠 각각 대조하라 (교차 셀은 S 가 급격히 작아져 세그먼트과소로만 떨어진다)"
        )
    axis, value = next(iter(stated.items()))
    return axis, (value[0] if isinstance(value, (list, tuple)) else value)


def _fate(review_id: int, ledger: RejectedLedger, kept_ids: set[int]) -> EvidenceFate:
    row = ledger.stopped_at(review_id)
    if row is not None:
        return EvidenceFate(review_id, False, row.gate, row.reason, row.label)
    if review_id in kept_ids:
        return EvidenceFate(review_id, True)
    # 게이트에 들어가지도 않았다 — 다른 제품이거나 옵션 범위 밖 묶음이다.
    return EvidenceFate(review_id, False, GATE_IDENTITY, None, "범위밖")


def trace_all(
    targets: list[ClaimTarget],
    records: list[dict],
    tags: list[dict],
    catalog,
    *,
    option_index: OptionIndex | None = None,
    policy: SufficiencyPolicy = DEFAULT_SUFFICIENCY,
) -> list[Trace]:
    """대상 묶음을 되짚는다. 제품별로 레코드·태그를 잘라 넘겨 반복 훑기를 줄인다."""
    by_product: dict[str, list[dict]] = collections.defaultdict(list)
    for record in records:
        by_product[record["productId"]].append(record)
    ids_by_product = {
        pid: {r["reviewId"] for r in rows} for pid, rows in by_product.items()}
    tags_by_product: dict[str, list[dict]] = {
        pid: [t for t in tags if t.get("reviewId") in ids]
        for pid, ids in ids_by_product.items()
    }
    return [
        trace_target(
            target, by_product.get(target.product_id, []),
            tags_by_product.get(target.product_id, []), catalog,
            option_index=option_index, policy=policy)
        for target in targets
    ]


def recall_summary(traces: list[Trace]) -> dict:
    """역추적 집계 — **재현율을 이 수로 센다.**

    분모는 되짚을 수 있는 claim 이고, `untraceable` 은 분모에서 빼되 수를 남긴다.
    빼고 안 적으면 재현율이 조용히 올라간다.
    """
    by_outcome: collections.Counter = collections.Counter(t.outcome for t in traces)
    traceable = [t for t in traces if t.outcome != OUTCOME_UNTRACEABLE]
    produced = [t for t in traceable if t.outcome == OUTCOME_PRODUCED]
    missed = [t for t in traceable if t.outcome != OUTCOME_PRODUCED]
    miss_reasons: collections.Counter = collections.Counter(
        f"{t.gate or '-'}/{t.reason}" for t in missed)
    return {
        "targets": len(traces),
        "byOutcome": {k: by_outcome[k] for k in OUTCOMES if by_outcome[k]},
        "traceable": len(traceable),
        "produced": len(produced),
        "missed": len(missed),
        "recall": round(len(produced) / len(traceable), 4) if traceable else None,
        "missByGateReason": dict(sorted(miss_reasons.items())),
        "missedAllTraced": all(t.reason for t in missed),
        "directionMismatch": [
            t.key for t in produced if t.direction_match is False],
        "note": (
            "분모는 되짚을 수 있는 claim 이다. untraceable(택소노미 밖 aspect 등)은 "
            "분모에서 빼되 byOutcome 에 수가 남는다 — 빼고 안 적으면 재현율이 조용히 오른다"
        ),
    }


# =============================================================================
# 러너 진입점 (`run_v5.py --steps gates`)
# =============================================================================


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def load_inputs() -> tuple[list[dict], dict[int, list[tuple[str, str]]], object]:
    """입수 산출물과 전수 태그를 읽는다. 없으면 조용히 건너뛰지 않고 멈춘다."""
    if not REVIEWS_PATH.exists():
        raise SystemExit(
            f"입수 산출물이 없다: {REVIEWS_PATH.relative_to(ROOT)}\n"
            "  먼저 입수를 돌린다 — python3 pipeline/run_v5.py --steps ingest"
        )
    if not TAGS_PATH.exists():
        raise SystemExit(
            f"전수 태그가 없다: {TAGS_PATH.relative_to(ROOT)}\n"
            "  먼저 정본을 못박는다 — python3 pipeline/run_v5.py --steps tag\n"
            "  태그 없이 건 게이트4 는 후보가 0건이라 원장이 게이트1·2 만 담는다."
        )
    records = _read_jsonl(REVIEWS_PATH)
    tags_by_review: dict[int, list[tuple[str, str]]] = collections.defaultdict(list)
    for tag in _read_jsonl(TAGS_PATH):
        tags_by_review[tag["reviewId"]].append((tag["aspect"], tag["polarity"]))
    return records, dict(tags_by_review), load_catalog()


def _summary(run: GateRun, records: list[dict]) -> dict:
    tags_meta = json.loads(TAGS_META_PATH.read_text()) if TAGS_META_PATH.exists() else {}
    return {
        **run.as_dict(),
        "source": {
            "reviews": str(REVIEWS_PATH.relative_to(ROOT)),
            "reviewsSha256": hashlib.sha256(REVIEWS_PATH.read_bytes()).hexdigest(),
            "tags": str(TAGS_PATH.relative_to(ROOT)),
            "tagsSha256": hashlib.sha256(TAGS_PATH.read_bytes()).hexdigest(),
            "tagsLabel": tags_meta.get("label"),
            "tagsModel": tags_meta.get("model"),
            "records": len(records),
        },
        "ledgerPath": str(LEDGER_PATH.relative_to(ROOT)),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="원장이 현재 입력으로 재현되는지만 확인 (§5-2 재현성)")
    args = ap.parse_args()

    records, tags_by_review, catalog = load_inputs()
    run = run_gates(records, tags_by_review, catalog)
    summary = _summary(run, records)
    payload = json.dumps(summary, ensure_ascii=False, indent=2) + "\n"

    if args.check:
        if not SUMMARY_PATH.exists() or not LEDGER_PATH.exists():
            raise SystemExit(
                f"FAIL: 원장이 없다 ({SUMMARY_PATH.relative_to(ROOT)}) — "
                "python3 pipeline/run_v5.py --steps gates"
            )
        if SUMMARY_PATH.read_text() != payload:
            raise SystemExit(
                f"FAIL: 원장이 재현되지 않는다 ({SUMMARY_PATH.relative_to(ROOT)})\n"
                "  → 게이트 판정이나 임계값이 바뀌었다면 원장을 다시 낸다"
            )
        print(f"OK: 게이트 원장 재현 일치 ({SUMMARY_PATH.relative_to(ROOT)})")
        return

    rows = run.ledger.write_jsonl(LEDGER_PATH)
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(payload)

    ledger = summary["ledger"]
    print(f"[게이트] 리뷰 {summary['reviewsIn']}건 → 게이트1 통과 {summary['afterGate1']} "
          f"→ 게이트2 통과 {summary['afterGate2']}")
    for gate, reasons in run.ledger.by_gate_reason().items():
        total = sum(reasons.values())
        detail = " · ".join(f"{label_of(c)} {n}" for c, n in reasons.items())
        print(f"  {gate:12s} 탈락 {total:6d}  ({detail})")
    print(f"  {GATE_POLARITY:12s} 탈락      -  (근거를 버리지 않는다 — PER-185 §1)")
    print(f"  주장 후보 {summary['claimCandidates']}건 → 통과 {summary['claimsPassed']}건")
    for code, info in summary["limitations"].items():
        print(f"  한계 {code:32s} {info['count']:6d} {info['unit']}")
    print(f"→ {LEDGER_PATH.relative_to(ROOT)} ({rows}행) · {SUMMARY_PATH.relative_to(ROOT)}")
    if ledger["reviewRows"] + ledger["claimRows"] != rows:
        # `assert` 로 두지 않는다 — -O 로 돌면 조용히 꺼진다
        raise SystemExit(
            f"원장 파일 {rows}행이 집계 "
            f"{ledger['reviewRows']}+{ledger['claimRows']} 와 다르다")


__all__ = [
    "CELL_AXES",
    "GATE_DUPLICATE",
    "GATE_IDENTITY",
    "GATE_POLARITY",
    "GATE_SUFFICIENCY",
    "LEDGER_PATH",
    "MISS_EMPTY_CELL",
    "MISS_NO_ASPECT",
    "MISS_NO_MENTION",
    "MISS_UNKNOWN_OPTION",
    "MISSING_SEGMENT",
    "OUTCOMES",
    "OUTCOME_NO_CANDIDATE",
    "OUTCOME_PRODUCED",
    "OUTCOME_REJECTED",
    "OUTCOME_UNTRACEABLE",
    "SUMMARY_PATH",
    "ClaimTarget",
    "EvidenceFate",
    "GateRun",
    "LedgerError",
    "LedgerRow",
    "RejectedLedger",
    "Trace",
    "build_claims",
    "cell_conditions",
    "claim_id",
    "load_inputs",
    "main",
    "recall_summary",
    "run_gates",
    "run_review_gates",
    "trace_all",
    "trace_target",
]


if __name__ == "__main__":
    main()
