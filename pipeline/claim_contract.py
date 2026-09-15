"""claim 출력 스키마 + 검증기 (PER-189).

PRD §6 — *"평가를 나중에 붙이려고 하면 안 붙는다. 스키마에 처음부터 넣는다."*

이 모듈이 정하는 것은 **무엇이 claim 1건인가**와 **누가 어느 필드를 채우는가**다.
생성기(PER-191)도, judge(PER-196)도, 화면(PER-202~204)도 이 스키마를 통해서만 만난다.

## 세 가지 강제

1. **`evidence[]` 가 비면 객체가 만들어지지 않는다.** 검증에서 걸러내는 게 아니라
   `Claim.__post_init__` 이 막는다 — 사후에 출처를 붙이는 게 아니라 출처 없이는
   문장이 존재할 수 없다 (PRD §1.2).
2. **모델이 쓰는 필드와 코드가 채우는 필드를 가른다.** `support` 수치(U+/U−/D/S)를
   모델이 쓰면 에러다. 침묵을 근거로 세지 않으려면 그 수를 코드가 세야 한다
   (PER-178). `direction` 도 인용된 근거가 아니라 **셀 전체의 고유 작성자**에서
   나오므로 모델의 것이 아니다.
3. **`failureReason` 이 그대로 노출 필터다.** `null` 인 것만 화면에 나간다.
   유형은 `failure_taxonomy.json`(PER-177) 8종이 정본이고 코드 상수로 들지 않는다.

## 이 모듈이 하지 않는 것

- **생성하지 않는다.** LLM 호출이 없다 (PER-191).
- **인용 원문성 100% 강제와 그 측정은 PER-190 이 소유한다.** 여기서는 원문을 넘겨받으면
  대조하고(`misattribution` 을 구조적으로 막는 수준), 안 넘기면 구조만 본다.
- **임계값을 확정하지 않는다.** `ConfidencePolicy` 기본값은 잠정이고 교정은 PER-200 이다.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).parent))

from codebook import load_codebook  # noqa: E402
from contracts import MISSING_SEGMENT  # noqa: E402
from golden_contract import (  # noqa: E402
    DIRECTIONS,
    STANCES,
    FailureTaxonomy,
    load_failure_taxonomy,
)
from reject_registry import assert_rejectable  # noqa: E402
from tag_contract import fold_invisible  # noqa: E402

CLAIM_SCHEMA_VERSION = "claim-v1"

# 조건축. `usagePeriod` 는 **비목표로 확정**됐다 (docs/V5_SPRINT_PLAN.md · PER-173·PER-187):
# 데이터에 필드가 없고 `isMonthUseReview`/`isMonthOverReview` 는 불리언 리뷰 종류지
# 기간이 아니다. 필드를 지우지 않고 **항상 null 로 남기는** 이유는, 지워 두면 다음 사람이
# "왜 없지" 하고 본문에서 추정해 채워 넣기 때문이다. 추정하면 조건축이 아니라 태거의
# 짐작이 세그먼트가 된다.
# 코드축의 값은 셋 중 하나다 — `None`(무관) · `[MISSING_SEGMENT]`(미기재 세그먼트) ·
# 코드 배열. 미기재를 `None` 과 합치면 "프로필을 안 밝힌 사람들"이 "모든 사람"이 된다.
CODED_AXES = ("skinType", "skinTrouble")
CONDITION_AXES = CODED_AXES + ("option", "usagePeriod")
NON_GOAL_AXES = ("usagePeriod",)

# 조건이 걸렸는가 하나로만 가른다. "리스크 질문 / 일반 질문" 같은 갈래는 질문–답 쌍을
# 만드는 이슈(PER-191)의 것이고, 여기서 미리 정하면 생성기가 그 칸에 맞춰 쓰게 된다.
CLAIM_TYPES = ("unconditional", "conditional")

# 단정 / 완곡. 소수점 점수를 두지 않는 이유는 §ConfidencePolicy 에 적었다.
CONFIDENCE_BANDS = ("assertive", "hedged")

# 모델이 쓰는 필드. 이 밖의 것을 초안에 담으면 에러다.
DRAFT_FIELDS = ("aspect", "question", "answer", "evidence")
DRAFT_EVIDENCE_FIELDS = ("reviewId", "quote", "stance")

CLAIM_FIELDS = (
    "schemaVersion", "claimId", "productId", "aspect", "question", "answer",
    "condition", "claimType", "direction", "evidence", "support",
    "rejected", "failureReasons", "failureReason", "confidence", "limitations", "meta",
)


class ClaimContractError(ValueError):
    """claim 이 출력 계약을 위반했다. 조용한 폴백 금지.

    `ValueError` 를 상속한다 — 호출부가 이미 `ValueError` 를 잡고 있어도 위반이
    통과한 claim 으로 둔갑하지 않게 하려는 것이다 (`gates.GateError` 와 같은 이유).
    """


@dataclass(frozen=True)
class ConfidencePolicy:
    """표현 강도를 정하는 규칙. **컷이 아니라 말투다** — 낮다고 버리지 않는다.

    소수점 점수(`0.73`)를 두지 않았다. 그 숫자를 만들 근거가 없고, 화면에 나가는
    순간 독자가 정밀도로 읽는다. 우리가 실제로 아는 것은 "이 주장은 갈렸다 / 소수가
    잡음 범위다 / 말한 사람이 적다" 같은 **이산적인 사실**이고, 그게 그대로 말투를
    정한다. 왜 완곡해졌는지는 `reasons[]` 에 남아 judge 와 화면이 같이 읽는다.

    기본값은 **잠정**이다. 교정은 PER-200(FP 우선 임계값 튜닝)의 몫이다.
    """
    # 말한 사람 중 침묵이 이 비율을 넘으면 완곡하게. "40명 중 3명이 언급" 을
    # 단정으로 쓰면 그게 PER-178 이 막으려던 일반화다.
    silent_ratio_hedge: float = 0.5
    # 방향을 말한 사람이 이보다 적으면 완곡하게. 게이트4 의 N_min 과 같은 수를 쓰되
    # 의미가 다르다 — 게이트4 는 통과/탈락이고 여기는 말투다.
    min_assertive_authors: int = 8

    def __post_init__(self) -> None:
        if not 0.0 < self.silent_ratio_hedge <= 1.0:
            raise ClaimContractError(
                f"silent_ratio_hedge 는 (0, 1] 이어야 한다: {self.silent_ratio_hedge!r}"
            )
        if self.min_assertive_authors < 1:
            raise ClaimContractError(
                f"min_assertive_authors 는 1 이상이어야 한다: {self.min_assertive_authors!r}"
            )


DEFAULT_CONFIDENCE = ConfidencePolicy()

# 완곡해지는 사유. 한계 코드(PER-185·186·188)를 그대로 받아 쓴다 — 여기서 새 어휘를
# 만들면 게이트가 남긴 한계와 화면의 말투가 서로 다른 말을 하게 된다.
HEDGE_MIXED = "direction_mixed"
HEDGE_SILENT = "silence_dominates"
HEDGE_FEW = "few_authors"
HEDGE_LIMITATION = "gate_limitation"


@dataclass(frozen=True)
class ClaimEvidence:
    """근거 1건 = 인용 1개. `stance` 는 **문장의 절대 긍/부정**이다 (골든셋 v2 규칙).

    주장 방향에 대한 찬반이 아니다 — "지속력이 별로" 는 주장이 무엇이든 `negative` 다.
    이 정의가 골든셋과 갈리면 judge 일치율이 생성 품질이 아니라 정의 차이를 잰다.
    """
    review_id: int
    quote: str
    stance: str

    def __post_init__(self) -> None:
        if not isinstance(self.review_id, int) or isinstance(self.review_id, bool):
            raise ClaimContractError(f"reviewId 는 정수여야 한다: {self.review_id!r}")
        if not isinstance(self.quote, str) or not self.quote.strip():
            raise ClaimContractError(f"인용이 비었다 (reviewId={self.review_id})")
        if self.stance not in STANCES:
            raise ClaimContractError(
                f"stance 는 {STANCES} 중 하나여야 한다: {self.stance!r} "
                "— 문장의 절대 긍/부정이지 주장에 대한 찬반이 아니다"
            )

    def as_dict(self) -> dict:
        return {"reviewId": self.review_id, "quote": self.quote, "stance": self.stance}


@dataclass(frozen=True)
class Claim:
    """claim 1건. **`evidence` 가 비면 이 객체는 만들어지지 않는다.**

    검증 함수가 나중에 걸러내는 방식이 아니다. 걸러내는 방식이면 "일단 만들고 나중에
    출처를 붙인다" 가 가능해지고, 그 순간 PRD §1.2 가 막으려던 것이 그대로 일어난다.
    """
    claim_id: str
    product_id: str
    aspect: str
    question: str
    answer: str
    condition: Mapping[str, object]
    direction: str
    evidence: tuple[ClaimEvidence, ...]
    support: Mapping[str, int]
    rejected: tuple[Mapping[str, object], ...] = ()
    failure_reasons: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    meta: Mapping[str, object] = field(default_factory=dict)
    confidence_policy: ConfidencePolicy = DEFAULT_CONFIDENCE

    def __post_init__(self) -> None:
        if not self.evidence:
            raise ClaimContractError(
                f"[{self.claim_id}] evidence 가 비었다 — claim 객체를 만들 수 없다 (PRD §1.2).\n"
                "  → 근거 없이 문장을 먼저 만들고 출처를 나중에 붙이는 경로를 구조에서 막는다"
            )
        for text, name in ((self.question, "question"), (self.answer, "answer"),
                           (self.aspect, "aspect"), (self.product_id, "productId"),
                           (self.claim_id, "claimId")):
            if not isinstance(text, str) or not text.strip():
                raise ClaimContractError(f"[{self.claim_id}] {name} 이 비었다")
        if self.direction not in DIRECTIONS:
            raise ClaimContractError(
                f"[{self.claim_id}] direction 은 {DIRECTIONS} 중 하나여야 한다: {self.direction!r}"
            )
        seen = [e.review_id for e in self.evidence]
        dup = {r for r in seen if seen.count(r) > 1}
        if dup:
            raise ClaimContractError(
                f"[{self.claim_id}] 같은 리뷰를 근거로 두 번 세고 있다: {sorted(dup)} "
                "— 근거는 고유 작성자 수로 세므로 한 리뷰는 한 번이다 (PER-170)"
            )
        _assert_condition(self.claim_id, self.condition)
        _assert_support(self.claim_id, self.support)

    # --- 코드가 파생시키는 값 (모델이 쓰지 않는다) ---------------------------

    @property
    def claim_type(self) -> str:
        scoped = any(
            self.condition.get(axis) not in (None, (), [])
            for axis in CONDITION_AXES if axis not in NON_GOAL_AXES
        )
        return "conditional" if scoped else "unconditional"

    @property
    def failure_reason(self) -> str | None:
        """대표 사유 — 정렬된 `failureReasons` 의 첫 원소. 없으면 `None`.

        `None` 은 "평가 완료 후 실패 없음" 이지 아홉 번째 유형이 아니다
        (`failure_taxonomy.json` `_meta.rule`).
        """
        return self.failure_reasons[0] if self.failure_reasons else None

    @property
    def exposable(self) -> bool:
        """화면에 나가는가. **`failureReason` 이 그대로 노출 필터다** (PER-189 완료 조건)."""
        return self.failure_reason is None

    @property
    def confidence(self) -> dict:
        """단정할지 완곡할지와 **그 사유**. 사유를 같이 내는 게 요점이다."""
        pol = self.confidence_policy
        reasons: list[str] = []
        if self.direction == "mixed":
            reasons.append(HEDGE_MIXED)
        spoke = self.support.get("spokeAuthors", 0)
        silent = self.support.get("silentAuthors", 0)
        cell = spoke + silent
        if cell and silent / cell >= pol.silent_ratio_hedge:
            reasons.append(HEDGE_SILENT)
        if self.support.get("supportAuthors", 0) < pol.min_assertive_authors:
            reasons.append(HEDGE_FEW)
        if self.limitations:
            reasons.append(HEDGE_LIMITATION)
        return {
            "band": "hedged" if reasons else "assertive",
            "reasons": reasons,
            "inputs": {
                "direction": self.direction,
                "supportAuthors": self.support.get("supportAuthors", 0),
                "spokeAuthors": spoke,
                "silentAuthors": silent,
                "limitations": list(self.limitations),
            },
            "policy": {
                "silentRatioHedge": pol.silent_ratio_hedge,
                "minAssertiveAuthors": pol.min_assertive_authors,
                "note": "잠정값. 교정은 PER-200 (FP 우선 임계값 튜닝)",
            },
        }

    def as_dict(self) -> dict:
        return {
            "schemaVersion": CLAIM_SCHEMA_VERSION,
            "claimId": self.claim_id,
            "productId": self.product_id,
            "aspect": self.aspect,
            "question": self.question,
            "answer": self.answer,
            "condition": {axis: self.condition.get(axis) for axis in CONDITION_AXES},
            "claimType": self.claim_type,
            "direction": self.direction,
            "evidence": [e.as_dict() for e in self.evidence],
            "support": dict(self.support),
            "rejected": [dict(r) for r in self.rejected],
            "failureReasons": list(self.failure_reasons),
            "failureReason": self.failure_reason,
            "confidence": self.confidence,
            "limitations": list(self.limitations),
            "meta": dict(self.meta),
        }


def _assert_condition(claim_id: str, condition: Mapping[str, object]) -> None:
    unknown = set(condition) - set(CONDITION_AXES)
    if unknown:
        raise ClaimContractError(
            f"[{claim_id}] 모르는 조건축: {sorted(unknown)}. 쓸 수 있는 축: {list(CONDITION_AXES)}"
        )
    for axis in NON_GOAL_AXES:
        if condition.get(axis) is not None:
            raise ClaimContractError(
                f"[{claim_id}] {axis} 는 조건축이 아니다 — 데이터에 필드가 없다. "
                "본문에서 기간을 추정하면 조건이 아니라 짐작이 세그먼트가 된다 "
                "(docs/V5_SPRINT_PLAN.md · PER-187 §3). 값은 항상 null 이다"
            )
    book = load_codebook()
    for axis in CODED_AXES:
        codes = condition.get(axis)
        if codes is None:
            continue
        if isinstance(codes, str):
            raise ClaimContractError(
                f"[{claim_id}] {axis} 는 배열이어야 한다 (받은 값: {codes!r}). "
                "문자열 하나를 주면 글자 단위로 쪼개져 조용히 다른 세그먼트가 된다 — "
                f"미기재 세그먼트도 ['{MISSING_SEGMENT}'] 로 준다"
            )
        # `null`(무관) 과 `"미기재"`(세그먼트) 는 다르다 (CLAUDE.md · PER-178 규격 §3).
        # null 은 "이 주장은 그 축과 무관하다", 미기재는 "프로필을 안 밝힌 리뷰들" 이다.
        # 둘을 합치면 미기재가 '모든 조건' 으로 둔갑한다 — 코드와 섞는 것도 같은 이유로 막는다.
        if MISSING_SEGMENT in codes:
            if len(codes) > 1:
                raise ClaimContractError(
                    f"[{claim_id}] {axis} 가 '{MISSING_SEGMENT}' 와 코드를 함께 담고 있다: "
                    f"{list(codes)!r} — 미기재는 '모든 조건' 이 아니라 별개 세그먼트다"
                )
            continue
        for code in codes:
            # 도메인 판정을 여기서 다시 구현하지 않는다 — 코드북이 소유한다 (PER-176).
            # 라벨('건성')이나 축 혼용(skinType 에 B03)은 조용히 새 세그먼트가 되지 않는다.
            try:
                book.assert_code(axis, code)
            except Exception as exc:
                raise ClaimContractError(f"[{claim_id}] 조건 코드가 계약을 위반했다 — {exc}") from None


def _assert_support(claim_id: str, support: Mapping[str, int]) -> None:
    """`support` 는 코드가 센 것이어야 한다. 모양이 맞는지만 여기서 본다.

    분모를 셀 크기(S)가 아니라 **말한 사람(D)** 으로 두는 것이 PER-178 결정이고,
    `silentAuthors` 는 지우지 않고 따로 싣는다 — 이 수가 크면 뒷단이 "대부분 괜찮다"
    같은 일반화를 못 하게 하는 근거가 된다.
    """
    required = ("positiveAuthors", "negativeAuthors", "neutralAuthors",
                "spokeAuthors", "silentAuthors", "supportAuthors")
    missing = [k for k in required if k not in support]
    if missing:
        raise ClaimContractError(
            f"[{claim_id}] support 에 {missing} 가 없다 — 코드가 세어 넣어야 하는 값이다 "
            "(LLM 이 쓰지 않는다, PER-178)"
        )
    for key in required:
        value = support[key]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ClaimContractError(f"[{claim_id}] support.{key} 는 0 이상의 정수여야 한다: {value!r}")
    spoke = support["spokeAuthors"]
    parts = support["positiveAuthors"] + support["negativeAuthors"] + support["neutralAuthors"]
    if parts != spoke:
        raise ClaimContractError(
            f"[{claim_id}] spokeAuthors({spoke}) 가 "
            f"긍정+부정+중립({parts}) 과 다르다 — D 는 그 주제를 말한 작성자의 합이다"
        )
    if support["supportAuthors"] > spoke:
        raise ClaimContractError(
            f"[{claim_id}] supportAuthors({support['supportAuthors']}) 가 "
            f"spokeAuthors({spoke}) 보다 크다 — U 는 D 의 부분집합이다"
        )


def assert_model_draft(draft: Mapping[str, object]) -> None:
    """모델 초안이 **자기 몫만** 담고 있는가. 넘어오면 에러다.

    `support`·`direction`·`confidence`·`failureReason` 을 모델이 쓰면, 침묵을 세지
    않는다는 규칙(PER-178)과 방향 정의(PER-185)가 모델의 재량이 된다. 그 순간
    골든셋과 대조할 수 있는 것이 사라진다.
    """
    if not isinstance(draft, Mapping):
        raise ClaimContractError(f"초안은 객체여야 한다: {type(draft).__name__}")
    extra = set(draft) - set(DRAFT_FIELDS)
    if extra:
        raise ClaimContractError(
            f"모델 초안에 코드가 채울 필드가 들어 있다: {sorted(extra)}.\n"
            f"  → 모델이 쓰는 것은 {list(DRAFT_FIELDS)} 뿐이다. support 수치는 코드가 세고"
            " (PER-178), direction 은 셀 전체의 고유 작성자에서 나온다 (PER-185)"
        )
    missing = [f for f in DRAFT_FIELDS if f not in draft]
    if missing:
        raise ClaimContractError(f"모델 초안에 {missing} 가 없다")
    evidence = draft["evidence"]
    if not isinstance(evidence, Sequence) or isinstance(evidence, (str, bytes)) or not evidence:
        raise ClaimContractError(
            "모델 초안의 evidence 가 비었다 — 근거 없는 문장은 claim 이 되지 못한다 (PRD §1.2)"
        )
    for item in evidence:
        unknown = set(item) - set(DRAFT_EVIDENCE_FIELDS)
        if unknown:
            raise ClaimContractError(f"초안 evidence 에 모르는 필드: {sorted(unknown)}")


def assert_quotes_verbatim(claim: Claim, reviews: Mapping[int, str]) -> None:
    """인용이 **그 리뷰의** 원문 부분문자열인가.

    두 가지를 동시에 막는다 — 원문에 없는 문장(`unsupported_claim`)과, 있긴 한데
    다른 리뷰의 것(`misattribution`). 후자는 인용만 보면 통과하므로 reviewId 와
    짝지어 봐야 잡힌다.

    대조 전에 **보이지 않는 문자만** 접는다 (`tag_contract.fold_invisible`). 원문에
    CRLF 46.3% · Zs 공백 변종 1.1% 가 섞여 있어 접지 않으면 정상 인용이 탈락하고,
    공백을 전부 squeeze 하면 띄어쓰기를 지운 편집까지 통과한다 (PER-175).

    **100% 강제와 그 측정은 PER-190 의 몫이다.** 여기서는 원문을 받은 경우에만 본다.
    """
    for e in claim.evidence:
        if e.review_id not in reviews:
            raise ClaimContractError(
                f"[{claim.claim_id}] 근거가 가리키는 리뷰가 코퍼스에 없다: {e.review_id}"
            )
        if fold_invisible(e.quote) not in fold_invisible(reviews[e.review_id]):
            raise ClaimContractError(
                f"[{claim.claim_id}] 인용이 리뷰 {e.review_id} 의 원문 부분문자열이 아니다.\n"
                f"  인용: {e.quote[:60]!r}\n"
                "  → 요약·재구성은 인용이 아니다. 다른 리뷰의 문장이면 misattribution 이다"
            )


def validate_claim(
    payload: Mapping[str, object],
    reviews: Mapping[int, str] | None = None,
    taxonomy: FailureTaxonomy | None = None,
    policy: ConfidencePolicy = DEFAULT_CONFIDENCE,
) -> Claim:
    """직렬화된 claim 1건을 계약대로 되읽는다. 위반은 전부 에러다.

    `reviews` 를 주면 인용 원문성까지 본다. 파이프라인 밖에서도 단독으로 돌아야 해서
    (평가·CI 재사용, PER-189 완료 조건) 입력은 평범한 dict 다.
    """
    if not isinstance(payload, Mapping):
        raise ClaimContractError(f"claim 은 객체여야 한다: {type(payload).__name__}")
    unknown = set(payload) - set(CLAIM_FIELDS)
    if unknown:
        raise ClaimContractError(f"모르는 필드: {sorted(unknown)}. 스키마: {list(CLAIM_FIELDS)}")
    version = payload.get("schemaVersion")
    if version != CLAIM_SCHEMA_VERSION:
        raise ClaimContractError(
            f"schemaVersion 이 다르다: {version!r} (기대 {CLAIM_SCHEMA_VERSION!r}) — "
            "스키마가 바뀌면 판정 수치가 어느 스키마의 것인지 모르게 된다"
        )

    tax = taxonomy or load_failure_taxonomy()
    reasons = payload.get("failureReasons") or []
    if isinstance(reasons, str):
        raise ClaimContractError("failureReasons 는 배열이어야 한다 (문자열 하나가 아니다)")
    for key in reasons:
        if key not in tax.keys:
            raise ClaimContractError(
                f"택소노미 밖 실패 유형: {key!r}. 8유형은 {list(tax.keys)} 이고 "
                "pipeline/failure_taxonomy.json 이 정본이다 (PER-177)"
            )
    ordered = tuple(tax.sort(list(reasons)))
    if tuple(reasons) != ordered:
        raise ClaimContractError(
            f"failureReasons 가 심각도 순이 아니다: {list(reasons)} → {list(ordered)}. "
            "대표 사유(failureReason)가 첫 원소여야 한다"
        )
    declared = payload.get("failureReason", None)
    expected = ordered[0] if ordered else None
    if declared != expected:
        raise ClaimContractError(
            f"failureReason({declared!r}) 이 failureReasons 의 첫 원소({expected!r})와 다르다"
        )

    for row in payload.get("rejected") or []:
        assert_rejectable(row.get("gate"), row.get("reason"))

    claim = Claim(
        claim_id=payload.get("claimId"),
        product_id=payload.get("productId"),
        aspect=payload.get("aspect"),
        question=payload.get("question"),
        answer=payload.get("answer"),
        condition=payload.get("condition") or {},
        direction=payload.get("direction"),
        evidence=tuple(
            ClaimEvidence(e.get("reviewId"), e.get("quote"), e.get("stance"))
            for e in (payload.get("evidence") or [])
        ),
        support=payload.get("support") or {},
        rejected=tuple(payload.get("rejected") or []),
        failure_reasons=ordered,
        limitations=tuple(payload.get("limitations") or []),
        meta=payload.get("meta") or {},
        confidence_policy=policy,
    )
    declared_type = payload.get("claimType")
    if declared_type is not None and declared_type != claim.claim_type:
        raise ClaimContractError(
            f"[{claim.claim_id}] claimType({declared_type!r}) 이 조건과 맞지 않는다 "
            f"(조건으로 계산하면 {claim.claim_type!r}) — 파생값을 손으로 쓰지 않는다"
        )
    if reviews is not None:
        assert_quotes_verbatim(claim, reviews)
    return claim


MAX_RETRIES = 2


def attempt_claim(
    produce: Callable[[int], Mapping[str, object]],
    reviews: Mapping[int, str] | None = None,
    max_retries: int = MAX_RETRIES,
    log: Callable[[str], None] | None = None,
) -> Claim | None:
    """검증을 통과할 때까지 **최대 2회 더** 부르고, 안 되면 버린다 (PRD §5-3).

    버린 사실을 남기는 게 요점이다 — 조용히 `None` 을 돌려주면 "생성이 안 된 것"과
    "검증에 걸린 것"이 화면에서 구별되지 않는다. 기본값은 아무것도 안 보여주는 것이고
    (PRD §5-3), 왜 안 보이는지는 로그에 남는다.
    """
    if max_retries < 0:
        raise ClaimContractError(f"max_retries 는 0 이상이어야 한다: {max_retries!r}")
    emit = log or (lambda message: print(message, file=sys.stderr))
    for attempt in range(max_retries + 1):
        payload = produce(attempt)
        try:
            return validate_claim(payload, reviews=reviews)
        except (ClaimContractError, ValueError) as exc:
            emit(f"[claim] {attempt + 1}/{max_retries + 1} 회차 폐기 — {exc}")
    emit(f"[claim] {max_retries + 1}회 모두 실패해 생성하지 않는다 (PRD §5-3)")
    return None


def validate_file(path: Path, reviews_path: Path | None = None) -> dict:
    """파일 1개를 단독 검증한다 — 파이프라인 밖(평가·CI)에서 재사용하는 진입점."""
    reviews = None
    if reviews_path is not None:
        reviews = {}
        for line in reviews_path.read_text().splitlines():
            if line.strip():
                row = json.loads(line)
                reviews[row["reviewId"]] = row["raw"]["content"]
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    claims = [validate_claim(r, reviews=reviews) for r in rows]
    exposable = [c for c in claims if c.exposable]
    return {
        "path": str(path),
        "claims": len(claims),
        "exposable": len(exposable),
        "withdrawn": len(claims) - len(exposable),
        "quotesChecked": reviews is not None,
        "byType": {t: sum(1 for c in claims if c.claim_type == t) for t in CLAIM_TYPES},
        "byBand": {b: sum(1 for c in claims if c.confidence["band"] == b) for b in CONFIDENCE_BANDS},
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="claim 출력 스키마 검증기 (PER-189)")
    ap.add_argument("path", type=Path, help="claim JSONL")
    ap.add_argument("--reviews", type=Path, default=None,
                    help="v5_reviews.jsonl — 주면 인용 원문성까지 본다")
    args = ap.parse_args()
    try:
        summary = validate_file(args.path, args.reviews)
    except (ClaimContractError, ValueError) as exc:
        raise SystemExit(f"FAIL: {exc}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"OK: claim {summary['claims']}건 계약 통과 "
          f"(노출 {summary['exposable']} · 보류 {summary['withdrawn']})")


if __name__ == "__main__":
    main()
