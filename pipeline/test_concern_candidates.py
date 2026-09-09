"""
후보 하네스 테스트 (PER-178 B안). 모델 출력을 들여올 때 계약 위반을 **버리지 않고 기록**하는지,
사람 결정이 붙은 후보를 조용히 덮어쓰지 않는지 고정한다.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))
sys.path.insert(0, str(ROOT / "eval"))

import concern_candidates as cc  # noqa: E402
import label_concern_golden as cli  # noqa: E402
import sample_concern_golden as sampler  # noqa: E402
from test_golden_contract import REVIEWS  # noqa: E402
from test_label_web import bundle_record  # noqa: E402

GOOD = {
    "aspect": "보습감", "question": "촉촉한가요?", "answer": "속보습은 못 느꼈다는 리뷰가 있다",
    "condition": {"skinType": None, "skinTrouble": None, "option": None}, "direction": "mixed",
    "evidence": [
        {"reviewId": 1, "stance": "support", "quote": "속보습은 못 느꼈어요"},
        {"reviewId": 2, "stance": "oppose", "quote": "촉촉하고 순해요"},
    ],
}
BAD_QUOTE = dict(GOOD, evidence=[{"reviewId": 2, "stance": "support", "quote": "촉촉하고 순하다"}], direction="positive")


class TestParse(unittest.TestCase):
    def test_strips_code_fence_and_prose(self):
        text = "결과입니다:\n```json\n{\"bundleId\": \"B01\", \"candidates\": []}\n```\n끝."
        self.assertEqual(cc.parse_model_output(text)["bundleId"], "B01")

    def test_requires_candidates_array(self):
        with self.assertRaises(ValueError):
            cc.parse_model_output('{"bundleId": "B01"}')
        with self.assertRaises(ValueError):
            cc.parse_model_output("json 아님")


class TestImport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        tmp = Path(self.tmp.name)
        self.sample, self.labels, self.cands = tmp / "s.jsonl", tmp / "l.jsonl", tmp / "c.jsonl"
        self.sample.write_text(json.dumps(bundle_record(), ensure_ascii=False) + "\n")
        self.patches = [
            mock.patch.object(sampler, "SAMPLE_PATH", self.sample),
            mock.patch.object(cc, "CANDIDATES_PATH", self.cands),
            mock.patch.object(cli, "LABELS_PATH", self.labels),
        ]
        for p in self.patches:
            p.start()
        cli.load_labels.__defaults__ = (self.labels,)
        sampler.load_bundles.__defaults__ = (self.sample,)
        cc.load_candidates.__defaults__ = (self.cands,)

    def tearDown(self):
        for p in self.patches:
            p.stop()
        cli.load_labels.__defaults__ = (cli.LABELS_PATH,)
        sampler.load_bundles.__defaults__ = (sampler.SAMPLE_PATH,)
        cc.load_candidates.__defaults__ = (cc.CANDIDATES_PATH,)
        self.tmp.cleanup()

    def output(self, *cands):
        return json.dumps({"bundleId": "B01", "candidates": list(cands)}, ensure_ascii=False)

    def test_import_records_violations_instead_of_dropping(self):
        rows = cc.import_candidates("B01", self.output(GOOD, BAD_QUOTE), model="test-model")
        self.assertEqual([r["candidateId"] for r in rows], ["B01-c1", "B01-c2"])
        self.assertEqual(rows[0]["contractErrors"], [])
        self.assertIn("원문 부분문자열", rows[1]["contractErrors"][0])
        self.assertEqual(rows[1]["model"], "test-model")
        self.assertEqual(len(cc.load_candidates()), 2)

    def test_wrong_bundle_output_is_refused(self):
        with self.assertRaises(SystemExit):
            cc.import_candidates("B01", json.dumps({"bundleId": "B02", "candidates": []}), model="m")

    def test_reimport_needs_replace(self):
        cc.import_candidates("B01", self.output(GOOD), model="m")
        with self.assertRaises(SystemExit):
            cc.import_candidates("B01", self.output(GOOD), model="m")
        cc.import_candidates("B01", self.output(GOOD, GOOD), model="m2", replace=True)
        self.assertEqual(len(cc.load_candidates()), 2)

    def test_replace_refused_when_human_decided(self):
        cc.import_candidates("B01", self.output(GOOD), model="m")
        self.labels.write_text(json.dumps({"labelId": "x", "bundleId": "B01", "source": "candidate_accepted", "candidateId": "B01-c1"}) + "\n")
        with self.assertRaises(SystemExit):
            cc.import_candidates("B01", self.output(GOOD), model="m", replace=True)

    def test_status_counts_human_labels_separately(self):
        cc.import_candidates("B01", self.output(GOOD), model="m")
        self.labels.write_text(
            json.dumps({"labelId": "a", "bundleId": "B01", "source": "candidate_accepted", "candidateId": "B01-c1"}) + "\n"
            + json.dumps({"labelId": "b", "bundleId": "B01", "source": "human", "candidateId": None}) + "\n"
        )
        row = cc.status()[0]
        self.assertEqual((row["candidates"], row["decided"], row["humanLabels"]), (1, 1, 1))


class TestHarness(unittest.TestCase):
    def test_harness_is_blind_and_carries_scope(self):
        b = bundle_record()
        b["scope"] = {"kind": "skinType", "axis": "skinType", "segment": "A02"}
        text = cc.render_harness(b, cc.PROMPT_PATH.read_text())
        self.assertIn("셀 번들", text)
        self.assertIn("`A02`", text)
        for r in REVIEWS:
            self.assertIn(f"[{r['reviewId']}]", text)
        for forbidden in ("concern", "tag", "v4"):
            self.assertNotIn(forbidden, text.lower().replace("tags", ""))


if __name__ == "__main__":
    unittest.main()
