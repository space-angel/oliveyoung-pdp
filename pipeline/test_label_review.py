"""
라벨 검수 앱(apps/label-review) 업무 규칙 테스트 (PER-178).

임시 저장소를 쓰므로 정본 라벨 파일을 건드리지 않는다. 실제 번들·후보 고정물은 그대로 읽는다.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "apps/label-review"))
sys.path.insert(0, str(ROOT / "pipeline"))

from service import REASONS, Service, ServiceError  # noqa: E402
from store import LocalStore  # noqa: E402


class TestLabelReview(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.store = LocalStore(d / "labels.jsonl", d / "assignments.jsonl")
        self.svc = Service(self.store, access_code="ok")
        self.me = self.svc.start("테스트", "ok")

    def tearDown(self):
        self.tmp.cleanup()

    def open_bundle(self, me=None):
        me = me or self.me
        self.svc.assign_next(me["labelerId"], me["name"])
        return self.svc.bundle_view(me["labelerId"])

    def test_access_code_and_name_required(self):
        with self.assertRaises(ServiceError):
            self.svc.start("", "ok")
        with self.assertRaises(ServiceError) as ctx:
            self.svc.start("누구", "wrong")
        self.assertEqual(ctx.exception.status, 403)

    def test_assignment_skips_labeled_bundles_and_is_exclusive(self):
        # B01 에 라벨이 있는 것처럼 — 라벨 있는 번들은 배정하지 않는다 (실제 정본에서 B01~B05 가 이렇게 건너뛰어진다)
        self.store.add_label({"labelId": "B01-1", "bundleId": "B01", "source": "human"})
        v = self.open_bundle()
        self.assertEqual(v["assignment"]["bundleId"], "B02")
        other = self.svc.start("둘째", "ok")
        v2 = self.open_bundle(other)
        self.assertEqual(v2["assignment"]["bundleId"], "B03")
        # 같은 사람이 다시 열면 같은 번들
        self.assertEqual(self.open_bundle()["assignment"]["bundleId"], "B02")

    def test_view_exposes_only_labeler_concepts(self):
        v = self.open_bundle()
        c = v["candidates"][0]
        for key in ("question", "answer", "quotes", "counts", "missed", "checks"):
            self.assertIn(key, c)
        self.assertTrue(all(q["verbatim"] for q in c["quotes"]))
        self.assertEqual(len(v["reasons"]), 4)
        self.assertIn("silent", c["counts"])

    def test_decide_maps_to_contract(self):
        v = self.open_bundle()
        lid = self.me["labelerId"]
        c = v["candidates"]
        r = self.svc.decide(lid, {"candidateId": c[0]["candidateId"], "kind": "accept", "minutesSpent": 1})
        self.assertEqual(r["kind"], "accept")
        r = self.svc.decide(lid, {"candidateId": c[1]["candidateId"], "kind": "reject", "reason": "duplicate"})
        labels = {l["labelId"]: l for l in self.store.labels()}
        self.assertEqual(labels[r["labelId"]]["source"], "candidate_rejected")
        self.assertEqual(labels[r["labelId"]]["failureReasons"], ["duplicate_claim"])
        ed = {"question": "수정된 질문인가요?", "answer": c[2]["answer"], "aspect": c[2]["aspect"],
              "evidence": [{"reviewId": q["reviewId"], "stance": q["stance"], "quote": q["quote"]} for q in c[2]["quotes"]]}
        r = self.svc.decide(lid, {"candidateId": c[2]["candidateId"], "kind": "edit", "edits": ed})
        saved = {l["labelId"]: l for l in self.store.labels()}[r["labelId"]]
        self.assertEqual((saved["source"], saved["question"]), ("candidate_edited", "수정된 질문인가요?"))
        # direction 은 근거에서 계산돼 들어간다
        self.assertIn(saved["direction"], ("positive", "negative", "mixed", "neutral"))
        with self.assertRaises(ServiceError) as ctx:
            self.svc.decide(lid, {"candidateId": c[0]["candidateId"], "kind": "accept"})
        self.assertEqual(ctx.exception.status, 409)

    def test_reject_reason_required_and_bad_edit_is_friendly(self):
        v = self.open_bundle()
        lid = self.me["labelerId"]
        c = v["candidates"][0]
        with self.assertRaises(ServiceError):
            self.svc.decide(lid, {"candidateId": c["candidateId"], "kind": "reject"})
        with self.assertRaises(ServiceError) as ctx:
            self.svc.decide(lid, {"candidateId": c["candidateId"], "kind": "edit",
                                  "edits": {"evidence": [{"reviewId": c["quotes"][0]["reviewId"], "stance": "positive", "quote": "원문에 없는 문장"}]}})
        self.assertIn("한 글자라도", str(ctx.exception))

    def test_reject_with_broken_quote_records_decision_without_label(self):
        v = self.open_bundle()
        lid = self.me["labelerId"]
        c = v["candidates"][0]
        # 후보 인용을 깨뜨린 상태를 흉내낸다
        cand = next(x for x in self.svc.candidates if x["candidateId"] == c["candidateId"])
        cand["candidate"]["evidence"][0]["quote"] = "원문에 없는 문장"
        r = self.svc.decide(lid, {"candidateId": c["candidateId"], "kind": "reject", "reason": "not_in_reviews"})
        self.assertIsNone(r["labelId"])
        self.assertIn("기각 기록만", r["note"])
        self.assertEqual(len(self.store.labels()), 0)
        self.assertEqual(self.store.decisions()[0]["kind"], "reject")

    def test_complete_requires_all_decisions_and_missed_answer(self):
        v = self.open_bundle()
        lid = self.me["labelerId"]
        with self.assertRaises(ServiceError):
            self.svc.complete(lid)
        for c in v["candidates"]:
            self.svc.decide(lid, {"candidateId": c["candidateId"], "kind": "accept"})
        with self.assertRaises(ServiceError) as ctx:
            self.svc.complete(lid)
        self.assertIn("놓친 질문", str(ctx.exception))
        self.svc.add_human(lid, {"none": True})
        r = self.svc.complete(lid)
        self.assertEqual(r["summary"]["accept"], len(v["candidates"]))
        self.assertEqual(r["next"]["bundleId"], "B02")
        self.assertEqual(self.svc.current_assignment(lid)["bundleId"], "B02")

    def test_human_claim_saved_with_source_human(self):
        v = self.open_bundle()
        lid = self.me["labelerId"]
        rv = v["reviews"][0]
        quote = rv["content"].strip()[:10]
        r = self.svc.add_human(lid, {"question": "직접 만든 질문인가요?", "answer": "직접 만든 답", "aspect": None,
                                     "evidence": [{"reviewId": rv["reviewId"], "stance": "neutral", "quote": quote}], "minutesSpent": 2})
        saved = {l["labelId"]: l for l in self.store.labels()}[r["labelId"]]
        self.assertEqual((saved["source"], saved["candidateId"], saved["direction"]), ("human", None, "neutral"))

    def test_checks_are_plain_language(self):
        # 라벨러 화면의 점검 문구에 실패유형 키(overbroad_question 등)가 새지 않는다
        import re
        v = self.open_bundle()
        for c in v["candidates"]:
            for chk in c["checks"]:
                self.assertFalse(re.search(r"[a-z]+_[a-z_]+", chk["text"]), chk["text"])
                self.assertEqual(chk["level"], "warn")

    def test_reasons_map_to_taxonomy_keys(self):
        keys = {r["failure"] for r in REASONS}
        self.assertEqual(keys, {"overfit_question", "overbroad_question", "duplicate_claim", "unsupported_claim"})


if __name__ == "__main__":
    unittest.main()
