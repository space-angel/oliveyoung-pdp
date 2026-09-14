"""
게이트1 — 동일성 (PER-182 / PRD §4-1).

"이 리뷰가 **정말 이 제품·이 옵션**에 대한 것인가." 근거를 고르기 전에 통과해야 하는
첫 관문이고, **전부 결정론적이다 — LLM 호출이 없다.** PRD §2 가 못 박은 대로
같은 제품인가는 카탈로그의 책임이지 모델에게 물을 것이 아니다.

## 네 개의 판정

  제품   `goodsNo` → 카탈로그가 정한 `productId` 가 대상과 같은가 (PER-171)
  세대   리뷰가 그 제품 **세대**의 것인가 — 리뉴얼 컷 (PER-172)
  시점   리뷰가 리센시 윈도우 안인가 — 스냅샷 최신 월 기준 24개월 (PER-172)
  옵션   색상·호수 질문이면 **같은 색상**의 리뷰인가 (PER-182, `pipeline/option_norm.py`)

세대·시점 판정은 `pipeline/policy.py` 가 소유한다. 이 모듈은 그 둘을 옵션·제품
판정과 한 관문으로 묶고, 탈락을 `rejected[]` 행으로 만든다.

## 옵션 판정은 질문이 요구할 때만 건다

옵션은 조건축이지 항상 거는 컷이 아니다. "지속력 어때요" 에 색상 컷을 걸면 근거의
68.1%(기재분)만 남고 나머지는 이유 없이 사라진다. 그래서 **호출부가 옵션 범위를
명시할 때만** 건다 — 그게 §4-1 의 "색상·호수 질문이면" 이다.

범위를 건 질문에서 옵션 미기재 리뷰는 **불일치가 아니라 미기재**로 따로 센다.
둘을 한 사유로 뭉치면 "색상을 안 적어서 못 쓴 근거"(31.9%)와 "다른 색이라 못 쓴
근거"를 구분할 수 없고, 옵션 정규화를 개선했을 때 무엇이 좋아졌는지 귀속되지 않는다.

## 탈락은 드롭이 아니다 (PER-188)

판정은 리뷰를 지우지 않는다. `rejected[]` 에 사유와 함께 남는다 — 통과분만 남기면
정밀도는 측정되지만 **재현율은 영영 측정되지 않는다** (PRD §6).

사유 코드는 `policy.py` 와 같은 영문 snake_case 를 쓰고(`renewal_cut` …), 사람이 읽는
리포트·UI 표기는 `REJECT_LABELS` 가 한글로 옮긴다 (`리뉴얼이전` · `옵션불일치`).
코드와 표기를 같은 문자열로 두면 표기를 고칠 때 판정 코드가 같이 흔들린다.

## 통과했다고 한계가 없는 것은 아니다

`renewalPolicy='unobserved'` 는 통과하되 `limitation` 을 남긴다. 현 스냅샷은 전 제품
`unobserved` 라 **리뉴얼 컷의 실효는 0**이고, 그 사실이 주장의 한계로 따라나가야 한다.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from option_norm import (  # noqa: E402
    KIND_SHADE,
    KIND_UNSTATED,
    OptionIndex,
    UnknownOptionScopeError,
)
from policy import (  # noqa: E402
    REJECT_RECENCY,
    REJECT_RENEWAL,
    GateDecision,
    recency_gate,
    renewal_gate,
)

GATE_IDENTITY = "identity"

# `rejected[]` 사유 코드 — 제품·옵션은 이 모듈이, 세대·시점은 policy.py 가 소유한다.
REJECT_PRODUCT = "product_mismatch"
REJECT_OPTION = "option_mismatch"
REJECT_OPTION_UNSTATED = "option_unstated"

# 사람이 읽는 표기. PER-182 완료 조건의 '옵션불일치' · '리뉴얼이전' 이 여기 있다.
REJECT_LABELS = {
    REJECT_PRODUCT: "제품불일치",
    REJECT_OPTION: "옵션불일치",
    REJECT_OPTION_UNSTATED: "옵션미기재",
    REJECT_RENEWAL: "리뉴얼이전",
    REJECT_RECENCY: "기간초과",
}


@dataclass(frozen=True)
class IdentityScope:
    """근거를 고르는 대상. 옵션 범위는 **색상·호수 질문일 때만** 채운다.

    `option_key` 는 정규화된 색상 키여야 한다. 자연어 표기는
    `OptionIndex.resolve_scope()` 로 먼저 옮긴다 — 리뷰와 같은 정규화를 거쳐야
    비교가 대칭이다.
    """
    product_id: str
    option_key: str | None = None


@dataclass(frozen=True)
class RejectedRow:
    """`rejected[]` 한 줄 (PER-188). 사유 없이 탈락시키지 않는다."""
    review_id: int
    gate: str
    reason: str
    detail: str | None = None

    def as_dict(self) -> dict:
        return {
            "reviewId": self.review_id,
            "gate": self.gate,
            "reason": self.reason,
            "label": REJECT_LABELS.get(self.reason, self.reason),
            "detail": self.detail,
        }


@dataclass
class GateResult:
    """게이트 1회 실행 결과. 통과분과 탈락분을 **함께** 낸다."""
    passed: list[dict] = field(default_factory=list)
    rejected: list[RejectedRow] = field(default_factory=list)
    limitations: set[str] = field(default_factory=set)

    def as_dict(self) -> dict:
        return {
            "gate": GATE_IDENTITY,
            "issue": "PER-182",
            "passed": len(self.passed),
            "rejected": [r.as_dict() for r in self.rejected],
            "rejectedByReason": self.rejected_by_reason(),
            "limitations": sorted(self.limitations),
        }

    def rejected_by_reason(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in self.rejected:
            counts[row.reason] = counts.get(row.reason, 0) + 1
        return dict(sorted(counts.items()))


def identity_gate(
    record: dict,
    scope: IdentityScope,
    catalog,
    option_index: OptionIndex | None = None,
) -> tuple[GateDecision, str | None]:
    """리뷰 1건의 동일성 판정 → (`GateDecision`, 탈락 상세).

    판정 순서는 **좁은 것부터**다 — 제품이 다르면 세대·옵션을 볼 이유가 없고,
    사유가 하나로 정해져야 `rejected[]` 가 재현율의 단서가 된다.
    """
    if record["productId"] != scope.product_id:
        return (
            GateDecision(passed=False, reason=REJECT_PRODUCT),
            f"{record['productId']} != {scope.product_id}",
        )

    product = catalog.product(scope.product_id)
    review_date = record["raw"]["reviewDate"]

    decision = renewal_gate(product, review_date)
    if not decision.passed:
        return decision, f"{review_date} / 세대 {product.renewal_from_month}~{product.renewal_to_month}"

    limitation = decision.limitation
    recency = recency_gate(review_date)
    if not recency.passed:
        return GateDecision(passed=False, reason=REJECT_RECENCY), review_date

    if scope.option_key is None:
        return GateDecision(passed=True, limitation=limitation), None

    if option_index is None:
        raise ValueError(
            "옵션 범위를 걸었는데 OptionIndex 가 없다. 색상 판정은 코퍼스 어휘가 필요하다"
        )
    key, kind = option_index.shade_key(scope.product_id, record["raw"].get("option"))
    if kind == KIND_UNSTATED:
        return GateDecision(passed=False, reason=REJECT_OPTION_UNSTATED), None
    if kind != KIND_SHADE or key != scope.option_key:
        return GateDecision(passed=False, reason=REJECT_OPTION), f"{key!r} != {scope.option_key!r}"
    return GateDecision(passed=True, limitation=limitation), None


def run_identity_gate(
    records: list[dict],
    scope: IdentityScope,
    catalog,
    option_index: OptionIndex | None = None,
) -> GateResult:
    """레코드 묶음에 게이트1을 건다. 탈락분은 버리지 않고 `rejected[]` 로 돌려준다."""
    result = GateResult()
    for record in records:
        decision, detail = identity_gate(record, scope, catalog, option_index)
        if decision.passed:
            result.passed.append(record)
            if decision.limitation:
                result.limitations.add(decision.limitation)
        else:
            result.rejected.append(
                RejectedRow(
                    review_id=record["reviewId"],
                    gate=GATE_IDENTITY,
                    reason=decision.reason,
                    detail=detail,
                )
            )
    return result


__all__ = [
    "GATE_IDENTITY",
    "REJECT_LABELS",
    "REJECT_OPTION",
    "REJECT_OPTION_UNSTATED",
    "REJECT_PRODUCT",
    "GateResult",
    "IdentityScope",
    "RejectedRow",
    "UnknownOptionScopeError",
    "identity_gate",
    "run_identity_gate",
]
