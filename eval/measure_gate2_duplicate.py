"""
PER-183 근거 측정 — 게이트2 중복(본문 해시 · 작성자 1표).

게이트2가 실제로 무엇을 걸러내는지 같은 스냅샷에서 잰다. 확인하는 것은 세 가지다.

  1) **카운트 오염이 실재하는가** — 게이트를 안 걸면 "리뷰 N건이 말한다"가 얼마나
     부푸는가. 제품별 팽창률까지 본다 (PER-170 §1 을 `productId` 단위로 다시 잰 것)
  2) **두 축이 서로 대체하는가** — 본문 해시만 / 작성자만 / 둘 다 걸었을 때의
     제거량. 한쪽만 걸면 다른 쪽 오염이 그대로 남는다는 것이 PER-170 §2 의 주장이고,
     이 리포트가 그 주장을 게이트 구현으로 다시 확인한다
  3) **판정 순서가 결과를 바꾸는가** — 본문→작성자 순서는 사유 귀속을 정하려는
     선택이다. 통과 집합까지 달라진다면 선택이 아니라 버그이므로 `orderSensitivity`
     에서 두 순서를 모두 돌려 비교한다

충분성(PER-186)이 바로 쓸 수 있게 `productId×skinType` 셀의 N_min 통과 수를
dedup 전/후로 함께 낸다 — 작성자 1표의 비용이 거기서 나타난다.

사용:
  .venv/bin/python eval/measure_gate2_duplicate.py
  → eval/reports/gate2_duplicate_per183.json
  .venv/bin/python eval/measure_gate2_duplicate.py --check   # 커밋본과 일치 확인 (병합 게이트)
"""
import argparse
import collections
import hashlib
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

from catalog import load_catalog  # noqa: E402
from contracts import build_record  # noqa: E402
from ingest import assert_matches_ingest, load_aspect_counts  # noqa: E402
from gates import (  # noqa: E402
    REJECT_DUPLICATE_CONTENT,
    REJECT_LABELS,
    REJECT_SAME_AUTHOR,
    IdentityScope,
    RejectedRow,
    independent_reviews,
    run_duplicate_gate,
    run_identity_gate,
)
from policy import (  # noqa: E402
    RECENCY_CUTOFF_MONTH,
    SNAPSHOT_LATEST_MONTH,
    SUFFICIENCY_N_MIN,
)
from trust import rank_key, score_all  # noqa: E402

INPUT_PATH = ROOT / "data/input/reviews_50products.json"
# 전수 태그 정본. trustPrior 의 onTopic 입력이라 없으면 대표 선택이 입수와 갈린다 (PER-174)
TAGS_PATH = ROOT / "data/intermediate/v5_tags.jsonl"
REPORT_PATH = ROOT / "eval/reports/gate2_duplicate_per183.json"


def load_records(path: Path) -> tuple[list[dict], object]:
    """    입수(PER-173)와 같은 경로로 레코드를 만든다.

    `data/intermediate/v5_reviews.jsonl` 을 읽지 않고 `data/input` 에서 다시 만드는 이유는
    그 경로 자체(`build_record` · `score_all`)가 입수와 같은지 이 측정이 함께 확인하기
    때문이다. 대신 **`aspect_counts` 를 반드시 넘긴다** — 넘기지 않으면 `trustPrior` 의
    `onTopic` 이 `unavailable` 로 남아 입수 산출물과 파생층이 갈리고, 게이트2 의 작성자
    1표 대표가 달라진다 (PER-174 · PER-188 인계).
    """
    catalog = load_catalog()
    records = []
    for row in json.loads(path.read_text()):
        product_id = catalog.resolve_goods_no(row["goodsNo"], row["reviewDate"])
        records.append(build_record(row, product_id).to_dict())
    priors = score_all(records, aspect_counts=load_aspect_counts(TAGS_PATH))
    for record in records:
        record["derived"]["trustPrior"] = priors[record["reviewId"]]
    # 다시 만든 것이 입수 산출물과 **정말로** 같은가. 리포트가 재현된다고 기반이
    # 같은 것은 아니다 — 2026-09-15 에 갈린 채로 재현되고 있었다.
    assert_matches_ingest(records, who="게이트2 측정")
    return records, catalog

def by_product(records: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = collections.defaultdict(list)
    for record in records:
        grouped[record["productId"]].append(record)
    return dict(sorted(grouped.items()))


def pct(part: int, whole: int) -> float:
    return round(100 * part / whole, 2) if whole else 0.0


def contamination(records: list[dict]) -> dict:
    """게이트가 없을 때 카운트가 얼마나 부푸는가. 제품 단위 팽창률이 핵심 수치다."""
    pairs = {(r["derived"]["authorKey"], r["productId"]) for r in records}
    authors = collections.Counter(r["derived"]["authorKey"] for r in records)
    content = collections.Counter((r["productId"], r["derived"]["contentHash"]) for r in records)
    corpus_content = collections.Counter(r["derived"]["contentHash"] for r in records)

    inflation = []
    for pid, rows in by_product(records).items():
        unique = len({r["derived"]["authorKey"] for r in rows})
        inflation.append(round(len(rows) / unique, 4))
    inflation.sort()
    return {
        "reviews": len(records),
        "uniqueAuthors": len(authors),
        "authorsWithMultipleReviews": sum(1 for v in authors.values() if v > 1),
        "reviewsByMultiReviewAuthors": sum(v for v in authors.values() if v > 1),
        "maxReviewsPerAuthor": max(authors.values()),
        "authorProductPairs": len(pairs),
        "excessVotes": len(records) - len(pairs),
        "excessVotesPct": pct(len(records) - len(pairs), len(records)),
        # 같은 본문이 두 제품에 나오면 제품별로 따로 센다 — 합치는 단위가 productId 다
        "identicalContentGroupsInProduct": sum(1 for v in content.values() if v > 1),
        "identicalContentGroupsCorpus": sum(1 for v in corpus_content.values() if v > 1),
        "excessByContentHash": sum(v - 1 for v in content.values() if v > 1),
        "inflationPerProduct": {
            "min": inflation[0],
            "median": round(statistics.median(inflation), 4),
            "p95": inflation[min(len(inflation) - 1, int(len(inflation) * 0.95))],
            "max": inflation[-1],
        },
    }


def gate_profile(grouped: dict[str, list[dict]], catalog) -> dict:
    """제품별로 게이트2를 걸었을 때의 통과·탈락. 통과 수가 `independentReviews` 다."""
    reasons: collections.Counter = collections.Counter()
    rows = {}
    passed_total = 0
    for pid, records in grouped.items():
        result = run_duplicate_gate(records)
        counted = independent_reviews(result.passed)  # 통과분이 정말 서로 독립인지 확인한다
        passed_total += counted
        for reason, n in result.rejected_by_reason().items():
            reasons[reason] += n
        product = catalog.product(pid)
        rows[pid] = {
            "displayName": product.display_name,
            "reviews": len(records),
            "independentReviews": counted,
            "inflation": round(len(records) / counted, 4) if counted else 0.0,
            "rejected": result.rejected_by_reason(),
        }
    reviews = sum(len(v) for v in grouped.values())
    return {
        "reviews": reviews,
        "independentReviews": passed_total,
        "rejected": {
            reason: {"reviews": n, "pct": pct(n, reviews), "label": REJECT_LABELS[reason]}
            for reason, n in sorted(reasons.items())
        },
        "byProduct": rows,
    }


def axis_coverage(grouped: dict[str, list[dict]]) -> dict:
    """축을 하나만 걸면 무엇이 남는가. 두 축이 서로 대체하지 않는다는 실측이다."""
    reviews = sum(len(v) for v in grouped.values())
    content_only = author_only = both = in_gate = 0
    for pid, records in grouped.items():
        content_only += len(records) - len({r["derived"]["contentHash"] for r in records})
        author_only += len(records) - len({r["derived"]["authorKey"] for r in records})
        result = run_duplicate_gate(records)
        both += len(result.rejected)
        in_gate += result.rejected_by_reason().get(REJECT_DUPLICATE_CONTENT, 0)
    return {
        "note": "제품 안에서만 센다 — 합치는 단위가 productId 다",
        "reviews": reviews,
        "removedByContentHashOnly": content_only,
        "removedByAuthorOnly": author_only,
        "removedByBoth": both,
        # 게이트 안에서 '중복' 으로 찍힌 수는 해시 축 단독 제거량보다 적다. 중복 판정은
        # **남은 리뷰**를 상대로 하기 때문이다 — 같은 본문의 짝이 이미 동일작성자로
        # 빠졌으면 그 본문은 한 번만 세어지고 있으므로 다시 뺄 이유가 없다
        "rejectedAsDuplicateContentInGate": in_gate,
        # 작성자 축을 포기하고 해시만 걸면 남는 오염. PER-170 §2 의 '잔존 19.7%' 에 대응한다
        "residualIfContentHashOnly": both - content_only,
        "residualIfContentHashOnlyPct": pct(both - content_only, reviews),
        # 해시를 포기하고 작성자만 걸면 남는 오염 — 서로 다른 사람의 템플릿·복붙.
        # 순증분은 작지만 방향이 치명적이다(서로 다른 사람이 같은 글을 올린 것을
        # 독립 근거 2건으로 센다). 비용은 0 에 가깝고 결정론적이라 유지한다
        "residualIfAuthorOnly": both - author_only,
        "contentHashCoverageOfBothPct": pct(content_only, both),
    }


def _author_first(records: list[dict]) -> list[RejectedRow]:
    """판정 순서를 뒤집은 구현(작성자 → 본문). 비교용이라 파이프라인에는 두지 않는다."""
    kept_author: dict[tuple, int] = {}
    kept_content: dict[tuple, int] = {}
    rejected: list[RejectedRow] = []
    passed: list[dict] = []
    for record in sorted(records, key=rank_key):
        pid, rid = record["productId"], record["reviewId"]
        akey = (pid, record["derived"]["authorKey"])
        ckey = (pid, record["derived"]["contentHash"])
        if akey in kept_author:
            rejected.append(RejectedRow(rid, "duplicate", REJECT_SAME_AUTHOR))
        elif ckey in kept_content:
            rejected.append(RejectedRow(rid, "duplicate", REJECT_DUPLICATE_CONTENT))
        else:
            kept_author[akey], kept_content[ckey] = rid, rid
            passed.append(record)
    return passed, rejected


def order_sensitivity(grouped: dict[str, list[dict]]) -> dict:
    """순서를 바꾸면 무엇이 달라지는가. 통과 집합이 달라지면 선택이 아니라 버그다."""
    same_passed = True
    counts = {"contentFirst": collections.Counter(), "authorFirst": collections.Counter()}
    moved = 0
    for records in grouped.values():
        result = run_duplicate_gate(records)
        alt_passed, alt_rejected = _author_first(records)
        if [r["reviewId"] for r in result.passed] != [r["reviewId"] for r in alt_passed]:
            same_passed = False
        for row in result.rejected:
            counts["contentFirst"][row.reason] += 1
        for row in alt_rejected:
            counts["authorFirst"][row.reason] += 1
        by_id = {row.review_id: row.reason for row in result.rejected}
        moved += sum(1 for row in alt_rejected if by_id.get(row.review_id) != row.reason)
    return {
        "samePassedSet": same_passed,
        "reasonCounts": {k: dict(sorted(v.items())) for k, v in counts.items()},
        "reviewsWithDifferentReason": moved,
        "note": (
            "통과 집합은 순서와 무관하고 사유 귀속만 달라진다. 좁은 판정(본문 완전일치)을 "
            "먼저 붙이는 쪽을 택했다 — '왜 빠졌나'가 읽기 쉽다"
        ),
    }


def gate_order(grouped: dict[str, list[dict]], catalog) -> dict:
    """게이트1 → 게이트2 순서가 규격인 이유를 수치로 남긴다.

    순서를 뒤집으면 컷될 리뷰가 1표의 대표로 남고, 그 대표가 게이트1에서 탈락하면서
    **같은 작성자의 살아 있는 리뷰까지 함께 사라진다.** 잃는 근거의 수가 그 비용이다.
    """
    correct = flipped = 0
    lost_products = 0
    for pid, records in grouped.items():
        first = run_identity_gate(records, IdentityScope(pid), catalog)
        a = len(run_duplicate_gate(first.passed).passed)
        b = len(run_identity_gate(run_duplicate_gate(records).passed,
                                  IdentityScope(pid), catalog).passed)
        correct += a
        flipped += b
        if b < a:
            lost_products += 1
    return {
        "independentReviewsGate1Then2": correct,
        "independentReviewsGate2Then1": flipped,
        "evidenceLostIfFlipped": correct - flipped,
        "productsAffected": lost_products,
        "note": (
            "뒤집으면 컷된 리뷰가 1표를 가져가고, 그 1표가 게이트1에서 탈락하면서 "
            "살아 있던 같은 작성자의 리뷰까지 함께 사라진다"
        ),
    }


def sufficiency_impact(records: list[dict], grouped: dict[str, list[dict]], catalog) -> dict:
    """작성자 1표의 비용 — `productId×skinType` 셀이 N_min 을 몇 개나 못 넘게 되는가."""

    def cells(rows: list[dict]) -> collections.Counter:
        return collections.Counter(
            (r["productId"], r["condition"]["skinType"]["segment"]) for r in rows)

    raw = cells(records)
    after_gate2 = cells([r for rows in grouped.values() for r in run_duplicate_gate(rows).passed])

    # 게이트1(리뉴얼·리센시 컷, 옵션 범위 없음) → 게이트2 순서로 걸었을 때
    chained: list[dict] = []
    for pid, rows in grouped.items():
        first = run_identity_gate(rows, IdentityScope(pid), catalog)
        chained.extend(run_duplicate_gate(first.passed).passed)
    after_chain = cells(chained)

    def at_least(counter: collections.Counter) -> int:
        return sum(1 for v in counter.values() if v >= SUFFICIENCY_N_MIN)

    return {
        "nMin": SUFFICIENCY_N_MIN,
        "cells": len(raw),
        "atLeastNminRaw": at_least(raw),
        "atLeastNminAfterGate2": at_least(after_gate2),
        "atLeastNminAfterGate1And2": at_least(after_chain),
        "reviewsAfterGate1And2": len(chained),
        "note": (
            "셀 크기는 게이트2 통과 수다. 줄어든 셀은 손실이 아니라 원래 없던 근거다. "
            "입수 프로파일(v5_ingest_profile.json)의 297·284 는 작성자 축만 건 수치라 "
            "본문 해시 축이 더해진 여기서 각각 1 셀씩 낮다"
        ),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, default=INPUT_PATH)
    ap.add_argument("--check", action="store_true",
                    help="재실행 결과가 커밋된 리포트와 같은지만 확인 (§5-2 재현성)")
    args = ap.parse_args()

    records, catalog = load_records(args.input)
    grouped = by_product(records)

    report = {
        "issue": "PER-183",
        "source": {
            "path": str(args.input.relative_to(ROOT)),
            "sha256": hashlib.sha256(args.input.read_bytes()).hexdigest(),
            "reviews": len(records),
            "products": len(grouped),
        },
        "policy": {
            "authorKey": "NFC(userName) 원문 (PER-170)",
            "mergeUnit": "(authorKey, productId)",
            "representative": "신뢰도 점수 최고 → reviewDate 최신 → reviewId 최소",
            "snapshotLatestMonth": SNAPSHOT_LATEST_MONTH,
            "recencyCutoffMonth": RECENCY_CUTOFF_MONTH,
            "sufficiencyNMin": SUFFICIENCY_N_MIN,
        },
        "contamination": contamination(records),
        "gate": gate_profile(grouped, catalog),
        "axisCoverage": axis_coverage(grouped),
        "orderSensitivity": order_sensitivity(grouped),
        "gateOrder": gate_order(grouped, catalog),
        "sufficiencyImpact": sufficiency_impact(records, grouped, catalog),
    }
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.check:
        if not REPORT_PATH.exists():
            raise SystemExit(f"FAIL: 리포트가 없다 ({REPORT_PATH.relative_to(ROOT)})")
        if REPORT_PATH.read_text() != payload:
            raise SystemExit(
                "FAIL: 게이트2 리포트가 재현되지 않는다 "
                f"({REPORT_PATH.relative_to(ROOT)})\n"
                "  → 중복 판정 규칙이나 신뢰도 가중치가 바뀌었다면 리포트를 다시 생성해 "
                "함께 커밋한다"
            )
        print(f"OK: 게이트2 리포트 재현 일치 ({REPORT_PATH.relative_to(ROOT)})")
        return

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(payload)

    c, g = report["contamination"], report["gate"]
    print(f"[게이트2] 리뷰 {c['reviews']}건 → 독립 근거 {g['independentReviews']}건")
    print(f"  오염: 초과 표 {c['excessVotes']}건 ({c['excessVotesPct']}%) · "
          f"제품별 팽창률 중위 {c['inflationPerProduct']['median']}배 "
          f"(최대 {c['inflationPerProduct']['max']}배)")
    for reason, v in g["rejected"].items():
        print(f"  탈락 {v['label']:6s} {v['reviews']}건 ({v['pct']}%)")
    a = report["axisCoverage"]
    print(f"  축: 해시만 걸면 {a['residualIfContentHashOnly']}건 오염 잔존 "
          f"({a['residualIfContentHashOnlyPct']}%) · 해시가 커버하는 몫 "
          f"{a['contentHashCoverageOfBothPct']}%")
    o = report["orderSensitivity"]
    print(f"  순서: 통과 집합 동일 {o['samePassedSet']} · 사유가 달라지는 리뷰 "
          f"{o['reviewsWithDifferentReason']}건")
    go = report["gateOrder"]
    print(f"  게이트 순서: 1→2 {go['independentReviewsGate1Then2']}건 vs 2→1 "
          f"{go['independentReviewsGate2Then1']}건 (뒤집으면 근거 "
          f"{go['evidenceLostIfFlipped']}건 손실 · 제품 {go['productsAffected']}개)")
    s = report["sufficiencyImpact"]
    print(f"  N>={s['nMin']} 셀 {s['atLeastNminRaw']} → 게이트2 후 {s['atLeastNminAfterGate2']} "
          f"→ 게이트1+2 후 {s['atLeastNminAfterGate1And2']}")
    print(f"→ {REPORT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
