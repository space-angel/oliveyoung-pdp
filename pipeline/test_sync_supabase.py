"""
라벨 검수 앱 Supabase 동기화 CLI(apps/label-review/sync_supabase.py) 로직 테스트 (PER-178).

실제 Supabase 는 없다 — PostgREST 를 흉내내는 가짜 클라이언트(로컬 dict)를 주입한다. 정본 파일은 읽기만 하고
쓰기는 임시 디렉터리에서만 한다. 번들 고정물(eval/gold/v5_concern_golden_sample.jsonl)은 그대로 읽는다.
"""
from __future__ import annotations

import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "apps/label-review"))
sys.path.insert(0, str(ROOT / "pipeline"))

import sync_supabase as sync  # noqa: E402
from golden_contract import load_failure_taxonomy  # noqa: E402
from sample_concern_golden import load_bundles  # noqa: E402
from store import LocalStore  # noqa: E402

PK = {"labelers": "labeler_id", "labels": "label_id", "assignments": "bundle_id"}


class FakePostgRest:
    """PostgREST 의 select / insert(on_conflict · ignore/merge-duplicates) 만 흉내낸다. decisions 는 identity PK."""

    def __init__(self):
        self.tables: dict[str, list[dict]] = {"labelers": [], "labels": [], "assignments": [], "decisions": []}
        self._seq = 0
        self.calls: list[tuple] = []

    def select(self, table, columns="*", order=None):
        self.calls.append(("select", table, columns))
        rows = [copy.deepcopy(r) for r in self.tables[table]]
        if columns != "*":
            cols = columns.split(",")
            rows = [{c: r.get(c) for c in cols} for r in rows]
        return rows

    def insert(self, table, rows, *, on_conflict=None, overwrite=False):
        self.calls.append(("insert", table, len(rows), on_conflict, overwrite))
        for row in rows:
            row = copy.deepcopy(row)
            if table == "decisions":
                self._seq += 1
                row.setdefault("id", self._seq)
                row.setdefault("created_at", f"2026-09-14T00:00:{self._seq:02d}+00:00")
                self.tables[table].append(row)
                continue
            key = PK[table]
            if on_conflict is None or on_conflict != key:
                raise AssertionError(f"{table} 는 on_conflict={key} 로 upsert 해야 한다 (받은 값 {on_conflict!r})")
            idx = next((i for i, r in enumerate(self.tables[table]) if r[key] == row[key]), None)
            if idx is None:
                row.setdefault("created_at", "2026-09-14T00:00:00+00:00")
                self.tables[table].append(row)
            elif overwrite:
                self.tables[table][idx] = {**self.tables[table][idx], **row}
            # ignore-duplicates: 아무것도 안 함


def _local(tmp: Path, labels: list[dict], meta: list[dict]) -> LocalStore:
    lp, ap = tmp / "labels.jsonl", tmp / "assignments.jsonl"
    lp.write_text("".join(json.dumps(l, ensure_ascii=False) + "\n" for l in labels))
    ap.write_text("".join(json.dumps(m, ensure_ascii=False) + "\n" for m in meta))
    return LocalStore(lp, ap)


class TestDotenvAndCredentials(unittest.TestCase):
    def test_parse_dotenv_quotes_comments_export(self):
        env = sync.parse_dotenv(
            "# 주석\nSUPABASE_URL=\"https://x.supabase.co\"\nexport SUPABASE_SERVICE_KEY='abc=def'\nPLAIN=v # 뒤 주석\nEMPTY=\n=bad\n")
        self.assertEqual(env["SUPABASE_URL"], "https://x.supabase.co")
        self.assertEqual(env["SUPABASE_SERVICE_KEY"], "abc=def")
        self.assertEqual(env["PLAIN"], "v")
        self.assertEqual(env["EMPTY"], "")
        self.assertNotIn("", env)

    def test_environ_overrides_dotenv(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            p.write_text("SUPABASE_URL=from-file\nSUPABASE_SERVICE_KEY=k\n")
            env = sync.load_env(p, environ={"SUPABASE_URL": "from-env"})
            self.assertEqual(env["SUPABASE_URL"], "from-env")
            self.assertEqual(env["SUPABASE_SERVICE_KEY"], "k")

    def test_missing_credentials_is_clear_korean_error(self):
        with self.assertRaises(sync.SyncError) as ctx:
            sync.credentials({"SUPABASE_URL": "https://x"})
        self.assertIn("SUPABASE_SERVICE_KEY", str(ctx.exception))
        self.assertIn("자격증명", str(ctx.exception))

    def test_main_exits_2_without_credentials(self):
        with tempfile.TemporaryDirectory() as d:
            import os
            saved_env_path, sync.ENV_PATH = sync.ENV_PATH, Path(d) / "no.env"
            saved = {k: os.environ.pop(k) for k in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY") if k in os.environ}
            try:
                err = io.StringIO()
                old = sys.stderr
                sys.stderr = err
                try:
                    code = sync.main(["status"])
                finally:
                    sys.stderr = old
                self.assertEqual(code, 2)
                self.assertIn("자격증명", err.getvalue())
            finally:
                os.environ.update(saved)
                sync.ENV_PATH = saved_env_path


class TestSync(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundles = load_bundles()
        cls.taxonomy = load_failure_taxonomy()
        # 정본에서 실제 라벨을 빌려 온다(읽기만). B01 의 첫 라벨은 계약을 통과한 상태다
        cls.gold = [json.loads(l) for l in (ROOT / "eval/gold/v5_concern_golden_labels.jsonl").read_text().splitlines() if l.strip()]
        cls.b01 = next(l for l in cls.gold if l["bundleId"] == "B01")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.client = FakePostgRest()
        self.out: list[str] = []
        self.err: list[str] = []

    def tearDown(self):
        self.tmp.cleanup()

    def _label(self, label_id: str, bundle_id: str | None = None) -> dict:
        l = copy.deepcopy(self.b01)
        l["labelId"] = label_id
        if bundle_id:
            l["bundleId"] = bundle_id
        return l

    # ---------- push ----------
    def test_push_uploads_labels_assignments_labelers_decisions(self):
        labels = [self._label("B01-1"), self._label("B01-2")]
        meta = [
            {"_kind": "labeler", "labelerId": "호윤-e7dec4", "name": "호윤"},
            {"_kind": "assignment", "bundleId": "B06", "labelerId": "호윤-e7dec4", "labelerName": "호윤", "status": "active", "updatedAt": "2026-09-14T01:00:00+00:00"},
            {"_kind": "decision", "bundleId": "B06", "labelerId": "호윤-e7dec4", "candidateId": "B06-c1", "kind": "accept", "reason": None, "labelId": "B01-2"},
        ]
        local = _local(self.dir, labels, meta)
        report = sync.cmd_push(self.client, local, out=self.out.append)
        self.assertEqual(report["labels"]["sent"], 2)
        self.assertEqual(report["assignments"]["sent"], 1)
        self.assertEqual(report["labelers"]["sent"], 1)
        self.assertEqual(report["decisions"]["sent"], 1)
        self.assertEqual({r["label_id"] for r in self.client.tables["labels"]}, {"B01-1", "B01-2"})
        # 라벨 jsonb 는 정본 그대로, 작성자는 결정 기록에서 되찾는다(없으면 null)
        by_id = {r["label_id"]: r for r in self.client.tables["labels"]}
        self.assertEqual(by_id["B01-1"]["label"], labels[0])
        self.assertIsNone(by_id["B01-1"]["labeler_id"])
        self.assertEqual(by_id["B01-2"]["labeler_id"], "호윤-e7dec4")
        self.assertEqual(self.client.tables["assignments"][0]["updated_at"], "2026-09-14T01:00:00+00:00")
        # FK 순서: labelers 가 labels/assignments/decisions 보다 먼저
        order = [c[1] for c in self.client.calls if c[0] == "insert"]
        self.assertEqual(order[0], "labelers")
        self.assertEqual(order[-1], "decisions")

    def test_push_skips_existing_unless_overwrite(self):
        local = _local(self.dir, [self._label("B01-1")], [])
        sync.cmd_push(self.client, local, out=self.out.append)
        # DB 쪽 값을 바꿔 놓고 다시 push — 기본은 건너뜀
        self.client.tables["labels"][0]["label"] = {"labelId": "B01-1", "changed": True}
        report = sync.cmd_push(self.client, local, out=self.out.append)
        self.assertEqual((report["labels"]["sent"], report["labels"]["skipped"]), (0, 1))
        self.assertEqual(self.client.tables["labels"][0]["label"], {"labelId": "B01-1", "changed": True})
        # --overwrite 면 정본 값으로 덮는다
        report = sync.cmd_push(self.client, local, overwrite=True, out=self.out.append)
        self.assertEqual(report["labels"]["sent"], 1)
        self.assertEqual(self.client.tables["labels"][0]["label"]["labelId"], "B01-1")
        self.assertNotIn("changed", self.client.tables["labels"][0]["label"])

    def test_push_dry_run_writes_nothing(self):
        local = _local(self.dir, [self._label("B01-1")], [{"_kind": "labeler", "labelerId": "a-1", "name": "a"}])
        report = sync.cmd_push(self.client, local, dry_run=True, out=self.out.append)
        self.assertEqual(report["labels"]["sent"], 1)
        self.assertEqual(self.client.tables["labels"], [])
        self.assertFalse(any(c[0] == "insert" for c in self.client.calls))
        self.assertTrue(any("dry-run" in s for s in self.out))

    def test_push_collapses_expired_assignments_and_backfills_labelers(self):
        meta = [
            {"_kind": "assignment", "bundleId": "B06", "labelerId": "old-1", "labelerName": "옛사람", "status": "expired", "updatedAt": "2026-09-10T00:00:00+00:00"},
            {"_kind": "assignment", "bundleId": "B06", "labelerId": "new-1", "labelerName": "새사람", "status": "active", "updatedAt": "2026-09-13T00:00:00+00:00"},
        ]
        local = _local(self.dir, [], meta)
        report = sync.cmd_push(self.client, local, out=self.out.append)
        self.assertEqual(report["assignments"]["sent"], 1)
        self.assertEqual(self.client.tables["assignments"][0]["labeler_id"], "new-1")
        # labelers 표에 없던 라벨러도 FK 를 위해 채운다
        self.assertEqual({r["labeler_id"] for r in self.client.tables["labelers"]}, {"old-1", "new-1"})

    def test_push_decisions_never_duplicate(self):
        d = {"_kind": "decision", "bundleId": "B06", "labelerId": "a-1", "candidateId": None, "kind": "no_missed", "reason": None, "labelId": None}
        local = _local(self.dir, [], [{"_kind": "labeler", "labelerId": "a-1", "name": "a"}, d])
        sync.cmd_push(self.client, local, out=self.out.append)
        report = sync.cmd_push(self.client, local, overwrite=True, out=self.out.append)
        self.assertEqual(report["decisions"]["sent"], 0)
        self.assertEqual(len(self.client.tables["decisions"]), 1)

    # ---------- pull ----------
    def _seed_db(self, labels: list[tuple[dict, str | None]]):
        self.client.tables["labelers"] = [{"labeler_id": "호윤-e7dec4", "name": "호윤"}]
        for i, (label, who) in enumerate(labels, 1):
            self.client.tables["labels"].append({"label_id": label["labelId"], "bundle_id": label["bundleId"], "labeler_id": who,
                                                 "label": label, "created_at": f"2026-09-14T00:00:{i:02d}+00:00"})
        self.client.tables["assignments"] = [{"bundle_id": "B01", "labeler_id": "호윤-e7dec4", "labeler_name": "호윤", "status": "done", "updated_at": "x"}]
        self.client.tables["decisions"] = [{"id": 1, "bundle_id": "B01", "labeler_id": "호윤-e7dec4",
                                            "decision": {"bundleId": "B01", "labelerId": "호윤-e7dec4", "candidateId": None, "kind": "human", "reason": None, "labelId": labels[0][0]["labelId"]},
                                            "created_at": "2026-09-14T00:00:09+00:00"}]

    def test_pull_merges_new_labels_and_writes_snapshot(self):
        existing = self._label("B01-1")
        new = self._label("B01-77")
        self._seed_db([(existing, None), (new, "호윤-e7dec4")])
        lp, sp = self.dir / "labels.jsonl", self.dir / "snapshot.json"
        lp.write_text(json.dumps({**existing, "notes": "정본에만 있는 표기"}, ensure_ascii=False) + "\n")
        result = sync.cmd_pull(self.client, labels_path=lp, snapshot_path=sp, bundles=self.bundles, taxonomy=self.taxonomy,
                               out=self.out.append, err=self.err.append)
        self.assertEqual(result["failed"], [])
        self.assertEqual((result["merged"], result["added"], result["replaced"]), (2, 1, 0))
        rows = [json.loads(l) for l in lp.read_text().splitlines()]
        self.assertEqual([r["labelId"] for r in rows], ["B01-1", "B01-77"])
        self.assertEqual(rows[0]["notes"], "정본에만 있는 표기")  # 기존 것은 유지
        # 라벨 파일에는 라벨러 필드가 없다(계약)
        self.assertTrue(all("labelerId" not in r and "labeler_id" not in r for r in rows))
        snap = json.loads(sp.read_text())
        who = {s["labelId"]: s for s in snap["labels"]}
        self.assertEqual(who["B01-77"]["labelerId"], "호윤-e7dec4")
        self.assertEqual(who["B01-77"]["labelerName"], "호윤")
        self.assertEqual(who["B01-77"]["createdAt"], "2026-09-14T00:00:02+00:00")
        self.assertEqual(len(snap["decisions"]), 1)
        self.assertEqual(snap["decisions"][0]["labelerName"], "호윤")
        self.assertEqual(snap["assignments"][0]["status"], "done")

    def test_pull_overwrite_prefers_db_value(self):
        db_version = self._label("B01-1")
        db_version["notes"] = "DB 쪽 표기"
        self._seed_db([(db_version, None)])
        lp, sp = self.dir / "labels.jsonl", self.dir / "snapshot.json"
        lp.write_text(json.dumps({**self._label("B01-1"), "notes": "정본 표기"}, ensure_ascii=False) + "\n")
        result = sync.cmd_pull(self.client, labels_path=lp, snapshot_path=sp, bundles=self.bundles, taxonomy=self.taxonomy,
                               overwrite=True, out=self.out.append, err=self.err.append)
        self.assertEqual(result["replaced"], 1)
        self.assertEqual(json.loads(lp.read_text().splitlines()[0])["notes"], "DB 쪽 표기")

    def test_pull_rejects_invalid_labels_but_merges_valid_ones(self):
        good = self._label("B01-77")
        bad_field = self._label("B01-78")
        bad_field["labelerId"] = "누구"  # 계약에 없는 필드
        bad_bundle = self._label("B99-1", "B99")  # 표본에 없는 번들
        bad_quote = self._label("B01-79")
        bad_quote["evidence"][0]["quote"] = "원문에 없는 문장입니다"
        self._seed_db([(good, None), (bad_field, None), (bad_bundle, None), (bad_quote, None)])
        lp, sp = self.dir / "labels.jsonl", self.dir / "snapshot.json"
        result = sync.cmd_pull(self.client, labels_path=lp, snapshot_path=sp, bundles=self.bundles, taxonomy=self.taxonomy,
                               out=self.out.append, err=self.err.append)
        self.assertEqual(sorted(lid for lid, _ in result["failed"]), ["B01-78", "B01-79", "B99-1"])
        rows = [json.loads(l) for l in lp.read_text().splitlines()]
        self.assertEqual([r["labelId"] for r in rows], ["B01-77"])
        joined = "\n".join(self.err)
        for lid in ("B01-78", "B01-79", "B99-1"):
            self.assertIn(lid, joined)
        snap = json.loads(sp.read_text())
        self.assertEqual(len(snap["failed"]), 3)
        self.assertFalse(next(s for s in snap["labels"] if s["labelId"] == "B01-78")["merged"])

    def test_pull_dry_run_writes_nothing(self):
        self._seed_db([(self._label("B01-77"), None)])
        lp, sp = self.dir / "labels.jsonl", self.dir / "snapshot.json"
        result = sync.cmd_pull(self.client, labels_path=lp, snapshot_path=sp, bundles=self.bundles, taxonomy=self.taxonomy,
                               dry_run=True, out=self.out.append, err=self.err.append)
        self.assertEqual(result["added"], 1)
        self.assertFalse(lp.exists())
        self.assertFalse(sp.exists())

    def test_pull_duplicate_label_id_in_db_is_a_failure(self):
        self._seed_db([(self._label("B01-77"), None)])
        dup = self._label("B01-77")
        self.client.tables["labels"].append({"label_id": "B01-77x", "bundle_id": "B01", "labeler_id": None, "label": dup, "created_at": "z"})
        lp, sp = self.dir / "labels.jsonl", self.dir / "snapshot.json"
        result = sync.cmd_pull(self.client, labels_path=lp, snapshot_path=sp, bundles=self.bundles, taxonomy=self.taxonomy,
                               out=self.out.append, err=self.err.append)
        self.assertEqual(len(result["failed"]), 1)
        self.assertEqual(result["merged"], 1)

    # ---------- status ----------
    def test_status_table_covers_all_bundles(self):
        self.client.tables["labels"] = [{"label_id": "B01-1", "bundle_id": "B01"}, {"label_id": "B01-2", "bundle_id": "B01"},
                                        {"label_id": "B06-1", "bundle_id": "B06"}, {"label_id": "B07-1", "bundle_id": "B07"}]
        self.client.tables["assignments"] = [{"bundle_id": "B06", "labeler_id": "a", "labeler_name": "호윤", "status": "active"},
                                             {"bundle_id": "B07", "labeler_id": "b", "labeler_name": "둘째", "status": "done"}]
        self.client.tables["decisions"] = [{"id": 1, "bundle_id": "B06", "decision": {}}, {"id": 2, "bundle_id": "B06", "decision": {}}]
        result = sync.cmd_status(self.client, bundles=self.bundles, out=self.out.append)
        self.assertEqual(result["total"], 40)
        rows = {r["bundleId"]: r for r in result["rows"]}
        self.assertEqual(len(rows), 40)
        self.assertEqual(rows["B01"]["labels"], 2)
        self.assertTrue(rows["B01"]["done"])          # 정본에서 올린 라벨 — 배정 없이 완료
        self.assertFalse(rows["B06"]["done"])         # 진행 중
        self.assertEqual(rows["B06"]["decisions"], 2)
        self.assertTrue(rows["B07"]["done"])
        self.assertEqual(rows["B40"]["status"], "-")
        self.assertEqual(result["done"], 2)
        self.assertEqual(result["active"], 1)
        self.assertTrue(any(s.startswith("B40") for s in self.out))
        self.assertIn("완료 2 / 40", self.out[-1])

    def test_status_without_bundles_still_lists_b01_to_b40(self):
        result = sync.cmd_status(self.client, bundles=None, out=self.out.append)
        self.assertEqual([r["bundleId"] for r in result["rows"]][:2], ["B01", "B02"])
        self.assertEqual(result["rows"][-1]["bundleId"], "B40")


if __name__ == "__main__":
    unittest.main()
