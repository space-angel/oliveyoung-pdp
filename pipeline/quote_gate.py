"""인용 원문성 게이트 (PER-190).

CLAUDE.md 서술 규칙 — *"concern의 인용문은 **원문 부분문자열이어야 한다.** v4에서
88.8%였고, 원문에 없는 수치를 생성한 사례가 있었다. 요약·재구성은 인용이 아니다."*

v4 는 이것을 **사후 평가 지표**로 뒀다 (`eval_report_v4.md` §5 "참고 지표"). 사후
지표는 수치를 알려줄 뿐 아무것도 막지 못한다 — 88.8% 를 알고도 "14시간 넘게 있는데
수정화장 안함"(원문에 없는 수치)이 그대로 출력에 실려 나갔다. 그래서 v5 는 같은 판정을
**생성 게이트**로 옮긴다. 이 모듈이 그 게이트다.

## 판정 규칙은 하나다 — `claim_contract` 와 같은 것

통과 기준은 `fold_invisible(quote) in fold_invisible(그 리뷰의 원문)` 이고, 이것은
`claim_contract.assert_quotes_verbatim` 이 쓰는 기준과 **글자 그대로 같다.** 여기서
따로 느슨한 기준을 들면 게이트를 통과한 claim 이 계약 검증에서 터진다. 그 일치는
`test_quote_gate.GateMatchesClaimContract` 가 양방향으로 고정한다.

## 정규화 규칙 — 표기 차이와 파라프레이즈를 가른다

**접는 것은 보이지 않는 문자뿐이다** (`tag_contract.fold_invisible`, PER-175).
원문 25,000건에 CRLF 46.3% · Zs 공백 변종 1.1% 가 섞여 있어 접지 않으면 정상 인용이
탈락한다. 글자 수와 띄어쓰기 수는 그대로 남는다.

**공백 전체 squeeze 는 통과 기준이 될 수 없다** (CLAUDE.md). v4 는 그렇게 쟀다 —
`re.sub(r"\\s+", "", text)` (`legacy/v4/eval/eval_v4.py:_normalize`). 띄어쓰기를 지운
편집까지 통과시키고, 실제로 v4 인용 143개 중 3개가 그 덕에 통과했다
(`eval/reports/quote_fidelity_per190.json` `v4.divergence`).

그래서 실패한 인용은 **버리되, 왜 실패했는지는 등급으로 분류한다.** 등급은 통과를
바꾸지 않는다 — 원인을 되짚기 위한 것이다. 프롬프트 문제(파라프레이즈)와 근거 연결
문제(misattribution)는 고치는 곳이 다르다.

| 등급 | 무엇인가 | 통과 |
|---|---|---|
| `verbatim` | 인용한 리뷰의 원문 부분문자열 | **O** |
| `linebreak_only` | 줄바꿈을 공백으로 옮겨 적은 것 외에 차이가 없다 | X |
| `spacing_edited` | 띄어쓰기를 지우거나 넣어야 맞는다 | X |
| `misattributed` | 원문이긴 한데 **다른 리뷰**의 것이다 | X |
| `fabricated` | 코퍼스 어디에도 없다 — 요약·재구성·지어낸 수치 | X |
| `unknown_review` | 인용이 가리키는 리뷰가 코퍼스에 없다 | X |

`linebreak_only` 를 통과시키지 않는 것은 **보수적인 선택이고 비용이 있다** — v4 인용
3건이 여기 해당한다. 통과시키려면 `fold_invisible` 을 바꿔야 하고, 그 함수는 태깅
입력·중복 판정·게이트3 이 함께 쓴다. 규칙을 한 이슈에서 조용히 넓히지 않는다.

## 실패하면 무엇을 하는가 — 폐기 → 대체 → 주장 폐기

PER-190 완료 조건이 정한 순서 그대로다.

1. 실패한 인용을 **버린다**. 고치지 않는다 — 인용을 고치는 것은 인용이 아니다
2. 같은 주장의 **다른 근거로 대체한다** (`alternates`). 대체 후보도 같은 게이트를 통과해야 한다
3. 그래도 `min_evidence` 를 못 채우면 **주장 자체를 버린다.** `evidence` 가 비면
   `Claim` 객체는 애초에 만들어지지 않는다 (PER-189) — 여기서 `None` 을 돌려주는 것이
   그 구조와 같은 말이다

**버린 사실은 반드시 남는다.** 폐기·대체·주장폐기가 전부 `QuoteGateResult.ledger` 행과
로그 한 줄로 남는다. 조용히 줄어들면 "생성이 안 된 것"과 "게이트에 걸린 것"이
구별되지 않고, 그 순간 재현율을 영영 못 잰다 (PRD §6).

## `claim.rejected[]` 에 넣지 않는 이유 — 어휘가 아직 없다

`reject_registry`(PER-188)에 인용 원문성 사유가 등록돼 있지 않다. 등록되지 않은 사유로
`rejected[]` 행을 만들면 `assert_rejectable` 이 멈춘다. 그게 맞는 동작이다 — 없는
어휘를 이 이슈에서 조용히 만들면 게이트별 사유 분포가 거짓이 된다.

그래서 이 모듈은 **자기 원장(`ledger`)과 로그**에 남기고, 레지스트리 등록은 PER-190
범위 밖으로 넘긴다. 등록이 필요한 항목은 `LEDGER_REASONS` 에 그대로 적혀 있다 —
그대로 `reject_registry` 로 옮기면 된다.

## 이 모듈이 하지 않는 것

- **생성하지 않는다.** LLM 호출이 없다 (생성기는 PER-191).
- **인용을 고치지 않는다.** 가장 비슷한 원문으로 갈아끼우는 자동 교정은 하지 않는다 —
  모델이 하려던 말과 다른 문장이 근거로 남는다.
- **측정하지 않는다.** 수치는 `eval/measure_quote_fidelity.py` 가 낸다.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).parent))

from tag_contract import fold_invisible  # noqa: E402

QUOTE_GATE = "quote"
QUOTE_GATE_ISSUE = "PER-190"

# --- 판정 등급 --------------------------------------------------------------

STATUS_VERBATIM = "verbatim"
STATUS_LINEBREAK_ONLY = "linebreak_only"
STATUS_SPACING_EDITED = "spacing_edited"
STATUS_MISATTRIBUTED = "misattributed"
STATUS_FABRICATED = "fabricated"
STATUS_UNKNOWN_REVIEW = "unknown_review"

STATUSES: tuple[str, ...] = (
    STATUS_VERBATIM,
    STATUS_LINEBREAK_ONLY,
    STATUS_SPACING_EDITED,
    STATUS_MISATTRIBUTED,
    STATUS_FABRICATED,
    STATUS_UNKNOWN_REVIEW,
)

#: **통과는 하나뿐이다.** 튜플로 둔 것은 나중에 늘리라는 뜻이 아니라, 통과 집합이
#: 코드 한 곳에만 있다는 사실을 드러내려는 것이다.
PASSING_STATUSES: tuple[str, ...] = (STATUS_VERBATIM,)

STATUS_LABELS: dict[str, str] = {
    STATUS_VERBATIM: "원문일치",
    STATUS_LINEBREAK_ONLY: "줄바꿈표기차",
    STATUS_SPACING_EDITED: "띄어쓰기편집",
    STATUS_MISATTRIBUTED: "출처불일치",
    STATUS_FABRICATED: "재구성",
    STATUS_UNKNOWN_REVIEW: "리뷰없음",
}

# --- 원장 사유 --------------------------------------------------------------
#
# `reject_registry`(PER-188) 에 아직 등록되지 않은 어휘다 (모듈 문서 §`rejected[]`).
# 등록할 때 이 표를 그대로 옮긴다 — 코드·단위·소유 모듈이 이미 그 형식이다.

REASON_NOT_VERBATIM = "quote_not_verbatim"
REASON_MISATTRIBUTED = "quote_misattributed"
REASON_NO_EVIDENCE_LEFT = "no_verbatim_evidence"

UNIT_EVIDENCE = "evidence"
UNIT_CLAIM = "claim"

LEDGER_REASONS: dict[str, dict[str, str]] = {
    REASON_NOT_VERBATIM: {
        "gate": QUOTE_GATE,
        "label": "인용불일치",
        "unit": UNIT_EVIDENCE,
        "owner": "pipeline/quote_gate.py",
        "note": "인용이 그 리뷰의 원문 부분문자열이 아니다. 요약·재구성은 인용이 아니다",
    },
    REASON_MISATTRIBUTED: {
        "gate": QUOTE_GATE,
        "label": "출처불일치",
        "unit": UNIT_EVIDENCE,
        "owner": "pipeline/quote_gate.py",
        "note": "원문이긴 한데 인용이 가리킨 리뷰의 것이 아니다. 인용만 보면 통과하므로 "
                "reviewId 와 짝지어 봐야 잡힌다",
    },
    REASON_NO_EVIDENCE_LEFT: {
        "gate": QUOTE_GATE,
        "label": "근거소진",
        "unit": UNIT_CLAIM,
        "owner": "pipeline/quote_gate.py",
        "note": "인용을 버리고 대체까지 했는데 min_evidence 를 못 채웠다 — 주장을 버린다",
    },
}

# --- 결과 등급 --------------------------------------------------------------

OUTCOME_CLEAN = "clean"
OUTCOME_REDUCED = "reduced"
OUTCOME_SUBSTITUTED = "substituted"
OUTCOME_DISCARDED = "discarded"
OUTCOMES: tuple[str, ...] = (OUTCOME_CLEAN, OUTCOME_REDUCED, OUTCOME_SUBSTITUTED,
                             OUTCOME_DISCARDED)


class QuoteGateError(ValueError):
    """게이트 설정이나 입력이 계약을 위반했다.

    `ValueError` 를 상속한다 — 호출부가 이미 `ValueError` 를 잡고 있어도 위반이 통과
    결과로 둔갑하지 않게 하려는 것이다 (`claim_contract.ClaimContractError` 와 같은 이유).
    """


# --- 정규화 -----------------------------------------------------------------

def fold_for_match(text: str) -> str:
    """대조에 쓰는 정본 정규화. **통과 판정은 이것 하나로만 한다.**

    `tag_contract.fold_invisible` 을 그대로 부른다 — 여기서 다시 구현하면 태깅 입력과
    대조 규칙이 조용히 갈린다 (PER-175 가 같은 함수를 쓰라고 정한 이유).
    """
    return fold_invisible(text)


def _fold_linebreaks(text: str) -> str:
    """**진단 등급 전용.** 줄바꿈을 공백으로 옮겨 적은 표기차를 알아보기 위한 것이다.

    통과 기준이 아니다. 여기를 통과 기준으로 쓰면 `fold_invisible` 이 두 벌이 되고,
    이 게이트를 통과한 claim 이 `claim_contract` 에서 터진다.
    """
    return re.sub(r"[ \n]+", " ", fold_for_match(text)).strip()


def _squeeze(text: str) -> str:
    """**진단 등급 전용.** v4 가 통과 기준으로 쓴 것 (`eval_v4.py:_normalize`).

    띄어쓰기를 지운 편집까지 통과시키므로 우리 통과 기준이 될 수 없다 (CLAUDE.md).
    실패 원인을 "표기 차이 쪽"과 "재구성 쪽"으로 가르는 데만 쓴다.
    """
    return "".join(fold_for_match(text).split())


# --- 판정 -------------------------------------------------------------------

@dataclass(frozen=True)
class QuoteVerdict:
    """인용 1개의 판정. `status` 가 `verbatim` 이 아니면 그 인용은 버려진다."""
    review_id: int
    quote: str
    status: str
    matched_review_id: int | None = None

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise QuoteGateError(f"모르는 판정 등급 {self.status!r} (등록: {STATUSES})")

    @property
    def ok(self) -> bool:
        return self.status in PASSING_STATUSES

    @property
    def reason(self) -> str | None:
        """원장에 남길 사유. 통과면 없다."""
        if self.ok:
            return None
        return (REASON_MISATTRIBUTED if self.status == STATUS_MISATTRIBUTED
                else REASON_NOT_VERBATIM)

    def as_dict(self) -> dict:
        return {
            "reviewId": self.review_id,
            "quote": self.quote,
            "status": self.status,
            "statusLabel": STATUS_LABELS[self.status],
            "matchedReviewId": self.matched_review_id,
            "reason": self.reason,
        }


def classify_quote(
    quote: str,
    review_id: int,
    reviews: Mapping[int, str],
    corpus: Mapping[int, str] | None = None,
) -> QuoteVerdict:
    """인용 1개를 판정한다. **인용한 리뷰를 기준으로 먼저 본다.**

    `corpus` 는 misattribution 을 잡기 위한 탐색 범위다 (기본값: `reviews`). 다른
    리뷰의 문장을 그대로 옮겨 적은 경우는 인용만 보면 통과하므로, reviewId 와 짝지어
    보고 나서 코퍼스를 뒤져야 "지어낸 것"과 "출처를 잘못 붙인 것"이 갈린다.

    등급 순서는 **인용한 리뷰 안의 표기 차이 → 코퍼스 안의 다른 리뷰** 다. 같은 인용이
    인용한 리뷰에서는 줄바꿈 표기차이고 다른 리뷰에서는 정확히 일치할 수 있는데,
    그때 고쳐야 할 것은 표기지 출처가 아니다.
    """
    if not isinstance(quote, str) or not quote.strip():
        raise QuoteGateError(f"인용이 비었다 (reviewId={review_id})")
    if review_id not in reviews:
        return QuoteVerdict(review_id, quote, STATUS_UNKNOWN_REVIEW)

    source = reviews[review_id]
    folded = fold_for_match(quote)
    if folded in fold_for_match(source):
        return QuoteVerdict(review_id, quote, STATUS_VERBATIM)
    if _fold_linebreaks(quote) in _fold_linebreaks(source):
        return QuoteVerdict(review_id, quote, STATUS_LINEBREAK_ONLY)
    if _squeeze(quote) in _squeeze(source):
        return QuoteVerdict(review_id, quote, STATUS_SPACING_EDITED)

    pool = reviews if corpus is None else corpus
    for other_id, content in pool.items():
        if other_id != review_id and folded in fold_for_match(content):
            return QuoteVerdict(review_id, quote, STATUS_MISATTRIBUTED, other_id)
    return QuoteVerdict(review_id, quote, STATUS_FABRICATED)


# --- 게이트 -----------------------------------------------------------------

@dataclass(frozen=True)
class QuoteGatePolicy:
    """게이트 설정. **조건을 조용히 끄는 값은 에러다** (`SufficiencyPolicy` 와 같은 규칙).

    `min_evidence = 0` 은 "근거가 하나도 안 남아도 주장을 낸다" 는 뜻이고, 그건 게이트를
    끈 것이다. 끄고 싶으면 게이트를 부르지 않으면 된다 — 설정으로 숨기지 않는다.
    """
    min_evidence: int = 1
    #: 대체를 몇 개까지 허용할지. `None` 이면 폐기한 만큼 채운다.
    max_substitutions: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.min_evidence, int) or isinstance(self.min_evidence, bool):
            raise QuoteGateError(f"min_evidence 는 정수여야 한다: {self.min_evidence!r}")
        if self.min_evidence < 1:
            raise QuoteGateError(
                f"min_evidence 는 1 이상이어야 한다: {self.min_evidence!r} — "
                "0 은 근거 없이 주장을 내겠다는 설정이고, 그건 게이트를 끈 것이다 (PRD §1.2)"
            )
        if self.max_substitutions is not None and self.max_substitutions < 0:
            raise QuoteGateError(
                f"max_substitutions 는 0 이상이거나 None 이어야 한다: {self.max_substitutions!r}"
            )

    def as_dict(self) -> dict:
        return {"minEvidence": self.min_evidence, "maxSubstitutions": self.max_substitutions}


DEFAULT_POLICY = QuoteGatePolicy()


@dataclass(frozen=True)
class QuoteGateResult:
    """게이트 1회분의 결과. **버린 것과 대체한 것이 전부 여기 남는다.**"""
    claim_id: str
    outcome: str
    kept: tuple[Mapping[str, object], ...]
    verdicts: tuple[QuoteVerdict, ...]
    dropped: tuple[QuoteVerdict, ...]
    substituted: tuple[QuoteVerdict, ...]
    ledger: tuple[Mapping[str, object], ...]
    log: tuple[str, ...] = field(default=())

    @property
    def discarded(self) -> bool:
        return self.outcome == OUTCOME_DISCARDED

    def as_dict(self) -> dict:
        return {
            "claimId": self.claim_id,
            "gate": QUOTE_GATE,
            "outcome": self.outcome,
            "kept": [dict(e) for e in self.kept],
            "verdicts": [v.as_dict() for v in self.verdicts],
            "dropped": [v.as_dict() for v in self.dropped],
            "substituted": [v.as_dict() for v in self.substituted],
            "ledger": [dict(r) for r in self.ledger],
            "log": list(self.log),
        }


def _evidence_parts(item: Mapping[str, object]) -> tuple[int, str]:
    if not isinstance(item, Mapping):
        raise QuoteGateError(f"근거는 객체여야 한다: {type(item).__name__}")
    review_id = item.get("reviewId")
    if not isinstance(review_id, int) or isinstance(review_id, bool):
        raise QuoteGateError(f"근거의 reviewId 가 정수가 아니다: {review_id!r}")
    quote = item.get("quote")
    if not isinstance(quote, str) or not quote.strip():
        raise QuoteGateError(f"근거의 인용이 비었다 (reviewId={review_id})")
    return review_id, quote


def enforce_evidence(
    claim_id: str,
    evidence: Sequence[Mapping[str, object]],
    reviews: Mapping[int, str],
    alternates: Iterable[Mapping[str, object]] = (),
    policy: QuoteGatePolicy = DEFAULT_POLICY,
    corpus: Mapping[int, str] | None = None,
    log: Callable[[str], None] | None = None,
) -> QuoteGateResult:
    """근거 목록에 게이트를 건다 — **폐기 → 대체 → 주장 폐기.**

    `alternates` 는 같은 주장의 다른 근거 후보다. 대체 후보도 같은 게이트를 통과해야
    하고, 이미 쓴 리뷰는 다시 쓰지 않는다 — 한 리뷰는 한 번이다 (PER-170·PER-189).
    """
    if not evidence:
        raise QuoteGateError(
            f"[{claim_id}] 근거가 비었다 — 게이트 이전에 claim 이 될 수 없다 (PRD §1.2)"
        )
    emit = log or (lambda message: None)
    lines: list[str] = []

    def say(message: str) -> None:
        lines.append(message)
        emit(message)

    kept: list[Mapping[str, object]] = []
    verdicts: list[QuoteVerdict] = []
    dropped: list[QuoteVerdict] = []
    ledger: list[Mapping[str, object]] = []
    used: set[int] = set()

    def record_drop(verdict: QuoteVerdict) -> None:
        dropped.append(verdict)
        ledger.append({
            "gate": QUOTE_GATE,
            "issue": QUOTE_GATE_ISSUE,
            "unit": UNIT_EVIDENCE,
            "claimId": claim_id,
            "reason": verdict.reason,
            "label": LEDGER_REASONS[verdict.reason]["label"],
            "status": verdict.status,
            "reviewId": verdict.review_id,
            "quote": verdict.quote,
            "matchedReviewId": verdict.matched_review_id,
        })
        where = ("" if verdict.matched_review_id is None
                 else f" (원문은 리뷰 {verdict.matched_review_id} 의 것)")
        say(f"[quote] [{claim_id}] 인용 폐기 — 리뷰 {verdict.review_id} "
            f"{STATUS_LABELS[verdict.status]}{where}: {verdict.quote[:40]!r}")

    for item in evidence:
        review_id, quote = _evidence_parts(item)
        verdict = classify_quote(quote, review_id, reviews, corpus)
        verdicts.append(verdict)
        if verdict.ok:
            kept.append(item)
            used.add(review_id)
        else:
            record_drop(verdict)

    wanted = len(evidence)
    budget = len(dropped) if policy.max_substitutions is None else min(
        len(dropped), policy.max_substitutions)
    substituted: list[QuoteVerdict] = []

    for item in alternates:
        if len(substituted) >= budget or len(kept) >= wanted:
            break
        review_id, quote = _evidence_parts(item)
        if review_id in used:
            continue
        verdict = classify_quote(quote, review_id, reviews, corpus)
        verdicts.append(verdict)
        if verdict.ok:
            kept.append(item)
            used.add(review_id)
            substituted.append(verdict)
            say(f"[quote] [{claim_id}] 근거 대체 — 리뷰 {review_id} 로 채웠다")
        else:
            record_drop(verdict)

    if len(kept) < policy.min_evidence:
        ledger.append({
            "gate": QUOTE_GATE,
            "issue": QUOTE_GATE_ISSUE,
            "unit": UNIT_CLAIM,
            "claimId": claim_id,
            "reason": REASON_NO_EVIDENCE_LEFT,
            "label": LEDGER_REASONS[REASON_NO_EVIDENCE_LEFT]["label"],
            "keptEvidence": len(kept),
            "minEvidence": policy.min_evidence,
            "droppedEvidence": len(dropped),
        })
        say(f"[quote] [{claim_id}] 주장 폐기 — 원문 인용이 {len(kept)}개 남아 "
            f"min_evidence({policy.min_evidence}) 미만이다")
        return QuoteGateResult(claim_id, OUTCOME_DISCARDED, (), tuple(verdicts),
                               tuple(dropped), tuple(substituted), tuple(ledger),
                               tuple(lines))

    if not dropped:
        outcome = OUTCOME_CLEAN
    elif substituted:
        outcome = OUTCOME_SUBSTITUTED
    else:
        outcome = OUTCOME_REDUCED
        say(f"[quote] [{claim_id}] 근거 축소 — {len(evidence)}개 중 {len(kept)}개만 남았다 "
            "(대체 후보가 없거나 전부 게이트에 걸렸다)")

    return QuoteGateResult(claim_id, outcome, tuple(kept), tuple(verdicts),
                           tuple(dropped), tuple(substituted), tuple(ledger), tuple(lines))


def gate_claim_payload(
    payload: Mapping[str, object],
    reviews: Mapping[int, str],
    alternates: Iterable[Mapping[str, object]] = (),
    policy: QuoteGatePolicy = DEFAULT_POLICY,
    corpus: Mapping[int, str] | None = None,
    log: Callable[[str], None] | None = None,
) -> tuple[dict | None, QuoteGateResult]:
    """claim payload 1건에 게이트를 건다. 폐기되면 payload 는 `None` 이다.

    `None` 을 돌려주는 것이 요점이다 — "생성이 안 된 것"과 "게이트에 걸린 것"은
    `QuoteGateResult.ledger` 로 구별된다. 조용히 빈 claim 을 내보내지 않는다.
    """
    claim_id = payload.get("claimId") or "<claimId 없음>"
    result = enforce_evidence(
        claim_id, list(payload.get("evidence") or ()), reviews,
        alternates=alternates, policy=policy, corpus=corpus, log=log,
    )
    if result.discarded:
        return None, result
    gated = dict(payload)
    gated["evidence"] = [dict(e) for e in result.kept]
    return gated, result


def summarize(results: Sequence[QuoteGateResult]) -> dict:
    """게이트 통과분의 요약. 리포트가 이 모양을 그대로 싣는다."""
    verdicts = [v for r in results for v in r.verdicts]
    by_status = {s: sum(1 for v in verdicts if v.status == s) for s in STATUSES}
    checked = len(verdicts)
    passed = by_status[STATUS_VERBATIM]
    return {
        "claims": len(results),
        "quotesChecked": checked,
        "quotesVerbatim": passed,
        "verbatimPct": round(passed / checked * 100, 1) if checked else 0.0,
        "byStatus": {s: n for s, n in by_status.items() if n},
        "byOutcome": {o: sum(1 for r in results if r.outcome == o) for o in OUTCOMES},
        "ledgerRows": sum(len(r.ledger) for r in results),
    }


def registry_entries() -> dict:
    """`reject_registry`(PER-188) 에 등록해야 할 항목. 리포트가 그대로 싣는다.

    지금은 등록돼 있지 않아 `claim.rejected[]` 에 넣을 수 없다 — 모듈 문서 참조.
    """
    return {
        "gate": QUOTE_GATE,
        "issue": QUOTE_GATE_ISSUE,
        "registered": False,
        "reasons": [{"code": code, **body} for code, body in sorted(LEDGER_REASONS.items())],
        "note": "pipeline/reject_registry.py 는 PER-190 의 파일 소유 범위 밖이다. "
                "등록 전까지 이 사유들은 quote_gate 의 자기 원장과 로그에만 남는다 — "
                "미등록 사유로 claim.rejected[] 행을 만들면 assert_rejectable 이 멈추고, "
                "그게 맞는 동작이다",
    }


__all__ = [
    "DEFAULT_POLICY",
    "LEDGER_REASONS",
    "OUTCOMES",
    "OUTCOME_CLEAN",
    "OUTCOME_DISCARDED",
    "OUTCOME_REDUCED",
    "OUTCOME_SUBSTITUTED",
    "PASSING_STATUSES",
    "QUOTE_GATE",
    "QUOTE_GATE_ISSUE",
    "QuoteGateError",
    "QuoteGatePolicy",
    "QuoteGateResult",
    "QuoteVerdict",
    "REASON_MISATTRIBUTED",
    "REASON_NOT_VERBATIM",
    "REASON_NO_EVIDENCE_LEFT",
    "STATUSES",
    "STATUS_FABRICATED",
    "STATUS_LABELS",
    "STATUS_LINEBREAK_ONLY",
    "STATUS_MISATTRIBUTED",
    "STATUS_SPACING_EDITED",
    "STATUS_UNKNOWN_REVIEW",
    "STATUS_VERBATIM",
    "classify_quote",
    "enforce_evidence",
    "fold_for_match",
    "gate_claim_payload",
    "registry_entries",
    "summarize",
]
