"""
라벨 검수 앱 저장소 (PER-178 외부 라벨러용).

두 구현이 같은 인터페이스를 낸다. **정본은 git 의 라벨 파일**이고 Supabase 는 여러 사람이 동시에 쓰는 수집 버퍼다.

  LocalStore      eval/gold/v5_concern_golden_labels.jsonl (라벨) + eval/gold/v5_concern_golden_assignments.jsonl (배정·결정)
  SupabaseStore   REST(PostgREST) — 표준 라이브러리 urllib 만 쓴다. 환경변수 SUPABASE_URL / SUPABASE_SERVICE_KEY 가 있으면 선택

저장소는 **모양을 검증하지 않는다.** 검증은 service 가 golden_contract 로 한다. 저장소는 넣고 꺼낼 뿐이다.

레코드
  label        golden_contract 의 라벨 (LABEL_FIELDS)
  assignment   {bundleId, labelerId, labelerName, status: active|done|expired, updatedAt: ISO8601 UTC}
               — updatedAt 은 저장소가 찍는다(upsert 마다 now). service 가 이 값으로 중간 이탈(TTL) 을 판정한다
               — expired 는 TTL 을 넘겨 다른 사람이 가져간 배정. 결정·라벨은 그대로 남는다
  decision     {bundleId, labelerId, candidateId|null, kind: accept|edit|reject|no_missed|human, reason|null, labelId|null}
               — 라벨이 되지 못한 기각(후보의 인용이 원문과 달라 계약을 못 통과)도 여기에는 남는다
  labeler      {labelerId, name}
"""
from __future__ import annotations

import json
import re
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LABELS_PATH = ROOT / "eval/gold/v5_concern_golden_labels.jsonl"
ASSIGNMENTS_PATH = ROOT / "eval/gold/v5_concern_golden_assignments.jsonl"


class StoreError(RuntimeError):
    pass


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


def now_iso() -> str:
    """배정 updatedAt. UTC 고정 — 서버 시간대에 따라 TTL 판정이 흔들리면 안 된다."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _assignment_row(a: dict) -> dict:
    # _update 는 store 에 주는 힌트일 뿐 레코드가 아니다. updatedAt 은 저장소가 찍는다
    return {**{k: v for k, v in a.items() if k != "_update"}, "updatedAt": now_iso()}


class LocalStore:
    """JSONL 두 파일. 단일 프로세스 가정 — 로컬에서 한 사람이 쓰거나, 배포 전 검증용."""

    def __init__(self, labels_path: Path = LABELS_PATH, assignments_path: Path = ASSIGNMENTS_PATH):
        self.labels_path = labels_path
        self.assignments_path = assignments_path

    # labels
    def labels(self) -> list[dict]:
        return _read_jsonl(self.labels_path)

    def add_label(self, label: dict, labeler_id: str | None = None) -> None:
        # 라벨 파일은 계약 필드만 담는다 — 누가 만들었는지는 assignments/decisions 에 남는다
        with self.labels_path.open("a") as f:
            f.write(json.dumps(label, ensure_ascii=False) + "\n")

    def delete_label(self, label_id: str) -> bool:
        rows = self.labels()
        kept = [r for r in rows if r["labelId"] != label_id]
        if len(kept) == len(rows):
            return False
        _write_jsonl(self.labels_path, kept)
        return True

    # assignments / decisions / labelers — 한 파일에 kind 로 구분
    def _meta(self) -> list[dict]:
        return _read_jsonl(self.assignments_path)

    def _save_meta(self, rows: list[dict]) -> None:
        _write_jsonl(self.assignments_path, rows)

    def labelers(self) -> list[dict]:
        return [r for r in self._meta() if r.get("_kind") == "labeler"]

    def upsert_labeler(self, labeler: dict) -> None:
        rows = [r for r in self._meta() if not (r.get("_kind") == "labeler" and r["labelerId"] == labeler["labelerId"])]
        rows.append({"_kind": "labeler", **labeler})
        self._save_meta(rows)

    def assignments(self) -> list[dict]:
        return [r for r in self._meta() if r.get("_kind") == "assignment"]

    def upsert_assignment(self, a: dict) -> None:
        # 키는 (bundleId, labelerId) — 만료돼 넘어간 번들은 옛 사람의 expired 행과 새 사람의 active 행이 같이 남는다
        rows = [r for r in self._meta() if not (r.get("_kind") == "assignment" and r["bundleId"] == a["bundleId"] and r["labelerId"] == a["labelerId"])]
        rows.append({"_kind": "assignment", **_assignment_row(a)})
        self._save_meta(rows)

    def take_over_assignment(self, old: dict, new: dict) -> None:
        """TTL 을 넘긴 active 배정을 다른 사람이 가져간다. 옛 배정은 expired 로 남기고 결정·라벨은 건드리지 않는다."""
        rows = [r for r in self._meta() if not (r.get("_kind") == "assignment" and r["bundleId"] == old["bundleId"] and r["labelerId"] == old["labelerId"])]
        rows.append({"_kind": "assignment", **_assignment_row({**old, "status": "expired"})})
        rows.append({"_kind": "assignment", **_assignment_row(new)})
        self._save_meta(rows)

    def decisions(self) -> list[dict]:
        return [r for r in self._meta() if r.get("_kind") == "decision"]

    def add_decision(self, d: dict) -> None:
        rows = self._meta()
        rows.append({"_kind": "decision", **d})
        self._save_meta(rows)


class SupabaseStore:
    """PostgREST. 테이블 스키마는 apps/label-review/supabase/schema.sql. service role 키는 서버에서만 쓴다."""

    def __init__(self, url: str, key: str):
        self.base = re.sub(r"/rest/v1/?$", "", url.strip().rstrip("/")) + "/rest/v1"  # 대시보드에서 복사한 URL 에 /rest/v1 이 붙어 와도 그대로 동작
        self.key = key

    def _req(self, method: str, table: str, *, params: dict | None = None, body=None, prefer: str | None = None):
        qs = ("?" + urllib.parse.urlencode(params)) if params else ""
        req = urllib.request.Request(f"{self.base}/{table}{qs}", method=method)
        req.add_header("apikey", self.key)
        req.add_header("Authorization", f"Bearer {self.key}")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        if prefer:
            req.add_header("Prefer", prefer)
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        try:
            with urllib.request.urlopen(req, data=data, timeout=15) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            raise StoreError(f"supabase {method} {table}: {e.code} {e.read().decode('utf-8', 'ignore')[:300]}") from None

    def labels(self) -> list[dict]:
        return [r["label"] for r in self._req("GET", "labels", params={"select": "label", "order": "created_at.asc"})]

    def add_label(self, label: dict, labeler_id: str | None = None) -> None:
        self._req("POST", "labels", body={"label_id": label["labelId"], "bundle_id": label["bundleId"], "labeler_id": labeler_id, "label": label}, prefer="return=minimal")

    def delete_label(self, label_id: str) -> bool:
        out = self._req("DELETE", "labels", params={"label_id": f"eq.{label_id}"}, prefer="return=representation")
        return bool(out)

    def labelers(self) -> list[dict]:
        return [{"labelerId": r["labeler_id"], "name": r["name"]} for r in self._req("GET", "labelers", params={"select": "labeler_id,name"})]

    def upsert_labeler(self, labeler: dict) -> None:
        self._req("POST", "labelers", body={"labeler_id": labeler["labelerId"], "name": labeler["name"]}, prefer="resolution=merge-duplicates,return=minimal")

    def assignments(self) -> list[dict]:
        return [{"bundleId": r["bundle_id"], "labelerId": r["labeler_id"], "labelerName": r["labeler_name"], "status": r["status"], "updatedAt": r.get("updated_at")}
                for r in self._req("GET", "assignments", params={"select": "bundle_id,labeler_id,labeler_name,status,updated_at"})]

    def _assignment_body(self, a: dict) -> dict:
        return {"bundle_id": a["bundleId"], "labeler_id": a["labelerId"], "labeler_name": a["labelerName"], "status": a["status"], "updated_at": now_iso()}

    def upsert_assignment(self, a: dict) -> None:
        # bundle_id 가 PK 다 — 두 사람이 동시에 같은 번들을 잡으면 두 번째 INSERT 가 409 로 실패한다. service 가 다음 번들로 넘어간다
        self._req("POST", "assignments", body=self._assignment_body(a),
                  prefer="resolution=merge-duplicates,return=minimal" if a.get("_update") else "return=minimal")

    def take_over_assignment(self, old: dict, new: dict) -> None:
        # bundle_id 가 PK 이고 status 체크가 active|done 뿐이라(schema.sql) 옛 배정을 expired 행으로 남길 자리가 없다 —
        # 행을 새 사람으로 덮어쓴다. 누가 먼저 잡았는지는 decisions.labeler_id 에 남는다
        self._req("POST", "assignments", body=self._assignment_body(new), prefer="resolution=merge-duplicates,return=minimal")

    def decisions(self) -> list[dict]:
        return [r["decision"] for r in self._req("GET", "decisions", params={"select": "decision"})]

    def add_decision(self, d: dict) -> None:
        self._req("POST", "decisions", body={"bundle_id": d["bundleId"], "labeler_id": d["labelerId"], "decision": d}, prefer="return=minimal")


def open_store():
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_KEY")
    if url and key:
        return SupabaseStore(url, key)
    data_dir = os.environ.get("LABEL_REVIEW_DATA_DIR")  # 정본 파일을 건드리지 않고 시험할 때
    if data_dir:
        d = Path(data_dir)
        return LocalStore(d / "labels.jsonl", d / "assignments.jsonl")
    return LocalStore()
