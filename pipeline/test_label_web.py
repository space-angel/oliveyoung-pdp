"""
라벨 웹 도구 API 테스트 (PER-178). 웹 경로도 CLI 와 같은 계약을 통과해야만 저장된다.

표본·라벨 파일 경로를 임시 디렉터리로 바꿔 실제 고정물을 건드리지 않는다.
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

import label_concern_golden as cli  # noqa: E402
import label_concern_golden_web as web  # noqa: E402
import sample_concern_golden as sampler  # noqa: E402
from test_golden_contract import REVIEWS  # noqa: E402


def bundle_record() -> dict:
    return {
        "bundleId": "B01", "phase": "pilot", "productId": "p001", "displayName": "테스트", "category": "크림",
        "lineageId": "L001", "renewalPolicy": "unobserved",
        "scope": {"kind": "product", "axis": None, "segment": None},
        "population": 4, "strata": [], "reviews": REVIEWS,
    }


class TestWebApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        tmp = Path(self.tmp.name)
        self.sample = tmp / "sample.jsonl"
        self.labels = tmp / "labels.jsonl"
        self.sample.write_text(json.dumps(bundle_record(), ensure_ascii=False) + "\n")
        self.patches = [
            mock.patch.object(sampler, "SAMPLE_PATH", self.sample),
            mock.patch.object(sampler, "LABELS_PATH", self.labels),
            mock.patch.object(web, "LABELS_PATH", self.labels),
            mock.patch.object(cli, "LABELS_PATH", self.labels),
        ]
        for p in self.patches:
            p.start()
        # 기본 인자로 박힌 경로도 바꿔야 한다
        cli.load_labels.__defaults__ = (self.labels,)
        cli.append_label.__defaults__ = (self.labels,)
        sampler.load_bundles.__defaults__ = (self.sample,)

    def tearDown(self):
        for p in self.patches:
            p.stop()
        cli.load_labels.__defaults__ = (cli.LABELS_PATH,)
        cli.append_label.__defaults__ = (cli.LABELS_PATH,)
        sampler.load_bundles.__defaults__ = (sampler.SAMPLE_PATH,)
        self.tmp.cleanup()

    def payload(self, **over):
        base = {
            "labelId": "B01-1", "bundleId": "B01", "aspect": "보습감",
            "question": "촉촉한가요?", "answer": "갈린다",
            "condition": {"skinType": None, "skinTrouble": None, "option": None},
            "direction": "mixed",
            "evidence": [
                {"reviewId": 2, "stance": "support", "quote": "촉촉하고 순해요"},
                {"reviewId": 1, "stance": "oppose", "quote": "속보습은 못 느꼈어요"},
            ],
            "failureReasons": ["duplicate_claim", "unsupported_claim"],  # 미정렬 — 서버가 정렬한다
            "evaluation": "complete", "notes": None, "minutesSpent": 4,
        }
        base.update(over)
        return base

    def test_bundle_view_has_no_pipeline_output(self):
        view = web.api_bundle("B01")
        self.assertEqual(len(view["reviews"]), 4)
        self.assertNotIn("tags", json.dumps(view))
        self.assertNotIn("concern", json.dumps(view))
        self.assertEqual(view["vocab"]["taxonomy"]["version"], "failure-taxonomy-v1")

    def test_add_valid_label_sorts_reasons_and_persists(self):
        status, body = web.api_add_label(self.payload())
        self.assertEqual(status, 200, body)
        self.assertEqual(body["label"]["failureReasons"], ["unsupported_claim", "duplicate_claim"])
        self.assertEqual(body["counts"]["supportAuthors"], 1)
        self.assertEqual(len(cli.load_labels()), 1)
        self.assertEqual(web.api_bundles()[0]["labels"], 1)

    def test_invalid_quote_is_400_and_not_written(self):
        status, body = web.api_add_label(self.payload(evidence=[{"reviewId": 2, "stance": "support", "quote": "없는 문장"}]))
        self.assertEqual(status, 400)
        self.assertIn("원문 부분문자열", body["error"])
        self.assertFalse(self.labels.exists())

    def test_duplicate_label_id_is_400(self):
        self.assertEqual(web.api_add_label(self.payload())[0], 200)
        status, body = web.api_add_label(self.payload())
        self.assertEqual(status, 400)
        self.assertIn("중복", body["error"])

    def test_unknown_bundle_is_400(self):
        status, _ = web.api_add_label(self.payload(bundleId="B99"))
        self.assertEqual(status, 400)

    def test_delete_rewrites_file(self):
        web.api_add_label(self.payload())
        web.api_add_label(self.payload(labelId="B01-2"))
        status, body = web.api_delete_label("B01-1")
        self.assertEqual((status, body["remaining"]), (200, 1))
        self.assertEqual([l["labelId"] for l in cli.load_labels()], ["B01-2"])
        self.assertEqual(web.api_delete_label("B01-1")[0], 404)

    def test_stats_after_add(self):
        web.api_add_label(self.payload())
        status, body = web.api_stats()
        self.assertEqual(status, 200)
        self.assertEqual(body["labels"], 1)
        self.assertEqual(body["minutesPerLabel"]["mean"], 4)


if __name__ == "__main__":
    unittest.main()
