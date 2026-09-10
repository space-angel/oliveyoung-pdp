"""
라벨 검수 앱 저장소 (PER-178 외부 라벨러용).

두 구현이 같은 인터페이스를 낸다. **정본은 git 의 라벨 파일**이고 Supabase 는 여러 사람이 동시에 쓰는 수집 버퍼다.

  LocalStore      eval/gold/v5_concern_golden_labels.jsonl (라벨) + eval/gold/v5_concern_golden_assignments.jsonl (배정·결정)
  SupabaseStore   REST(PostgREST) — 표준 라이브러리 urllib 만 쓴다. 환경변수 SUPABASE_URL / SUPABASE_SERVICE_KEY 가 있으면 선택

저장소는 **모양을 검증하지 않는다.** 검증은 service 가 golden_contract 로 한다. 저장소는 넣고 꺼낼 뿐이다.

레코드
  label        golden_contract 의 라벨 (LABEL_FIELDS)
  assignment   {bundleId, labelerId, labelerName, status: active|done}
  decision     {bundleId, labelerId, candidateId|null, kind: accept|edit|reject|no_missed|human, reason|null, labelId|null}
               — 라벨이 되지 못한 기각(후보의 인용이 원문과 달라 계약을 못 통과)도 여기에는 남는다
  labeler      {labelerId, name}
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
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
        rows = [r for r in self._meta() if not (r.get("_kind") == "assignment" and r["bundleId"] == a["bundleId"])]
        rows.append({"_kind": "assignment", **a})
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
        self.base = url.rstrip("/") + "/rest/v1"
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
        return [{"bundleId": r["bundle_id"], "labelerId": r["labeler_id"], "labelerName": r["labeler_name"], "status": r["status"]}
                for r in self._req("GET", "assignments", params={"select": "bundle_id,labeler_id,labeler_name,status"})]

    def upsert_assignment(self, a: dict) -> None:
        # bundle_id 가 PK 다 — 두 사람이 동시에 같은 번들을 잡으면 두 번째 INSERT 가 409 로 실패한다. service 가 다음 번들로 넘어간다
        self._req("POST", "assignments", body={"bundle_id": a["bundleId"], "labeler_id": a["labelerId"], "labeler_name": a["labelerName"], "status": a["status"]},
                  prefer="resolution=merge-duplicates,return=minimal" if a.get("_update") else "return=minimal")

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
