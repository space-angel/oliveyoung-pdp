"""
게이트3(방향성, PER-185) 계약 테스트.

## 완료 조건

  1. polarity 가 `(리뷰 × 주제)` 단위로 저장·집계된다 — 리뷰 1건에 방향 1개가 아니다
  2. 별점과 교차 검증하되 **자동으로 정하지 않는다** — 불일치는 판정을 바꾸지 않고
     플래그로 모이고, 라벨 가치 순으로 층화된다
  3. `혼재` 판정 시 `support.negativeRatio` 가 함께 산출된다
  4. 반대 근거를 버리지 않는다 — 이 게이트에는 `rejected[]` 가 없다
  5. 침묵은 근거가 아니다 — `silentAuthors` 는 `negativeRatio` 의 분모 밖이다 (PER-178)
  6. 소수 방향이 태거 잡음과 구별될 때만 `혼재` 다
  7. 입력이 계약을 위반하면 조용히 넘기지 않고 에러다

  python3 -m unittest discover -s pipeline -p 'test_*.py'
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from gates import GateError  # noqa: E402
from polarity import (  # noqa: E402
    CONFLICT_ALL_ASPECTS,
    CONFLICT_MINORITY_ASPECT,
    CONFLICT_SOLE_ASPECT,
    LIMIT_ORDER_CHOSEN_DIRECTION,
    LIMIT_TAGGER_DIRECTION_ERROR,
    TAGGER_FLIP_RATE,
    VERDICT_LABELS,
    VERDICT_MIXED,
    VERDICT_NEGATIVE,
    VERDICT_NEUTRAL_ONLY,
    VERDICT_POSITIVE,
    VERDICT_SILENT,
    AspectSupport,
    PolarityError,
    aspect_support,
    binom_sf,
    polarity_gate,
    rating_conflicts,
)

ABSORB = "흡수력"
LASTING = "지속성"


def record(review_id: int, rating: int = 5, *, author: str | None = None,
           product_id: str = "p001") -> dict:
    """게이트3 가 읽는 필드만 갖춘 최소 레코드 (게이트2 통과분 모양)."""
    sentiment = "negative" if rating <= 2 else "positive" if rating >= 4 else "neutral"
    return {
        "reviewId": review_id,
        "productId": product_id,
        "raw": {"rating": rating, "reviewDate": "2026-01-15"},
        "derived": {
            "authorKey": author or f"u{review_id}",
            "contentHash": f"h{review_id}",
            "sentimentPrior": sentiment,
            "trustPrior": {"score": 0.5},
        },
    }


def tag(review_id: int, aspect: str = ABSORB, polarity: str = "positive",
        snippet: str = "잘 흡수돼요") -> dict:
    return {"reviewId": review_id, "aspect": aspect, "polarity": polarity,
            "snippet": snippet, "skinTypeHint": None}


def bulk(start: int, n: int, polarity: str, aspect: str = ABSORB,
         rating: int = 5) -> tuple[list[dict], list[dict]]:
    recs = [record(start + i, rating) for i in range(n)]
    tags = [tag(start + i, aspect, polarity) for i in range(n)]
    return recs, tags


class TagUnit(unittest.TestCase):
    """완료 조건 1 — 방향은 `(리뷰 × 주제)` 단위다."""

    def test_one_review_carries_two_directions_on_two_aspects(self):
        """"발색은 좋은데 지속력은 별로" — 리뷰 1건이 축마다 다른 방향을 낸다."""
        recs = [record(1)]
        tags = [tag(1, ABSORB, "positive"), tag(1, LASTING, "negative")]
        result = polarity_gate(recs, tags, [ABSORB, LASTING])
        by = result.by_aspect()
        self.assertEqual(by[ABSORB].positive_authors, 1)
        self.assertEqual(by[ABSORB].negative_authors, 0)
        self.assertEqual(by[LASTING].negative_authors, 1)
        self.assertEqual(by[LASTING].positive_authors, 0)

    def test_same_aspect_twice_is_an_error(self):
        """한 사람이 두 표가 되는 유일한 경로라 조용히 합치지 않는다."""
        with self.assertRaises(PolarityError) as cm:
            polarity_gate([record(1)], [tag(1, ABSORB, "positive"),
                                        tag(1, ABSORB, "negative")], [ABSORB])
        self.assertIn("두 번", str(cm.exception))

    def test_counts_are_authors_not_reviews(self):
        """게이트2 를 통과하지 않은 묶음(같은 작성자 2건)은 에러다."""
        recs = [record(1, author="같은사람"), record(2, author="같은사람")]
        with self.assertRaises(GateError):
            polarity_gate(recs, [tag(1), tag(2)], [ABSORB])


class MixedVerdict(unittest.TestCase):
    """완료 조건 3·6 — `혼재` 와 `negativeRatio`."""

    def test_mixed_emits_negative_ratio(self):
        pos_r, pos_t = bulk(100, 12, "positive")
        neg_r, neg_t = bulk(200, 8, "negative", rating=1)
        support = aspect_support(pos_r + neg_r, pos_t + neg_t, ABSORB)
        self.assertEqual(support.verdict, VERDICT_MIXED)
        self.assertEqual(support.directional_authors, 20)
        self.assertEqual(support.negative_ratio, 0.4)
        self.assertEqual(support.as_dict()["support"]["negativeRatio"], 0.4)
        self.assertEqual(support.as_dict()["label"], "혼재")

    def test_negative_ratio_is_emitted_even_when_not_mixed(self):
        """판정일 때만 내면 호출부가 판정을 보고 비율을 감출 수 있다."""
        pos_r, pos_t = bulk(100, 40, "positive")
        neg_r, neg_t = bulk(200, 1, "negative", rating=1)
        support = aspect_support(pos_r + neg_r, pos_t + neg_t, ABSORB)
        self.assertEqual(support.verdict, VERDICT_POSITIVE)
        self.assertIsNotNone(support.negative_ratio)
        self.assertAlmostEqual(support.negative_ratio, 1 / 41, places=4)

    def test_lone_minority_is_not_mixed(self):
        """소수 1건은 태거가 뒤집은 것으로 설명된다 — 갈렸다고 말하지 않는다."""
        pos_r, pos_t = bulk(100, 30, "positive")
        neg_r, neg_t = bulk(200, 1, "negative", rating=1)
        self.assertEqual(
            aspect_support(pos_r + neg_r, pos_t + neg_t, ABSORB).verdict, VERDICT_POSITIVE)

    def test_threshold_scales_with_sample_size(self):
        """같은 비율이라도 표본이 크면 갈림이고 작으면 잡음이다.

        고정 비율 문턱을 쓰지 않는 이유가 이것이다 — 10% 소수는 n=20 에서 2건이라
        태거 잡음으로 설명되지만 n=200 에서 20건이면 설명되지 않는다.
        """
        small_r, small_t = bulk(100, 18, "positive")
        small_n, small_nt = bulk(200, 2, "negative", rating=1)
        small = aspect_support(small_r + small_n, small_t + small_nt, ABSORB)

        big_r, big_t = bulk(1000, 180, "positive")
        big_n, big_nt = bulk(2000, 20, "negative", rating=1)
        big = aspect_support(big_r + big_n, big_t + big_nt, ABSORB)

        self.assertEqual(small.negative_ratio, big.negative_ratio)
        self.assertEqual(small.verdict, VERDICT_POSITIVE)
        self.assertEqual(big.verdict, VERDICT_MIXED)

    def test_majority_negative_reads_negative(self):
        pos_r, pos_t = bulk(100, 1, "positive")
        neg_r, neg_t = bulk(200, 30, "negative", rating=1)
        self.assertEqual(
            aspect_support(pos_r + neg_r, pos_t + neg_t, ABSORB).verdict, VERDICT_NEGATIVE)

    def test_binom_sf_edges(self):
        self.assertEqual(binom_sf(0, 10, 0.033), 1.0)
        self.assertEqual(binom_sf(11, 10, 0.033), 0.0)
        self.assertAlmostEqual(binom_sf(1, 1, 0.033), 0.033, places=6)

    def test_binom_sf_matches_direct_sum_on_small_n(self):
        """로그 계산이 직접 합과 같은 값을 내는지 — 정확도 회귀 방지."""
        import math
        for n in (1, 5, 20, 80):
            for k in (1, 2, n // 2 or 1, n):
                direct = sum(math.comb(n, i) * 0.033**i * 0.967 ** (n - i)
                             for i in range(k, n + 1))
                self.assertAlmostEqual(binom_sf(k, n, 0.033), direct, places=12)

    def test_binom_sf_survives_large_cells(self):
        """`math.comb` 를 그대로 쓰면 n≈1,100 부터 OverflowError 다.

        지금 스냅샷의 최대 셀은 그보다 작지만, 조건축을 합치거나 새 수집분이
        들어오면 닿는 크기다 — 큰 제품에서만 터지는 버그는 가장 늦게 발견된다.
        """
        for n in (1500, 25000):
            self.assertEqual(binom_sf(n // 2, n, TAGGER_FLIP_RATE), 0.0)
            self.assertAlmostEqual(binom_sf(1, n, TAGGER_FLIP_RATE), 1.0, places=9)

    def test_mixed_verdict_holds_at_large_n(self):
        pos_r, pos_t = bulk(10_000, 900, "positive")
        neg_r, neg_t = bulk(90_000, 700, "negative", rating=1)
        support = aspect_support(pos_r + neg_r, pos_t + neg_t, ABSORB)
        self.assertEqual(support.verdict, VERDICT_MIXED)

    def test_flip_rate_outside_open_unit_interval_is_an_error(self):
        with self.assertRaises(PolarityError):
            binom_sf(1, 10, 0.0)
        with self.assertRaises(PolarityError):
            binom_sf(1, 10, 1.0)

    def test_flip_rate_is_a_measured_upper_bound(self):
        """점추정(1.29%)이 아니라 95% 상한을 쓴다 — 혼재를 덜 붙이는 쪽이다."""
        self.assertGreater(TAGGER_FLIP_RATE, 0.0129)
        self.assertLess(TAGGER_FLIP_RATE, 0.065)


class SilenceIsNotEvidence(unittest.TestCase):
    """완료 조건 5 — 침묵은 근거가 아니다 (PER-178)."""

    def test_silent_authors_stay_out_of_the_ratio(self):
        """태그가 없는 리뷰를 분모에 넣으면 모든 비율이 조용히 희석된다."""
        recs = [record(i) for i in range(1, 101)]
        tags = [tag(1, ABSORB, "positive"), tag(2, ABSORB, "negative")]
        support = aspect_support(recs, tags, ABSORB)
        self.assertEqual(support.silent_authors, 98)
        self.assertEqual(support.directional_authors, 2)
        self.assertEqual(support.negative_ratio, 0.5)

    def test_no_mention_is_silent_not_positive(self):
        support = aspect_support([record(i) for i in range(1, 21)], [], ABSORB)
        self.assertEqual(support.verdict, VERDICT_SILENT)
        self.assertEqual(support.silent_authors, 20)
        self.assertIsNone(support.negative_ratio)

    def test_neutral_only_is_distinct_from_silence(self):
        """언급했는데 방향이 없는 것과, 아예 언급이 없는 것은 다르다."""
        recs = [record(1), record(2)]
        support = aspect_support(recs, [tag(1, ABSORB, "neutral")], ABSORB)
        self.assertEqual(support.verdict, VERDICT_NEUTRAL_ONLY)
        self.assertEqual(support.neutral_authors, 1)
        self.assertEqual(support.silent_authors, 1)
        self.assertIsNone(support.negative_ratio)

    def test_neutral_is_out_of_the_denominator(self):
        pos_r, pos_t = bulk(100, 5, "positive")
        neu_r, neu_t = bulk(300, 20, "neutral")
        neg_r, neg_t = bulk(200, 5, "negative", rating=1)
        support = aspect_support(pos_r + neu_r + neg_r, pos_t + neu_t + neg_t, ABSORB)
        self.assertEqual(support.directional_authors, 10)
        self.assertEqual(support.negative_ratio, 0.5)
        self.assertEqual(support.neutral_authors, 20)


class RatingCrossCheck(unittest.TestCase):
    """완료 조건 2 — 교차 검증하되 자동으로 정하지 않는다."""

    def test_conflict_does_not_change_the_verdict(self):
        """별점이 태그를 이기지도, 태그가 별점을 이기지도 않는다."""
        recs = [record(i, rating=1) for i in range(1, 21)]
        tags = [tag(i, ABSORB, "positive") for i in range(1, 21)]
        result = polarity_gate(recs, tags, [ABSORB])
        self.assertEqual(result.by_aspect()[ABSORB].verdict, VERDICT_POSITIVE)
        self.assertEqual(result.by_aspect()[ABSORB].negative_authors, 0)
        self.assertEqual(len(result.conflicts), 20)

    def test_sole_aspect_conflict(self):
        """유일한 방향 태그가 별점과 반대 — 둘 중 하나는 틀렸다."""
        conflicts = rating_conflicts([record(1, rating=1)], [tag(1, ABSORB, "positive")])
        self.assertEqual([c.kind for c in conflicts], [CONFLICT_SOLE_ASPECT])
        self.assertEqual(conflicts[0].rating, 1)
        self.assertEqual(conflicts[0].rating_sentiment, "negative")
        self.assertEqual(conflicts[0].tag_polarity, "positive")

    def test_minority_aspect_conflict_is_the_normal_case(self):
        """"좋은데 지속력은 별로" — 별점 5점에 부정 태그 1개는 정상이다."""
        conflicts = rating_conflicts(
            [record(1, rating=5)],
            [tag(1, ABSORB, "positive"), tag(1, LASTING, "negative")])
        self.assertEqual([c.kind for c in conflicts], [CONFLICT_MINORITY_ASPECT])
        self.assertEqual(conflicts[0].aspect, LASTING)

    def test_all_aspects_conflict(self):
        conflicts = rating_conflicts(
            [record(1, rating=5)],
            [tag(1, ABSORB, "negative"), tag(1, LASTING, "negative")])
        self.assertEqual({c.kind for c in conflicts}, {CONFLICT_ALL_ASPECTS})
        self.assertEqual(len(conflicts), 2)

    def test_rating_three_is_no_information_not_a_conflict(self):
        """3점은 방향을 말하지 않는다. 불일치로 세면 평가셋이 3점으로 채워진다."""
        self.assertEqual(rating_conflicts([record(1, rating=3)],
                                          [tag(1, ABSORB, "negative")]), [])

    def test_neutral_tag_is_never_a_conflict(self):
        self.assertEqual(rating_conflicts([record(1, rating=1)],
                                          [tag(1, ABSORB, "neutral")]), [])

    def test_conflicts_are_stratified_for_labelling(self):
        recs = [record(1, rating=1), record(2, rating=5), record(3, rating=5)]
        tags = [
            tag(1, ABSORB, "positive"),
            tag(2, ABSORB, "positive"), tag(2, LASTING, "negative"),
            tag(3, ABSORB, "negative"), tag(3, LASTING, "negative"),
        ]
        result = polarity_gate(recs, tags, [ABSORB, LASTING])
        self.assertEqual(result.conflicts_by_kind(), {
            CONFLICT_ALL_ASPECTS: 2,
            CONFLICT_MINORITY_ASPECT: 1,
            CONFLICT_SOLE_ASPECT: 1,
        })

    def test_missing_sentiment_prior_is_an_error(self):
        rec = record(1, rating=1)
        del rec["derived"]["sentimentPrior"]
        with self.assertRaises(PolarityError):
            rating_conflicts([rec], [tag(1, ABSORB, "positive")])


class EvidenceIsNotDiscarded(unittest.TestCase):
    """완료 조건 4 — 반대 근거를 버리지 않는다."""

    def test_gate_has_no_rejected_rows(self):
        pos_r, pos_t = bulk(100, 12, "positive")
        neg_r, neg_t = bulk(200, 8, "negative", rating=1)
        result = polarity_gate(pos_r + neg_r, pos_t + neg_t, [ABSORB])
        self.assertFalse(hasattr(result, "rejected"))
        support = result.by_aspect()[ABSORB]
        self.assertEqual(support.positive_authors + support.negative_authors,
                         len(pos_r) + len(neg_r))

    def test_both_sides_are_reported(self):
        pos_r, pos_t = bulk(100, 12, "positive")
        neg_r, neg_t = bulk(200, 8, "negative", rating=1)
        d = aspect_support(pos_r + neg_r, pos_t + neg_t, ABSORB).as_dict()
        self.assertEqual(d["support"]["positiveAuthors"], 12)
        self.assertEqual(d["support"]["negativeAuthors"], 8)


class Limitations(unittest.TestCase):
    """판정이 통과해도 한계는 따라나간다 (게이트1 `renewal_unobserved` 와 같은 규칙)."""

    def test_direction_always_carries_tagger_error(self):
        recs, tags = bulk(100, 10, "positive")
        support = aspect_support(recs, tags, ABSORB)
        self.assertIn(LIMIT_TAGGER_DIRECTION_ERROR, support.limitations)

    def test_silence_carries_no_direction_limitation(self):
        support = aspect_support([record(1)], [], ABSORB)
        self.assertNotIn(LIMIT_TAGGER_DIRECTION_ERROR, support.limitations)

    def test_order_chosen_direction_is_surfaced(self):
        """태거가 같은 축에 긍·부정을 둘 다 뱉으면 방향이 출력 순서로 정해졌다."""
        recs, tags = bulk(100, 10, "positive")
        support = aspect_support(recs, tags, ABSORB, order_chosen={(100, ABSORB)})
        self.assertIn(LIMIT_ORDER_CHOSEN_DIRECTION, support.limitations)

    def test_order_chosen_on_another_aspect_does_not_leak(self):
        recs, tags = bulk(100, 10, "positive")
        support = aspect_support(recs, tags, ABSORB, order_chosen={(100, LASTING)})
        self.assertNotIn(LIMIT_ORDER_CHOSEN_DIRECTION, support.limitations)


class InputContract(unittest.TestCase):
    """완료 조건 7 — 위반은 조용히 넘기지 않는다."""

    def test_unknown_review_id_is_an_error(self):
        """다른 실행의 태그를 섞으면 집계에서 조용히 빠진다."""
        with self.assertRaises(PolarityError) as cm:
            polarity_gate([record(1)], [tag(999)], [ABSORB])
        self.assertIn("999", str(cm.exception))

    def test_aspect_outside_taxonomy_is_an_error(self):
        with self.assertRaises(PolarityError):
            polarity_gate([record(1)], [tag(1, "가격흥정", "positive")], [ABSORB])

    def test_unknown_polarity_is_an_error(self):
        with self.assertRaises(PolarityError):
            polarity_gate([record(1)], [tag(1, ABSORB, "매우긍정")], [ABSORB])

    def test_asking_for_an_unknown_aspect_is_an_error(self):
        with self.assertRaises(PolarityError):
            aspect_support([record(1)], [], "없는축")


class Vocabulary(unittest.TestCase):
    def test_every_verdict_has_a_korean_label(self):
        for verdict in (VERDICT_POSITIVE, VERDICT_NEGATIVE, VERDICT_MIXED,
                        VERDICT_NEUTRAL_ONLY, VERDICT_SILENT):
            self.assertIn(verdict, VERDICT_LABELS)
        self.assertEqual(VERDICT_LABELS[VERDICT_MIXED], "혼재")

    def test_codes_and_labels_are_not_the_same_string(self):
        """표기를 고칠 때 판정 코드가 같이 흔들리지 않게 (게이트1·2 와 같은 규칙)."""
        for code, label in VERDICT_LABELS.items():
            self.assertNotEqual(code, label)

    def test_support_dict_shape(self):
        d = AspectSupport(ABSORB, 3, 1, 2, 5, VERDICT_POSITIVE, 0.4).as_dict()
        self.assertEqual(set(d["support"]), {
            "positiveAuthors", "negativeAuthors", "neutralAuthors",
            "silentAuthors", "directionalAuthors", "negativeRatio",
        })


if __name__ == "__main__":
    unittest.main()
