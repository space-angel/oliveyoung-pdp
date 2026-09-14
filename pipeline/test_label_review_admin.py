"""
라벨 검수 앱 — 관리자 진행 현황 테스트 (PER-178).

임시 저장소를 쓰므로 정본 라벨·배정 파일을 건드리지 않는다. 번들·후보 고정물은 실제 것을 읽는다.
서버 라우트는 ThreadingHTTPServer 를 임시 포트에 띄워 urllib 로 404 / 403 / 200 을 확인한다.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "apps/label-review"))
sys.path.insert(0, str(ROOT / "pipeline"))

from admin import bundle_state, status_report  # noqa: E402
from sample_concern_golden import load_bundles  # noqa: E402
from store import LocalStore  # noqa: E402

BUNDLES = load_bundles()


def _label(bid: str, n: int, source: str = "human") -> dict:
    return {"labelId": f"{bid}-{n}", "bundleId": bid, "source": source}


def _decision(bid: str, lid: str, kind: str, cid: str | None = None, label_id: str | None = None) -> dict:
    return {"bundleId": bid, "labelerId": lid, "candidateId": cid, "kind": kind, "reason": None, "labelId": label_id}


class TestStatusReport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.store = LocalStore(d / "labels.jsonl", d / "assignments.jsonl")

    def tearDown(self):
        self.tmp.cleanup()

    def test_empty_store_is_all_free(self):
        rep = status_report(self.store, BUNDLES, candidates=[])
        s = rep["summary"]
        self.assertEqual(s["totalBundles"], 40)
        self.assertEqual((s["doneBundles"], s["activeBundles"], s["expiredBundles"], s["freeBundles"]), (0, 0, 0, 40))
        self.assertEqual((s["totalLabels"], s["humanLabels"]), (0, 0))
        self.assertEqual(s["labelers"], [])
        self.assertEqual([b["bundleId"] for b in rep["bundles"]], sorted(BUNDLES))
        self.assertTrue(all(b["state"] == "free" and b["assignment"] is None for b in rep["bundles"]))
        b06 = next(b for b in rep["bundles"] if b["bundleId"] == "B06")
        self.assertEqual(b06["displayName"], BUNDLES["B06"]["displayName"])
        self.assertEqual(b06["reviewCount"], len(BUNDLES["B06"]["reviews"]))
        self.assertEqual(b06["candidateCount"], 0)

    def test_candidate_count_from_real_fixture(self):
        rep = status_report(self.store, BUNDLES)  # candidates 생략 → 정본 후보 파일
        self.assertTrue(all(b["candidateCount"] > 0 for b in rep["bundles"]), "번들마다 모델 후보가 있어야 한다")

    def test_states_and_summary(self):
        st = self.store
        st.upsert_labeler({"labelerId": "가-1", "name": "가"})
        st.upsert_labeler({"labelerId": "나-2", "name": "나"})
        st.upsert_labeler({"labelerId": "다-3", "name": "다"})  # 등록만 하고 아무것도 안 한 사람
        # B06: 배정 done
        st.upsert_assignment({"bundleId": "B06", "labelerId": "가-1", "labelerName": "가", "status": "done", "updatedAt": "2026-09-14T00:00:00+00:00"})
        st.add_label(_label("B06", 1, "candidate_accepted"))
        st.add_label(_label("B06", 2))
        st.add_decision(_decision("B06", "가-1", "accept", "B06-c1", "B06-1"))
        st.add_decision(_decision("B06", "가-1", "reject", "B06-c2"))
        st.add_decision(_decision("B06", "가-1", "human", None, "B06-2"))
        # B07: 배정은 active 이지만 라벨 + no_missed 가 있다 → done
        st.upsert_assignment({"bundleId": "B07", "labelerId": "가-1", "labelerName": "가", "status": "active"})
        st.add_label(_label("B07", 1, "candidate_edited"))
        st.add_decision(_decision("B07", "가-1", "edit", "B07-c1", "B07-1"))
        st.add_decision(_decision("B07", "가-1", "no_missed"))
        # B08: active, 라벨 없음
        st.upsert_assignment({"bundleId": "B08", "labelerId": "나-2", "labelerName": "나", "status": "active"})
        st.add_decision(_decision("B08", "나-2", "accept", "B08-c1", "B08-1"))  # 라벨 없는 결정 — 상태를 바꾸지 않는다
        # B09: expired
        st.upsert_assignment({"bundleId": "B09", "labelerId": "나-2", "labelerName": "나", "status": "expired"})
        # B10: active 이고 라벨은 있지만 놓친 질문에 답하지 않았다 → 아직 active
        st.upsert_assignment({"bundleId": "B10", "labelerId": "라-4", "labelerName": "라", "status": "active"})  # 라벨러 레코드 없이 배정만
        st.add_label(_label("B10", 1, "candidate_accepted"))
        st.add_decision(_decision("B10", "라-4", "accept", "B10-c1", "B10-1"))
        # B01: 배정 기록 없이 라벨만 (CLI 로 손수 만든 정본 번들) → done
        st.add_label(_label("B01", 1))

        rep = status_report(self.store, BUNDLES, candidates=[{"bundleId": "B06", "candidateId": "B06-c1"}, {"bundleId": "B06", "candidateId": "B06-c2"}])
        by = {b["bundleId"]: b for b in rep["bundles"]}
        self.assertEqual(by["B06"]["state"], "done")
        self.assertEqual(by["B07"]["state"], "done")
        self.assertEqual(by["B08"]["state"], "active")
        self.assertEqual(by["B09"]["state"], "expired")
        self.assertEqual(by["B10"]["state"], "active")
        self.assertEqual(by["B01"]["state"], "done")
        self.assertEqual(by["B11"]["state"], "free")

        self.assertEqual(by["B06"]["candidateCount"], 2)
        self.assertEqual(by["B06"]["labels"], 2)
        self.assertEqual(by["B06"]["decisions"], {"accept": 1, "edit": 0, "reject": 1, "human": 1, "no_missed": 0})
        a06 = by["B06"]["assignment"]
        self.assertEqual({k: a06[k] for k in ("labelerId", "labelerName", "status")}, {"labelerId": "가-1", "labelerName": "가", "status": "done"})
        self.assertIn("updatedAt", a06)  # 저장소가 찍든(있으면 그 값) 없든(None) 키는 항상 있다
        self.assertIn("updatedAt", by["B08"]["assignment"])

        s = rep["summary"]
        self.assertEqual(s["totalBundles"], 40)
        self.assertEqual(s["doneBundles"], 3)
        self.assertEqual(s["activeBundles"], 2)
        self.assertEqual(s["expiredBundles"], 1)
        self.assertEqual(s["freeBundles"], 34)
        self.assertEqual(s["totalLabels"], 5)
        self.assertEqual(s["humanLabels"], 2)  # B06-2, B01-1
        labelers = {p["labelerId"]: p for p in s["labelers"]}
        self.assertEqual(set(labelers), {"가-1", "나-2", "다-3", "라-4"})
        self.assertEqual((labelers["가-1"]["doneBundles"], labelers["가-1"]["activeBundle"]), (2, None))
        self.assertEqual((labelers["나-2"]["doneBundles"], labelers["나-2"]["activeBundle"]), (0, "B08"))
        self.assertEqual((labelers["다-3"]["doneBundles"], labelers["다-3"]["activeBundle"]), (0, None))
        self.assertEqual((labelers["라-4"]["name"], labelers["라-4"]["activeBundle"]), ("라", "B10"))
        self.assertEqual(s["labelers"][0]["labelerId"], "가-1")  # 완료 수 내림차순

    def test_bundle_state_rules(self):
        none = {k: 0 for k in ("accept", "edit", "reject", "human", "no_missed")}
        self.assertEqual(bundle_state({"status": "done"}, 0, none), "done")
        self.assertEqual(bundle_state({"status": "active"}, 1, {**none, "no_missed": 1}), "done")
        self.assertEqual(bundle_state({"status": "active"}, 1, {**none, "human": 1}), "done")
        self.assertEqual(bundle_state({"status": "active"}, 0, {**none, "no_missed": 1}), "active")  # 라벨 없이 없어요만 → 아직
        self.assertEqual(bundle_state({"status": "active"}, 1, {**none, "accept": 3}), "active")
        self.assertEqual(bundle_state({"status": "expired"}, 0, none), "expired")
        self.assertEqual(bundle_state(None, 1, none), "done")  # 배정 없이 라벨만 (B01~B05)
        self.assertEqual(bundle_state(None, 0, none), "free")

    def test_report_is_json_serializable(self):
        json.dumps(status_report(self.store, BUNDLES, candidates=[]), ensure_ascii=False)


class TestAdminRoutes(unittest.TestCase):
    """서버를 임시 포트에 띄워 키 검사 동작을 본다. get_service 가 전역 캐시라 데이터 디렉터리를 먼저 임시로 돌려둔다."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.prev_env = {k: os.environ.get(k) for k in ("LABEL_REVIEW_DATA_DIR", "LABEL_ADMIN_KEY", "SUPABASE_URL", "SUPABASE_SERVICE_KEY", "LABEL_ACCESS_CODE")}
        os.environ["LABEL_REVIEW_DATA_DIR"] = cls.tmp.name
        os.environ.pop("SUPABASE_URL", None)
        os.environ.pop("SUPABASE_SERVICE_KEY", None)
        import server  # noqa: E402  (환경변수 설정 뒤에 import)
        server._service = None
        cls.server_mod = server
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.server_mod._service = None
        for k, v in cls.prev_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        cls.tmp.cleanup()

    def get(self, path: str, headers: dict | None = None) -> tuple[int, bytes, str]:
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read(), r.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers.get("Content-Type", "")

    def test_admin_closed_when_key_unset(self):
        os.environ.pop("LABEL_ADMIN_KEY", None)
        self.assertEqual(self.get("/admin")[0], 404)
        self.assertEqual(self.get("/api/admin/status?key=anything")[0], 404)
        self.assertEqual(self.get("/api/admin/status", {"X-Admin-Key": "anything"})[0], 404)
        # 빈 문자열도 "설정 안 됨" 이다
        os.environ["LABEL_ADMIN_KEY"] = ""
        self.assertEqual(self.get("/admin")[0], 404)

    def test_admin_key_checks(self):
        os.environ["LABEL_ADMIN_KEY"] = "secret-1"
        status, body, ctype = self.get("/admin")
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)
        self.assertIn("검수 진행 현황", body.decode("utf-8"))

        self.assertEqual(self.get("/api/admin/status")[0], 403)
        self.assertEqual(self.get("/api/admin/status?key=wrong")[0], 403)
        self.assertEqual(self.get("/api/admin/status", {"X-Admin-Key": "wrong"})[0], 403)

        for path, headers in (("/api/admin/status?key=secret-1", None), ("/api/admin/status", {"X-Admin-Key": "secret-1"})):
            status, body, ctype = self.get(path, headers)
            self.assertEqual(status, 200, path)
            self.assertIn("application/json", ctype)
            rep = json.loads(body)
            self.assertEqual(rep["summary"]["totalBundles"], 40)
            self.assertEqual(len(rep["bundles"]), 40)
            self.assertIn("labelers", rep["summary"])

    def test_existing_routes_untouched(self):
        os.environ["LABEL_ADMIN_KEY"] = "secret-1"
        status, body, ctype = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)
        self.assertEqual(self.get("/api/bundle")[0], 401)  # 이름 없이 → 기존 동작
        self.assertEqual(self.get("/nope")[0], 404)


if __name__ == "__main__":
    unittest.main()
