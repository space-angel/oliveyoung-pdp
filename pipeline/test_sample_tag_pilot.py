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

from sample_tag_pilot import ROOT, SEED, STRATA, load_reviews, rel


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


if __name__ == "__main__":
    unittest.main()
