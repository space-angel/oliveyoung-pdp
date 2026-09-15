"""
플레이그라운드에 그대로 붙일 태깅 입력 만들기 (PER-175).

배치와 **같은 프롬프트·같은 본문 접기**를 쓴다. 여기서 만든 입력으로 콘솔에서 돌린
결과는 배치 결과와 같은 잣대로 비교할 수 있다.

  python3 eval/playground_payload.py --size 5                 # 앞에서 5건
  python3 eval/playground_payload.py --review-ids 11886119 61847286 62505035
  python3 eval/playground_payload.py --chunk 0 --size 20      # 배치 청크 그대로
  python3 eval/playground_payload.py --size 5 --with-gold     # 정답셋 태그도 함께

`--system` 을 주면 시스템 프롬프트를 그대로 찍는다 (정본은 pipeline/prompts/tag/v1.md).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

from tag import PROMPT_PATH, chunk_payload, load_reviews  # noqa: E402

GOLD_PATH = ROOT / "eval/gold/v5_tags_pilot_gold.jsonl"


def main() -> None:
    ap = argparse.ArgumentParser(description="플레이그라운드용 태깅 입력")
    ap.add_argument("--size", type=int, default=5, help="리뷰 수 (기본 5)")
    ap.add_argument("--chunk", type=int, help="배치 청크 번호. 주면 그 청크를 그대로 낸다")
    ap.add_argument("--review-ids", nargs="+", type=int, help="특정 reviewId 만")
    ap.add_argument("--with-gold", action="store_true", help="정답셋 태그도 함께 출력")
    ap.add_argument("--system", action="store_true", help="시스템 프롬프트를 출력하고 끝")
    a = ap.parse_args()

    if a.system:
        print(PROMPT_PATH.read_text(), end="")
        return

    reviews = load_reviews(pilot=True)
    if a.review_ids:
        by_id = {r["reviewId"]: r for r in reviews}
        missing = [i for i in a.review_ids if i not in by_id]
        if missing:
            raise SystemExit(f"표본에 없는 reviewId: {missing}")
        picked = [by_id[i] for i in a.review_ids]
    elif a.chunk is not None:
        start = a.chunk * a.size
        picked = reviews[start : start + a.size]
        if not picked:
            raise SystemExit(f"청크 {a.chunk} 가 표본 범위를 벗어난다 (표본 {len(reviews)}건)")
    else:
        picked = reviews[: a.size]

    print(chunk_payload(picked))

    if a.with_gold:
        ids = {r["reviewId"] for r in picked}
        gold = [
            json.loads(l)
            for l in GOLD_PATH.read_text().splitlines()
            if l.strip() and json.loads(l)["reviewId"] in ids
        ]
        print("\n--- 정답셋 태그 ---", file=sys.stderr)
        print(json.dumps({"results": [
            {"reviewId": rid,
             "aspects": [{k: v for k, v in g.items() if k != "reviewId"} for g in gold if g["reviewId"] == rid]}
            for rid in (r["reviewId"] for r in picked)
        ]}, ensure_ascii=False, indent=2), file=sys.stderr)


if __name__ == "__main__":
    main()
