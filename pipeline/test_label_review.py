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

    # ---------- 1인당 상한 (LABEL_MAX_BUNDLES) ----------
    def finish_bundle(self, svc, me):
        v = svc.bundle_view(me["labelerId"])
        for c in v["candidates"]:
            if not c["decision"]:  # 이어받은 번들이면 다른 사람이 판단한 후보가 있다
                svc.decide(me["labelerId"], {"candidateId": c["candidateId"], "kind": "accept"})
        svc.add_human(me["labelerId"], {"none": True})
        return svc.complete(me["labelerId"])

    def test_max_bundles_stops_new_assignments(self):
        svc = Service(self.store, access_code="ok", max_bundles=1)
        me = svc.start("상한", "ok")
        svc.assign_next(me["labelerId"], me["name"])
        self.assertFalse(svc.bundle_view(me["labelerId"])["progress"]["limitReached"])
        r = self.finish_bundle(svc, me)
        self.assertIsNone(r["next"])
        self.assertEqual(r["progress"], {"doneBundles": 1, "maxBundles": 1, "limitReached": True})
        self.assertIsNone(svc.assign_next(me["labelerId"], me["name"]))
        v = svc.bundle_view(me["labelerId"])
        self.assertIsNone(v["assignment"])
        self.assertTrue(v["progress"]["limitReached"])
        # 0 은 무제한
        free = Service(self.store, access_code="ok", max_bundles=0)
        self.assertEqual(free.progress(me["labelerId"]), {"doneBundles": 1, "maxBundles": 0, "limitReached": False})
        self.assertIsNotNone(free.assign_next(me["labelerId"], me["name"]))

    def test_max_bundles_does_not_interrupt_active_assignment(self):
        # 상한은 새 배정에만 걸린다 — 하던 제품은 끝까지 볼 수 있다
        svc = Service(self.store, access_code="ok", max_bundles=1)
        me = svc.start("진행중", "ok")
        a = svc.assign_next(me["labelerId"], me["name"])
        self.store.upsert_assignment({"bundleId": "B99", "labelerId": me["labelerId"], "labelerName": me["name"], "status": "done"})
        self.assertTrue(svc.limit_reached(me["labelerId"]))
        self.assertEqual(svc.assign_next(me["labelerId"], me["name"])["bundleId"], a["bundleId"])

    # ---------- 식별자: 이름 해시가 아니라 랜덤, 클라이언트 id 재사용 ----------
    def test_same_name_gets_different_ids_and_client_id_is_reused(self):
        import re
        a = self.svc.start("호윤", "ok")
        b = self.svc.start("호윤", "ok")
        self.assertNotEqual(a["labelerId"], b["labelerId"])
        for x in (a, b):
            self.assertRegex(x["labelerId"], r"^labeler-[0-9a-f]{8}$")  # 한글 이름은 slug 에 안 들어간다 (URL 안전) — name 에 남는다
        self.assertRegex(self.svc.start("Ho Yun!", "ok")["labelerId"], r"^HoYun-[0-9a-f]{8}$")
        # 클라이언트가 들고 온 id 는 그대로 — 이름이 바뀌어도 id 는 유지되고 표시 이름만 갱신
        again = self.svc.start("호윤(폰)", "ok", a["labelerId"])
        self.assertEqual(again["labelerId"], a["labelerId"])
        self.assertEqual({l["labelerId"]: l["name"] for l in self.store.labelers()}[a["labelerId"]], "호윤(폰)")
        # 옛 형식(이름 해시)도 그대로 받는다 — 정본 배정 파일의 id 와 호환
        self.assertEqual(self.svc.start("호윤", "ok", "호윤-e7dec4")["labelerId"], "호윤-e7dec4")
        # 형식이 깨진 값은 쓰지 않고 새로 만든다
        for bad in ("", "a", "x" * 80, "../etc", "호 윤-1234", 123, None):
            self.assertRegex(self.svc.start("호윤", "ok", bad)["labelerId"], r"^labeler-[0-9a-f]{8}$")

    def test_legacy_assignment_row_without_updated_at_is_read_and_not_expired(self):
        lid = "호윤-e7dec4"
        rows = self.store._meta()
        rows.append({"_kind": "labeler", "labelerId": lid, "name": "호윤"})
        rows.append({"_kind": "assignment", "bundleId": "B06", "labelerId": lid, "labelerName": "호윤", "status": "active"})
        self.store._save_meta(rows)
        svc = Service(self.store, access_code="ok", assignment_ttl_hours=1)
        self.assertEqual(svc.current_assignment(lid)["bundleId"], "B06")
        other = svc.start("둘째", "ok")
        svc.assign_next(other["labelerId"], other["name"])
        # updatedAt 이 없는 옛 배정은 만료로 치지 않는다 — 둘째는 B06 을 가져가지 못한다
        self.assertNotEqual(svc.current_assignment(other["labelerId"])["bundleId"], "B06")
        self.assertEqual(svc.bundle_view(lid)["assignment"]["bundleId"], "B06")

    # ---------- 중간 이탈 만료 (LABEL_ASSIGNMENT_TTL_HOURS) ----------
    def backdate(self, bid, lid, hours):
        from datetime import datetime, timedelta, timezone
        rows = self.store._meta()
        for r in rows:
            if r.get("_kind") == "assignment" and r["bundleId"] == bid and r["labelerId"] == lid:
                r["updatedAt"] = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
        self.store._save_meta(rows)

    def test_stale_assignment_is_taken_over_and_original_expires(self):
        svc = Service(self.store, access_code="ok", assignment_ttl_hours=1)
        a = svc.start("첫째", "ok")
        b = svc.start("둘째", "ok")
        svc.assign_next(a["labelerId"], a["name"])
        v = svc.bundle_view(a["labelerId"])
        self.assertEqual(v["assignment"]["bundleId"], "B01")
        self.assertIn("updatedAt", v["assignment"])
        svc.decide(a["labelerId"], {"candidateId": v["candidates"][0]["candidateId"], "kind": "accept"})
        # 판정 직후엔 갱신돼 있어 만료가 아니다 — 둘째는 B02
        svc.assign_next(b["labelerId"], b["name"])
        self.assertEqual(svc.current_assignment(b["labelerId"])["bundleId"], "B02")
        self.store.upsert_assignment({**svc.current_assignment(b["labelerId"]), "status": "done", "_update": True})
        # 첫째가 2시간 자리를 비웠다 (라벨 1개가 이미 B01 에 있어도 이어받을 수 있어야 한다)
        self.backdate("B01", a["labelerId"], 2)
        self.assertEqual(svc.assign_next(b["labelerId"], b["name"])["bundleId"], "B01")
        st = {(r["bundleId"], r["labelerId"]): r["status"] for r in self.store.assignments()}
        self.assertEqual(st[("B01", a["labelerId"])], "expired")
        self.assertEqual(st[("B01", b["labelerId"])], "active")
        # 저장된 결정·라벨은 그대로, 새 사람 화면엔 resumed 표시
        vb = svc.bundle_view(b["labelerId"])
        self.assertTrue(vb["resumed"])
        self.assertEqual(vb["candidates"][0]["decision"]["labelerId"], a["labelerId"])
        self.assertEqual(len(self.store.labels()), 1)
        self.assertFalse(svc.bundle_view(a["labelerId"]).get("resumed", False))
        # 첫째가 돌아오면 expired 는 무시하고 새 배정을 받는다
        self.assertIsNone(svc.current_assignment(a["labelerId"]))
        self.assertEqual(svc.assign_next(a["labelerId"], a["name"])["bundleId"], "B03")
        # 둘째가 이어서 마치면 결정 수는 두 사람 것이 합쳐진다
        r = self.finish_bundle(svc, b)
        self.assertEqual(r["summary"]["accept"], len(vb["candidates"]))

    def test_touch_refreshes_updated_at_and_ttl_zero_never_expires(self):
        svc = Service(self.store, access_code="ok", assignment_ttl_hours=1)
        lid = self.me["labelerId"]
        v = self.open_bundle()
        self.backdate("B01", lid, 2)
        self.assertTrue(svc._is_stale(svc.current_assignment(lid)))
        svc.add_human(lid, {"none": True})  # 활동이 있으면 updatedAt 이 갱신된다
        self.assertFalse(svc._is_stale(svc.current_assignment(lid)))
        self.backdate("B01", lid, 200)
        never = Service(self.store, access_code="ok", assignment_ttl_hours=0)
        self.assertFalse(never._is_stale(never.current_assignment(lid)))
        other = never.start("둘째", "ok")
        self.assertEqual(never.assign_next(other["labelerId"], other["name"])["bundleId"], "B02")

    # ---------- 작성자 원문은 화면에 안 나간다 ----------
    def test_review_author_is_hashed(self):
        import re
        v = self.open_bundle()
        b = self.svc.bundles[v["assignment"]["bundleId"]]
        raw = {r["derived"]["authorKey"] for r in b["reviews"]}
        for rv in v["reviews"]:
            self.assertRegex(rv["author"], r"^[0-9a-f]{12}$")
            self.assertNotIn(rv["author"], raw)
        # 해시여도 고유 사람 수는 그대로다 — 화면은 이 수만 센다
        self.assertEqual(len({rv["author"] for rv in v["reviews"]}), v["bundle"]["authors"])
        # 다른 필드에도 작성자 원문이 새지 않는다
        import json
        blob = json.dumps(v, ensure_ascii=False)
        for name in raw:
            if len(name) >= 2:
                self.assertNotIn(f'"{name}"', blob)


if __name__ == "__main__":
    unittest.main()
