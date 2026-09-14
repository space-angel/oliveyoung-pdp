"""재작성 감사 도구가 길이 위반과 편집된 인용을 놓치지 않는지 검증."""
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "eval"))
import regold_audit


class RegoldAuditTests(unittest.TestCase):
    def tag(self, snippet):
        return {"reviewId": 1, "aspect": "보습감", "polarity": "positive",
                "snippet": snippet, "skinTypeHint": None}

    def check(self, content, tags):
        with patch.object(regold_audit, "sample", return_value=[
            {"reviewId": 1, "productId": "test", "raw": {"content": content}}
        ]):
            return regold_audit.violations(tags)

    def test_exact_quote_length_boundary(self):
        self.assertEqual(self.check("가" * 41, [self.tag("가" * 40)]), [])
        errors = self.check("가" * 41, [self.tag("가" * 41)])
        self.assertTrue(any("41 > 40" in r for r in errors[0]["reasons"]))

    def test_newline_edit_and_duplicate_rejected(self):
        self.assertTrue(self.check("촉촉\n해요", [self.tag("촉촉 해요")]))
        self.assertTrue(self.check("촉촉\n해요", [self.tag("촉촉\n해요")]))
        errors = self.check("촉촉해요", [self.tag("촉촉해요")] * 2)
        self.assertIn("중복 reviewId/aspect", errors[0]["reasons"])

    def test_profile_records_actual_prompt(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "data/intermediate") as temporary:
            out = Path(temporary) / "profile.json"
            for prompt in [None, "eval/gold/regold_prompt_v1.md"]:
                command = [sys.executable, str(ROOT / "eval/validate_tags.py"),
                           # v2 는 2026-09-14 에 정본이 됐다. 파일명이 아니라 정본을 가리킨다
                           "--tags", "eval/gold/v5_tags_pilot_gold.jsonl",
                           "--label", "test", "--out", str(out)]
                if prompt:
                    command.extend(["--prompt", prompt])
                subprocess.run(command, cwd=ROOT, check=True, capture_output=True)
                actual = json.loads(out.read_text())["prompt"]
                expected = prompt or "pipeline/prompts/tag/v1.md"
                self.assertEqual(actual["path"], expected)
                self.assertEqual(actual["sha256"], hashlib.sha256((ROOT / expected).read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
