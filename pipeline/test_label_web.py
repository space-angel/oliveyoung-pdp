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
                {"reviewId": 2, "stance": "positive", "quote": "촉촉하고 순해요"},
                {"reviewId": 1, "stance": "negative", "quote": "속보습은 못 느꼈어요"},
            ],
            "failureReasons": ["duplicate_claim", "unsupported_claim"],  # 미정렬 — 서버가 정렬한다
            "evaluation": "complete", "notes": None, "minutesSpent": 4,
            "source": "human", "candidateId": None,
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
        self.assertEqual(body["counts"]["positiveAuthors"], 1)
        self.assertEqual(len(cli.load_labels()), 1)
        self.assertEqual(web.api_bundles()[0]["labels"], 1)

    def test_invalid_quote_is_400_and_not_written(self):
        status, body = web.api_add_label(self.payload(evidence=[{"reviewId": 2, "stance": "positive", "quote": "없는 문장"}]))
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

    def test_aspect_keywords_cover_exactly_the_14_aspects(self):
        from tag_contract import ASPECTS
        self.assertEqual(set(web.load_aspect_keywords()), set(ASPECTS))

    def test_aspect_hits_are_string_matches_not_tags(self):
        hits = web.api_bundle("B01")["aspectHits"]
        self.assertEqual(hits["보습감"]["reviews"], 3)   # 속보습·촉촉(2)·촉촉함
        self.assertEqual(hits["유분/번들거림"]["reviewIds"], [4])
        self.assertEqual(hits["분사력"]["reviews"], 0)

    # --- 후보 검수 경로 (이 프로젝트의 핵심 흐름) ---

    def seed_candidate(self, cand=None):
        import concern_candidates as cc
        cands_path = Path(self.tmp.name) / "c.jsonl"
        self._cc_patch = mock.patch.object(cc, "CANDIDATES_PATH", cands_path); self._cc_patch.start()
        cc.load_candidates.__defaults__ = (cands_path,)
        cand = cand or {"aspect": "보습감", "question": "속보습까지 촉촉하게 유지되나요?", "answer": "속보습은 못 느꼈다는 리뷰와 촉촉하다는 리뷰가 있다",
                        "condition": {"skinType": None, "skinTrouble": None, "option": None}, "direction": "mixed",
                        "evidence": [{"reviewId": 1, "stance": "negative", "quote": "속보습은 못 느꼈어요"},
                                     {"reviewId": 2, "stance": "positive", "quote": "촉촉하고 순해요"}]}
        cc.import_candidates("B01", json.dumps({"bundleId": "B01", "candidates": [cand]}, ensure_ascii=False), model="test-model")
        self.addCleanup(self._cc_patch.stop)
        self.addCleanup(lambda: setattr(cc.load_candidates, "__defaults__", (cc.CANDIDATES_PATH,)))
        return web.api_bundle("B01")["candidates"][0]

    def candidate_payload(self, c, **over):
        k = c["candidate"]
        base = {"labelId": "B01-1", "bundleId": "B01", "aspect": k["aspect"], "question": k["question"], "answer": k["answer"],
                "condition": k["condition"], "direction": k["direction"], "evidence": k["evidence"],
                "failureReasons": [], "evaluation": "complete", "notes": None, "minutesSpent": 2,
                "source": "candidate_accepted", "candidateId": c["candidateId"]}
        base.update(over)
        return base

    def test_candidate_view_carries_checks_and_no_decision(self):
        c = self.seed_candidate()
        self.assertEqual(c["candidateId"], "B01-c1")
        self.assertIsNone(c["decision"])
        self.assertEqual(c["contractErrors"], [])
        self.assertIn("checks", c)

    def test_accept_candidate_records_source_and_decision(self):
        c = self.seed_candidate()
        status, body = web.api_add_label(self.candidate_payload(c))
        self.assertEqual(status, 200, body)
        self.assertEqual((body["label"]["source"], body["label"]["candidateId"]), ("candidate_accepted", "B01-c1"))
        again = web.api_bundle("B01")["candidates"][0]
        self.assertEqual((again["decision"], again["labelId"]), ("candidate_accepted", "B01-1"))

    def test_edited_candidate_is_saved_with_edited_source(self):
        c = self.seed_candidate()
        payload = self.candidate_payload(c, source="candidate_edited",
                                         evidence=c["candidate"]["evidence"] + [{"reviewId": 3, "stance": "positive", "quote": "촉촉함이 오래가요"}])
        status, body = web.api_add_label(payload)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["label"]["source"], "candidate_edited")
        self.assertEqual(body["counts"]["positiveAuthors"], 2)

    def test_reject_candidate_requires_failure_reason(self):
        c = self.seed_candidate()
        status, body = web.api_add_label(self.candidate_payload(c, source="candidate_rejected"))
        self.assertEqual(status, 400)
        self.assertIn("기각 사유", body["error"])
        status, body = web.api_add_label(self.candidate_payload(c, source="candidate_rejected", failureReasons=["overbroad_question"]))
        self.assertEqual(status, 200, body)
        self.assertEqual(web.api_bundle("B01")["candidates"][0]["decision"], "candidate_rejected")

    def test_candidate_source_without_candidate_id_is_rejected(self):
        c = self.seed_candidate()
        status, body = web.api_add_label(self.candidate_payload(c, candidateId=None))
        self.assertEqual(status, 400)
        self.assertIn("candidateId", body["error"])

    def test_direction_mismatch_from_candidate_is_rejected(self):
        c = self.seed_candidate()
        status, body = web.api_add_label(self.candidate_payload(c, direction="positive"))
        self.assertEqual(status, 400)
        self.assertIn("계산한 방향", body["error"])

    def test_stats_after_add(self):
        web.api_add_label(self.payload())
        status, body = web.api_stats()
        self.assertEqual(status, 200)
        self.assertEqual(body["labels"], 1)
        self.assertEqual(body["minutesPerLabel"]["mean"], 4)


if __name__ == "__main__":
    unittest.main()
