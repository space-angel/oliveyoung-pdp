"""공유용 페이지 만들기 — 템플릿 + 요약 JSON → 단일 HTML (PER-191).

페이지를 손으로 고치지 않는다. 수치가 바뀌면 `summarize_claims_share.py` 를 다시
돌리고 이 스크립트로 다시 찍는다 — 그래야 페이지의 수치가 리포트와 갈리지 않는다.

사용:
  .venv/bin/python eval/share/build_page.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]
TEMPLATE = Path(__file__).parent / "claims_page.template.html"
SUMMARY = ROOT / "eval/reports/claims_share_summary.json"
OUT = Path(__file__).parent / "claims_page.html"

PLACEHOLDER = "window.__CLAIMS_DATA__"


def main() -> None:
    if not SUMMARY.exists():
        raise SystemExit(f"FAIL: 요약이 없다 ({SUMMARY.relative_to(ROOT)})\n"
                         "  → .venv/bin/python eval/summarize_claims_share.py")
    html = TEMPLATE.read_text()
    if PLACEHOLDER not in html:
        raise SystemExit(f"FAIL: 템플릿에 {PLACEHOLDER} 자리가 없다")
    data = json.loads(SUMMARY.read_text())
    # </script> 가 데이터 안에 있으면 스크립트 블록이 조기 종료된다 — 인용문은 리뷰 원문이라
    # 그럴 일이 거의 없지만, 막지 않으면 페이지가 조용히 깨진다.
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    OUT.write_text(html.replace(PLACEHOLDER, blob))
    print(f"→ {OUT.relative_to(ROOT)}  ({OUT.stat().st_size:,} bytes · "
          f"claim {data['generation']['produced']:,}건 · 예시 {len(data['examples'])}건)")


if __name__ == "__main__":
    main()
