"""
v5 Step 1 — 입수 태깅 (PER-173 / PRD §3-1).

수집 스냅샷을 v5 입력 계약(원문/조건/파생 3층)으로 옮긴다. LLM 없음, 순수 함수.
같은 입력 → 같은 출력이어야 하므로 시각을 기록하지 않고 입력 sha256 만 남긴다 (§5-2).

  입력  data/input/reviews_50products.json      (25,000건 스냅샷)
        data/input/product_catalog.json         (제품 동일성 — PER-171)
  출력  data/intermediate/v5_reviews.jsonl      (레코드, 재생성 가능)
        data/intermediate/v5_reviews_meta.json  (재현 메타)
        eval/reports/v5_ingest_profile.json     (커밋되는 프로파일 — 조건 기재율·중복 규모)

멈추는 조건 (조용한 폴백 금지 — 계약은 `docs/INPUT_CONTRACT.md`)
  - 카탈로그에 없는 `goodsNo`      → UnknownGoodsNoError (PER-171)
  - 스냅샷 최신 월이 정책보다 새로움 → PolicyError (PER-172)
  - 필드 집합이 계약과 다름         → ContractError (PER-176)
  - 필수 필드 결측·공백·타입·범위   → ContractError
  - 날짜를 월로 읽을 수 없음        → ContractError
  - 코드북 도메인 밖 조건 코드      → ContractError (PER-169)
  - `reviewId` 중복                → ContractError
  - 가중치 설정 위반 (PER-174)     → TrustConfigError

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

from catalog import CatalogError, load_catalog  # noqa: E402
from codebook import load_codebook  # noqa: E402
from policy import (  # noqa: E402
    DEFAULT_SNAPSHOT,
    PolicyError,
    SnapshotPolicy,
    assert_snapshot_current,
)
from trust import ScoringContext, TrustWeights, trust_prior  # noqa: E402
from contracts import (  # noqa: E402
    CONDITION_AXES,
    DROPPED_FIELDS,
    MISSING_SEGMENT,
    RATING_RANGE,
    REQUIRED_FIELDS,
    SCHEMA_VERSION,
    ContractError,
    assert_row_schema,
    build_record,
)

ROOT = Path(__file__).parents[1]
INPUT_PATH = ROOT / "data/input/reviews_50products.json"
OUTPUT_PATH = ROOT / "data/intermediate/v5_reviews.jsonl"
META_PATH = ROOT / "data/intermediate/v5_reviews_meta.json"
PROFILE_PATH = ROOT / "eval/reports/v5_ingest_profile.json"
# 전수 태깅 정본 (PER-175 `tag` 단계 산출). 있으면 trustPrior 의 `onTopic` 이 살아난다.
TAGS_PATH = ROOT / "data/intermediate/v5_tags.jsonl"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_aspect_counts(tags_path: Path) -> dict[int, int]:
    """reviewId → aspect 태그 수. `trustPrior` 의 `onTopic` 입력이다 (PER-174 인계)."""
    counts: dict[int, int] = collections.Counter()
    for line in tags_path.read_text().splitlines():
        if line.strip():
            counts[json.loads(line)["reviewId"]] += 1
    return dict(counts)


def ingest(
    input_path: Path = INPUT_PATH,
    tags_path: Path | None = None,
    catalog_path: Path | None = None,
    snapshot: SnapshotPolicy = DEFAULT_SNAPSHOT,
) -> tuple[list[dict], dict, dict]:
    """`catalog_path` · `snapshot` 은 런 격리용이다 (PER-194).

    기본값은 정본이고, 기본값으로 부르면 지금까지와 완전히 같게 동작한다 —
    `test_workspace.py` 가 그걸 고정한다.
    """
    catalog = load_catalog(catalog_path) if catalog_path else load_catalog()
    rows = json.loads(input_path.read_text())
    # 새 수집분이 들어왔는데 리센시 컷 기준을 안 고치면 24개월 윈도우가 조용히
    # 과거로 밀린다 (PER-172). 그 전에 멈춘다.
    snapshot.assert_current(max(r["reviewDate"] for r in rows))

    records: list[dict] = []
    seen_ids: set[int] = set()
    for row in rows:
        # 필드 집합 → 값 순서로 검증한다. 필드가 통째로 바뀐 스냅샷에서 값 오류 25,000줄이
        # 쏟아지는 것보다, "크롤러가 바뀌었다" 한 줄이 낫다.
        assert_row_schema(row)
        if row["reviewId"] in seen_ids:
            raise ContractError(
                f"reviewId 중복: {row['reviewId']} — 식별자가 겹치면 중복 게이트(PER-183)의 "
                "판정 단위가 무너진다"
            )
        seen_ids.add(row["reviewId"])
        # 날짜를 함께 넘긴다 — 리뉴얼 세대가 나뉜 제품은 (goodsNo, reviewDate) 로만
        # 세대가 정해진다 (PER-172). 지금은 전 제품 단일 세대라 결과가 같지만,
        # 세대가 생기는 순간 여기서 조용히 틀리지 않게 하려는 것이다.
        product_id = catalog.resolve_goods_no(row["goodsNo"], row["reviewDate"])
        records.append(build_record(row, product_id).to_dict())

    # 신뢰도 사전 점수 (PER-174). 중복 본문 그룹 크기와 제품별 좋아요 백분위는 리뷰
    # 1건만 봐서는 알 수 없어 두 번째 패스로 매긴다. **필터가 아니라 가중치다** —
    # 점수가 낮아도 여기서 버리지 않는다. 버리는 판단은 게이트(PER-182~188)에서만 한다.
    # `onTopic` 은 aspect 태깅 결과가 있어야 판정된다 (PER-175). 없으면 0점으로 깔지
    # 않고 `unavailable` 에 남긴다 — 신호가 없는 것과 신호가 0인 것은 다르다.
    aspect_counts = load_aspect_counts(tags_path) if tags_path else None
    if aspect_counts is not None:
        unknown = set(aspect_counts) - {r["reviewId"] for r in records}
        if unknown:
            raise ContractError(
                f"태그 파일에 입력에 없는 reviewId 가 {len(unknown)}건 있다 "
                f"(예: {sorted(unknown)[:3]}) — 다른 스냅샷으로 만든 태그다. "
                "pipeline/run_v5.py --steps tag 이 정본을 다시 고른다"
            )

    weights = TrustWeights.load()
    context = ScoringContext.from_records(records, aspect_counts)
    for record in records:
        record["derived"]["trustPrior"] = trust_prior(record, context, weights)

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
        "trustPrior": weights.as_dict(),
        # 어느 태그로 재점수했는지. 없으면 `onTopic` 이 unavailable 로 남는다는 사실 자체를 남긴다.
        "tags": (
            {
                "path": str(tags_path.relative_to(ROOT)),
                "sha256": sha256(tags_path),
                "taggedReviews": len(aspect_counts),
            }
            if tags_path
            else None
        ),
        # 이 실행이 실제로 강제한 계약. 어떤 규칙 아래 나온 산출물인지 산출물만 보고 알 수 있게 한다.
        "contract": {
            "doc": "docs/INPUT_CONTRACT.md",
            "issue": "PER-176",
            "codebook": {
                "path": "data/input/skin_codebook.json",
                "sha256": sha256(ROOT / "data/input/skin_codebook.json"),
                "codes": len(load_codebook()),
            },
            "requiredFields": list(REQUIRED_FIELDS),
            "ratingRange": list(RATING_RANGE),
            "enforced": [
                "필드 집합이 계약과 정확히 같아야 한다 (모르는 필드도, 사라진 필드도 에러)",
                "필수 필드는 결측·공백·타입·범위 위반이면 에러",
                "reviewDate 는 월로 파싱돼야 한다 (리센시 컷의 입력)",
                "조건 코드는 코드북 도메인 안이어야 한다 (라벨·축 혼용 포함)",
                "reviewId 는 스냅샷 안에서 유일해야 한다",
                "goodsNo 는 카탈로그에 등록돼 있어야 한다",
            ],
        },
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
        # 사전 점수 분포 (PER-174). 점수가 한쪽으로 뭉치면 정렬 신호로서 무의미해지므로
        # 분포를 커밋된 프로파일에 남긴다.
        "trustPrior": trust_prior_profile(records),
    }


def trust_prior_profile(records: list[dict]) -> dict:
    scores = sorted(r["derived"]["trustPrior"]["score"] for r in records)
    n = len(scores)
    if not n:
        return {}
    q = lambda p: scores[min(n - 1, int(n * p))]  # noqa: E731
    unavailable = sorted({s for r in records for s in r["derived"]["trustPrior"].get("unavailable", [])})
    return {
        "issue": "PER-174",
        "min": scores[0],
        "p25": q(0.25),
        "median": q(0.5),
        "p75": q(0.75),
        "p95": q(0.95),
        "max": scores[-1],
        "distinctScores": len(set(scores)),
        "unavailableSignals": unavailable,
    }


class BaseParityError(ContractError):
    """측정이 다시 만든 레코드가 입수 산출물과 다르다. 조용한 폴백 금지."""


def assert_matches_ingest(
    records: list[dict], output_path: Path = OUTPUT_PATH, who: str = "이 측정"
) -> None:
    """다시 만든 레코드가 **입수 산출물과 같은가.** 다르면 에러다.

    measure 스크립트들은 `data/input` 에서 레코드를 다시 만든다 — 그 경로
    (`build_record` · `score_all`)가 입수와 같은지 측정이 함께 확인하기 위해서다.
    그런데 **같다는 것을 아무도 확인하지 않고 있었다.** 2026-09-15 에 실제로 갈렸다:
    `aspect_counts` 를 안 넘겨 `trustPrior` 의 `onTopic` 이 비었고, 게이트2 의 작성자
    1표 대표가 파이프라인과 달라져 후보 수가 7,660 대 7,689 로 벌어졌다. 리포트는
    재현됐다 — `--check` 는 "리포트가 재생성되는가"만 보기 때문이다.

    이 함수가 그 구멍을 막는다. 리포트 재현이 아니라 **기반 자체**를 대조한다.
    첫 불일치의 경로와 양쪽 값을 그대로 보여준다 — "다르다" 만으로는 못 고친다.
    """
    if not output_path.exists():
        raise BaseParityError(
            f"{who}의 기반을 입수 산출물과 대조할 수 없다 — {output_path} 가 없다.\n"
            "  → python3 pipeline/ingest.py 를 먼저 돌려라"
        )
    committed = [json.loads(line) for line in output_path.read_text().splitlines() if line.strip()]
    if len(committed) != len(records):
        raise BaseParityError(
            f"{who}의 레코드 수가 입수 산출물과 다르다: {len(records)} vs {len(committed)}"
        )

    def first_diff(a, b, path: str = "") -> str | None:
        if isinstance(a, dict) and isinstance(b, dict):
            for key in sorted(set(a) | set(b)):
                if key not in a:
                    return f"{path}.{key}: 입수에만 있다 ({b[key]!r})"
                if key not in b:
                    return f"{path}.{key}: 측정에만 있다 ({a[key]!r})"
                found = first_diff(a[key], b[key], f"{path}.{key}")
                if found:
                    return found
            return None
        if isinstance(a, list) and isinstance(b, list):
            if len(a) != len(b):
                return f"{path}: 길이 {len(a)} vs {len(b)}"
            for i, (x, y) in enumerate(zip(a, b)):
                found = first_diff(x, y, f"{path}[{i}]")
                if found:
                    return found
            return None
        return None if a == b else f"{path}: 측정 {a!r} vs 입수 {b!r}"

    for mine, theirs in zip(records, committed):
        if mine == theirs:
            continue
        where = first_diff(mine, theirs) or "(구조는 같은데 값이 다르다)"
        raise BaseParityError(
            f"{who}의 기반이 입수 산출물과 다르다 — reviewId {mine.get('reviewId')}\n"
            f"  {where}\n"
            "  → 측정이 파이프라인과 다른 것을 재고 있다. 재현되는 리포트가 맞다는 뜻이\n"
            "     아니다 (2026-09-15 PER-183·186·188 이 그렇게 갈렸다). 레코드를 다시\n"
            "     만드는 경로를 ingest 와 맞춰라 — score_all 에 aspect_counts 를\n"
            "     넘겼는지부터 본다"
        )


def write(
    records: list[dict],
    meta: dict,
    prof: dict,
    output_path: Path = OUTPUT_PATH,
    meta_path: Path = META_PATH,
    profile_path: Path | None = PROFILE_PATH,
) -> None:
    """`profile_path=None` 이면 프로파일을 쓰지 않는다 — 런은 eval/reports 에 쓰지 않는다."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records))
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
    if profile_path is not None:
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        profile_path.write_text(json.dumps(prof, ensure_ascii=False, indent=2) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, default=INPUT_PATH)
    ap.add_argument("--check", action="store_true", help="재실행 결과가 기존 출력과 같은지만 확인")
    ap.add_argument(
        "--tags",
        type=Path,
        help=f"전수 태그 jsonl. 생략하면 {TAGS_PATH.relative_to(ROOT)} 가 있을 때만 쓴다",
    )
    ap.add_argument(
        "--no-tags",
        action="store_true",
        help="태그가 있어도 무시하고 입수 시점 점수만 낸다 (onTopic 은 unavailable)",
    )
    args = ap.parse_args()

    tags_path = None if args.no_tags else (args.tags or (TAGS_PATH if TAGS_PATH.exists() else None))
    if tags_path is not None and not tags_path.exists():
        raise SystemExit(f"[입수 중단] 태그 파일이 없다: {tags_path}")

    try:
        records, meta, prof = ingest(args.input, tags_path)
    except (ContractError, CatalogError, PolicyError) as e:
        # 계약 위반은 버그가 아니라 판정이다. 스택트레이스 대신 무엇을 정해야 하는지 낸다.
        raise SystemExit(f"[입수 중단] 입력이 계약을 위반했다 (docs/INPUT_CONTRACT.md)\n  {e}")

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
