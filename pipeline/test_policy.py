"""
리뉴얼 · 리센시 정책 계약 테스트 (PER-172).

이 결정의 완료 조건은 세 문장이고, 셋 다 여기서 테스트로 고정된다.

  1. 리센시 컷은 `today` 가 아니라 **스냅샷 최신 월**에 고정된다 (재현성, §5-2)
  2. 새 수집분이 들어오면 윈도우가 조용히 밀리지 않고 **에러**다
  3. 리뉴얼을 확정하지 않은 제품(`unobserved`)은 컷 없이 통과하되 **한계를 남긴다**

  python3 -m unittest discover -s pipeline -p 'test_*.py'
"""
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from policy import (  # noqa: E402
    LIMIT_RENEWAL_UNOBSERVED,
    RECENCY_CUTOFF_MONTH,
    RECENCY_WINDOW_MONTHS,
    REJECT_RECENCY,
    REJECT_RENEWAL,
    RENEWAL_SEPARATE,
    RENEWAL_SINGLE,
    RENEWAL_UNOBSERVED,
    SNAPSHOT_LATEST_MONTH,
    PolicyError,
    assert_snapshot_current,
    month_of,
    recency_cutoff_month,
    recency_gate,
    renewal_gate,
)


@dataclass(frozen=True)
class FakeProduct:
    """`renewal_gate` 가 읽는 필드만 흉내낸다 (카탈로그 로드 없이 정책만 검증)."""
    product_id: str = "p001"
    renewal_policy: str = RENEWAL_UNOBSERVED
    renewal_from_month: str | None = None
    renewal_to_month: str | None = None


class MonthParsing(unittest.TestCase):
    def test_accepts_snapshot_and_iso_forms(self):
        self.assertEqual(month_of("2026.07.19"), "2026-07")
        self.assertEqual(month_of("2026-07-19"), "2026-07")
        self.assertEqual(month_of("2018.12.26"), "2018-12")

    def test_unparseable_date_raises(self):
        for bad in ("", None, "2026", "26.07.19", "2026.13.01", "  "):
            with self.subTest(bad=bad), self.assertRaises(PolicyError):
                month_of(bad)


class RecencyWindow(unittest.TestCase):
    """윈도우는 최신 월을 **포함**한다 — 24개월이면 latest-23 이 첫 달이다."""

    def test_adopted_cutoff(self):
        self.assertEqual(SNAPSHOT_LATEST_MONTH, "2026-08")
        self.assertEqual(RECENCY_WINDOW_MONTHS, 24)
        self.assertEqual(RECENCY_CUTOFF_MONTH, "2024-09")

    def test_window_arithmetic_crosses_year_boundaries(self):
        self.assertEqual(recency_cutoff_month("2026-08", 1), "2026-08")
        self.assertEqual(recency_cutoff_month("2026-08", 12), "2025-09")
        self.assertEqual(recency_cutoff_month("2026-01", 2), "2025-12")
        self.assertEqual(recency_cutoff_month("2026-01", 13), "2025-01")

    def test_bad_arguments_raise(self):
        with self.assertRaises(PolicyError):
            recency_cutoff_month("2026-13", 24)
        with self.assertRaises(PolicyError):
            recency_cutoff_month("2026-08", 0)


class RecencyGate(unittest.TestCase):
    def test_inside_window_passes(self):
        for date in ("2024.09.01", "2025.06.30", "2026.08.10"):
            with self.subTest(date=date):
                self.assertTrue(recency_gate(date).passed)

    def test_outside_window_is_rejected_with_a_reason(self):
        decision = recency_gate("2024.08.31")
        self.assertFalse(decision.passed)
        # 컷은 드롭이 아니다 — rejected[] 에 실릴 사유 코드가 있어야 한다.
        self.assertEqual(decision.reason, REJECT_RECENCY)

    def test_oldest_review_in_the_snapshot_is_rejected(self):
        self.assertFalse(recency_gate("2018.12.26").passed)


class SnapshotCurrency(unittest.TestCase):
    """새 수집분이 들어왔는데 정책을 안 고치면 윈도우가 조용히 과거로 밀린다."""

    def test_snapshot_at_or_before_policy_month_is_fine(self):
        assert_snapshot_current("2026.08.10")
        assert_snapshot_current("2025.01.02")

    def test_newer_snapshot_raises_and_names_the_fix(self):
        with self.assertRaises(PolicyError) as ctx:
            assert_snapshot_current("2026.09.01")
        self.assertIn("SNAPSHOT_LATEST_MONTH", str(ctx.exception))


class RenewalGate(unittest.TestCase):
    def test_single_passes_clean(self):
        decision = renewal_gate(FakeProduct(renewal_policy=RENEWAL_SINGLE), "2019.01.01")
        self.assertTrue(decision.passed)
        self.assertIsNone(decision.limitation)

    def test_unobserved_passes_but_carries_a_limitation(self):
        # 여기가 이 결정의 핵심이다: 'unobserved' 를 조용히 'single' 로 취급하면
        # 세대가 섞인 근거와 확인된 근거를 구분할 수 없게 된다.
        decision = renewal_gate(FakeProduct(renewal_policy=RENEWAL_UNOBSERVED), "2026.01.01")
        self.assertTrue(decision.passed)
        self.assertEqual(decision.limitation, LIMIT_RENEWAL_UNOBSERVED)

    def test_separate_cuts_reviews_outside_the_generation(self):
        current = FakeProduct(
            renewal_policy=RENEWAL_SEPARATE, renewal_from_month="2025-07", renewal_to_month=None
        )
        self.assertTrue(renewal_gate(current, "2025.07.01").passed)
        self.assertTrue(renewal_gate(current, "2026.08.10").passed)
        old = renewal_gate(current, "2025.06.30")
        self.assertFalse(old.passed)
        self.assertEqual(old.reason, REJECT_RENEWAL)

    def test_separate_closed_generation_cuts_both_ends(self):
        legacy = FakeProduct(
            renewal_policy=RENEWAL_SEPARATE, renewal_from_month="2024-01", renewal_to_month="2025-06"
        )
        self.assertTrue(renewal_gate(legacy, "2024.01.31").passed)
        self.assertTrue(renewal_gate(legacy, "2025.06.30").passed)
        self.assertFalse(renewal_gate(legacy, "2023.12.31").passed)
        self.assertFalse(renewal_gate(legacy, "2025.07.01").passed)

    def test_unknown_policy_raises(self):
        with self.assertRaises(PolicyError):
            renewal_gate(FakeProduct(renewal_policy="maybe"), "2026.01.01")


class CommittedCatalogUnderPolicy(unittest.TestCase):
    """커밋된 카탈로그 전체가 정책을 통과하는지 (정책과 데이터가 어긋나지 않게)."""

    def test_every_product_is_gateable(self):
        """전 제품이 판정 가능하고, 미확정 제품만 한계를 달고 나간다."""
        from catalog import load_catalog

        catalog = load_catalog()
        unobserved = 0
        for p in catalog.products:
            with self.subTest(product=p.product_id):
                decision = renewal_gate(p, "2026.08.10")
                if p.renewal_policy == RENEWAL_UNOBSERVED:
                    unobserved += 1
                    self.assertTrue(decision.passed)
                    self.assertEqual(decision.limitation, LIMIT_RENEWAL_UNOBSERVED)
                else:
                    self.assertIsNone(decision.limitation)
        # 확정한 5개 계보(세대 분할 3 + single 2, 이전 세대 3 포함 = 8개 엔트리)를 뺀 나머지
        self.assertEqual(unobserved, len(catalog) - 8)

    def test_confirmed_generations_cut_by_date(self):
        """날짜로 갈리는 계보에서 구세대 구간 밖 리뷰가 실제로 컷된다 (L011)."""
        from catalog import load_catalog

        catalog = load_catalog()
        previous = catalog.product("p052")   # 에스쁘아 비벨벳 커버쿠션 (2025 리뉴얼 이전)
        current = catalog.product("p011")
        self.assertTrue(renewal_gate(previous, "2024.06.01").passed)
        self.assertFalse(renewal_gate(previous, "2025.03.01").passed)
        self.assertEqual(renewal_gate(previous, "2025.03.01").reason, REJECT_RENEWAL)
        self.assertTrue(renewal_gate(current, "2025.03.01").passed)
        self.assertFalse(renewal_gate(current, "2024.06.01").passed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
