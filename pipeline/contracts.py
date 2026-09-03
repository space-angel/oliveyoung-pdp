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
    표기 단계에서 쓴다 — 입수는 코드를 그대로 보존한다
"""
from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass
from enum import Enum

SCHEMA_VERSION = "v5-1"
MISSING_SEGMENT = "미기재"
CONTENT_HASH_HEX = 16

# 조건축. 여기에 없는 필드는 세그먼트로 쓰지 않는다.
CONDITION_AXES = ("skinType", "skinTrouble", "option")


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
    def of(cls, raw_value) -> "SingleCondition":
        code = _clean(raw_value)
        if not code:
            return cls(code=None, stated=False, segment=MISSING_SEGMENT)
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
    def of(cls, raw_value) -> "MultiCondition":
        codes = tuple(sorted({_clean(c) for c in (raw_value or []) if _clean(c)}))
        if not codes:
            return cls(codes=(), stated=False, segments=(MISSING_SEGMENT,))
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
    "reviewerRank": "신뢰도 산식이 등급 신호를 쓸지 미결 (PER-174)",
    "isTopReviewer": "같음 (PER-174)",
}


def build_record(row: dict, product_id: str) -> ReviewRecord:
    """원본 리뷰 1건 → v5 레코드. LLM 없음, 순수 함수."""
    missing = [f for f in ("reviewId", "content", "rating", "reviewDate", "userName") if f not in row]
    if missing:
        raise ValueError(f"필수 필드 누락 {missing} (reviewId={row.get('reviewId')})")

    raw = {f: row.get(f) for f in RAW_FIELDS}
    condition = {
        "skinType": SingleCondition.of(row.get("skinType")).as_dict(),
        "skinTrouble": MultiCondition.of(row.get("skinTrouble")).as_dict(),
        "option": SingleCondition.of(row.get("option")).as_dict(),
    }
    date = _clean(row.get("reviewDate"))
    derived = {
        "authorKey": author_key(row["userName"]),
        "contentHash": content_hash(row["content"]),
        "contentLength": len(row["content"].strip()),
        "sentimentPrior": sentiment_prior(row["rating"]).value,
        "reviewYearMonth": date[:7].replace(".", "-") if len(date) >= 7 else None,
    }
    return ReviewRecord(
        review_id=row["reviewId"],
        product_id=product_id,
        raw=raw,
        condition=condition,
        derived=derived,
    )
