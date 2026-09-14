"""
라벨 검수 앱 HTTP 서버 (PER-178 외부 라벨러용). 표준 라이브러리만.

  python3 apps/label-review/server.py                 # http://127.0.0.1:8180  (로컬 JSONL 저장)
  LABEL_ACCESS_CODE=abcd python3 apps/label-review/server.py     # 접속 코드 요구
  SUPABASE_URL=… SUPABASE_SERVICE_KEY=… python3 apps/label-review/server.py   # Supabase 저장

Vercel 등 서버리스에 올릴 때는 `handler` 클래스를 그대로 노출한다 (BaseHTTPRequestHandler 규약).

API (모두 JSON)
  POST /api/start        {name, code, labelerId?}              → {labelerId, name}  (labelerId 는 브라우저가 저장해 둔 것 재사용)
  GET  /api/bundle?labeler=…                                  → 배정된 번들 화면 데이터 (없으면 새로 배정)
  POST /api/decide       {labelerId, candidateId, kind, reason?, edits?, minutesSpent?}
  POST /api/human        {labelerId, none?|question, answer, aspect?, evidence[], condition?, minutesSpent?}
  POST /api/complete     {labelerId}                          → {summary, next}
  POST /api/undo         {labelerId, candidateId}             → 판정 되돌리기 (결정·라벨 삭제, 다시 판단 가능)

관리자 (환경변수 LABEL_ADMIN_KEY 가 있을 때만 열린다 — 비어 있으면 아래 두 주소는 404)
  GET  /admin                                                 → public/admin.html
  GET  /api/admin/status?key=…  (또는 헤더 X-Admin-Key)        → admin.status_report (번들 40개 상태 + 요약 + 라벨러 표)
                                                              키가 다르면 403
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from admin import status_report  # noqa: E402
from service import Service, ServiceError  # noqa: E402
from store import StoreError, open_store  # noqa: E402

HTML_PATH = HERE / "public" / "index.html"
ADMIN_HTML_PATH = HERE / "public" / "admin.html"
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

    def _html(self, path: Path) -> None:
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _admin_key_ok(self, q: dict) -> bool:
        """LABEL_ADMIN_KEY 와 요청 키(쿼리 key= 또는 헤더 X-Admin-Key) 비교. 매 요청마다 환경변수를 읽는다."""
        expected = os.environ.get("LABEL_ADMIN_KEY") or ""
        given = self.headers.get("X-Admin-Key") or (q.get("key") or [""])[0]
        return bool(expected) and hmac.compare_digest(expected, given)

    def do_HEAD(self) -> None:  # noqa: N802  (Render 헬스체크가 HEAD / 를 보낸다)
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        url = urllib.parse.urlparse(self.path)
        if url.path in ("/", "/index.html"):
            return self._html(HTML_PATH)
        q = urllib.parse.parse_qs(url.query)
        if url.path in ("/admin", "/admin.html", "/api/admin/status"):
            # 키가 설정돼 있지 않으면 관리자 화면 자체가 없는 것처럼 — 키 없이 열리는 일이 없게
            if not os.environ.get("LABEL_ADMIN_KEY"):
                return self._json(404, {"error": "없는 주소예요"})
            if url.path != "/api/admin/status":
                return self._html(ADMIN_HTML_PATH)
            if not self._admin_key_ok(q):
                return self._json(403, {"error": "관리자 키가 맞지 않아요"})
            svc = get_service()
            return self._run(lambda: status_report(svc.store, svc.bundles, svc.candidates))
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
            "/api/start": lambda: svc.start(body.get("name") or "", body.get("code"), body.get("labelerId")),
            "/api/decide": lambda: svc.decide(lid, body),
            "/api/human": lambda: svc.add_human(lid, body),
            "/api/complete": lambda: svc.complete(lid),
            "/api/undo": lambda: svc.undo(lid, body.get("candidateId") or ""),
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
    admin = "있음" if os.environ.get("LABEL_ADMIN_KEY") else "없음 → /admin 은 404"
    print(f"리뷰 질문 검수 → http://127.0.0.1:{args.port}   저장소 {kind} · 접속 코드 {'있음' if svc.access_code else '없음'} · 관리자 키 {admin} · "
          f"상한 {os.environ.get('LABEL_MAX_BUNDLES', '2')}개 · Ctrl-C 종료", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n종료")


if __name__ == "__main__":
    main()
