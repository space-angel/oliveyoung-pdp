"""
라벨 검수 앱 — Supabase(수집 버퍼) ↔ 정본(git 의 eval/gold) 동기화 CLI (PER-178). 표준 라이브러리만.

정본은 git 이다. Supabase 는 여러 라벨러가 동시에 쓰는 버퍼일 뿐이라, 시작할 때 정본을 올리고(push) 마감하면 내려받아(pull)
계약 검증 후 정본에 합친다. 진행 중에는 status 로 본다.

  python3 apps/label-review/sync_supabase.py push   [--overwrite] [--dry-run]
      eval/gold/v5_concern_golden_labels.jsonl · v5_concern_golden_assignments.jsonl(라벨러·배정·결정 혼재, LocalStore 규칙) 을
      labels / assignments / labelers / decisions 테이블로 올린다. 이미 있는 행(label_id · bundle_id · labeler_id · 결정은 내용 동일)은
      건너뛴다 — 덮어쓰려면 --overwrite. 목적: B01~B05 라벨이 DB 에 있어야 배정에서 빠진다.
  python3 apps/label-review/sync_supabase.py pull   [--overwrite] [--dry-run]
      labels.label(jsonb) 전부를 내려받아 golden_contract 로 검증한 뒤 정본 라벨 파일에 labelId 기준으로 병합한다
      (기존 유지·새 것만 추가, --overwrite 면 DB 값 우선). 누가 어떤 라벨을 만들었는지와 결정 전체는
      eval/gold/v5_concern_golden_supabase_snapshot.json 사이드카에 남긴다 — 라벨 파일에는 라벨러 필드를 넣지 않는다(계약에 없다).
      검증 실패 라벨은 병합하지 않고 stderr 에 labelId 와 이유를 찍고 종료코드 1.
  python3 apps/label-review/sync_supabase.py status
      번들별 라벨 수 / 배정 상태 / 결정 수 표. 마지막에 완료 번들 수 / 40.

자격증명은 저장소 루트의 .env(있으면)와 환경변수에서 읽는다: SUPABASE_URL, SUPABASE_SERVICE_KEY (service role — 서버·운영자만).

테스트(`pipeline/test_sync_supabase.py`)는 PostgREST 를 흉내내는 가짜 클라이언트를 주입한다 — cmd_* 함수가 client 를 인자로 받는다.
"""
from __future__ import annotations

import argparse
import json
import re
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "pipeline"))

from store import ASSIGNMENTS_PATH, LABELS_PATH, LocalStore, StoreError  # noqa: E402

SNAPSHOT_PATH = ROOT / "eval/gold/v5_concern_golden_supabase_snapshot.json"
ENV_PATH = ROOT / ".env"
BUNDLE_COUNT = 40  # 번들 고정물 B01~B40 (pipeline/sample_concern_golden.py)


class SyncError(RuntimeError):
    pass


# ---------- 자격증명 ----------
def parse_dotenv(text: str) -> dict[str, str]:
    """아주 단순한 .env 파서. `KEY=value` · `export KEY=value` · 따옴표('…' / "…") · `#` 주석 · 빈 줄만 안다."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:  # 따옴표 없는 값 뒤의 주석
            value = value.split(" #", 1)[0].rstrip()
        out[key] = value
    return out


def load_env(env_path: Path = ENV_PATH, environ: dict | None = None) -> dict[str, str]:
    """.env 가 있으면 먼저, 환경변수가 그 위에 덮는다 (환경변수 우선)."""
    merged: dict[str, str] = {}
    if env_path.exists():
        merged.update(parse_dotenv(env_path.read_text()))
    merged.update(os.environ if environ is None else environ)
    return merged


def credentials(env: dict[str, str]) -> tuple[str, str]:
    url, key = (env.get("SUPABASE_URL") or "").strip(), (env.get("SUPABASE_SERVICE_KEY") or "").strip()
    if not url or not key:
        raise SyncError(
            "Supabase 자격증명이 없다. SUPABASE_URL 과 SUPABASE_SERVICE_KEY 를 환경변수나 저장소 루트의 .env 에 넣어라 "
            "(service role 키 — 서버·운영자만 쓴다. .env 는 커밋하지 않는다)"
        )
    return url, key


# ---------- PostgREST 클라이언트 ----------
class PostgRestClient:
    """SupabaseStore 의 공개 메서드는 jsonb 본문만 돌려주고 upsert 옵션(on_conflict · ignore-duplicates)이 없어서,
    동기화에 필요한 최소 호출을 여기서 직접 한다. 인터페이스는 select / insert 둘 — 테스트는 같은 모양의 가짜를 주입한다."""

    def __init__(self, url: str, key: str, timeout: int = 30):
        self.base = re.sub(r"/rest/v1/?$", "", url.strip().rstrip("/")) + "/rest/v1"  # 대시보드에서 복사한 URL 에 /rest/v1 이 붙어 와도 그대로 동작
        self.key = key
        self.timeout = timeout

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
            with urllib.request.urlopen(req, data=data, timeout=self.timeout) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            raise StoreError(f"supabase {method} {table}: {e.code} {e.read().decode('utf-8', 'ignore')[:300]}") from None
        except urllib.error.URLError as e:
            raise StoreError(f"supabase {method} {table}: 연결 실패 {e.reason}") from None

    def select(self, table: str, columns: str = "*", order: str | None = None) -> list[dict]:
        """표 전체. PostgREST 기본 상한(1000행)을 넘을 수 있어 Range 로 페이지를 넘긴다."""
        params = {"select": columns}
        if order:
            params["order"] = order
        out: list[dict] = []
        page, start = 1000, 0
        while True:
            qs = "?" + urllib.parse.urlencode(params)
            req = urllib.request.Request(f"{self.base}/{table}{qs}", method="GET")
            req.add_header("apikey", self.key)
            req.add_header("Authorization", f"Bearer {self.key}")
            req.add_header("Accept", "application/json")
            req.add_header("Range-Unit", "items")
            req.add_header("Range", f"{start}-{start + page - 1}")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    rows = json.loads(resp.read() or b"[]")
            except urllib.error.HTTPError as e:
                raise StoreError(f"supabase GET {table}: {e.code} {e.read().decode('utf-8', 'ignore')[:300]}") from None
            except urllib.error.URLError as e:
                raise StoreError(f"supabase GET {table}: 연결 실패 {e.reason}") from None
            out.extend(rows)
            if len(rows) < page:
                return out
            start += page

    def insert(self, table: str, rows: list[dict], *, on_conflict: str | None = None, overwrite: bool = False) -> None:
        """여러 행 INSERT. on_conflict 가 있으면 upsert — overwrite 면 merge-duplicates(덮어쓰기), 아니면 ignore-duplicates(건너뛰기)."""
        if not rows:
            return
        params = {"on_conflict": on_conflict} if on_conflict else None
        prefer = "return=minimal"
        if on_conflict:
            prefer = ("resolution=merge-duplicates," if overwrite else "resolution=ignore-duplicates,") + prefer
        self._req("POST", table, params=params, body=rows, prefer=prefer)


# ---------- 공통 ----------
def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _decision_key(d: dict) -> tuple:
    """decisions 는 identity PK 라 자연키가 없다 — 내용이 같으면 같은 결정으로 본다 (push 중복 방지용)."""
    return (d.get("bundleId"), d.get("labelerId"), d.get("candidateId"), d.get("kind"), d.get("reason"), d.get("labelId"), d.get("contractError"))


def _bundle_ids(bundles: dict | None) -> list[str]:
    if bundles:
        return sorted(bundles)
    return [f"B{i:02d}" for i in range(1, BUNDLE_COUNT + 1)]


# ---------- push ----------
def cmd_push(client, local: LocalStore, *, overwrite: bool = False, dry_run: bool = False, out=print) -> dict:
    """정본 → Supabase. 반환값은 표별 {total, sent, skipped} — 테스트와 로그용."""
    labels = local.labels()
    labelers = local.labelers()
    assignments = local.assignments()
    decisions = local.decisions()

    # 라벨의 작성자: 라벨 파일에는 없다(계약) — 결정 기록에서 labelId → labelerId 를 되찾는다. 없으면 null
    labeler_of_label = {d["labelId"]: d["labelerId"] for d in decisions if d.get("labelId")}

    existing_labels = {r["label_id"] for r in client.select("labels", "label_id")}
    existing_labelers = {r["labeler_id"] for r in client.select("labelers", "labeler_id")}
    existing_assignments = {r["bundle_id"] for r in client.select("assignments", "bundle_id")}
    existing_decisions = {_decision_key(r["decision"]) for r in client.select("decisions", "decision")}

    # 결정이 참조하는 라벨러가 labelers 표에 없으면 FK 로 막힌다 — 결정·배정에 등장하는 이름을 라벨러로 보강한다
    known = {l["labelerId"] for l in labelers}
    for a in assignments:
        if a["labelerId"] not in known:
            labelers.append({"labelerId": a["labelerId"], "name": a.get("labelerName") or a["labelerId"]})
            known.add(a["labelerId"])

    def plan(rows: list[dict], key, existing: set) -> tuple[list[dict], int]:
        if overwrite:
            return rows, 0
        keep = [r for r in rows if key(r) not in existing]
        return keep, len(rows) - len(keep)

    labeler_rows, labeler_skip = plan([{"labeler_id": l["labelerId"], "name": l["name"]} for l in labelers], lambda r: r["labeler_id"], existing_labelers)
    label_rows, label_skip = plan(
        [{"label_id": l["labelId"], "bundle_id": l["bundleId"], "labeler_id": labeler_of_label.get(l["labelId"]), "label": l} for l in labels],
        lambda r: r["label_id"], existing_labels)
    # DB 의 assignments 는 bundle_id 가 PK 고 status 체크가 active|done 이다(schema.sql). 로컬은 (bundleId, labelerId) 키라
    # 만료(expired) 이력이 같은 번들에 여러 행 남을 수 있다 — 번들당 살아 있는 행 1개만 올리고 expired 는 올리지 않는다
    live_assignments: dict[str, dict] = {}
    for a in assignments:
        if a["status"] not in ("active", "done"):
            continue
        prev = live_assignments.get(a["bundleId"])
        if prev is None or (a.get("updatedAt") or "") >= (prev.get("updatedAt") or ""):
            live_assignments[a["bundleId"]] = a
    assignment_rows, assignment_skip = plan(
        [{"bundle_id": a["bundleId"], "labeler_id": a["labelerId"], "labeler_name": a["labelerName"], "status": a["status"],
          **({"updated_at": a["updatedAt"]} if a.get("updatedAt") else {})} for a in live_assignments.values()],
        lambda r: r["bundle_id"], existing_assignments)
    assignment_skip += len(assignments) - len(live_assignments)
    # 결정은 자연키가 없어 --overwrite 라도 같은 내용은 다시 넣지 않는다 (덮어쓸 대상이 없다)
    decision_rows = [{"bundle_id": d["bundleId"], "labeler_id": d.get("labelerId"), "decision": d}
                     for d in decisions if _decision_key(d) not in existing_decisions]
    decision_skip = len(decisions) - len(decision_rows)

    report = {
        "labelers": {"total": len(labelers), "sent": len(labeler_rows), "skipped": labeler_skip},
        "labels": {"total": len(labels), "sent": len(label_rows), "skipped": label_skip},
        "assignments": {"total": len(assignments), "sent": len(assignment_rows), "skipped": assignment_skip},
        "decisions": {"total": len(decisions), "sent": len(decision_rows), "skipped": decision_skip},
    }
    mode = "덮어쓰기" if overwrite else "있는 행은 건너뜀"
    out(f"push {'(dry-run) ' if dry_run else ''}— {mode}")
    for table, r in report.items():
        out(f"  {table:<12} 정본 {r['total']:>4}건 → 올림 {r['sent']:>4}건 · 건너뜀 {r['skipped']:>4}건")
    if dry_run:
        return report

    # FK 순서: labelers → labels/assignments → decisions
    client.insert("labelers", labeler_rows, on_conflict="labeler_id", overwrite=overwrite)
    client.insert("labels", label_rows, on_conflict="label_id", overwrite=overwrite)
    client.insert("assignments", assignment_rows, on_conflict="bundle_id", overwrite=overwrite)
    client.insert("decisions", decision_rows)
    out("push 완료")
    return report


# ---------- pull ----------
def cmd_pull(client, *, labels_path: Path = LABELS_PATH, snapshot_path: Path = SNAPSHOT_PATH, bundles: dict | None = None,
             taxonomy=None, overwrite: bool = False, dry_run: bool = False, out=print, err=None) -> dict:
    """Supabase → 정본. 반환값 {merged, added, replaced, failed: [(labelId, reason)]}. failed 가 있으면 호출자가 종료코드 1 을 낸다."""
    err = err or (lambda s: print(s, file=sys.stderr))
    from golden_contract import GoldenContractError, load_failure_taxonomy, validate_label, validate_labels  # noqa: E402
    from sample_concern_golden import load_bundles  # noqa: E402

    bundles = bundles or load_bundles()
    taxonomy = taxonomy or load_failure_taxonomy()

    rows = client.select("labels", "label_id,bundle_id,labeler_id,label,created_at", order="created_at.asc")
    labelers = {r["labeler_id"]: r["name"] for r in client.select("labelers", "labeler_id,name")}
    assignments = client.select("assignments", "bundle_id,labeler_id,labeler_name,status,updated_at")
    decisions = client.select("decisions", "id,bundle_id,labeler_id,decision,created_at", order="id.asc")

    valid: dict[str, dict] = {}
    failed: list[tuple[str, str]] = []
    for r in rows:
        label = r["label"]
        lid = label.get("labelId") if isinstance(label, dict) else None
        lid = lid or r.get("label_id") or "?"
        bundle = bundles.get(label.get("bundleId")) if isinstance(label, dict) else None
        if bundle is None:
            failed.append((lid, f"bundleId {label.get('bundleId') if isinstance(label, dict) else None!r} 가 표본에 없다"))
            continue
        try:
            normalized = validate_label(label, bundle, taxonomy)
        except GoldenContractError as e:
            failed.append((lid, str(e)))
            continue
        if normalized["labelId"] in valid:
            failed.append((lid, "DB 안에서 labelId 중복"))
            continue
        valid[normalized["labelId"]] = normalized

    current = [json.loads(l) for l in labels_path.read_text().splitlines() if l.strip()] if labels_path.exists() else []
    current_ids = {l["labelId"] for l in current}
    added = [l for lid, l in valid.items() if lid not in current_ids]
    replaced = 0
    merged: list[dict] = []
    for l in current:
        if overwrite and l["labelId"] in valid and valid[l["labelId"]] != l:
            merged.append(valid[l["labelId"]])
            replaced += 1
        else:
            merged.append(l)
    merged.extend(added)

    # 병합 결과 전체를 다시 검증한다 — 정본은 통째로 계약을 통과해야 게이트(label_concern_golden.py validate)를 넘는다
    try:
        validate_labels(merged, bundles, taxonomy)
    except GoldenContractError as e:
        raise SyncError(f"병합 결과가 계약을 위반한다 — 정본을 쓰지 않았다: {e}")

    snapshot = {
        "pulledAt": _now(),
        "note": "라벨러 간 일치도 분석용 사이드카. 라벨 파일에는 라벨러 필드가 없다(계약). 정본은 v5_concern_golden_labels.jsonl.",
        "labels": [{"labelId": r["label_id"], "bundleId": r["bundle_id"], "labelerId": r.get("labeler_id"),
                    "labelerName": labelers.get(r.get("labeler_id")), "createdAt": r.get("created_at"),
                    "merged": r["label_id"] in valid} for r in rows],
        "labelers": [{"labelerId": k, "name": v} for k, v in sorted(labelers.items())],
        "assignments": [{"bundleId": a["bundle_id"], "labelerId": a["labeler_id"], "labelerName": a["labeler_name"],
                         "status": a["status"], "updatedAt": a.get("updated_at")} for a in assignments],
        "decisions": [{**d["decision"], "labelerName": labelers.get(d.get("labeler_id")), "createdAt": d.get("created_at")} for d in decisions],
        "failed": [{"labelId": lid, "reason": reason} for lid, reason in failed],
    }

    out(f"pull {'(dry-run) ' if dry_run else ''}— DB 라벨 {len(rows)}건 · 검증 통과 {len(valid)}건 · 실패 {len(failed)}건")
    out(f"  정본 {len(current)}건 → 병합 {len(merged)}건 (추가 {len(added)} · {'덮어씀 ' + str(replaced) if overwrite else '기존 유지'})")
    out(f"  결정 {len(decisions)}건 · 라벨러 {len(labelers)}명 · 배정 {len(assignments)}건 → {snapshot_path.name}")
    for lid, reason in failed:
        err(f"검증 실패 [labelId={lid}] {reason}")
    if not dry_run:
        labels_path.parent.mkdir(parents=True, exist_ok=True)
        labels_path.write_text("".join(json.dumps(l, ensure_ascii=False) + "\n" for l in merged))
        snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n")
        out(f"씀: {labels_path.name} · {snapshot_path.name}")
    if failed:
        err(f"검증 실패 {len(failed)}건은 병합하지 않았다 — 종료코드 1")
    return {"merged": len(merged), "added": len(added), "replaced": replaced, "failed": failed, "snapshot": snapshot}


# ---------- status ----------
def cmd_status(client, *, bundles: dict | None = None, out=print) -> dict:
    """번들별 진행 현황. 완료 = 배정 status 가 done 이거나, 라벨이 있고 활성 배정이 없는 번들(정본에서 올린 B01~B05 등)."""
    label_rows = client.select("labels", "label_id,bundle_id")
    assignment_rows = client.select("assignments", "bundle_id,labeler_name,status")
    decision_rows = client.select("decisions", "bundle_id")

    labels_by: dict[str, int] = {}
    for r in label_rows:
        labels_by[r["bundle_id"]] = labels_by.get(r["bundle_id"], 0) + 1
    decisions_by: dict[str, int] = {}
    for r in decision_rows:
        decisions_by[r["bundle_id"]] = decisions_by.get(r["bundle_id"], 0) + 1
    assignment_by = {r["bundle_id"]: r for r in assignment_rows}

    ids = _bundle_ids(bundles)
    rows_out: list[dict] = []
    done = 0
    out(f"{'번들':<6}{'라벨':>5}{'결정':>5}  {'배정':<8}{'라벨러'}")
    for bid in ids:
        n_labels = labels_by.get(bid, 0)
        n_dec = decisions_by.get(bid, 0)
        a = assignment_by.get(bid)
        status = a["status"] if a else "-"
        who = a["labeler_name"] if a else "-"
        is_done = status == "done" or (n_labels > 0 and status != "active")
        done += is_done
        rows_out.append({"bundleId": bid, "labels": n_labels, "decisions": n_dec, "status": status, "labelerName": who, "done": is_done})
        out(f"{bid:<6}{(n_labels or '-')!s:>5}{(n_dec or '-')!s:>5}  {status:<8}{who}{'  ✓' if is_done else ''}")
    active = sum(1 for r in rows_out if r["status"] == "active")
    out(f"\n완료 {done} / {len(ids)}  · 진행 중 {active}  · 라벨 {len(label_rows)}건 · 결정 {len(decision_rows)}건")
    return {"rows": rows_out, "done": done, "active": active, "total": len(ids)}


# ---------- main ----------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="라벨 검수 앱 Supabase ↔ 정본 동기화 (PER-178)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("push", help="정본(eval/gold) → Supabase. 있는 행은 건너뜀")
    p.add_argument("--overwrite", action="store_true", help="있는 행도 정본 값으로 덮어쓴다")
    p.add_argument("--dry-run", action="store_true", help="몇 건 올릴지만 출력")
    p = sub.add_parser("pull", help="Supabase → 정본. 계약 검증 후 labelId 기준 병합 + 사이드카 스냅샷")
    p.add_argument("--overwrite", action="store_true", help="같은 labelId 는 DB 값이 우선")
    p.add_argument("--dry-run", action="store_true", help="파일을 쓰지 않고 결과만 출력")
    sub.add_parser("status", help="번들별 진행 현황 표")
    args = ap.parse_args(argv)

    try:
        url, key = credentials(load_env())
        client = PostgRestClient(url, key)
        if args.cmd == "push":
            cmd_push(client, LocalStore(LABELS_PATH, ASSIGNMENTS_PATH), overwrite=args.overwrite, dry_run=args.dry_run)
        elif args.cmd == "pull":
            result = cmd_pull(client, overwrite=args.overwrite, dry_run=args.dry_run)
            if result["failed"]:
                return 1
        elif args.cmd == "status":
            cmd_status(client)
    except (SyncError, StoreError) as e:
        print(f"오류: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
