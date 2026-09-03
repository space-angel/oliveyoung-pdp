"""
Data schemas for concern pipeline v4.
All dataclasses map directly to the PM data model spec (02_data_model.md).
"""
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


class Sentiment(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


class ConcernCategory(str, Enum):
    FIT = "적합성"
    RISK = "리스크"
    USAGE = "실사용"
    COMPARISON = "비교"


@dataclass
class Review:
    review_id: int
    product_key: str
    content: str
    rating: int
    likes: int
    is_repurchase: bool
    category: str
    sentiment_prior: Sentiment = Sentiment.NEUTRAL

    def __post_init__(self):
        if self.rating <= 2:
            self.sentiment_prior = Sentiment.NEGATIVE
        elif self.rating >= 4:
            self.sentiment_prior = Sentiment.POSITIVE
        else:
            self.sentiment_prior = Sentiment.NEUTRAL


@dataclass
class Claim:
    """One extracted aspect+sentiment from a single review (Step 1 output)."""
    review_id: int
    product_key: str
    aspect: str
    sentiment: Sentiment
    snippet: str
    skin_type_hint: Optional[str] = None


@dataclass
class ClaimGroup:
    """Aggregated claims for one aspect across reviews (Step 2 output)."""
    product_key: str
    aspect_label: str
    cluster_id: str
    review_ids: list[int] = field(default_factory=list)
    pos_count: int = 0
    neg_count: int = 0
    neutral_count: int = 0
    positive_snippets: list[str] = field(default_factory=list)
    negative_snippets: list[str] = field(default_factory=list)
    concern_category: Optional[ConcernCategory] = None  # filled in Step 3

    @property
    def total_count(self) -> int:
        return self.pos_count + self.neg_count + self.neutral_count

    @property
    def is_mixed(self) -> bool:
        return self.pos_count >= 2 and self.neg_count >= 2


@dataclass
class Concern:
    """Final purchase concern question with evidence (Step 4 output)."""
    concern_id: str
    product_key: str
    question: str
    category: ConcernCategory
    supporting_review_ids: list[int]
    positive_count: int
    negative_count: int
    positive_snippets: list[str]
    negative_snippets: list[str]
    confidence: float
