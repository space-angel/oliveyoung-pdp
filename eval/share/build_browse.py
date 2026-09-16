"""탐색 페이지 만들기 — 템플릿 + 전건 데이터 → 단일 HTML (PER-191).

`build_page.py` 와 같은 규칙이고 싣는 것만 다르다. 저쪽은 **요약과 예시 몇 건**이고
이쪽은 **claim 전건 + 인용이 걸린 리뷰 원문**이다. 원문을 통째로 싣는 이유는
읽는 사람이 인용을 원문 안에서 확인할 수 있어야 하기 때문이다 (PER-190).

페이지를 손으로 고치지 않는다. 산출물이 바뀌면 `export_claims.py` 를 다시 돌리고
이 스크립트로 다시 찍는다 — 그래야 페이지가 산출물과 갈리지 않는다. 실제로 갈렸었다:
claim 236건짜리 산출물 옆에 997건짜리 페이지가 남아 있었고, 생성기가 없어서
무엇으로 만든 페이지인지 되짚을 수도 없었다.

사용:
  .venv/bin/python eval/share/export_claims.py     # 데이터 먼저
  .venv/bin/python eval/share/build_browse.py
"""
import json
from pathlib import Path

ROOT = Path(__file__).parents[2]
TEMPLATE = Path(__file__).parent / "browse_template.html"
DATA = Path(__file__).parent / "claims_data.json"
OUT = Path(__file__).parent / "claims_browse.html"

PLACEHOLDER = "__BROWSE_DATA__"


def main() -> None:
    if not DATA.exists():
        raise SystemExit(
            f"FAIL: 데이터가 없다 ({DATA.relative_to(ROOT)})\n"
            "  → .venv/bin/python eval/share/export_claims.py")
    html = TEMPLATE.read_text()
    if PLACEHOLDER not in html:
        raise SystemExit(f"FAIL: 템플릿에 {PLACEHOLDER} 자리가 없다")
    data = json.loads(DATA.read_text())
    # </script> 가 데이터 안에 있으면 스크립트 블록이 조기 종료된다 — 인용문은 리뷰
    # 원문이라 그럴 일이 거의 없지만, 막지 않으면 페이지가 조용히 깨진다.
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    OUT.write_text(html.replace(PLACEHOLDER, blob))
    print(f"→ {OUT.relative_to(ROOT)}  ({OUT.stat().st_size:,} bytes · "
          f"claim {data['totals']['claims']:,}건 · 인용 {data['totals']['quotes']:,}개 · "
          f"원문 {len(data['reviews']):,}건)")


if __name__ == "__main__":
    main()
