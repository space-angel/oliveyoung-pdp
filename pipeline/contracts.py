"""
v5 입력 계약 — 원문 / 조건 / 파생 3층 (PRD §3-1, PER-173).

원문만 저장하면 나중에 전부 다시 계산해야 하고, 파생만 저장하면 계산이 틀렸을 때
되돌릴 수 없다. 그래서 층을 나눈다.

  raw        수집한 값. 손대지 않는다. 재계산의 기준점
  condition  세그먼트 축. `skinType`(단일) · `skinTrouble`(다중) · `option`
  derived    재계산 가능한 값. 규칙이 바뀌면 raw 에서 다시 만든다

확정된 경계 (이전 결정들)
  - 집계 단위는 `productId` (PER-171 카탈로그). 리뷰 행의 `productKey` 문자열은 쓰지 않는다
  - 작성자 키는 `NFC(userName)` 원문 (PER-170). 중복 판정 단위는 `(authorKey, productId)`
  - `usagePeriod` 는 조건축에서 제외 — 데이터에 필드가 없다 (확정된 비목표)
  - `skinTone` 은 조건축이 아니다. raw 에만 남긴다
  - **미기재를 "조건 없음"으로 취급하지 않는다.** `segment` 는 항상 값이 있고,
    미기재는 `MISSING_SEGMENT` 라는 별도 세그먼트다 (§7-1 '조건 누락' 실패 방지)
  - 조건 코드의 라벨(A01 → 건성 …)은 이 층에서 붙이지 않는다. 코드북(PER-169)은
    표기 단계에서 쓴다 — 입수는 코드를 **검증만 하고** 그대로 보존한다

## 계약 위반은 에러다 (PER-176)

이 모듈이 계약의 **강제 지점**이다. 위반을 통과시키면 사고가 집계 수치로 나타난 뒤에야
발견되므로, 아래는 전부 `ContractError` 다 — 조용한 폴백·기본값 없음.

  결측/공백    필수 5필드(reviewId·content·rating·reviewDate·userName)가 없거나 비었다
  타입/범위    `reviewId` 가 int 가 아니거나 ≤0, `rating` 이 1~5 밖
  날짜         `reviewDate` 를 월로 읽을 수 없다 — 리센시 컷(PER-172)의 입력이다
  조건 코드    코드북(PER-169) 도메인 밖 · 라벨 · 축이 섞인 코드
  스키마       스냅샷의 필드 집합이 계약과 다르다 (`assert_row_schema`)

특히 두 가지는 **조용히 틀리는** 경로라 명시적으로 막는다.
  - 빈 `userName` → 작성자 키가 빈 문자열이 되어 서로 다른 사람이 한 사람으로 합쳐진다
  - 파싱 불가 `reviewDate` → 예전에는 `reviewYearMonth` 가 조용히 `None` 이 됐다.
    리센시 컷이 그 값을 읽으므로, 여기서 멈추지 않으면 컷이 뒤에서 터지거나 빗나간다
"""
from __future__ import annotations

import hashlib
import sys
import unicodedata
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from codebook import UnknownConditionCodeError, load_codebook  # noqa: E402
from policy import PolicyError, month_of  # noqa: E402

SCHEMA_VERSION = "v5-1"
MISSING_SEGMENT = "미기재"
CONTENT_HASH_HEX = 16

# 조건축. 여기에 없는 필드는 세그먼트로 쓰지 않는다.
CONDITION_AXES = ("skinType", "skinTrouble", "option")

# 조건축 → 코드북 축. `option` 은 자유 문자열이라 도메인이 없다(25K 에서 798종).
CODED_AXES = {"skinType": "skinType", "skinTrouble": "skinTrouble"}

# 값이 없으면 레코드를 만들 수 없는 필드. 나머지 raw 필드는 없으면 None 이다.
REQUIRED_FIELDS = ("reviewId", "content", "rating", "reviewDate", "userName")
RATING_RANGE = (1, 5)


class ContractError(ValueError):
    """입력이 v5 입력 계약을 위반했다. 조용한 폴백 금지 (PER-176).

    `ValueError` 를 상속한다 — 호출부가 이미 `ValueError` 로 잡고 있어도 계약 위반이
    빠져나가지 않게 하려는 것이다.
    """


def _fail(row: dict, message: str) -> None:
    raise ContractError(f"[reviewId={row.get('reviewId')!r}] {message}")


class Sentiment(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


def author_key(user_name: str) -> str:
    """작성자 키 (PER-170). 원문을 쓰고 유니코드 표현만 NFC 로 고정한다."""
    return unicodedata.normalize("NFC", user_name)


def content_hash(content: str) -> str:
    """본문 완전일치 중복 판정용 (게이트2, PER-183)."""
    return hashlib.sha256(content.strip().encode("utf-8")).hexdigest()[:CONTENT_HASH_HEX]


def sentiment_prior(rating: int) -> Sentiment:
    if rating <= 2:
        return Sentiment.NEGATIVE
    if rating >= 4:
        return Sentiment.POSITIVE
    return Sentiment.NEUTRAL


def _clean(value) -> str:
    return (value or "").strip() if isinstance(value, str) or value is None else str(value)


@dataclass(frozen=True)
class SingleCondition:
    """단일 라벨 조건축 (`skinType`, `option`)."""
    code: str | None
    stated: bool
    segment: str

    def as_dict(self) -> dict:
        return {"code": self.code, "stated": self.stated, "segment": self.segment}

    @classmethod
    def of(cls, raw_value, axis: str | None = None) -> "SingleCondition":
        """`axis` 를 주면 코드북 도메인까지 검증한다. `option` 은 도메인이 없어 None 이다."""
        if raw_value is not None and not isinstance(raw_value, str):
            raise ContractError(
                f"{axis or '단일 조건축'} 은 문자열이어야 한다 (받은 값 {raw_value!r})"
            )
        code = _clean(raw_value)
        if not code:
            return cls(code=None, stated=False, segment=MISSING_SEGMENT)
        if axis in CODED_AXES:
            load_codebook().assert_code(CODED_AXES[axis], code)
        return cls(code=code, stated=True, segment=code)


@dataclass(frozen=True)
class MultiCondition:
    """다중 라벨 조건축 (`skinTrouble`). 한 리뷰가 여러 세그먼트에 속한다.

    조합을 하나의 키로 묶지 않는다 — 25K 에서 조합이 89종이라 셀이 즉시 희소해진다.
    """
    codes: tuple[str, ...]
    stated: bool
    segments: tuple[str, ...]

    def as_dict(self) -> dict:
        """JSON 계약 형태 — tuple 이 아니라 list 로 낸다."""
        return {"codes": list(self.codes), "stated": self.stated, "segments": list(self.segments)}

    @classmethod
    def of(cls, raw_value, axis: str | None = None) -> "MultiCondition":
        # 문자열을 그냥 받으면 문자 단위로 쪼개져 'C05' 가 세그먼트 ['0','5','C'] 가 된다.
        if raw_value is not None and not isinstance(raw_value, (list, tuple)):
            raise ContractError(
                f"{axis or '다중 조건축'} 은 코드 배열이어야 한다 (받은 값 {raw_value!r}). "
                "문자열을 넘기면 문자 단위로 쪼개진다"
            )
        codes = tuple(sorted({_clean(c) for c in (raw_value or []) if _clean(c)}))
        if not codes:
            return cls(codes=(), stated=False, segments=(MISSING_SEGMENT,))
        if axis in CODED_AXES:
            codebook = load_codebook()
            for code in codes:
                codebook.assert_code(CODED_AXES[axis], code)
        return cls(codes=codes, stated=True, segments=codes)


@dataclass(frozen=True)
class ReviewRecord:
    """v5 입수 레코드 1건. `to_dict()` 결과가 파이프라인의 유일한 입력 형식이다."""
    review_id: int
    product_id: str
    raw: dict
    condition: dict
    derived: dict

    def to_dict(self) -> dict:
        return {
            "reviewId": self.review_id,
            "productId": self.product_id,
            "raw": self.raw,
            "condition": self.condition,
            "derived": self.derived,
        }


# 원문층에 그대로 옮기는 필드. 여기 없는 원본 필드는 v5 입력에 들어가지 않는다.
RAW_FIELDS = (
    "content",
    "rating",
    "reviewDate",
    "reviewType",
    "isRepurchase",
    "isMonthUseReview",
    "isMonthOverReview",
    "hasPhoto",
    "usefulPoint",
    "recommendCount",
    "goodsNo",
    "requestedGoodsNo",
    "productName",
    "option",
    "userName",
    "skinType",
    "skinTone",
    "skinTrouble",
)

# 입수에서 버리는 필드와 사유. 계약 위반 여부를 사람이 확인할 수 있게 남긴다.
DROPPED_FIELDS = {
    "productKey": "제품 동일성은 카탈로그가 정한다 (PER-171). 행의 문자열을 신뢰하지 않는다",
    "category": "카탈로그의 category 를 쓴다 (같은 제품인데 행마다 다를 수 있다)",
    "profileImageUrl": "식별자 조합키로는 증가분 0 (PER-170). 감사 리포트에서만 쓴다",
    "reviewImages": "v5 텍스트 파이프라인 미사용. PDP 렌더링은 별도 조회",
    "reviewerRank": "신뢰도 산식이 등급 신호를 쓰지 않기로 확정 (PER-174). isTopReviewer 와 사실상 같은 신호이고(3,070/3,083 중첩) 효과 구간이 0을 포함한다",
    "isTopReviewer": "같음 (PER-174)",
}


# 계약이 아는 필드 전체. 스냅샷이 이 집합과 다르면 새 수집분이거나 크롤러가 바뀐 것이다.
KNOWN_FIELDS = frozenset(RAW_FIELDS) | frozenset(DROPPED_FIELDS) | {"reviewId"}


def assert_row_schema(row: dict) -> None:
    """스냅샷 행의 **필드 집합**을 검증한다 (값이 아니라 모양).

    `build_record` 가 아니라 파일을 읽는 경계(`ingest`)에서 부른다 — 값 검증은 레코드
    단위지만, 필드가 늘거나 준 것은 스냅샷 전체의 사건이기 때문이다.

    모르는 필드를 조용히 버리지 않는 이유: 새 필드는 원문/조건/파생/드롭 중 어디에
    속하는지 **사람이 정해야 한다.** 버리고 넘어가면 그 결정이 영영 일어나지 않는다.
    """
    unknown = sorted(set(row) - KNOWN_FIELDS)
    if unknown:
        _fail(row, (
            f"계약에 없는 필드 {unknown}. 크롤러가 바뀌었거나 새 수집분이다 — "
            "RAW_FIELDS(원문에 싣는다) 나 DROPPED_FIELDS(버리는 사유를 적는다) 중 "
            "어디에 속하는지 정하라"
        ))
    absent = sorted(KNOWN_FIELDS - set(row))
    if absent:
        _fail(row, (
            f"계약이 기대하는 필드가 없다 {absent}. 그냥 두면 원문층에 조용히 null 이 "
            "들어가고, 그 사실은 집계가 틀어진 뒤에 발견된다"
        ))


def _validate_required(row: dict) -> None:
    """필수 5필드 — 있음 / 비어 있지 않음 / 타입 / 범위."""
    absent = [f for f in REQUIRED_FIELDS if f not in row]
    if absent:
        _fail(row, f"필수 필드 누락 {absent}")

    review_id = row["reviewId"]
    # bool 은 int 의 하위 타입이라 따로 막는다
    if isinstance(review_id, bool) or not isinstance(review_id, int) or review_id <= 0:
        _fail(row, f"reviewId 는 양의 정수여야 한다 (받은 값 {review_id!r})")

    for field in ("content", "reviewDate", "userName"):
        value = row[field]
        if not isinstance(value, str) or not value.strip():
            _fail(row, f"{field} 가 비었거나 문자열이 아니다 (받은 값 {value!r})")

    rating = row["rating"]
    low, high = RATING_RANGE
    if isinstance(rating, bool) or not isinstance(rating, int) or not low <= rating <= high:
        _fail(row, f"rating 은 {low}~{high} 의 정수여야 한다 (받은 값 {rating!r})")


def build_record(row: dict, product_id: str) -> ReviewRecord:
    """원본 리뷰 1건 → v5 레코드. LLM 없음, 순수 함수. 위반은 `ContractError`."""
    _validate_required(row)

    raw = {f: row.get(f) for f in RAW_FIELDS}
    try:
        condition = {
            axis: (MultiCondition if axis == "skinTrouble" else SingleCondition)
            .of(row.get(axis), axis=axis)
            .as_dict()
            for axis in CONDITION_AXES
        }
    except UnknownConditionCodeError as e:
        # 코드북 위반도 계약 위반이다. 호출부가 예외 종류로 갈라 처리하지 않게 하나로 모으고,
        # 어느 리뷰인지 문맥을 붙인다.
        _fail(row, str(e))
    # 리센시 컷(PER-172)이 읽는 값이다. 예전에는 파싱 실패가 조용히 None 이 됐다.
    try:
        year_month = month_of(row["reviewDate"])
    except PolicyError as e:
        _fail(row, f"{e} — 리센시 컷이 이 값을 읽는다")

    derived = {
        "authorKey": author_key(row["userName"]),
        "contentHash": content_hash(row["content"]),
        "contentLength": len(row["content"].strip()),
        "sentimentPrior": sentiment_prior(row["rating"]).value,
        "reviewYearMonth": year_month,
    }
    return ReviewRecord(
        review_id=row["reviewId"],
        product_id=product_id,
        raw=raw,
        condition=condition,
        derived=derived,
    )
