"""
v5 Step 1 — 입수 태깅 (PER-173 / PRD §3-1).

수집 스냅샷을 v5 입력 계약(원문/조건/파생 3층)으로 옮긴다. LLM 없음, 순수 함수.
같은 입력 → 같은 출력이어야 하므로 시각을 기록하지 않고 입력 sha256 만 남긴다 (§5-2).

  입력  data/input/reviews_50products.json      (25,000건 스냅샷)
        data/input/product_catalog.json         (제품 동일성 — PER-171)
  출력  data/intermediate/v5_reviews.jsonl      (레코드, 재생성 가능)
        data/intermediate/v5_reviews_meta.json  (재현 메타)
        eval/reports/v5_ingest_profile.json     (커밋되는 프로파일 — 조건 기재율·중복 규모)

멈추는 조건 (조용한 폴백 금지)
  - 카탈로그에 없는 `goodsNo`      → UnknownGoodsNoError
  - 필수 필드 누락                 → ValueError
  - `reviewId` 중복                → SystemExit

사용:
  .venv/bin/python pipeline/ingest.py
  .venv/bin/python pipeline/ingest.py --check   # 재실행 결과가 기존 출력과 같은지
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from catalog import load_catalog  # noqa: E402
from contracts import (  # noqa: E402
    CONDITION_AXES,
    DROPPED_FIELDS,
    MISSING_SEGMENT,
    SCHEMA_VERSION,
    build_record,
)

ROOT = Path(__file__).parents[1]
INPUT_PATH = ROOT / "data/input/reviews_50products.json"
OUTPUT_PATH = ROOT / "data/intermediate/v5_reviews.jsonl"
META_PATH = ROOT / "data/intermediate/v5_reviews_meta.json"
PROFILE_PATH = ROOT / "eval/reports/v5_ingest_profile.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ingest(input_path: Path = INPUT_PATH) -> tuple[list[dict], dict, dict]:
    catalog = load_catalog()
    rows = json.loads(input_path.read_text())

    records: list[dict] = []
    seen_ids: set[int] = set()
    for row in rows:
        if row["reviewId"] in seen_ids:
            raise SystemExit(f"reviewId 중복: {row['reviewId']}")
        seen_ids.add(row["reviewId"])
        product_id = catalog.resolve_goods_no(row["goodsNo"])
        records.append(build_record(row, product_id).to_dict())

    meta = {
        "schemaVersion": SCHEMA_VERSION,
        "issue": "PER-173",
        "records": len(records),
        "source": {"path": str(input_path.relative_to(ROOT)), "sha256": sha256(input_path)},
        "catalog": {
            "path": "data/input/product_catalog.json",
            "sha256": sha256(ROOT / "data/input/product_catalog.json"),
            "products": len(catalog),
        },
        "conditionAxes": list(CONDITION_AXES),
        "droppedFields": DROPPED_FIELDS,
    }
    return records, meta, profile(records)


def profile(records: list[dict]) -> dict:
    """조건 기재율과 중복 규모 — 하위 이슈(게이트·충분성)가 바로 쓰는 수치."""
    n = len(records)

    def pct(part: int) -> float:
        return round(100 * part / n, 2) if n else 0.0

    stated = {
        axis: sum(1 for r in records if r["condition"][axis]["stated"]) for axis in CONDITION_AXES
    }
    authors = {(r["derived"]["authorKey"], r["productId"]) for r in records}
    content_dupes = collections.Counter(r["derived"]["contentHash"] for r in records)
    products = collections.Counter(r["productId"] for r in records)
    cells = collections.Counter(
        (r["productId"], r["condition"]["skinType"]["segment"]) for r in records
    )
    deduped_cells: dict[tuple[str, str], set] = collections.defaultdict(set)
    for r in records:
        key = (r["productId"], r["condition"]["skinType"]["segment"])
        deduped_cells[key].add(r["derived"]["authorKey"])

    return {
        "issue": "PER-173",
        "records": n,
        "products": len(products),
        "reviewsPerProduct": {
            "min": min(products.values()),
            "median": sorted(products.values())[len(products) // 2],
            "max": max(products.values()),
        },
        "conditionStatedPct": {axis: pct(stated[axis]) for axis in CONDITION_AXES},
        "missingSegmentLabel": MISSING_SEGMENT,
        "duplication": {
            "uniqueAuthorProductPairs": len(authors),
            "excessVotes": n - len(authors),
            "excessVotesPct": pct(n - len(authors)),
            "identicalContentGroups": sum(1 for v in content_dupes.values() if v > 1),
            "excessByContentHash": sum(v - 1 for v in content_dupes.values() if v > 1),
        },
        "skinTypeCells": {
            "cells": len(cells),
            "atLeast8Raw": sum(1 for v in cells.values() if v >= 8),
            "atLeast8AfterAuthorDedup": sum(1 for v in deduped_cells.values() if len(v) >= 8),
        },
        "skinTroubleSegments": len(
            {s for r in records for s in r["condition"]["skinTrouble"]["segments"]}
        ),
    }


def write(records: list[dict], meta: dict, prof: dict) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records))
    META_PATH.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
    PROFILE_PATH.write_text(json.dumps(prof, ensure_ascii=False, indent=2) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, default=INPUT_PATH)
    ap.add_argument("--check", action="store_true", help="재실행 결과가 기존 출력과 같은지만 확인")
    args = ap.parse_args()

    records, meta, prof = ingest(args.input)

    if args.check:
        payload = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
        if not OUTPUT_PATH.exists():
            print(f"FAIL: 출력이 없다 ({OUTPUT_PATH.relative_to(ROOT)})", file=sys.stderr)
            sys.exit(1)
        if OUTPUT_PATH.read_text() != payload:
            print("FAIL: 재실행 결과가 기존 출력과 다르다", file=sys.stderr)
            sys.exit(1)
        print(f"OK: {len(records)}건 재현 일치")
        return

    write(records, meta, prof)
    print(f"[입수] {prof['records']}건 → {prof['products']}제품  ({OUTPUT_PATH.relative_to(ROOT)})")
    for axis, v in prof["conditionStatedPct"].items():
        print(f"  조건 기재율 {axis:12s} {v}%")
    d = prof["duplication"]
    print(f"  중복: 고유 (작성자,제품) {d['uniqueAuthorProductPairs']} / 초과 표 {d['excessVotes']} ({d['excessVotesPct']}%)")
    c = prof["skinTypeCells"]
    print(f"  productId×skinType 셀 {c['cells']} — N>=8 {c['atLeast8Raw']} → dedup 후 {c['atLeast8AfterAuthorDedup']}")
    print(f"→ {META_PATH.relative_to(ROOT)}, {PROFILE_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
