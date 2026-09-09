"""
주장 골든셋 라벨 도구 — 웹 UI (PER-178). CLI(`label_concern_golden.py`)와 같은 계약·같은 파일.

  python3 eval/label_concern_golden_web.py            # http://127.0.0.1:8178
  python3 eval/label_concern_golden_web.py --port 9000

표준 라이브러리만 쓴다(의존성 0). 로컬에서만 듣는다 — 라벨은 사람이 만드는 것이고 공개할 이유가 없다.

화면: 왼쪽 번들 목록(진척) · 가운데 리뷰(원문을 드래그 → support/oppose 버튼으로 근거 추가) ·
오른쪽 claim 폼. 저장은 `golden_contract.validate_label` 을 통과해야만 된다 — 위반이면 저장되지 않고
이유가 그대로 뜬다. 블라인드 규칙은 CLI 와 같다: 파이프라인 결과·v4 산출물은 어디에도 없다.

라벨 삭제는 라벨 파일을 다시 쓰는 것이다. 골든셋은 사람이 만든 고정물이라 git 이 이력을 가진다.
"""
from __future__ import annotations

import argparse
import html
import json
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))
sys.path.insert(0, str(Path(__file__).parent))

from codebook import load_codebook  # noqa: E402
from contracts import CONDITION_AXES, MISSING_SEGMENT  # noqa: E402
from golden_contract import (  # noqa: E402
    DIRECTIONS,
    EVALUATIONS,
    STANCES,
    GoldenContractError,
    load_failure_taxonomy,
    support_counts,
    validate_label,
)
from concern_candidates import load_candidates  # noqa: E402
from label_concern_golden import _condition_line, append_label, load_labels, summarize  # noqa: E402
from policy import SUFFICIENCY_N_MIN  # noqa: E402
from sample_concern_golden import LABELS_PATH, load_bundles  # noqa: E402
from tag_contract import ASPECTS, fold_invisible  # noqa: E402

DEFAULT_PORT = 8178
HTML_PATH = Path(__file__).with_name("label_concern_golden_web.html")
ASPECT_KEYWORDS_PATH = Path(__file__).with_name("aspect_keywords.json")


def load_aspect_keywords(path: Path = ASPECT_KEYWORDS_PATH) -> dict[str, list[str]]:
    """읽기 보조 사전 (A안). 키는 ASPECTS 14종과 같아야 한다 — 아니면 라벨러가 없는 칸을 고른다."""
    data = json.loads(path.read_text())
    words = {k: v["keywords"] for k, v in data.items() if not k.startswith("_")}
    if set(words) != set(ASPECTS):
        raise ValueError(
            f"aspect_keywords.json 의 키가 ASPECTS 와 다르다: 빠짐 {sorted(set(ASPECTS) - set(words))}, "
            f"초과 {sorted(set(words) - set(ASPECTS))}"
        )
    return words


def load_question_templates(path: Path = ASPECT_KEYWORDS_PATH) -> dict[str, list[str]]:
    """aspect 별 질문 틀. 라벨러가 골라 고친다 — 질문 짜내기가 건당 시간의 대부분이었다 (B01 실측)."""
    data = json.loads(path.read_text())
    return {k: list(v.get("questions") or []) for k, v in data.items() if not k.startswith("_")}


def aspect_hits(reviews: list[dict], keywords: dict[str, list[str]]) -> dict[str, dict]:
    """aspect → 언급 리뷰 수·리뷰 ID. 문자열 포함 검색이지 태깅이 아니다."""
    out = {}
    for aspect, words in keywords.items():
        ids = [r["reviewId"] for r in reviews if any(w in fold_invisible(r["raw"]["content"]) for w in words)]
        out[aspect] = {"reviews": len(ids), "reviewIds": ids, "keywords": words}
    return out


# --- API 로직 (핸들러와 분리 — 테스트가 이걸 부른다) ---


def api_bundles() -> list[dict]:
    bundles = load_bundles()
    counts: dict[str, int] = {}
    for l in load_labels():
        counts[l["bundleId"]] = counts.get(l["bundleId"], 0) + 1
    cand_counts: dict[str, int] = {}
    for c in load_candidates():
        cand_counts[c["bundleId"]] = cand_counts.get(c["bundleId"], 0) + 1
    return [
        {
            "bundleId": b["bundleId"],
            "phase": b["phase"],
            "productId": b["productId"],
            "displayName": b["displayName"],
            "category": b["category"],
            "scope": b["scope"],
            "reviews": len(b["reviews"]),
            "labels": counts.get(b["bundleId"], 0),
            "candidates": cand_counts.get(b["bundleId"], 0),
        }
        for b in bundles.values()
    ]


def api_bundle(bundle_id: str) -> dict:
    bundles = load_bundles()
    if bundle_id not in bundles:
        raise KeyError(bundle_id)
    b = bundles[bundle_id]
    codebook = load_codebook()
    taxonomy = load_failure_taxonomy()
    scope = b["scope"]
    scope_label = None
    if scope["axis"]:
        seg = scope["segment"]
        scope_label = seg if scope["axis"] == "option" or seg == MISSING_SEGMENT else f"{codebook.label(scope['axis'], seg)}({seg})"
    option_segments = sorted({r["condition"]["option"]["segment"] for r in b["reviews"]} - {MISSING_SEGMENT})
    return {
        "bundle": {k: v for k, v in b.items() if k != "reviews"},
        "scopeLabel": scope_label,
        "reviews": [
            {
                "reviewId": r["reviewId"],
                "rating": r["raw"]["rating"],
                "month": r["derived"]["reviewYearMonth"],
                "author": r["derived"]["authorKey"],
                "conditionLine": _condition_line(r, codebook),
                "skinType": r["condition"]["skinType"]["segment"],
                "skinTrouble": r["condition"]["skinTrouble"]["segments"],
                "option": r["condition"]["option"]["segment"],
                "content": fold_invisible(r["raw"]["content"]),
            }
            for r in b["reviews"]
        ],
        "labels": [l for l in load_labels() if l["bundleId"] == bundle_id],
        "candidates": bundle_candidates(bundle_id),
        "aspectHits": aspect_hits(b["reviews"], load_aspect_keywords()),
        "questionTemplates": load_question_templates(),
        "vocab": {
            "aspects": list(ASPECTS),
            "directions": list(DIRECTIONS),
            "stances": list(STANCES),
            "evaluations": list(EVALUATIONS),
            "axes": list(CONDITION_AXES),
            "missing": MISSING_SEGMENT,
            "skinType": codebook.domain("skinType") and {c: codebook.label("skinType", c) for c in sorted(codebook.domain("skinType"))},
            "skinTrouble": {c: codebook.label("skinTrouble", c) for c in sorted(codebook.domain("skinTrouble"))},
            "optionSegments": option_segments,
            "taxonomy": {"version": taxonomy.version, "types": list(taxonomy.types)},
            "nMin": SUFFICIENCY_N_MIN,
        },
    }


def bundle_candidates(bundle_id: str) -> list[dict]:
    """모델 후보 + 사람 결정 상태. 후보는 정답이 아니다 — 사람이 채택·수정·기각한 라벨이 정답이다."""
    decisions = {l["candidateId"]: l for l in load_labels() if l.get("candidateId")}
    out = []
    for c in load_candidates():
        if c["bundleId"] != bundle_id:
            continue
        d = decisions.get(c["candidateId"])
        out.append({**c, "decision": (d["source"] if d else None), "labelId": (d["labelId"] if d else None)})
    return out


def api_add_label(payload: dict) -> tuple[int, dict]:
    """(HTTP 상태, 응답). 계약 위반은 400 이고 파일에 쓰지 않는다."""
    bundles = load_bundles()
    bundle = bundles.get(payload.get("bundleId"))
    if bundle is None:
        return 400, {"error": f"번들 {payload.get('bundleId')!r} 이 없다"}
    taxonomy = load_failure_taxonomy()
    raw = dict(payload)
    raw.setdefault("productId", bundle["productId"])
    raw["failureReasons"] = taxonomy.sort(list(raw.get("failureReasons") or []))
    try:
        label = validate_label(raw, bundle, taxonomy)
        if any(l.get("labelId") == label["labelId"] for l in load_labels()):
            raise GoldenContractError(f"labelId 중복: {label['labelId']!r}")
    except GoldenContractError as e:
        return 400, {"error": str(e)}
    append_label(label)
    return 200, {"label": label, "counts": support_counts(label, bundle)}


def api_delete_label(label_id: str) -> tuple[int, dict]:
    labels = load_labels()
    kept = [l for l in labels if l["labelId"] != label_id]
    if len(kept) == len(labels):
        return 404, {"error": f"labelId {label_id!r} 가 없다"}
    LABELS_PATH.write_text("".join(json.dumps(l, ensure_ascii=False) + "\n" for l in kept))
    return 200, {"deleted": label_id, "remaining": len(kept)}


def api_stats() -> tuple[int, dict]:
    try:
        return 200, summarize(load_labels(), load_bundles())
    except GoldenContractError as e:
        return 400, {"error": str(e)}


# --- HTTP ---


class Handler(BaseHTTPRequestHandler):
    def _json(self, status: int, body) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _html(self, text: str) -> None:
        data = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/":
                return self._html(HTML_PATH.read_text())
            if path == "/api/bundles":
                return self._json(200, api_bundles())
            if path.startswith("/api/bundle/"):
                return self._json(200, api_bundle(path.rsplit("/", 1)[1]))
            if path == "/api/stats":
                return self._json(*api_stats())
        except KeyError as e:
            return self._json(404, {"error": f"없다: {e}"})
        except SystemExit as e:  # load_bundles 가 표본 없음을 SystemExit 로 알린다
            return self._json(500, {"error": str(e)})
        self._json(404, {"error": html.escape(path)})

    def do_POST(self) -> None:  # noqa: N802
        if urllib.parse.urlparse(self.path).path != "/api/labels":
            return self._json(404, {"error": "no route"})
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError as e:
            return self._json(400, {"error": f"JSON 파싱 실패: {e}"})
        self._json(*api_add_label(payload))

    def do_DELETE(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        if not path.startswith("/api/labels/"):
            return self._json(404, {"error": "no route"})
        self._json(*api_delete_label(urllib.parse.unquote(path.rsplit("/", 1)[1])))

    def log_message(self, fmt, *args):  # 조용히
        if self.command != "GET":
            sys.stderr.write(f"{self.command} {self.path} — {fmt % args}\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = ap.parse_args()
    load_bundles()  # 표본이 없으면 여기서 안내와 함께 멈춘다
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"골든셋 라벨 도구 → http://127.0.0.1:{args.port}   (라벨 파일 {LABELS_PATH.relative_to(ROOT)}, Ctrl-C 종료)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n종료")


if __name__ == "__main__":
    main()
