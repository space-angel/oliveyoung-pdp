"""
신뢰도 사전 점수 계약 테스트 (PER-174).

이 이슈의 완료 조건 세 문장을 여기서 고정한다.

  1. **필터가 아니라 가중치다** — 점수가 아무리 낮아도 레코드가 사라지지 않는다
  2. 산식이 **순수 함수** — 같은 리뷰에 항상 같은 점수 (§5-2)
  3. 각 신호의 가중치가 **상수가 아니라 설정** — 코드를 안 고치고 바꿀 수 있다

여기에 더해, 조용한 폴백을 막는 계약도 함께 고정한다.
  - 신호를 빠뜨린 설정은 기본값으로 돌아가지 않고 **에러**
  - 미가용 신호(`onTopic`)는 0점으로 깔리지 않고 `unavailable` 에 남는다

  python3 -m unittest discover -s pipeline -p 'test_*.py'
"""
import hashlib
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from trust import (  # noqa: E402
    DEFERRED_SIGNALS,
    SIGNALS,
    ScoringContext,
    TrustConfigError,
    TrustWeights,
    rank_key,
    rescore,
    score_all,
    signal_values,
    trust_prior,
)

WEIGHTS_PATH = Path(__file__).parent / "trust_weights.json"


def record(review_id: int, content: str, **raw) -> dict:
    """`trust` 가 읽는 필드만 갖춘 최소 레코드."""
    base = {
        "content": content,
        "hasPhoto": False,
        "isMonthOverReview": False,
        "recommendCount": 0,
        "reviewDate": "2026.07.19",
    }
    base.update(raw)
    return {
        "reviewId": review_id,
        "productId": "p001",
        "raw": base,
        "condition": {},
        "derived": {
            "contentHash": hashlib.sha256(content.strip().encode()).hexdigest()[:16],
            "contentLength": len(content.strip()),
        },
    }


class TestWeightsAreConfiguration(unittest.TestCase):
    """완료 조건 3 — 가중치는 코드 상수가 아니라 설정이다."""

    def test_default_config_loads_and_covers_every_signal(self):
        w = TrustWeights.load()
        self.assertEqual(set(w.weights), set(SIGNALS))
        self.assertTrue(w.source_sha256, "설정 해시를 meta 에 남길 수 있어야 한다")

    def test_changing_config_changes_score_without_touching_code(self):
        cfg = json.loads(WEIGHTS_PATH.read_text())
        recs = [record(1, "짧은 리뷰입니다 스무자를 넘겨서 씁니다", hasPhoto=True)]
        ctx = ScoringContext.from_records(recs)

        base = trust_prior(recs[0], ctx, TrustWeights.from_dict(cfg))
        cfg["weights"]["hasPhoto"] = 5.0
        boosted = trust_prior(recs[0], ctx, TrustWeights.from_dict(cfg))
        self.assertNotEqual(base["score"], boosted["score"])
        self.assertGreater(boosted["score"], base["score"])

    def test_missing_signal_is_an_error_not_a_default(self):
        cfg = json.loads(WEIGHTS_PATH.read_text())
        del cfg["weights"]["hasPhoto"]
        with self.assertRaises(TrustConfigError) as e:
            TrustWeights.from_dict(cfg)
        self.assertIn("hasPhoto", str(e.exception))

    def test_unknown_signal_is_an_error(self):
        cfg = json.loads(WEIGHTS_PATH.read_text())
        cfg["weights"]["reviewerRank"] = 1.0
        with self.assertRaises(TrustConfigError):
            TrustWeights.from_dict(cfg)

    def test_all_available_weights_zero_is_an_error(self):
        cfg = json.loads(WEIGHTS_PATH.read_text())
        for name in SIGNALS:
            cfg["weights"][name] = 0.0
        cfg["weights"]["onTopic"] = 1.0  # 미가용 신호만 남기면 입수 시점에 점수가 없다
        with self.assertRaises(TrustConfigError):
            TrustWeights.from_dict(cfg)

    def test_negative_weight_is_an_error(self):
        cfg = json.loads(WEIGHTS_PATH.read_text())
        cfg["weights"]["hasPhoto"] = -1.0
        with self.assertRaises(TrustConfigError):
            TrustWeights.from_dict(cfg)

    def test_bad_length_ramp_is_an_error(self):
        cfg = json.loads(WEIGHTS_PATH.read_text())
        cfg["contentLength"]["capChars"] = 10
        with self.assertRaises(TrustConfigError):
            TrustWeights.from_dict(cfg)
        cfg = json.loads(WEIGHTS_PATH.read_text())
        cfg["contentLength"]["shape"] = "exponential"
        with self.assertRaises(TrustConfigError):
            TrustWeights.from_dict(cfg)


class TestPureFunction(unittest.TestCase):
    """완료 조건 2 — 같은 리뷰에 항상 같은 점수 (§5-2)."""

    def test_same_input_same_score(self):
        recs = [record(1, "촉촉하고 흡수가 빨라서 아침에 쓰기 좋아요" * 3)]
        w = TrustWeights.load()
        ctx = ScoringContext.from_records(recs)
        first = trust_prior(recs[0], ctx, w)
        for _ in range(5):
            self.assertEqual(first, trust_prior(recs[0], ctx, w))

    def test_score_does_not_depend_on_record_order(self):
        a = record(1, "촉촉하고 흡수가 빨라요 아주 좋습니다 재구매합니다")
        b = record(2, "향이 강해서 저한테는 좀 부담스러웠어요 그래도 보습은 좋아요")
        forward = score_all([a, b])
        backward = score_all([b, a])
        self.assertEqual(forward, backward)

    def test_likes_ties_get_the_same_percentile(self):
        """동점을 순서로 가르면 파일 순서가 점수에 새 든다."""
        recs = [record(i, f"같은 좋아요 수를 가진 리뷰 본문입니다 {i}", recommendCount=0) for i in range(1, 5)]
        ctx = ScoringContext.from_records(recs)
        pcts = {ctx.like_percentile[r["reviewId"]] for r in recs}
        self.assertEqual(len(pcts), 1)


class TestSignalDirections(unittest.TestCase):
    """PRD §3-4 가 정한 방향이 산식에 그대로 들어갔는지."""

    def setUp(self):
        self.w = TrustWeights.load()

    def values(self, recs, i=0):
        return signal_values(recs[i], ScoringContext.from_records(recs), self.w)

    def test_longer_content_scores_higher_up_to_the_cap(self):
        short, long = record(1, "가" * 30), record(2, "나" * 300)
        recs = [short, long]
        ctx = ScoringContext.from_records(recs)
        self.assertLess(
            signal_values(short, ctx, self.w)["contentLength"],
            signal_values(long, ctx, self.w)["contentLength"],
        )

    def test_length_saturates_at_the_cap(self):
        """상한 위에서는 더 길어도 이득이 없다 — 장문 1건이 정렬을 지배하지 않게."""
        at_cap = record(1, "가" * self.w.cap_chars)
        over = record(2, "가" * (self.w.cap_chars * 3))
        ctx = ScoringContext.from_records([at_cap, over])
        self.assertEqual(signal_values(at_cap, ctx, self.w)["contentLength"], 1.0)
        self.assertEqual(signal_values(over, ctx, self.w)["contentLength"], 1.0)

    def test_identical_content_scales_down_by_group_size(self):
        """↓↓ — 같은 본문이 n 개면 1/n."""
        same = "완전히 같은 본문을 붙여넣은 리뷰입니다 정말로요"
        recs = [record(i, same) for i in (1, 2, 3)] + [record(4, "혼자만 쓴 다른 본문입니다 정말로")]
        ctx = ScoringContext.from_records(recs)
        self.assertAlmostEqual(signal_values(recs[0], ctx, self.w)["uniqueContent"], 1 / 3, places=5)
        self.assertEqual(signal_values(recs[3], ctx, self.w)["uniqueContent"], 1.0)

    def test_photo_and_usage_period_are_computed_even_at_zero_weight(self):
        """가중치 0 은 '안 쟀다'가 아니라 '효과가 0과 구별되지 않았다'는 기록이다."""
        recs = [record(1, "사진 있고 한 달 이상 썼습니다 보습이 좋아요", hasPhoto=True, isMonthOverReview=True)]
        v = self.values(recs)
        self.assertEqual(v["hasPhoto"], 1.0)
        self.assertEqual(v["usagePeriod"], 1.0)
        self.assertEqual(self.w.weights["hasPhoto"], 0.0)
        self.assertEqual(self.w.weights["usagePeriod"], 0.0)

    def test_likes_are_normalized_within_product(self):
        """제품마다 노출량이 달라 원값은 섞이지 않는다."""
        big = [record(i, f"인기 제품 리뷰 본문입니다 번호 {i}", recommendCount=c) for i, c in enumerate([0, 50, 500], 1)]
        for r in big:
            r["productId"] = "big"
        small = [record(i, f"비인기 제품 리뷰 본문입니다 번호 {i}", recommendCount=c) for i, c in enumerate([0, 1, 2], 10)]
        for r in small:
            r["productId"] = "small"
        ctx = ScoringContext.from_records(big + small)
        # 각 제품에서 최상위 좋아요는 원값(500 vs 2)이 달라도 같은 백분위 1.0 이다
        self.assertEqual(ctx.like_percentile[big[-1]["reviewId"]], 1.0)
        self.assertEqual(ctx.like_percentile[small[-1]["reviewId"]], 1.0)


class TestUnavailableSignals(unittest.TestCase):
    """미가용 신호를 0점으로 깔면 '근거 없음'과 '아직 안 쟀음'이 같아진다 (PER-172 `unobserved` 와 같은 이유)."""

    def test_ontopic_is_unavailable_at_ingest(self):
        recs = [record(1, "보습이 좋아서 계속 쓰고 있어요 흡수도 빠릅니다")]
        out = trust_prior(recs[0], ScoringContext.from_records(recs), TrustWeights.load())
        self.assertEqual(out["unavailable"], ["onTopic"])
        self.assertNotIn("onTopic", out["signals"])
        self.assertIn("onTopic", DEFERRED_SIGNALS)

    def test_zero_aspect_review_scores_lower_once_tags_arrive(self):
        text = "좋아요 잘 쓰고 있습니다 배송도 빨랐어요 감사합니다"
        recs = [record(1, text), record(2, text.replace("좋아요", "촉촉해요"))]
        w = TrustWeights.load()
        ctx = ScoringContext.from_records(recs, aspect_counts={1: 0, 2: 3})
        off = trust_prior(recs[0], ctx, w)
        on = trust_prior(recs[1], ctx, w)
        self.assertNotIn("unavailable", off)
        self.assertEqual(off["signals"]["onTopic"], 0.0)
        self.assertLess(off["score"], on["score"])

    def test_rescore_reweights_without_reingest(self):
        recs = [record(1, "보습이 좋고 향도 은은해서 계속 쓰고 있어요 흡수도 빠릅니다")]
        w = TrustWeights.load()
        values = signal_values(recs[0], ScoringContext.from_records(recs), w)
        self.assertEqual(rescore(values, w), trust_prior(recs[0], ScoringContext.from_records(recs), w))


class TestWeightNotFilter(unittest.TestCase):
    """완료 조건 1 — 낮은 점수는 버리는 근거가 아니다."""

    def test_worst_possible_review_still_gets_a_record(self):
        same = "좋아요"
        recs = [record(i, same, hasPhoto=False, isMonthOverReview=False, recommendCount=0) for i in (1, 2, 3)]
        out = score_all(recs)
        self.assertEqual(set(out), {1, 2, 3})
        for prior in out.values():
            self.assertGreaterEqual(prior["score"], 0.0)

    def test_scores_stay_in_unit_range(self):
        recs = [
            record(1, "가" * 5),
            record(2, "나" * 2000, hasPhoto=True, isMonthOverReview=True, recommendCount=999),
        ]
        for prior in score_all(recs).values():
            self.assertGreaterEqual(prior["score"], 0.0)
            self.assertLessEqual(prior["score"], 1.0)

    def test_ranking_is_deterministic_on_ties(self):
        """점수 동점은 흔하다 — reviewDate 최신 → reviewId 최소로 결정적으로 가른다 (PER-170)."""
        a = record(1, "완전히 같은 길이의 본문입니다 하나", reviewDate="2026.01.02")
        b = record(2, "완전히 같은 길이의 본문입니다 둘둘", reviewDate="2026.03.04")
        c = record(3, "완전히 같은 길이의 본문입니다 셋셋", reviewDate="2026.03.04")
        priors = score_all([a, b, c])
        for r in (a, b, c):
            r["derived"]["trustPrior"] = priors[r["reviewId"]]
        self.assertEqual([r["reviewId"] for r in sorted([c, a, b], key=rank_key)], [2, 3, 1])


class TestUsefulPointIsRejected(unittest.TestCase):
    """`usefulPoint` 는 좋아요 수가 아니라 올리브영의 정렬 점수다 — 산식에 들어가면 안 된다."""

    def test_useful_point_is_not_a_signal(self):
        self.assertNotIn("usefulPoint", SIGNALS)

    def test_useful_point_does_not_move_the_score(self):
        recs = [record(1, "보습이 좋아서 계속 쓰고 있어요 흡수도 빠릅니다")]
        w, ctx = TrustWeights.load(), ScoringContext.from_records(recs)
        before = trust_prior(recs[0], ctx, w)
        recs[0]["raw"]["usefulPoint"] = 75480.0
        self.assertEqual(before, trust_prior(recs[0], ScoringContext.from_records(recs), w))


if __name__ == "__main__":
    unittest.main()
