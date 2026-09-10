"""
라벨 검수 앱 HTTP 서버 (PER-178 외부 라벨러용). 표준 라이브러리만.

  python3 apps/label-review/server.py                 # http://127.0.0.1:8180  (로컬 JSONL 저장)
  LABEL_ACCESS_CODE=abcd python3 apps/label-review/server.py     # 접속 코드 요구
  SUPABASE_URL=… SUPABASE_SERVICE_KEY=… python3 apps/label-review/server.py   # Supabase 저장

Vercel 등 서버리스에 올릴 때는 `handler` 클래스를 그대로 노출한다 (BaseHTTPRequestHandler 규약).

API (모두 JSON)
  POST /api/start        {name, code}                         → {labelerId, name}
  GET  /api/bundle?labeler=…                                  → 배정된 번들 화면 데이터 (없으면 새로 배정)
  POST /api/decide       {labelerId, candidateId, kind, reason?, edits?, minutesSpent?}
  POST /api/human        {labelerId, none?|question, answer, aspect?, evidence[], condition?, minutesSpent?}
  POST /api/complete     {labelerId}                          → {summary, next}
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from service import Service, ServiceError  # noqa: E402
from store import StoreError, open_store  # noqa: E402

HTML_PATH = HERE / "public" / "index.html"
_service: Service | None = None


def get_service() -> Service:
    global _service
    if _service is None:
        _service = Service(open_store(), access_code=os.environ.get("LABEL_ACCESS_CODE") or None)
    return _service


class handler(BaseHTTPRequestHandler):  # noqa: N801  (Vercel 이 이 이름을 찾는다)
    def _json(self, status: int, body) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw or b"{}")
        except json.JSONDecodeError:
            raise ServiceError("요청을 읽을 수 없어요")

    def _run(self, fn):
        try:
            self._json(200, fn())
        except ServiceError as e:
            self._json(e.status, {"error": str(e)})
        except StoreError as e:
            self._json(502, {"error": f"저장소 오류: {e}"})

    def do_GET(self) -> None:  # noqa: N802
        url = urllib.parse.urlparse(self.path)
        if url.path in ("/", "/index.html"):
            data = HTML_PATH.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        q = urllib.parse.parse_qs(url.query)
        svc = get_service()
        if url.path == "/api/bundle":
            lid = (q.get("labeler") or [""])[0]
            name = (q.get("name") or [""])[0]

            def fn():
                if not lid:
                    raise ServiceError("먼저 이름을 알려주세요", 401)
                if not svc.current_assignment(lid):
                    svc.assign_next(lid, name or lid)
                view = svc.bundle_view(lid)
                view["progress"] = svc.progress(lid)
                return view
            return self._run(fn)
        self._json(404, {"error": "없는 주소예요"})

    def do_POST(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        svc = get_service()
        try:
            body = self._body()
        except ServiceError as e:
            return self._json(e.status, {"error": str(e)})
        lid = body.get("labelerId") or ""
        routes = {
            "/api/start": lambda: svc.start(body.get("name") or "", body.get("code")),
            "/api/decide": lambda: svc.decide(lid, body),
            "/api/human": lambda: svc.add_human(lid, body),
            "/api/complete": lambda: svc.complete(lid),
        }
        if path not in routes:
            return self._json(404, {"error": "없는 주소예요"})
        self._run(routes[path])

    def log_message(self, fmt, *args):
        if self.command != "GET":
            sys.stderr.write(f"{self.command} {self.path} — {fmt % args}\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8180)
    args = ap.parse_args()
    svc = get_service()
    kind = type(svc.store).__name__
    server = ThreadingHTTPServer(("0.0.0.0", args.port), handler)
    print(f"리뷰 질문 검수 → http://127.0.0.1:{args.port}   저장소 {kind} · 접속 코드 {'있음' if svc.access_code else '없음'} · Ctrl-C 종료")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n종료")


if __name__ == "__main__":
    main()
