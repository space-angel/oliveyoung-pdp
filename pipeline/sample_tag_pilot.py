"""
태깅 파일럿 표본 추출 (PER-175 선행).

25K 전수를 Batch API 로 돌리기 전에, **정답셋(gold set)** 을 만들 표본을 뽑는다.
표본은 배치 결과를 채점할 기준이므로 재현 가능해야 한다 — 시드 고정, 시각 미기록.

층화 이유: 평점 분포가 5점 85% · 1~2점 1.6% 라 무작위로 뽑으면 부정 태그가
표본에 3건쯤 들어온다. 부정 방향을 못 채점하는 정답셋은 쓸모가 없다.
**희소 클래스를 의도적으로 과표집하고 층별 가중치를 함께 기록한다** — 코퍼스
비율로 되돌릴 수 있어야 "표본에서 부정 20%"를 코퍼스 수치로 오독하지 않는다.

  입력  data/intermediate/v5_reviews.jsonl        (PER-173 입수 결과)
  출력  eval/gold/v5_tag_pilot_sample.jsonl       (레코드 전문 — **커밋한다**)
        eval/gold/v5_tag_pilot_meta.json          (시드·층별 가중치·입력 해시 — 커밋)
        data/intermediate/v5_tag_pilot_batches/*.json (태거 입력 형식, 재생성 가능)

표본을 `eval/gold/` 에 두는 이유: 이 표본 위에 손으로 만든 정답셋
(`eval/gold/v5_tags_pilot_gold.jsonl`)이 올라간다. 입수 결과가 바뀌어 표본이 달라지면
정답셋이 통째로 무의미해지므로, 표본은 재생성물이 아니라 **평가 고정물**로 취급한다.
`--check` 는 재실행 표본이 이 고정물과 같은지 본다 — 달라지면 세운다.

사용:
  python3 pipeline/sample_tag_pilot.py
  python3 pipeline/sample_tag_pilot.py --check   # 재실행이 같은 표본을 주는지
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from tag_contract import tagging_text  # noqa: E402

ROOT = Path(__file__).parents[1]
REVIEWS_PATH = ROOT / "data/intermediate/v5_reviews.jsonl"
SAMPLE_PATH = ROOT / "eval/gold/v5_tag_pilot_sample.jsonl"
BATCH_DIR = ROOT / "data/intermediate/v5_tag_pilot_batches"
GOLD_PATH = ROOT / "eval/gold/v5_tags_pilot_gold.jsonl"
META_PATH = ROOT / "eval/gold/v5_tag_pilot_meta.json"

SEED = 20260904
BATCH_SIZE = 20  # 비용 실측(PER-175 설명)이 배치 20건 기준이라 같은 크기를 쓴다

# (층 이름, 평점 조건, 목표 건수). 합 200.
STRATA = (
    ("rating_1_2", lambda r: r <= 2, 60),
    ("rating_3", lambda r: r == 3, 40),
    ("rating_4", lambda r: r == 4, 40),
    ("rating_5", lambda r: r == 5, 60),
)


def load_reviews() -> list[dict]:
    if not REVIEWS_PATH.exists():
        raise SystemExit(f"입수 결과가 없다 ({REVIEWS_PATH.relative_to(ROOT)}). 먼저 pipeline/ingest.py 를 돌려라")
    return [json.loads(line) for line in REVIEWS_PATH.read_text().splitlines() if line]


def _spread_by_product(pool: list[dict], want: int, rng: random.Random) -> list[dict]:
    """제품이 한쪽으로 쏠리지 않게 고른다. 이미 많이 뽑힌 제품을 뒤로 민다."""
    rng.shuffle(pool)
    taken: list[dict] = []
    per_product: collections.Counter = collections.Counter()
    remaining = list(pool)
    while remaining and len(taken) < want:
        remaining.sort(key=lambda rec: per_product[rec["productId"]])
        floor = per_product[remaining[0]["productId"]]
        # 같은 선택 횟수인 후보들 중에서는 셔플 순서를 유지한다 (시드 재현)
        pick = next(rec for rec in remaining if per_product[rec["productId"]] == floor)
        remaining.remove(pick)
        per_product[pick["productId"]] += 1
        taken.append(pick)
    return taken


def sample() -> tuple[list[dict], dict]:
    reviews = load_reviews()
    rng = random.Random(SEED)

    picked: list[dict] = []
    strata_meta = []
    for name, predicate, want in STRATA:
        pool = [r for r in reviews if predicate(r["raw"]["rating"])]
        # 층 안에서 skinType 기재/미기재를 반씩 — 조건축이 붙는 쪽만 보면 편향된다
        stated = [r for r in pool if r["condition"]["skinType"]["stated"]]
        missing = [r for r in pool if not r["condition"]["skinType"]["stated"]]
        half = want // 2
        take_stated = _spread_by_product(stated, min(half, len(stated)), rng)
        take_missing = _spread_by_product(missing, min(want - len(take_stated), len(missing)), rng)
        # 한쪽이 모자라면 나머지를 다른 쪽에서 채운다
        got = take_stated + take_missing
        if len(got) < want:
            rest = [r for r in stated if r not in got]
            got += _spread_by_product(rest, want - len(got), rng)
        picked.extend(got)
        strata_meta.append(
            {
                "name": name,
                "population": len(pool),
                "sampled": len(got),
                "skinTypeStated": len(take_stated),
                "skinTypeMissing": len(take_missing),
                # 코퍼스 비율로 되돌릴 때 곱하는 값
                "weight": round(len(pool) / len(got), 4) if got else None,
            }
        )

    picked.sort(key=lambda r: r["reviewId"])
    meta = {
        "issue": "PER-175",
        "purpose": "Batch API 전수 실행 전 정답셋. 배치 결과를 이 표본 위에서 채점한다",
        "seed": SEED,
        "batchSize": BATCH_SIZE,
        "sampled": len(picked),
        "corpus": len(reviews),
        "source": {
            "path": str(REVIEWS_PATH.relative_to(ROOT)),
            "sha256": hashlib.sha256(REVIEWS_PATH.read_bytes()).hexdigest(),
        },
        "strata": strata_meta,
        "products": len({r["productId"] for r in picked}),
        "note": "층별 sampled/population 이 코퍼스와 다르다. 표본 비율을 코퍼스 비율로 읽지 말 것 — weight 로 되돌린다",
    }
    return picked, meta


def write(picked: list[dict], meta: dict) -> None:
    SAMPLE_PATH.parent.mkdir(parents=True, exist_ok=True)
    SAMPLE_PATH.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in picked))
    META_PATH.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n")

    BATCH_DIR.mkdir(parents=True, exist_ok=True)
    for old in BATCH_DIR.glob("*.json"):
        old.unlink()
    for i in range(0, len(picked), BATCH_SIZE):
        chunk = picked[i : i + BATCH_SIZE]
        payload = [
            {
                "reviewId": r["reviewId"],
                # 태거에게도 대조에도 같은 접기를 쓴다 (tag_contract 참고)
                "content": tagging_text(r["raw"]["content"]),
                "rating": r["raw"]["rating"],
            }
            for r in chunk
        ]
        path = BATCH_DIR / f"batch_{i // BATCH_SIZE:02d}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="재실행이 같은 표본을 주는지만 확인")
    args = ap.parse_args()

    picked, meta = sample()
    payload = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in picked)

    if args.check:
        if not SAMPLE_PATH.exists():
            raise SystemExit(f"FAIL: 표본이 없다 ({SAMPLE_PATH.relative_to(ROOT)})")
        if SAMPLE_PATH.read_text() != payload:
            raise SystemExit(
                "FAIL: 재실행 표본이 고정물과 다르다.\n"
                "  시드가 풀렸거나 입수 결과가 바뀌었다. 표본을 덮어쓰면 "
                f"{GOLD_PATH.relative_to(ROOT)} 의 정답셋이 무의미해진다 — 원인을 먼저 확인하라."
            )
        print(f"OK: 표본 재현 확인 ({meta['sampled']}건)")
        return

    write(picked, meta)
    for s in meta["strata"]:
        print(f"  {s['name']:10s} 모집단 {s['population']:6,d} → 표본 {s['sampled']:3d}  (가중치 {s['weight']})")
    print(f"[표본] {meta['sampled']}건 / {meta['products']}제품 → {SAMPLE_PATH.relative_to(ROOT)}")
    print(f"       태거 입력 {len(list(BATCH_DIR.glob('*.json')))}배치 → {BATCH_DIR.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
