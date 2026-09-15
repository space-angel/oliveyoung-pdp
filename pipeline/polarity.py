"""
게이트3 — 방향성 (PER-185 / PRD §4-3).

주제가 같다고 방향이 같은 게 아니다. "흡수" 주제에는 "잘 흡수된다"와 "끈적여서
환불"이 같이 있다. 게이트1이 **이 제품의 리뷰가 맞는가**를, 게이트2가 **같은 사람을
두 번 세고 있지 않은가**를 정했다면, 이 게이트는 **남은 근거가 한 방향인가**를 정한다.

## 이 게이트는 리뷰를 탈락시키지 않는다

게이트1·2와 다른 점이고, 그게 설계 결정이다 (§4-3 결정 3). 방향이 갈리면 한쪽을
버리는 게 아니라 `혼재`로 표시하고 양쪽 비율을 함께 낸다 — **갈린 걸 숨기고 한쪽만
보여주는 게 신뢰를 깨는 지점이다.** 그래서 이 모듈에는 `rejected[]` 가 없다.
대신 두 가지를 낸다.

  판정   `(제품 × aspect)` 묶음의 방향 — `positive` · `negative` · `혼재` · 언급없음
  플래그 별점과 태그 방향이 어긋난 `(리뷰 × aspect)` — **자동으로 정하지 않는다**

## 판정 단위는 `(리뷰 × aspect)` 다 (결정 1)

리뷰 전체에 긍정/부정 하나를 붙이면 "발색은 좋은데 지속력은 별로"가 뭉개진다.
그 단위는 태깅 층(PER-175)이 이미 정해 뒀으므로 이 모듈은 태그를 **집계**한다.
게이트2를 통과한 묶음을 받으므로 리뷰 1건 = 작성자 1명이고, 따라서 여기서 세는
수는 리뷰 수가 아니라 **고유 작성자 수**다 (PER-170). 그 전제는 가정하지 않고
`independent_reviews` 로 확인한다.

## 별점은 방향의 대리값이 아니다 (결정 2)

별점과 교차 검증하되 **둘 중 뭐가 맞는지 자동으로 정하지 않는다.** 별점이 이기게
하면 "발색은 좋은데 지속력은 별로"라고 쓴 별점 5점 리뷰에서 지속성 부정이 사라지고,
태그가 이기게 하면 태거의 방향 오류가 그대로 굳는다. 어느 쪽도 정답을 알지 못한다.

그래서 어긋난 건은 판정을 바꾸지 않고 `RatingConflict` 로 모은다 — **그런 케이스를
모아두면 그게 곧 평가셋이다** (#3 / PER-178). 다만 한 덩어리로 모으면 쓸모가 없어서
세 유형으로 나눈다. 세 유형의 "둘 중 하나가 틀렸을 가능성"이 서로 다르기 때문이다.

  `sole_aspect`      그 리뷰의 유일한 방향 태그가 별점과 반대다. 두 신호가 같은 것을
                     가리키는데 어긋났으므로 **둘 중 하나는 틀렸다** — 라벨 가치가 가장 높다
  `all_aspects`      방향 태그가 여럿인데 **전부** 별점과 반대다. 별점 오기입이거나
                     태거가 리뷰 하나를 통째로 뒤집었다
  `minority_aspect`  일부만 반대다. "좋은데 X는 별로" — **정상일 가능성이 높다.**
                     이걸 앞의 둘과 섞으면 평가셋이 노이즈가 된다

## `혼재` 는 태거 잡음과 구별될 때만 붙인다

소수 방향이 1건 있다고 "의견이 갈린다"고 말하면 안 된다. 태거의 방향이 틀릴 수
있기 때문이다. 그래서 귀무가설을 세우고 잰다 — **"이 묶음은 사실 전부 다수 방향이고,
소수 방향은 태거가 뒤집은 것이다."** 이 가설로 관측된 소수 개수 이상이 나올 확률이
`MIXED_ALPHA` 미만이면 갈림이 실재한다고 본다.

이 규칙의 좋은 성질은 **문턱이 표본 크기에 따라 자동으로 움직인다**는 것이다.
고정 비율(예: 20%)을 쓰면 n=8 에서는 너무 헐겁고 n=400 에서는 너무 빡빡하다.

  n=  8  소수 2건 이상 (25.0%)      n=100  소수  7건 이상 ( 7.0%)
  n= 20  소수 3건 이상 (15.0%)      n=200  소수 12건 이상 ( 6.0%)
  n= 50  소수 5건 이상 (10.0%)      n=400  소수 20건 이상 ( 5.0%)

`negativeRatio` 는 `혼재` 일 때만이 아니라 **항상** 낸다. 판정일 때만 내면 호출부가
판정을 보고 비율을 감출 수 있고, 그건 이 게이트가 막으려는 바로 그 동작이다.

## 침묵은 근거가 아니다 (PER-178)

그 aspect 를 언급하지 않은 리뷰는 긍정도 부정도 아니다. `silentAuthors` 로 따로 세고
**`negativeRatio` 의 분모에 넣지 않는다.** 전수에서 태그가 0개인 리뷰가 5,443건이라
분모에 섞으면 모든 비율이 조용히 희석된다. 언급 없음을 "대부분 문제 없다"로
일반화하면 `unsupported_claim` 이다 (`docs/DECISION_PER178_SILENCE_IS_NOT_EVIDENCE.md`).

`neutral` 도 분모 밖이다 — 언급은 했지만 방향이 없는 것이라 침묵과도, 방향과도 다르다.

## 통과했다고 한계가 없는 것은 아니다

게이트1의 `renewal_unobserved` 와 같은 규칙이다. 두 가지 한계가 판정에 따라붙는다.

  `tagger_direction_error`   태그 방향 자체가 틀릴 수 있다 (정답셋 200건에서 방향
                             불일치 6.5%, 방향끼리 뒤집힌 것만 보면 1.3%)
  `order_chosen_direction`   태거가 한 리뷰의 같은 축에 긍·부정을 **둘 다** 뱉은 경우,
                             태깅 계약이 먼저 나온 것만 남기고 나머지를 버렸다.
                             그 축의 방향은 판정된 게 아니라 **출력 순서로 정해졌다**
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from gates import independent_reviews  # noqa: E402
from tag_contract import ASPECTS, POLARITIES  # noqa: E402

GATE_POLARITY = "polarity"

POSITIVE = "positive"
NEGATIVE = "negative"
NEUTRAL = "neutral"
DIRECTIONS = (POSITIVE, NEGATIVE)

# --- 판정 어휘 ---

VERDICT_POSITIVE = "positive"
VERDICT_NEGATIVE = "negative"
VERDICT_MIXED = "mixed"
VERDICT_NEUTRAL_ONLY = "neutral_only"
VERDICT_SILENT = "silent"
VERDICTS = (
    VERDICT_POSITIVE,
    VERDICT_NEGATIVE,
    VERDICT_MIXED,
    VERDICT_NEUTRAL_ONLY,
    VERDICT_SILENT,
)

# 사람이 읽는 표기. 완료 조건의 '혼재' 가 여기 있다. 게이트1·2 의 `REJECT_LABELS` 와
# 같은 규칙이다 — 판정 코드와 표기를 같은 문자열로 두면 표기를 고칠 때 코드가 흔들린다.
VERDICT_LABELS = {
    VERDICT_POSITIVE: "긍정",
    VERDICT_NEGATIVE: "부정",
    VERDICT_MIXED: "혼재",
    VERDICT_NEUTRAL_ONLY: "언급만",
    VERDICT_SILENT: "침묵",
}

# --- 별점 교차검증 어휘 ---

CONFLICT_SOLE_ASPECT = "sole_aspect"
CONFLICT_ALL_ASPECTS = "all_aspects"
CONFLICT_MINORITY_ASPECT = "minority_aspect"
CONFLICT_KINDS = (CONFLICT_SOLE_ASPECT, CONFLICT_ALL_ASPECTS, CONFLICT_MINORITY_ASPECT)

CONFLICT_LABELS = {
    CONFLICT_SOLE_ASPECT: "유일축불일치",
    CONFLICT_ALL_ASPECTS: "전축불일치",
    CONFLICT_MINORITY_ASPECT: "일부축불일치",
}

# --- 주장에 남기는 한계 코드 ---

LIMIT_TAGGER_DIRECTION_ERROR = "tagger_direction_error"
LIMIT_ORDER_CHOSEN_DIRECTION = "order_chosen_direction"

# --- 혼재 판정 모수 ---

# 태거가 방향을 **반대 방향으로** 뒤집는 비율. 정답셋 200건(`eval/gold/v5_tags_pilot_gold.jsonl`)
# 과 전수 태그를 (리뷰 × aspect) 로 맞춰 본 실측이다 — 양쪽 다 방향(positive/negative)인
# 233쌍 중 서로 반대인 것이 3쌍(1.29%)이고, 그 **95% 상한이 3.29%** 다.
#
# 점추정(1.29%)이 아니라 상한을 쓴다. 이 값이 작을수록 `혼재` 가 쉽게 붙으므로,
# 표본 233쌍의 불확실성을 혼재를 **덜** 붙이는 쪽으로 흡수시키는 것이다.
#
# 리포트에 실린 방향 불일치 6.5% 를 그대로 쓰지 않는 이유: 그 수치는 3종
# (positive/negative/neutral) 일치율이고, 16건의 불일치 중 12건이 방향↔`neutral` 이다.
# `neutral` 로 잘못 붙은 태그는 **가짜 반대 방향을 만들지 않는다** — 분모에서 빠질 뿐이다.
# 혼재 판정을 위협하는 건 방향이 반대로 뒤집히는 경우뿐이라 그것만 모수로 쓴다.
#
# 태거를 바꾸면(PER-213) 이 값이 낡는다. `eval/measure_gate3_polarity.py` 가 정답셋에서
# 다시 재고 이 상수와 어긋나면 **에러를 낸다** — 조용히 낡지 않게 하려는 것이다.
TAGGER_FLIP_RATE = 0.033
TAGGER_FLIP_RATE_SOURCE = "정답셋 200건 · 방향쌍 233 중 반대 3 (1.29%) 의 95% 상한"
MIXED_ALPHA = 0.05

_ASPECTS = frozenset(ASPECTS)
_POLARITIES = frozenset(POLARITIES)


class PolarityError(ValueError):
    """게이트3 입력이 계약을 위반했다. 조용한 폴백 금지.

    `ValueError` 를 상속한다 — 호출부가 이미 `ValueError` 를 잡고 있어도 위반이
    판정 결과로 둔갑하지 않게 하려는 것이다 (`GateError` 와 같은 이유).
    """


def binom_sf(k: int, n: int, p: float) -> float:
    """P(X >= k), X ~ B(n, p). 의존성(scipy)을 늘리지 않으려고 직접 센다.

    `k <= 0` 이면 1.0 이다 — "0건 이상"은 항상 참이라 소수가 없는 묶음은 절대
    `혼재` 가 되지 않는다.

    항을 **로그로** 계산한다. `math.comb(n, k)` 를 그대로 쓰면 n 이 1,100 근처를
    넘는 순간 이항계수가 float 로 변환되지 않아 `OverflowError` 가 난다 — 지금
    스냅샷의 최대 셀은 그보다 작지만, 조건축을 합치거나 새 수집분이 들어오면 닿는
    크기다. 게이트가 큰 제품에서만 터지는 건 가장 늦게 발견되는 종류의 버그다.
    """
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    if not 0.0 < p < 1.0:
        raise PolarityError(f"뒤집힘 비율은 (0, 1) 안이어야 한다: {p!r}")

    log_p, log_q = math.log(p), math.log1p(-p)
    log_fact_n = math.lgamma(n + 1)
    total = 0.0
    for i in range(k, n + 1):
        log_term = (
            log_fact_n - math.lgamma(i + 1) - math.lgamma(n - i + 1)
            + i * log_p + (n - i) * log_q
        )
        if log_term < -745.0:  # exp 가 0.0 으로 언더플로하는 구간
            if i > n * p:
                break  # 최빈값을 지난 꼬리는 단조 감소한다 — 남은 항도 0 이다
            continue
        total += math.exp(log_term)
    return min(total, 1.0)


@dataclass(frozen=True)
class AspectSupport:
    """`(제품 × aspect)` 묶음 하나의 방향 판정과 근거 수.

    수는 전부 **고유 작성자 수**다 (게이트2 통과분이라 리뷰 1건 = 작성자 1명).
    `silent_authors` 와 `neutral_authors` 는 `negative_ratio` 의 분모 밖이다.
    """
    aspect: str
    positive_authors: int
    negative_authors: int
    neutral_authors: int
    silent_authors: int
    verdict: str
    minority_p_value: float
    limitations: frozenset[str] = frozenset()

    @property
    def directional_authors(self) -> int:
        """방향이 있는 근거 수 — `negativeRatio` 의 분모. 침묵·중립은 여기 없다."""
        return self.positive_authors + self.negative_authors

    @property
    def mentioned_authors(self) -> int:
        return self.directional_authors + self.neutral_authors

    @property
    def negative_ratio(self) -> float | None:
        """부정 비율. 방향 근거가 없으면 비율이 아니라 `None` 이다 — 0.0 으로 내면
        "부정이 없다"로 읽히지만 실제로는 "방향을 말한 사람이 없다"다."""
        if not self.directional_authors:
            return None
        return round(self.negative_authors / self.directional_authors, 4)

    @property
    def mixed(self) -> bool:
        return self.verdict == VERDICT_MIXED

    def as_dict(self) -> dict:
        return {
            "aspect": self.aspect,
            "verdict": self.verdict,
            "label": VERDICT_LABELS[self.verdict],
            "support": {
                "positiveAuthors": self.positive_authors,
                "negativeAuthors": self.negative_authors,
                "neutralAuthors": self.neutral_authors,
                # 침묵은 근거가 아니다 (PER-178). 세되 비율에는 넣지 않는다
                "silentAuthors": self.silent_authors,
                "directionalAuthors": self.directional_authors,
                "negativeRatio": self.negative_ratio,
            },
            "minorityPValue": round(self.minority_p_value, 6),
            "limitations": sorted(self.limitations),
        }


@dataclass(frozen=True)
class RatingConflict:
    """별점과 태그 방향이 어긋난 `(리뷰 × aspect)` 1건.

    **판정이 아니라 플래그다.** 어느 쪽이 맞는지 정하지 않는다 — 그 판단은 사람이
    #3 평가셋에서 한다 (PER-178). `kind` 는 라벨 우선순위를 정하려는 층화다.
    """
    review_id: int
    aspect: str
    tag_polarity: str
    rating: int
    rating_sentiment: str
    kind: str
    snippet: str | None = None

    def as_dict(self) -> dict:
        return {
            "reviewId": self.review_id,
            "aspect": self.aspect,
            "tagPolarity": self.tag_polarity,
            "rating": self.rating,
            "ratingSentiment": self.rating_sentiment,
            "kind": self.kind,
            "label": CONFLICT_LABELS[self.kind],
            "snippet": self.snippet,
        }


@dataclass
class PolarityResult:
    """게이트3 1회 실행 결과. 판정과 플래그를 **함께** 낸다.

    게이트1·2 의 `GateResult` 와 달리 `rejected[]` 가 없다 — 이 게이트는 근거를
    버리지 않는다 (§4-3 결정 3). 대신 `conflicts` 가 #3 평가셋의 입력이다.
    """
    gate: str = GATE_POLARITY
    issue: str = "PER-185"
    supports: list[AspectSupport] = field(default_factory=list)
    conflicts: list[RatingConflict] = field(default_factory=list)

    def by_aspect(self) -> dict[str, AspectSupport]:
        return {s.aspect: s for s in self.supports}

    def mixed_aspects(self) -> list[AspectSupport]:
        return [s for s in self.supports if s.mixed]

    def conflicts_by_kind(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for c in self.conflicts:
            counts[c.kind] = counts.get(c.kind, 0) + 1
        return dict(sorted(counts.items()))

    def as_dict(self) -> dict:
        return {
            "gate": self.gate,
            "issue": self.issue,
            "aspects": [s.as_dict() for s in self.supports],
            "mixedAspects": [s.aspect for s in self.mixed_aspects()],
            "ratingConflicts": len(self.conflicts),
            "ratingConflictsByKind": self.conflicts_by_kind(),
        }


def _check_tags(tags: list[dict], review_ids: set[int]) -> None:
    """태그가 계약 안인지 본다. 태깅 층이 이미 본 규칙이지만 여기서 **다시** 본다.

    태깅과 게이트 사이에 사람 손이 닿는 경로(다른 실행의 태그 파일을 섞어 넣는 것)가
    있고, 그 사고는 조용하다 — 모르는 `reviewId` 는 그냥 집계에서 빠지고, 같은 축이
    두 번 들어오면 한 사람이 두 표가 된다.
    """
    seen: set[tuple[int, str]] = set()
    for i, tag in enumerate(tags):
        rid, aspect, polarity = tag.get("reviewId"), tag.get("aspect"), tag.get("polarity")
        if rid not in review_ids:
            raise PolarityError(
                f"tags[{i}]: 게이트 통과 묶음에 없는 reviewId {rid!r} 다. "
                "다른 실행의 태그를 섞으면 집계에서 조용히 빠진다"
            )
        if aspect not in _ASPECTS:
            raise PolarityError(f"tags[{i}]: 택소노미 밖 aspect {aspect!r} (PER-175 동결 14종)")
        if polarity not in _POLARITIES:
            raise PolarityError(f"tags[{i}]: polarity 가 {POLARITIES} 밖이다 — {polarity!r}")
        key = (rid, aspect)
        if key in seen:
            raise PolarityError(
                f"tags[{i}]: reviewId {rid} 의 aspect {aspect!r} 가 두 번 들어왔다. "
                "그대로 세면 한 사람이 두 표가 된다 (PER-175 태깅 규칙 2)"
            )
        seen.add(key)


def _rating_of(record: dict) -> int:
    rating = record["raw"]["rating"]
    if not isinstance(rating, int):
        raise PolarityError(f"[reviewId={record['reviewId']}] rating 이 정수가 아니다: {rating!r}")
    return rating


def _rating_sentiment(record: dict) -> str:
    """별점이 가리키는 방향. 입수(PER-173)가 파생층에 이미 넣어 둔 값을 쓴다.

    여기서 임계값을 다시 쓰지 않는 것이 요점이다 — `contracts.sentiment_prior` 와
    다른 기준을 쓰면 같은 리뷰가 층마다 다른 방향을 갖는다.
    """
    prior = (record.get("derived") or {}).get("sentimentPrior")
    if prior not in _POLARITIES:
        raise PolarityError(
            f"[reviewId={record['reviewId']}] derived.sentimentPrior 가 없다. "
            "별점 교차검증을 걸 수 없다 — 입수(PER-173)를 거친 레코드를 넘겨라"
        )
    return prior


def rating_conflicts(records: list[dict], tags: list[dict]) -> list[RatingConflict]:
    """별점과 태그 방향이 어긋난 건을 모은다 (§4-3 결정 2).

    **판정을 바꾸지 않는다.** 어긋났다는 사실만 남기고, 어느 쪽이 맞는지는 사람이
    정한다. 별점 3점은 방향이 아니므로(`sentiment_prior` 가 `neutral`) 불일치가
    아니라 **정보 없음**이다 — 여기 넣으면 평가셋이 3점 리뷰로 채워진다.
    """
    by_review: dict[int, list[dict]] = {}
    for tag in tags:
        if tag["polarity"] in DIRECTIONS:
            by_review.setdefault(tag["reviewId"], []).append(tag)

    out: list[RatingConflict] = []
    for record in records:
        rid = record["reviewId"]
        directional = by_review.get(rid)
        if not directional:
            continue
        sentiment = _rating_sentiment(record)
        if sentiment not in DIRECTIONS:
            continue  # 별점 3점 — 방향을 말하지 않는다
        opposing = [t for t in directional if t["polarity"] != sentiment]
        if not opposing:
            continue
        if len(directional) == 1:
            kind = CONFLICT_SOLE_ASPECT
        elif len(opposing) == len(directional):
            kind = CONFLICT_ALL_ASPECTS
        else:
            kind = CONFLICT_MINORITY_ASPECT
        for tag in opposing:
            out.append(RatingConflict(
                review_id=rid,
                aspect=tag["aspect"],
                tag_polarity=tag["polarity"],
                rating=_rating_of(record),
                rating_sentiment=sentiment,
                kind=kind,
                snippet=tag.get("snippet"),
            ))
    out.sort(key=lambda c: (c.review_id, c.aspect))
    return out


def aspect_support(
    records: list[dict],
    tags: list[dict],
    aspect: str,
    *,
    flip_rate: float = TAGGER_FLIP_RATE,
    alpha: float = MIXED_ALPHA,
    order_chosen: set[tuple[int, str]] | None = None,
) -> AspectSupport:
    """`aspect` 하나의 방향 판정. `records` 는 **게이트2 통과분**이어야 한다.

    `records` 를 어떤 묶음으로 자를지는 호출부가 정한다 — 제품 전체일 수도, 조건축
    세그먼트(`productId × skinType`)일 수도 있다. 이 함수는 넘겨받은 묶음이 곧
    모집단이라고 보고, 태그가 없는 리뷰를 **침묵**으로 센다.

    `order_chosen` 은 태거가 같은 축에 긍·부정을 둘 다 뱉어 방향이 출력 순서로
    정해진 `(reviewId, aspect)` 집합이다. 넘기면 그 사실이 `limitation` 으로 남는다.
    """
    if aspect not in _ASPECTS:
        raise PolarityError(f"택소노미 밖 aspect {aspect!r} (PER-175 동결 14종)")

    ids = {r["reviewId"] for r in records}
    if len(ids) != len(records):
        raise PolarityError("같은 reviewId 가 두 번 들어왔다")
    for tag in tags:
        if tag.get("aspect") == aspect and tag.get("reviewId") not in ids:
            raise PolarityError(
                f"묶음에 없는 reviewId {tag.get('reviewId')!r} 의 {aspect!r} 태그가 들어왔다"
            )

    counts = {POSITIVE: 0, NEGATIVE: 0, NEUTRAL: 0}
    seen: set[int] = set()
    for tag in tags:
        if tag.get("aspect") != aspect:
            continue
        rid = tag["reviewId"]
        if rid in seen:
            raise PolarityError(
                f"reviewId {rid} 의 {aspect!r} 태그가 두 번이다 — 한 사람이 두 표가 된다"
            )
        seen.add(rid)
        polarity = tag.get("polarity")
        if polarity not in counts:
            raise PolarityError(f"polarity 가 {POLARITIES} 밖이다 — {polarity!r}")
        counts[polarity] += 1

    pos, neg, neu = counts[POSITIVE], counts[NEGATIVE], counts[NEUTRAL]
    directional = pos + neg
    # 침묵 — 이 묶음에 있으면서 이 축을 말하지 않은 사람. 분모에 넣지 않는다 (PER-178)
    silent = len(records) - len(seen)

    limitations: set[str] = set()
    if order_chosen:
        if any((rid, aspect) in order_chosen for rid in seen):
            limitations.add(LIMIT_ORDER_CHOSEN_DIRECTION)

    if directional == 0:
        verdict = VERDICT_NEUTRAL_ONLY if neu else VERDICT_SILENT
        return AspectSupport(aspect, pos, neg, neu, silent, verdict, 1.0,
                             frozenset(limitations))

    minority = min(pos, neg)
    p_value = binom_sf(minority, directional, flip_rate)
    if p_value < alpha:
        verdict = VERDICT_MIXED
    else:
        verdict = VERDICT_NEGATIVE if neg > pos else VERDICT_POSITIVE
    # 방향 판정에는 언제나 태거의 방향 오류가 얹힌다 (정답셋 200건 기준 6.5%)
    limitations.add(LIMIT_TAGGER_DIRECTION_ERROR)
    return AspectSupport(aspect, pos, neg, neu, silent, verdict, p_value,
                         frozenset(limitations))


def polarity_gate(
    records: list[dict],
    tags: list[dict],
    aspects: tuple[str, ...] | list[str] = ASPECTS,
    *,
    flip_rate: float = TAGGER_FLIP_RATE,
    alpha: float = MIXED_ALPHA,
    order_chosen: set[tuple[int, str]] | None = None,
) -> PolarityResult:
    """게이트3 — 남은 근거가 한 방향인가. 탈락시키지 않고 판정과 플래그를 낸다.

    입력은 **게이트1·2 통과분**이다. 그 전제를 가정하지 않고 `independent_reviews` 로
    확인한다 — 같은 작성자가 두 번 들어와 있으면 그 사람의 의견이 두 표가 되고,
    `negativeRatio` 가 조용히 그 사람 쪽으로 기운다.
    """
    independent_reviews(records)  # 게이트2 통과분인지 확인. 아니면 GateError
    ids = {r["reviewId"] for r in records}
    _check_tags(tags, ids)

    result = PolarityResult()
    for aspect in aspects:
        support = aspect_support(records, tags, aspect, flip_rate=flip_rate,
                                 alpha=alpha, order_chosen=order_chosen)
        result.supports.append(support)
    result.conflicts = rating_conflicts(records, tags)
    return result


__all__ = [
    "CONFLICT_ALL_ASPECTS",
    "CONFLICT_KINDS",
    "CONFLICT_LABELS",
    "CONFLICT_MINORITY_ASPECT",
    "CONFLICT_SOLE_ASPECT",
    "GATE_POLARITY",
    "LIMIT_ORDER_CHOSEN_DIRECTION",
    "LIMIT_TAGGER_DIRECTION_ERROR",
    "MIXED_ALPHA",
    "TAGGER_FLIP_RATE",
    "TAGGER_FLIP_RATE_SOURCE",
    "VERDICTS",
    "VERDICT_LABELS",
    "VERDICT_MIXED",
    "VERDICT_NEGATIVE",
    "VERDICT_NEUTRAL_ONLY",
    "VERDICT_POSITIVE",
    "VERDICT_SILENT",
    "AspectSupport",
    "PolarityError",
    "PolarityResult",
    "RatingConflict",
    "aspect_support",
    "binom_sf",
    "polarity_gate",
    "rating_conflicts",
]
