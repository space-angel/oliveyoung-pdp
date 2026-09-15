"""
컨텍스트 정렬 — 5단 배치 (PER-187 / PRD §4-5).

게이트를 통과한 근거를 **아무 순서로나 넣지 않는다.** 모델이 확인하게 만들고 싶은
순서대로 배치한다. 이 모듈이 내는 것은 문자열이 아니라 **자료 구조**다.

    1 subject       무엇에 대한 주장인가   제품 · 카테고리 · 옵션
    2 condition     어떤 조건에서인가     skinType · skinTrouble (코드북 26종)
    3 evidence      근거가 무엇인가       리뷰 인용 — 신뢰도 내림차순 (PER-174)
    4 direction     방향이 일치하는가     긍정/부정 비율 (게이트3 판정, PER-185)
    5 sufficiency   충분한가             관찰 수 · 최신성 · 침묵 (게이트4, PER-186)

## 이 모듈이 하지 않는 것

**LLM 을 부르지 않고 프롬프트 문자열을 조립하지 않는다.** 배치는 자료의 문제이고
문구는 생성기(PER-189~192)의 문제다. 둘을 한 모듈에 두면 "순서를 바꿨더니 좋아졌다"와
"문장을 다듬었더니 좋아졌다"를 나중에 구별할 수 없다. 문자열이 필요한 호출부를 위해
`render_layout()` 을 따로 두되, 그것도 순수 함수이고 같은 입력에 같은 바이트를 낸다.

**판정을 다시 하지 않는다.** 방향은 게이트3(`polarity.AspectSupport`)이, 충분성은
게이트4(`sufficiency.Claim` + `policy.sufficiency_gate`)가 이미 정했다. 이 모듈은
그 판정을 **받아서** 순서대로 놓고, 두 게이트가 같은 셀에서 나온 것인지 대조한다
(`_assert_same_cell`). 대조가 요점이다 — 제품 전체의 방향 판정을 조건 셀의 충분성
수치와 나란히 놓으면 "건성 23명 중 19명" 자리에 전 제품의 비율이 들어앉는다.

## 2단은 `skinType` · `skinTrouble` 뿐이다 — 사용기간·계절은 축이 아니다

이슈 설명문이 2단을 "피부타입 · 사용기간 · 계절"로 적었지만 뒤 둘은 **데이터에 없다.**

  `usagePeriod`  필드가 없다 (CLAUDE.md · `contracts.CONDITION_AXES` 에도 없다).
                 `isMonthUseReview`/`isMonthOverReview` 는 불리언 리뷰 종류지 기간이
                 아니고, **본문에서 기간을 추정하지 않는다.** 추정하면 조건축이 아니라
                 태거의 짐작이 세그먼트가 된다
  `season`       입력 스키마에 없다. `reviewDate` 에서 월을 뽑아 계절을 만들 수는 있지만
                 그건 **작성 시점**이지 사용 계절이 아니다. 리센시 컷이 이미 같은 값을
                 쓰고 있어(PER-172) 5단 최신성에 실린다 — 2단에 다시 넣으면 같은 축이
                 조건과 충분성 양쪽에 앉는다

빼기로 한 사실을 주석으로만 남기면 다음 사람이 다시 넣는다. 그래서 `EXCLUDED_AXES` 로
배치 자료에 실어 낸다 — 빠진 이유가 출력에 남는다 (PER-174 의 `unavailable` 과 같은 규칙).

옵션은 조건축이지만 **1단**에 놓는다. "무엇에 대한 주장인가"의 일부이고(21호인가
23호인가), 게이트1이 이미 근거를 그 옵션으로 좁혔기 때문이다 (PER-182).

## 3단 정렬은 `trust.rank_key()` 다 — 새로 정하지 않는다

정렬 키를 이 모듈이 다시 정의하면 중복 게이트의 1표 선택(PER-170)과 근거 정렬이
갈린다. `rank_key()` 는 **점수 내림차순 → `reviewDate` 최신 → `reviewId` 최소**이고,
게이트2 통과분에서 `reviewId` 가 유일하므로 **전순서(total order)** 다. 동률에서
순서가 흔들릴 자리가 없다는 뜻이고, 그 사실을 `test_context_layout` 이 고정한다.

점수가 없는 레코드는 0점으로 깔지 않고 **에러다.** 0점으로 깔면 "신뢰도가 낮다"와
"아직 안 쟀다"가 같은 자리에 정렬된다 (PER-174 의 `unavailable` 과 같은 이유).

## 상한이 방향을 지우지 않는다 (`max_quotes`)

3단에 상한을 걸면 순위 뒤쪽이 잘린다. 그런데 전수 실측에서 `p019 × 트러블/자극` 은
긍정 309명 · 부정 6명이고, 신뢰도 상위 8건이 **전부 긍정**이다
(`eval/reports/context_layout_per187.json`). 그대로 자르면 4단은 "부정 6명"이라고
적혀 있는데 3단에는 부정 인용이 한 줄도 없다 — 생성기가 인용할 수 있는 반대 근거가
화면에서 사라진다. 갈린 걸 숨기는 것이 신뢰를 깨는 지점이라는 게 게이트3의 결정이다
(PER-185 §4-3 결정 3).

그래서 **정렬과 선별을 나눈다.** 정렬은 `rank_key()` 하나뿐이고 바뀌지 않는다.
상한을 걸 때만, 통째로 빠지게 된 방향마다 그 방향의 **순위 1위** 한 건을 채워 넣고
가장 많이 실린 방향의 순위 최하위 한 건을 뺀다. 채워 넣은 인용도 원래 순위(`rank`)를
그대로 달고 나가므로 "몇 등짜리 근거인지"가 감춰지지 않고, 최종 목록은 다시 순위대로
정렬된다. 무엇을 그렇게 넣었는지는 `reservedForDirection` 에 남는다.

이건 컷이 아니라 **자리 배정**이다. 방향별 수는 4단이, 잘린 몫은 `omittedByRank` 가
그대로 낸다.

## 침묵은 5단에 실리되 어떤 비율의 분모도 아니다 (PER-178)

`silentAuthors` 는 5단의 관찰 수와 함께 나간다 — 이 수가 크면 답을 일반화하면 안
된다는 뜻이고, 그 판단은 생성·judge 가 이 수를 보고 한다. **4단 비율의 분모는
`positive + negative` 다.** 중립(U0)도 침묵(S−D)도 분모 밖이다.

## 빈 단

1·2·4·5 단은 항상 존재한다. 조건이 없는 주장의 2단은 **비어 있는 게 아니라
`scoped=false` 라고 적힌 단**이다 — 무관(`null`)과 미기재(세그먼트)를 구별해야 하기
때문이다 (PER-177 §3). 3단만 예외로, **근거가 0건이면 배치를 만들지 않고 에러다**:
`evidence[]` 가 비면 claim 객체 자체가 생성되지 않는다는 규칙(PRD §1.2)을 배치
단계에서 먼저 세운다. 비어 있는 3단을 만들어 두면 모델이 1·2·4·5 단만 보고 답을 쓴다.
"""
from __future__ import annotations

import collections
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from contracts import MISSING_SEGMENT  # noqa: E402
from gates import independent_reviews  # noqa: E402
from polarity import AspectSupport, VERDICT_LABELS  # noqa: E402
from policy import (  # noqa: E402
    RECENCY_CUTOFF_MONTH,
    RECENCY_WINDOW_MONTHS,
    SNAPSHOT_LATEST_MONTH,
    DEFAULT_SUFFICIENCY,
    GateDecision,
    SufficiencyPolicy,
    month_of,
)
from sufficiency import Claim, MULTI_AXES, matches  # noqa: E402
from tag_contract import ASPECTS, fold_invisible, is_verbatim  # noqa: E402
from trust import rank_key  # noqa: E402

LAYOUT_SCHEMA_VERSION = "context-layout-v1"
ISSUE = "PER-187"

# 배치 순서가 규격이다. 이 순서를 바꾸면 모델이 확인하는 순서가 바뀐다 —
# 상수를 정본으로 두고 `assert_stage_order()` 가 출력에서 다시 확인한다.
STAGE_SUBJECT = "subject"
STAGE_CONDITION = "condition"
STAGE_EVIDENCE = "evidence"
STAGE_DIRECTION = "direction"
STAGE_SUFFICIENCY = "sufficiency"
STAGES: tuple[str, ...] = (
    STAGE_SUBJECT,
    STAGE_CONDITION,
    STAGE_EVIDENCE,
    STAGE_DIRECTION,
    STAGE_SUFFICIENCY,
)
STAGE_QUESTIONS = {
    STAGE_SUBJECT: "무엇에 대한 주장인가",
    STAGE_CONDITION: "어떤 조건에서인가",
    STAGE_EVIDENCE: "근거가 무엇인가",
    STAGE_DIRECTION: "방향이 일치하는가",
    STAGE_SUFFICIENCY: "충분한가",
}

# 2단이 쓰는 조건축. `contracts.CONDITION_AXES` 에서 `option` 을 뺀 것이다 —
# 옵션은 1단('무엇에 대한')에 놓는다.
LAYOUT_CONDITION_AXES: tuple[str, ...] = ("skinType", "skinTrouble")

# 이슈 설명문에 있었지만 **데이터에 없어서 기각한 축.** 자료에 실어 낸다.
EXCLUDED_AXES: tuple[dict, ...] = (
    {
        "axis": "usagePeriod",
        "reason": "데이터에 필드가 없다. isMonthUseReview/isMonthOverReview 는 불리언 "
                  "리뷰 종류이지 기간이 아니고, 본문에서 기간을 추정하지 않는다",
        "owner": "CLAUDE.md · pipeline/contracts.py CONDITION_AXES",
    },
    {
        "axis": "season",
        "reason": "입력 스키마에 없다. reviewDate 로 만들면 사용 계절이 아니라 작성 "
                  "시점이고, 그 값은 이미 5단 최신성(리센시 컷, PER-172)에 실린다",
        "owner": "pipeline/policy.py RECENCY_CUTOFF_MONTH",
    },
)

# 4단 비율의 분모 정의. 문자열을 자료에 실어 둔다 — 분모가 바뀌면 출력이 바뀐다.
DIRECTION_DENOMINATOR = "positiveAuthors + negativeAuthors"
DIRECTION_DENOMINATOR_NOTE = (
    "방향을 명시한 고유 작성자만 분모다. 중립(언급했으나 방향 없음)도 "
    "침묵(언급 없음)도 분모 밖이다 (PER-178)"
)
SILENCE_NOTE = (
    "침묵은 근거가 아니다. 이 수는 어떤 비율의 분모도 아니고, 크면 답을 "
    "일반화하면 안 된다는 뜻이다 (PER-178)"
)
EVIDENCE_ORDER_NOTE = (
    "trust.rank_key() 내림차순 — 신뢰도 점수 ↓ · reviewDate 최신 · reviewId 최소 "
    "(PER-174). 게이트2 통과분에서 reviewId 가 유일하므로 전순서다"
)


class ContextLayoutError(ValueError):
    """배치 입력이 계약을 위반했다. 조용한 폴백 금지.

    `ValueError` 를 상속한다 — 호출부가 이미 `ValueError` 를 잡고 있어도 위반이
    배치 결과로 둔갑하지 않게 한다 (`GateError` · `SufficiencyError` 와 같은 이유).
    """


@dataclass(frozen=True)
class EvidenceQuote:
    """3단의 인용 1건 = `(리뷰 × aspect)` 태그 하나 (PER-175 태그 단위).

    `quote` 는 **원문 부분문자열**이다 (`tag_contract.is_verbatim`, 보이지 않는
    문자만 접는다). 요약·재구성은 인용이 아니다 — v4 에서 88.8% 였던 지점이다.
    """
    rank: int
    review_id: int
    quote: str
    polarity: str
    trust_score: float
    review_month: str

    def as_dict(self) -> dict:
        return {
            "rank": self.rank,
            "reviewId": self.review_id,
            "quote": self.quote,
            "polarity": self.polarity,
            "trustScore": self.trust_score,
            "reviewMonth": self.review_month,
        }


@dataclass(frozen=True)
class ContextLayout:
    """5단 배치 1건. `as_dict()` 가 이 모듈의 출력 형식이고 순서가 규격이다."""
    product_id: str
    display_name: str
    category: str
    lineage_id: str
    aspect: str
    option_scope: str | None
    condition: dict
    quotes: tuple[EvidenceQuote, ...]
    quotes_total: int
    reserved_for_direction: tuple[int, ...]
    polarity: AspectSupport
    support: dict
    decision: GateDecision
    policy: SufficiencyPolicy
    evidence_months: tuple[str, str]
    codebook_labels: dict

    # --- 단별 자료 ---

    def _subject(self) -> dict:
        return {
            "productId": self.product_id,
            "displayName": self.display_name,
            "category": self.category,
            "lineageId": self.lineage_id,
            "aspect": self.aspect,
            "option": {
                "scoped": self.option_scope is not None,
                "key": self.option_scope,
                "note": (
                    "게이트1이 이 옵션의 리뷰만 남겼다 (PER-182)"
                    if self.option_scope is not None
                    else "옵션 범위를 걸지 않았다 — 색상·호수 질문에서만 건다 (PER-182)"
                ),
            },
        }

    def _condition(self) -> dict:
        axes = []
        for axis in LAYOUT_CONDITION_AXES:
            want = self.condition.get(axis)
            if want is None:
                axes.append({
                    "axis": axis,
                    "scoped": False,
                    "segments": [],
                    "labels": [],
                    "stated": None,
                })
                continue
            segments = list(want) if isinstance(want, (list, tuple)) else [want]
            axes.append({
                "axis": axis,
                "scoped": True,
                "segments": segments,
                "labels": [self.codebook_labels.get((axis, s)) for s in segments],
                # 미기재는 '조건 없음'이 아니라 세그먼트다 (PER-173). 무관(null)과 다르다
                "stated": all(s != MISSING_SEGMENT for s in segments),
            })
        return {
            "conditional": any(a["scoped"] for a in axes),
            "axes": axes,
            "excludedAxes": [dict(a) for a in EXCLUDED_AXES],
            "note": (
                "축이 scoped=false 면 '무관'이고, segment 가 '미기재' 면 프로필을 "
                "밝히지 않은 리뷰들의 세그먼트다. 둘은 다르다 (PER-177 §3)"
            ),
        }

    def _evidence(self) -> dict:
        return {
            "order": EVIDENCE_ORDER_NOTE,
            "quotesShown": len(self.quotes),
            "quotesTotal": self.quotes_total,
            # 잘린 몫은 감춘 것이 아니라 순위 뒤쪽이다. 수를 남겨야 상한을 바꿨을 때
            # 무엇이 더 들어왔는지 귀속시킬 수 있다
            "omittedByRank": self.quotes_total - len(self.quotes),
            # 상한 때문에 통째로 빠질 뻔한 방향의 1위를 채워 넣은 인용 (PER-185 §4-3)
            "reservedForDirection": list(self.reserved_for_direction),
            "directionsShown": sorted({q.polarity for q in self.quotes}),
            "verbatim": "모든 인용은 원문 부분문자열이다 (tag_contract.is_verbatim)",
            "quotes": [q.as_dict() for q in self.quotes],
        }

    def _direction(self) -> dict:
        pos, neg = self.polarity.positive_authors, self.polarity.negative_authors
        denominator = self.polarity.directional_authors
        return {
            "direction": self.polarity.direction,
            "verdict": self.polarity.verdict,
            "label": VERDICT_LABELS[self.polarity.verdict],
            "positiveAuthors": pos,
            "negativeAuthors": neg,
            "denominator": {
                "value": denominator,
                "definition": DIRECTION_DENOMINATOR,
                "note": DIRECTION_DENOMINATOR_NOTE,
            },
            "positiveRatio": round(pos / denominator, 4) if denominator else None,
            "negativeRatio": self.polarity.negative_ratio,
            "minorityAuthors": self.polarity.minority_authors,
            "minorityBeyondNoise": self.polarity.minority_beyond_noise,
            "limitations": sorted(self.polarity.limitations),
        }

    def _sufficiency(self) -> dict:
        earliest, latest = self.evidence_months
        return {
            "passed": self.decision.passed,
            "reason": self.decision.reason,
            "limitations": [self.decision.limitation] if self.decision.limitation else [],
            "supportAuthors": self.support["supportAuthors"],
            "spokeAuthors": self.support["spokeAuthors"],
            "neutralAuthors": self.support["neutralAuthors"],
            "cellAuthors": self.support["cellAuthors"],
            "silentAuthors": self.support["silentAuthors"],
            "silenceNote": SILENCE_NOTE,
            "supportShare": self.support["supportShare"],
            "policy": self.policy.as_meta(),
            "recency": {
                "windowFromMonth": RECENCY_CUTOFF_MONTH,
                "snapshotLatestMonth": SNAPSHOT_LATEST_MONTH,
                "windowMonths": RECENCY_WINDOW_MONTHS,
                "evidenceEarliestMonth": earliest,
                "evidenceLatestMonth": latest,
                "note": (
                    "스냅샷 최신 월 기준 고정 윈도우다 — today 기준 롤링을 쓰면 "
                    "재현성이 깨진다 (PER-172)"
                ),
            },
        }

    def as_dict(self) -> dict:
        """배치 자료. **키 순서가 규격이다** — `STAGES` 와 같은 순서로 낸다."""
        stages = {
            STAGE_SUBJECT: self._subject(),
            STAGE_CONDITION: self._condition(),
            STAGE_EVIDENCE: self._evidence(),
            STAGE_DIRECTION: self._direction(),
            STAGE_SUFFICIENCY: self._sufficiency(),
        }
        payload = {
            "schemaVersion": LAYOUT_SCHEMA_VERSION,
            "issue": ISSUE,
            "stageOrder": list(STAGES),
            "stages": {
                key: {"stage": i + 1, "key": key, "question": STAGE_QUESTIONS[key], **stages[key]}
                for i, key in enumerate(STAGES)
            },
        }
        assert_stage_order(payload)
        return payload


# --- 순서 검증 ---


def assert_stage_order(payload: dict) -> None:
    """배치 자료의 5단 순서를 확인한다. 뒤바뀌거나 빠지면 에러다.

    `as_dict()` 가 스스로 부르고, 파일에서 읽어 온 배치를 쓰는 호출부도 부른다.
    순서는 이 이슈의 **내용 그 자체**라 "그렇게 만들었으니 맞겠지"로 두지 않는다.
    """
    order = payload.get("stageOrder")
    if list(order or ()) != list(STAGES):
        raise ContextLayoutError(
            f"stageOrder 가 규격과 다르다: {order!r} (기대: {list(STAGES)})"
        )
    stages = payload.get("stages")
    if not isinstance(stages, dict):
        raise ContextLayoutError("stages 객체가 없다")
    if list(stages) != list(STAGES):
        raise ContextLayoutError(
            f"stages 의 배치 순서가 규격과 다르다: {list(stages)} (기대: {list(STAGES)}). "
            "순서를 바꾸면 모델이 확인하는 순서가 바뀐다 — 이 이슈의 내용이다"
        )
    for i, key in enumerate(STAGES):
        body = stages[key]
        if body.get("stage") != i + 1 or body.get("key") != key:
            raise ContextLayoutError(
                f"{key} 단의 번호가 어긋났다: stage={body.get('stage')!r} "
                f"key={body.get('key')!r} (기대: {i + 1} · {key!r})"
            )


# --- 입력 대조 ---


def _assert_same_cell(claim: Claim, polarity: AspectSupport, support: dict) -> None:
    """게이트3 판정과 게이트4 근거가 **같은 셀**에서 나왔는지 대조한다.

    두 게이트를 따로 돌린 결과를 나란히 놓는 것이 이 모듈의 일이라, 여기가 조용히
    틀릴 수 있는 유일한 자리다. 제품 전체의 방향 판정을 조건 셀의 수치와 붙이면
    "건성 23명 중 19명" 자리에 전 제품의 비율이 들어앉고, 화면에서는 구별되지 않는다.
    """
    if polarity.aspect != claim.support.aspect:
        raise ContextLayoutError(
            f"게이트3 aspect {polarity.aspect!r} 와 게이트4 aspect "
            f"{claim.support.aspect!r} 가 다르다"
        )
    pairs = (
        ("positiveAuthors", polarity.positive_authors, support["positiveAuthors"]),
        ("negativeAuthors", polarity.negative_authors, support["negativeAuthors"]),
        ("neutralAuthors", polarity.neutral_authors, support["neutralAuthors"]),
        ("silentAuthors", polarity.silent_authors, support["silentAuthors"]),
    )
    for name, gate3, gate4 in pairs:
        if gate3 != gate4:
            raise ContextLayoutError(
                f"{name} 가 게이트3({gate3}) 과 게이트4({gate4}) 에서 다르다. "
                "두 판정이 같은 셀에서 나온 것이 아니다 — 제품 전체의 방향을 조건 "
                "셀의 수치와 나란히 놓으면 비율이 조용히 바뀐다"
            )


def _quote_rows(
    records: list[dict], tags: list[dict], aspect: str
) -> tuple[list[EvidenceQuote], int]:
    """`(리뷰 × aspect)` 태그 → 신뢰도 순 인용. 정렬 키는 `trust.rank_key()` 다."""
    by_id = {r["reviewId"]: r for r in records}
    seen: set[int] = set()
    rows: list[tuple[tuple, int, str, str]] = []
    for i, tag in enumerate(tags):
        if tag.get("aspect") != aspect:
            continue
        review_id = tag.get("reviewId")
        record = by_id.get(review_id)
        if record is None:
            # 조용히 건너뛰면 다른 실행의 태그가 섞여도 근거 수만 줄어든다 (PER-185 와 같은 규칙)
            raise ContextLayoutError(
                f"tags[{i}]: 배치 묶음에 없는 reviewId {review_id!r} 의 {aspect!r} 태그다"
            )
        if review_id in seen:
            raise ContextLayoutError(
                f"tags[{i}]: reviewId {review_id} 의 {aspect!r} 태그가 두 번이다 "
                "— 한 사람이 두 번 인용된다 (PER-175 태깅 규칙 2)"
            )
        seen.add(review_id)
        quote = fold_invisible(tag.get("snippet") or "")
        if not quote:
            raise ContextLayoutError(
                f"tags[{i}] reviewId={review_id}: snippet 이 비었다 — 근거 없는 태그는 인용이 아니다"
            )
        content = record["raw"]["content"]
        if not is_verbatim(quote, content):
            raise ContextLayoutError(
                f"tags[{i}] reviewId={review_id}: 인용이 원문 부분문자열이 아니다 "
                f"{quote!r}. 요약·재구성은 인용이 아니다 (CLAUDE.md 서술 규칙)"
            )
        polarity = tag.get("polarity")
        prior = (record.get("derived") or {}).get("trustPrior") or {}
        if "score" not in prior:
            # 0 점으로 깔면 '신뢰도가 낮다' 와 '아직 안 쟀다' 가 같은 자리에 정렬된다
            raise ContextLayoutError(
                f"[reviewId={review_id}] derived.trustPrior 가 없다. 근거 정렬 키가 "
                "0 으로 깔리면 순서가 신뢰도가 아니라 입력 순서로 정해진다 (PER-174)"
            )
        rows.append((rank_key(record), review_id, quote, polarity))

    rows.sort(key=lambda row: row[0])
    quotes = [
        EvidenceQuote(
            rank=i + 1,
            review_id=review_id,
            quote=quote,
            polarity=polarity,
            trust_score=float(by_id[review_id]["derived"]["trustPrior"]["score"]),
            review_month=month_of(by_id[review_id]["raw"]["reviewDate"]),
        )
        for i, (_, review_id, quote, polarity) in enumerate(rows)
    ]
    return quotes, len(quotes)


def _truncate(
    quotes: list[EvidenceQuote], max_quotes: int
) -> tuple[list[EvidenceQuote], list[int]]:
    """상한을 걸되 **방향을 통째로 지우지 않는다** (PER-185 §4-3 결정 3).

    순위 상위 `max_quotes` 건을 먼저 잡고, 그 때문에 한 줄도 남지 않은 방향이 있으면
    그 방향의 1위를 채워 넣는다. 자리는 **가장 많이 실린 방향의 순위 최하위**에서
    뺀다 — 적게 실린 쪽을 또 깎으면 같은 일이 반복된다.

    정렬 규칙은 건드리지 않는다. 최종 목록은 다시 `rank` 순이고, 채워 넣은 인용도
    원래 순위를 그대로 달고 나간다.
    """
    present = sorted({q.polarity for q in quotes})
    if max_quotes < len(present):
        raise ContextLayoutError(
            f"max_quotes={max_quotes} 가 방향 수 {len(present)}({present}) 보다 작다. "
            "그러면 어떤 방향은 인용 없이 수만 남는다 — 상한을 올려라"
        )
    kept = list(quotes[:max_quotes])
    reserved: list[int] = []
    for polarity in present:
        if any(q.polarity == polarity for q in kept):
            continue
        promoted = next(q for q in quotes if q.polarity == polarity)
        counts = collections.Counter(q.polarity for q in kept)
        majority = max(sorted(counts), key=lambda p: counts[p])
        evicted = max(
            (q for q in kept if q.polarity == majority), key=lambda q: q.rank)
        kept.remove(evicted)
        kept.append(promoted)
        reserved.append(promoted.review_id)
    kept.sort(key=lambda q: q.rank)
    return kept, sorted(reserved)


def layout_context(
    *,
    product,
    claim: Claim,
    polarity: AspectSupport,
    decision: GateDecision,
    records: list[dict],
    tags: list[dict],
    codebook=None,
    option_scope: str | None = None,
    policy: SufficiencyPolicy = DEFAULT_SUFFICIENCY,
    max_quotes: int | None = None,
) -> ContextLayout:
    """게이트 통과분 → 5단 배치. **순수 함수**다 — 같은 인자면 같은 배치다.

      product       `catalog.Product`. 제품 동일성은 카탈로그가 소유한다 (PER-171)
      claim         게이트4 입력 1건 (`sufficiency.Claim` — 셀 + 근거 집합)
      polarity      게이트3 판정 (`polarity.AspectSupport`)
      decision      게이트4 판정 (`policy.sufficiency_gate` 의 결과)
      records       셀 안의 **게이트1·2 통과** 레코드
      tags          `(리뷰 × aspect)` 태그 (PER-175)
      codebook      2단 라벨용. 없으면 라벨 없이 코드만 낸다 (표기 단계 전용)
      option_scope  게이트1에 건 옵션 범위. 안 걸었으면 `None`
      max_quotes    3단 인용 상한. 자르면 그 수를 `omittedByRank` 에 남긴다

    전역 상태를 읽지 않는다 — 카탈로그·코드북·정책을 전부 인자로 받는다
    (`trust.ScoringContext` 와 같은 규칙).
    """
    if claim.support.aspect not in ASPECTS:
        raise ContextLayoutError(
            f"택소노미 밖 aspect {claim.support.aspect!r} (PER-175 동결 14종)"
        )
    if product.product_id != claim.cell.product_id:
        raise ContextLayoutError(
            f"카탈로그 제품 {product.product_id!r} 와 셀 제품 "
            f"{claim.cell.product_id!r} 가 다르다"
        )
    if max_quotes is not None and (not isinstance(max_quotes, int) or max_quotes < 1):
        raise ContextLayoutError(f"max_quotes 는 1 이상의 정수여야 한다: {max_quotes!r}")

    cell = claim.cell
    # 게이트2 통과분인지 확인한다 — 가정하지 않는다. 같은 작성자가 두 번 있으면
    # 3단 인용이 두 번 나가고 4단 비율이 그 사람 쪽으로 기운다
    independent_reviews(records)
    for record in records:
        if record["productId"] != cell.product_id:
            raise ContextLayoutError(
                f"[reviewId={record['reviewId']}] 셀 밖 제품이다: "
                f"{record['productId']} != {cell.product_id}"
            )
        if not matches(record, cell.condition):
            raise ContextLayoutError(
                f"[reviewId={record['reviewId']}] 셀 조건 {cell.condition} 밖이다. "
                "조건부 주장의 근거는 그 세그먼트 리뷰만이다 (PER-177 §3)"
            )
        if record["derived"]["authorKey"] not in cell.authors:
            raise ContextLayoutError(
                f"[reviewId={record['reviewId']}] 작성자가 셀 밖이다"
            )
    if len(records) != cell.size:
        raise ContextLayoutError(
            f"레코드 {len(records)}건 과 셀 작성자 {cell.size}명 이 다르다. "
            "셀 전체를 넘겨야 침묵(S−D)이 맞는다 (PER-178)"
        )

    support = claim.support.as_dict(cell)  # 셀 밖 작성자 대조는 여기서 한다
    _assert_same_cell(claim, polarity, support)

    quotes, total = _quote_rows(records, tags, claim.support.aspect)
    if not quotes:
        # 빈 3단을 만들면 모델이 1·2·4·5 단만 보고 답을 쓴다 (PRD §1.2)
        raise ContextLayoutError(
            f"[{cell.product_id} {claim.support.aspect}] 3단 근거가 0건이다. "
            "evidence[] 가 비면 배치를 만들지 않는다 — claim 객체 자체가 생성되지 않는다"
        )
    if len(quotes) != support["spokeAuthors"]:
        raise ContextLayoutError(
            f"인용 {len(quotes)}건 과 언급 작성자 D={support['spokeAuthors']} 가 다르다. "
            "게이트3·4 의 근거와 3단 인용이 같은 태그 묶음에서 나온 것이 아니다"
        )
    months = sorted(q.review_month for q in quotes)
    reserved: list[int] = []
    if max_quotes is not None and len(quotes) > max_quotes:
        quotes, reserved = _truncate(quotes, max_quotes)

    labels: dict = {}
    if codebook is not None:
        for axis in LAYOUT_CONDITION_AXES:
            want = cell.condition.get(axis)
            if want is None:
                continue
            for segment in (want if isinstance(want, (list, tuple)) else [want]):
                if segment != MISSING_SEGMENT:
                    labels[(axis, segment)] = codebook.label(axis, segment)

    return ContextLayout(
        product_id=product.product_id,
        display_name=product.display_name,
        category=product.category,
        lineage_id=product.lineage_id,
        aspect=claim.support.aspect,
        option_scope=option_scope,
        condition={
            axis: (list(v) if isinstance(v, (list, tuple)) else v)
            for axis, v in cell.condition.items()
            if axis in LAYOUT_CONDITION_AXES and v is not None
        },
        quotes=tuple(quotes),
        quotes_total=total,
        reserved_for_direction=tuple(reserved),
        polarity=polarity,
        support=support,
        decision=decision,
        policy=policy,
        evidence_months=(months[0], months[-1]),
        codebook_labels=labels,
    )


# --- 문자열 렌더링 (배치와 분리한다) ---


def render_layout(payload: dict) -> str:
    """배치 자료 → 사람이 읽는 문자열. **프롬프트 조립이 아니다.**

    문구는 생성기(PER-189~192)의 것이고, 이 함수는 배치를 눈으로 확인하고 diff 를
    뜨기 위한 것이다. 같은 자료에 같은 바이트를 낸다 (테스트로 고정).
    """
    assert_stage_order(payload)
    stages = payload["stages"]
    out: list[str] = []

    s = stages[STAGE_SUBJECT]
    out.append(f"[1] {s['question']}")
    out.append(f"  제품: {s['displayName']} ({s['productId']} · 계보 {s['lineageId']})")
    out.append(f"  카테고리: {s['category']}")
    out.append(f"  주제: {s['aspect']}")
    out.append(f"  옵션: {s['option']['key'] if s['option']['scoped'] else '범위 없음'}")

    c = stages[STAGE_CONDITION]
    out.append(f"[2] {c['question']}")
    for axis in c["axes"]:
        if not axis["scoped"]:
            out.append(f"  {axis['axis']}: 무관")
            continue
        parts = [
            f"{seg}({label})" if label else seg
            for seg, label in zip(axis["segments"], axis["labels"])
        ]
        out.append(f"  {axis['axis']}: {' · '.join(parts)}")
    for excluded in c["excludedAxes"]:
        out.append(f"  {excluded['axis']}: 축이 아니다 — {excluded['reason']}")

    e = stages[STAGE_EVIDENCE]
    out.append(f"[3] {e['question']} (인용 {e['quotesShown']}/{e['quotesTotal']}건, {e['order']})")
    reserved = set(e["reservedForDirection"])
    for quote in e["quotes"]:
        mark = " ← 방향 보존" if quote["reviewId"] in reserved else ""
        out.append(
            f"  {quote['rank']:>3}. [{quote['polarity']}] \"{quote['quote']}\" "
            f"(리뷰 {quote['reviewId']} · {quote['reviewMonth']} · 신뢰도 {quote['trustScore']}){mark}"
        )
    if e["omittedByRank"]:
        out.append(f"  … 순위 뒤쪽 {e['omittedByRank']}건은 싣지 않았다")

    d = stages[STAGE_DIRECTION]
    out.append(f"[4] {d['question']}")
    out.append(
        f"  판정: {d['label']}({d['direction']}) — 긍정 {d['positiveAuthors']}명 · "
        f"부정 {d['negativeAuthors']}명 / 분모 {d['denominator']['value']}명"
        f"({d['denominator']['definition']})"
    )
    out.append(f"  부정 비율: {d['negativeRatio']}")
    if d["minorityAuthors"] is not None:
        out.append(
            f"  소수 방향 {d['minorityAuthors']}명 · 태거 잡음 밖: {d['minorityBeyondNoise']}"
        )
    for limitation in d["limitations"]:
        out.append(f"  한계: {limitation}")

    f = stages[STAGE_SUFFICIENCY]
    out.append(f"[5] {f['question']}")
    out.append(
        f"  U={f['supportAuthors']} · D={f['spokeAuthors']} · S={f['cellAuthors']} "
        f"(언급 없음 {f['silentAuthors']}명 — {f['silenceNote']})"
    )
    out.append(
        f"  판정: {'통과' if f['passed'] else '탈락(' + str(f['reason']) + ')'} "
        f"[N_min={f['policy']['nMin']} · R_min={f['policy']['rMin']} · S_min={f['policy']['sMin']}]"
    )
    out.append(
        f"  최신성: {f['recency']['windowFromMonth']}~{f['recency']['snapshotLatestMonth']} "
        f"윈도우 · 근거 {f['recency']['evidenceEarliestMonth']}~{f['recency']['evidenceLatestMonth']}"
    )
    for limitation in f["limitations"]:
        out.append(f"  한계: {limitation}")

    return "\n".join(out) + "\n"


__all__ = [
    "DIRECTION_DENOMINATOR",
    "EXCLUDED_AXES",
    "ISSUE",
    "LAYOUT_CONDITION_AXES",
    "LAYOUT_SCHEMA_VERSION",
    "SILENCE_NOTE",
    "STAGES",
    "STAGE_CONDITION",
    "STAGE_DIRECTION",
    "STAGE_EVIDENCE",
    "STAGE_QUESTIONS",
    "STAGE_SUBJECT",
    "STAGE_SUFFICIENCY",
    "ContextLayout",
    "ContextLayoutError",
    "EvidenceQuote",
    "assert_stage_order",
    "layout_context",
    "render_layout",
]
