"""
골든셋 번들 추출기 테스트 (PER-178).

표본은 라벨의 토대다 — 시드·할당·층 규칙이 바뀌면 라벨이 통째로 무효가 되므로 상수를
테스트로 고정하고, 결정적 배분 함수는 손으로 검산한 값과 대조한다.
"""
from __future__ import annotations

import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from contracts import MISSING_SEGMENT  # noqa: E402
from sample_concern_golden import (  # noqa: E402
    BUNDLE_SIZE,
    BUNDLES,
    PILOT_BUNDLES,
    SCOPE_QUOTA,
    SEED,
    STRATA,
    _stratify,
    cell_options,
    diagnose,
    largest_remainder,
)


def rec(review_id: int, rating: int, skin_type: str | None = None, troubles=(), option: str | None = None) -> dict:
    def single(code):
        return {"code": code, "stated": code is not None, "segment": code if code else MISSING_SEGMENT}

    return {
        "reviewId": review_id,
        "productId": "p001",
        "raw": {"rating": rating},
        "condition": {
            "skinType": single(skin_type),
            "skinTrouble": {"codes": list(troubles), "stated": bool(troubles), "segments": list(troubles) or [MISSING_SEGMENT]},
            "option": single(option),
        },
        "derived": {"authorKey": f"a{review_id}"},
    }


class TestConstants(unittest.TestCase):
    def test_seed_is_fixed(self):
        self.assertEqual(SEED, 20260909)

    def test_quotas_sum(self):
        self.assertEqual(sum(SCOPE_QUOTA.values()), BUNDLES)
        self.assertEqual(BUNDLE_SIZE, 40)
        self.assertLess(PILOT_BUNDLES, BUNDLES)

    def test_strata_cover_every_rating_once(self):
        for rating in (1, 2, 3, 4, 5):
            matched = [name for name, pred, _ in STRATA if pred(rating)]
            self.assertEqual(len(matched), 1, f"평점 {rating} 이 {matched} 에 걸린다")


class TestLargestRemainder(unittest.TestCase):
    def test_proportional_allocation_sums_to_total(self):
        alloc = largest_remainder({"a": 13, "b": 11, "c": 9, "d": 7, "e": 8}, 40)
        self.assertEqual(sum(alloc.values()), 40)
        # raw 10.83 / 9.17 / 7.50 / 5.83 / 6.67 → 바닥 37, 잉여 큰 순 a·d·e 가 +1
        self.assertEqual(alloc, {"a": 11, "b": 9, "c": 7, "d": 6, "e": 7})

    def test_every_category_gets_at_least_its_floor(self):
        alloc = largest_remainder({"a": 100, "b": 1}, 10)
        self.assertEqual(alloc["a"] + alloc["b"], 10)
        self.assertGreaterEqual(alloc["a"], 9)


class TestCellOptions(unittest.TestCase):
    def test_only_cells_at_or_above_n_min(self):
        rows = [rec(i, 5, skin_type="A02") for i in range(8)] + [rec(100 + i, 5, skin_type="A01") for i in range(7)]
        opts = cell_options(rows)
        self.assertEqual(opts["skinType"], ["A02"])
        self.assertNotIn("missing", opts)  # 미기재 0건

    def test_missing_segment_is_its_own_cell_kind(self):
        rows = [rec(i, 5) for i in range(8)]
        opts = cell_options(rows)
        self.assertEqual(opts.get("missing"), [MISSING_SEGMENT])
        self.assertNotIn("skinType", opts)  # 기재 세그먼트는 없다

    def test_skin_trouble_multi_label_counts_each_segment(self):
        rows = [rec(i, 5, troubles=("C01", "C05")) for i in range(8)]
        self.assertEqual(cell_options(rows)["skinTrouble"], ["C01", "C05"])


class TestStratify(unittest.TestCase):
    def test_quota_per_stratum_and_fill_from_low_ratings(self):
        pool = [rec(i, 1) for i in range(5)] + [rec(10 + i, 3) for i in range(20)] + [rec(100 + i, 5) for i in range(50)]
        picked, meta = _stratify(pool, random.Random(0))
        self.assertEqual(len(picked), BUNDLE_SIZE)
        by = {m["name"]: m for m in meta}
        self.assertEqual(by["rating_1_2"]["sampled"], 5)  # 있는 만큼 전부
        self.assertEqual(by["rating_4"]["sampled"], 0)
        # 목표 5+8+0+12 = 25, 모자란 15 는 낮은 평점부터: 남은 3점 12건 전부 → 5점 3건
        self.assertEqual(by["rating_3"]["sampled"], 20)
        self.assertEqual(by["rating_5"]["sampled"], 15)
        self.assertEqual(by["rating_5"]["weight"], round(50 / 15, 4))

    def test_small_pool_takes_everything(self):
        pool = [rec(i, 5) for i in range(9)]
        picked, _ = _stratify(pool, random.Random(0))
        self.assertEqual(len(picked), 9)

    def test_deterministic_for_same_seed(self):
        pool = [rec(i, (i % 5) + 1) for i in range(200)]
        a, _ = _stratify(list(pool), random.Random(SEED))
        b, _ = _stratify(list(pool), random.Random(SEED))
        self.assertEqual([r["reviewId"] for r in a], [r["reviewId"] for r in b])


class TestDiagnose(unittest.TestCase):
    def bundle(self, bid, pid, ids, scope=None):
        return {"bundleId": bid, "productId": pid, "scope": scope or {"kind": "product", "axis": None, "segment": None}, "reviews": [{"reviewId": i} for i in ids]}

    def test_changed_review_set_is_critical(self):
        old = [self.bundle("B01", "p001", [1, 2])]
        new = [self.bundle("B01", "p001", [1, 3])]
        self.assertIn("치명", diagnose(new, old))

    def test_changed_scope_is_critical(self):
        old = [self.bundle("B01", "p001", [1, 2])]
        new = [self.bundle("B01", "p001", [1, 2], scope={"kind": "skinType", "axis": "skinType", "segment": "A02"})]
        self.assertIn("치명", diagnose(new, old))

    def test_same_ids_is_benign(self):
        old = [self.bundle("B01", "p001", [1, 2])]
        new = [self.bundle("B01", "p001", [2, 1])]
        self.assertIn("양성", diagnose(new, old))


if __name__ == "__main__":
    unittest.main()
