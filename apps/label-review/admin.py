"""
라벨 검수 앱 — 관리자 진행 현황 (PER-178).

라벨러 화면(service.py)과 달리 **아무것도 쓰지 않는다.** 저장소의 공개 메서드(labels / assignments / decisions / labelers)만
읽어 번들 40개의 상태를 한 장으로 만든다. 정본 규칙은 그대로다 — 여기서 상태를 바꾸거나 배정을 풀지 않는다.

번들 상태 판정 (위에서부터 먼저 맞는 것)
  done      배정 status 가 done
            또는 라벨이 1개 이상 있고 "놓친 질문" 에 답했다 (no_missed 또는 human 결정)
            또는 라벨이 1개 이상 있는데 배정 기록이 없다 (CLI 로 손수 만든 B01~B05 — 배정에서 이미 빠지므로 남은 것으로 세지 않는다)
  active    배정 status 가 active
  expired   배정 status 가 expired
  free      그 외 (아무도 잡지 않았다)
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "eval"))

from concern_candidates import load_candidates  # noqa: E402

DECISION_KINDS = ("accept", "edit", "reject", "human", "no_missed")


def bundle_state(assignment: dict | None, label_count: int, decisions: dict[str, int]) -> str:
    """번들 하나의 상태. 모듈 docstring 의 규칙을 그대로 코드로 옮겼다."""
    status = (assignment or {}).get("status")
    if status == "done":
        return "done"
    if label_count > 0 and (decisions.get("no_missed", 0) or decisions.get("human", 0)):
        return "done"
    if label_count > 0 and assignment is None:
        return "done"
    if status == "active":
        return "active"
    if status == "expired":
        return "expired"
    return "free"


def status_report(store, bundles: dict[str, dict], candidates: list[dict] | None = None) -> dict:
    """관리자 화면 JSON. `store` 는 LocalStore / SupabaseStore 어느 쪽이든 공개 메서드만 쓴다.

    `bundles` 는 sample_concern_golden.load_bundles() 의 dict, `candidates` 는 concern_candidates.load_candidates() 의 목록
    (생략하면 정본 후보 파일을 읽는다). service.Service 인스턴스가 있으면 `.store` · `.bundles` · `.candidates` 를 그대로 넘기면 된다.
    """
    if candidates is None:
        candidates = load_candidates()

    labels = store.labels()
    assignments = {a["bundleId"]: a for a in store.assignments()}
    decisions = store.decisions()

    labels_by_bundle: dict[str, list[dict]] = {}
    for l in labels:
        labels_by_bundle.setdefault(l["bundleId"], []).append(l)
    candidates_by_bundle: dict[str, int] = {}
    for c in candidates:
        candidates_by_bundle[c["bundleId"]] = candidates_by_bundle.get(c["bundleId"], 0) + 1
    decisions_by_bundle: dict[str, dict[str, int]] = {}
    for d in decisions:
        counts = decisions_by_bundle.setdefault(d["bundleId"], {k: 0 for k in DECISION_KINDS})
        kind = d.get("kind")
        if kind in counts:
            counts[kind] += 1

    rows = []
    for bid in sorted(bundles):
        b = bundles[bid]
        a = assignments.get(bid)
        dec = decisions_by_bundle.get(bid, {k: 0 for k in DECISION_KINDS})
        n_labels = len(labels_by_bundle.get(bid, []))
        rows.append({
            "bundleId": bid,
            "displayName": b.get("displayName", bid),
            "reviewCount": len(b.get("reviews", [])),
            "candidateCount": candidates_by_bundle.get(bid, 0),
            "labels": n_labels,
            "decisions": dec,
            # updatedAt 은 곧 추가될 필드 — 없으면 None
            "assignment": ({"labelerId": a.get("labelerId"), "labelerName": a.get("labelerName"), "status": a.get("status"),
                            "updatedAt": a.get("updatedAt")} if a else None),
            "state": bundle_state(a, n_labels, dec),
        })

    # 라벨러 표 — 등록된 라벨러 + 배정에만 이름이 남은 사람(등록 레코드가 없어도 표에는 나와야 한다)
    labelers: dict[str, dict] = {}
    for p in store.labelers():
        labelers[p["labelerId"]] = {"labelerId": p["labelerId"], "name": p.get("name") or p["labelerId"], "doneBundles": 0, "activeBundle": None}
    for r in rows:
        a = r["assignment"]
        if not a or not a.get("labelerId"):
            continue
        p = labelers.setdefault(a["labelerId"], {"labelerId": a["labelerId"], "name": a.get("labelerName") or a["labelerId"], "doneBundles": 0, "activeBundle": None})
        if r["state"] == "done":
            p["doneBundles"] += 1
        elif r["state"] == "active":
            p["activeBundle"] = r["bundleId"]

    by_state = {s: sum(1 for r in rows if r["state"] == s) for s in ("done", "active", "expired", "free")}
    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "summary": {
            "doneBundles": by_state["done"],
            "activeBundles": by_state["active"],
            "expiredBundles": by_state["expired"],
            "freeBundles": by_state["free"],
            "totalBundles": len(rows),
            "labelers": sorted(labelers.values(), key=lambda p: (-p["doneBundles"], p["name"])),
            "totalLabels": len(labels),
            "humanLabels": sum(1 for l in labels if l.get("source") == "human"),
        },
        "bundles": rows,
    }
