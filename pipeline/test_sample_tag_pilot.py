"""표본 추출기 테스트 (PER-175).

게이트(`scripts/verify.sh`)가 gitignore 대상인 `data/intermediate/v5_reviews.jsonl` 에
의존하므로, 그 파일이 없을 때 **스택트레이스가 아니라 무엇을 먼저 돌려야 하는지**가
나와야 한다. 새로 클론한 사람이 처음 만나는 에러다.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from sample_tag_pilot import ROOT, SEED, STRATA, diagnose, load_reviews, rel


class TestMissingInput(unittest.TestCase):
    def test_missing_input_says_what_to_run(self):
        with self.assertRaises(SystemExit) as ctx:
            load_reviews(ROOT / "data/intermediate/does_not_exist.jsonl")
        self.assertIn("pipeline/ingest.py", str(ctx.exception))

    def test_message_survives_path_outside_repo(self):
        """안내를 만들다가 relative_to 가 터지면 안내가 사라진다."""
        with self.assertRaises(SystemExit) as ctx:
            load_reviews(Path("/nonexistent/v5_reviews.jsonl"))
        self.assertIn("pipeline/ingest.py", str(ctx.exception))

    def test_rel_handles_path_outside_repo(self):
        self.assertEqual(rel(Path("/nonexistent/x.jsonl")), "/nonexistent/x.jsonl")
        self.assertEqual(rel(ROOT / "pipeline/x.py"), "pipeline/x.py")


class TestStrata(unittest.TestCase):
    def test_strata_cover_every_rating_and_sum_to_200(self):
        self.assertEqual(sum(want for _, _, want in STRATA), 200)
        for rating in (1, 2, 3, 4, 5):
            matched = [name for name, pred, _ in STRATA if pred(rating)]
            self.assertEqual(len(matched), 1, f"평점 {rating} 이 {matched} 에 걸린다")

    def test_seed_is_fixed(self):
        """시드가 바뀌면 표본이 바뀌고 정답셋이 무의미해진다."""
        self.assertEqual(SEED, 20260904)


def rec(review_id: int, product_id: str = "p001", author: str = "a") -> dict:
    return {
        "reviewId": review_id,
        "productId": product_id,
        "raw": {"content": "본문", "rating": 5},
        "condition": {},
        "derived": {"authorKey": author},
    }


class TestDiagnose(unittest.TestCase):
    """FAIL 메시지가 '치명'과 '양성'을 구분해야 한다.

    구분이 없으면 발견한 사람이 파일 주인에게 물어보고 별도 스크립트를 짜야 한다
    (2026-09-04 카탈로그 세대 분할 때 실제로 그랬다).
    """

    def test_review_id_dropped_is_fatal(self):
        ok, why = diagnose([rec(1)], [rec(1), rec(2)])
        self.assertFalse(ok)
        self.assertIn("치명", why)
        self.assertIn("덮어쓰지 마라", why)

    def test_review_id_added_is_fatal(self):
        _, why = diagnose([rec(1), rec(2)], [rec(1)])
        self.assertIn("치명", why)

    def test_same_ids_different_order_is_fatal_and_points_at_seed(self):
        _, why = diagnose([rec(2), rec(1)], [rec(1), rec(2)])
        self.assertIn("치명", why)
        self.assertIn("SEED", why)

    def test_product_id_only_change_is_benign(self):
        """세대 분할이 productId 만 바꾼 경우 — 재생성하면 된다."""
        _, why = diagnose([rec(1, "p053")], [rec(1, "p017")])
        self.assertIn("양성", why)
        self.assertIn("무효화되는 태그가 없다", why)
        self.assertIn("p017 → p053", why)

    def test_benign_message_names_the_regeneration_command(self):
        _, why = diagnose([rec(1, "p053")], [rec(1, "p017")])
        self.assertIn("sample_tag_pilot.py", why)

    def test_derived_change_is_benign_too(self):
        _, why = diagnose([rec(1, author="b")], [rec(1, author="a")])
        self.assertIn("양성", why)
        self.assertIn("derived", why)


if __name__ == "__main__":
    unittest.main()
