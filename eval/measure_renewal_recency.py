"""
PER-172 근거 측정 — 리뉴얼 취급과 리센시 컷.

PRD §3-3 권장안은 "리뉴얼은 별개 제품"이다. 그 권장안을 채택할지 판단하려면
먼저 **리뉴얼 시점을 무엇으로 관측하는가**를 정해야 한다. 후보는 세 가지다.

  1) `goodsNo` 교체          SKU 코드가 바뀌면 리뉴얼로 본다
  2) `productName` 표기      상품명의 "리뉴얼"/"NEW" 문구
  3) 리뷰 본문 언급          "리뉴얼 전에는…" 류의 서술

이 스크립트는 세 후보의 신호 대 잡음을 같은 스냅샷에서 실측하고, 별도로
리센시 컷의 비용(잔존 리뷰 · 충분성 게이트를 통과하는 셀 수)을 시뮬레이션한다.

측정 항목은 결정 문서 docs/DECISION_PER172_RENEWAL_AND_RECENCY.md 의 표와 1:1 대응한다.

사용:
  .venv/bin/python eval/measure_renewal_recency.py
  → eval/reports/renewal_recency_per172.json

주의 — 본문 언급은 **약한 신호다.** "리뉴얼 궁금하네요"(구매 전 기대), 타 제품의
리뉴얼 언급이 섞여 있어 그대로 컷 기준으로 쓸 수 없다. 여기서는 규모와 분포만
재고, 컷 기준으로 승격하지 않는다는 판단의 근거로만 쓴다.
"""
import argparse
import collections
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

from catalog import load_catalog  # noqa: E402
from contracts import MISSING_SEGMENT, author_key  # noqa: E402
from policy import (  # noqa: E402
    RECENCY_CUTOFF_MONTH,
    RECENCY_WINDOW_MONTHS,
    SNAPSHOT_LATEST_MONTH,
    SUFFICIENCY_N_MIN,
    month_of,
)

DEFAULT_INPUT = ROOT / "data/input/reviews_50products.json"
DEFAULT_OUTPUT = ROOT / "eval/reports/renewal_recency_per172.json"

# 본문 리뉴얼 언급 탐지. 재현 가능하도록 패턴을 리포트에 함께 싣는다.
MENTION_PATTERN = r"리뉴얼|리뉴월|신형|구형"
# 상품명 표기 탐지. "NEW"는 신컬러 표기로도 쓰이므로 분리해서 센다.
NAME_RENEWAL_PATTERN = r"리뉴얼"
NAME_NEW_PATTERN = r"\bNEW\b"

# 시뮬레이션할 컷오프(월). 채택값은 policy.RECENCY_CUTOFF_MONTH 다.
SIMULATED_CUTOFFS = (
    None,
    "2022-01",
    "2023-01",
    "2023-09",
    "2024-01",
    "2024-09",
    "2025-01",
    "2025-09",
)


def pct(part: int, whole: int) -> float:
    return round(100 * part / whole, 2) if whole else 0.0


def cells_passing(rows, resolve, n_min: int = SUFFICIENCY_N_MIN) -> tuple[int, int]:
    """`productId×skinType` 셀 수와 그중 고유 작성자 N 이상인 셀 수.

    정의는 `pipeline/ingest.py` 의 `skinTypeCells.atLeast8AfterAuthorDedup` 과 같다
    (셀 안에서 작성자를 dedup 한다). 컷 전후 수치를 커밋된 프로파일과 비교할 수 있어야
    하므로 정의를 바꾸지 않는다.
    """
    cells = collections.defaultdict(set)
    for r in rows:
        segment = (r.get("skinType") or "").strip() or MISSING_SEGMENT
        cells[(resolve(r["goodsNo"]), segment)].add(author_key(r["userName"]))
    return len(cells), sum(1 for authors in cells.values() if len(authors) >= n_min)


def measure(reviews: list[dict]) -> dict:
    catalog = load_catalog()
    resolve = catalog.resolve_goods_no
    mention = re.compile(MENTION_PATTERN, re.I)
    n = len(reviews)

    dates = sorted(r["reviewDate"] for r in reviews)
    snapshot = {
        "reviews": n,
        "earliestReviewDate": dates[0],
        "latestReviewDate": dates[-1],
        "reviewsByYear": dict(sorted(collections.Counter(d[:4] for d in dates).items())),
    }

    # --- 후보 1: goodsNo 교체가 리뉴얼 신호인가 ---
    # 한 productId 안에서 goodsNo 들의 리뷰 기간이 서로 겹치지 않으면 "교체형"(리뉴얼 후보),
    # 겹치면 "병존형"(기획세트·용량·컬러 변형)이다.
    spans: dict[tuple[str, str], list[str]] = collections.defaultdict(list)
    for r in reviews:
        spans[(resolve(r["goodsNo"]), r["goodsNo"])].append(r["reviewDate"])
    by_product: dict[str, list[dict]] = collections.defaultdict(list)
    for (pid, goods_no), ds in spans.items():
        by_product[pid].append({"goodsNo": goods_no, "first": min(ds), "last": max(ds), "reviews": len(ds)})

    replacement, coexisting = [], []
    for pid, entries in sorted(by_product.items()):
        if len(entries) < 2:
            continue
        ordered = sorted(entries, key=lambda e: e["first"])
        disjoint = all(
            ordered[i]["last"] < ordered[i + 1]["first"] for i in range(len(ordered) - 1)
        )
        record = {"productId": pid, "goodsNos": len(entries)}
        (replacement if disjoint else coexisting).append(record)

    # --- 후보 2: 상품명 표기 ---
    names = {r["productName"] for r in reviews}
    name_renewal = sorted(n_ for n_ in names if re.search(NAME_RENEWAL_PATTERN, n_, re.I))
    name_new = sorted(n_ for n_ in names if re.search(NAME_NEW_PATTERN, n_, re.I))

    # --- 후보 3: 본문 언급 ---
    mentions = [r for r in reviews if mention.search(r["content"])]
    mentions_by_product = collections.Counter(resolve(r["goodsNo"]) for r in mentions)

    # SKU 분할 불가의 직접 증거: goodsNo 가 하나뿐인데 본문 리뉴얼 언급이 있는 제품.
    # 이런 제품은 (goodsNo → productId) 매핑을 아무리 쪼개도 세대를 가를 수 없다.
    single_goods = {pid for pid, entries in by_product.items() if len(entries) == 1}
    single_goods_with_mentions = sorted(
        (
            {
                "productId": pid,
                "displayName": catalog.product(pid).display_name,
                "goodsNos": 1,
                "mentions": count,
                "firstMention": min(
                    r["reviewDate"] for r in mentions if resolve(r["goodsNo"]) == pid
                ),
                "goodsNoFirstReview": by_product[pid][0]["first"],
            }
            for pid, count in mentions_by_product.items()
            if pid in single_goods
        ),
        key=lambda e: -e["mentions"],
    )

    # --- 리센시 컷 시뮬레이션 ---
    base_cells, base_pass = cells_passing(reviews, resolve)
    simulations = []
    for cutoff in SIMULATED_CUTOFFS:
        kept = reviews if cutoff is None else [
            r for r in reviews if month_of(r["reviewDate"]) >= cutoff
        ]
        cells, passing = cells_passing(kept, resolve)
        per_product = collections.Counter(resolve(r["goodsNo"]) for r in kept)
        simulations.append(
            {
                "cutoffMonth": cutoff,
                "reviewsKept": len(kept),
                "reviewsKeptPct": pct(len(kept), n),
                "cells": cells,
                "cellsPassingNMin": passing,
                "cellsLostVsNoCut": base_pass - passing,
                "renewalMentionsKept": sum(1 for r in kept if mention.search(r["content"])),
                "minReviewsPerProduct": min(per_product.values()) if per_product else 0,
                "productsBelowNMin": sum(
                    1 for pid in by_product if per_product.get(pid, 0) < SUFFICIENCY_N_MIN
                ),
                "adopted": cutoff == RECENCY_CUTOFF_MONTH,
            }
        )

    # --- 사람이 확정할 리뉴얼 후보 큐 ---
    # 본문 언급이 많은 제품 순. 여기 오른다고 리뉴얼이 확정된 것은 아니다 —
    # cutoverDate 를 근거와 함께 확정한 제품만 카탈로그에서 separate 가 된다.
    candidates = [
        {
            "productId": pid,
            "displayName": catalog.product(pid).display_name,
            "mentions": count,
            "mentionPctOfProduct": pct(
                count, sum(e["reviews"] for e in by_product[pid])
            ),
            "goodsNos": len(by_product[pid]),
            "mentionRange": [
                min(r["reviewDate"] for r in mentions if resolve(r["goodsNo"]) == pid),
                max(r["reviewDate"] for r in mentions if resolve(r["goodsNo"]) == pid),
            ],
        }
        for pid, count in mentions_by_product.most_common()
        if count >= 10
    ]

    return {
        "issue": "PER-172",
        "snapshot": snapshot,
        "renewalSignalCandidates": {
            "goodsNoReplacement": {
                "multiGoodsNoProducts": len(replacement) + len(coexisting),
                "replacementType": len(replacement),
                "coexistingType": len(coexisting),
                "replacementProducts": replacement,
                "note": (
                    "교체형만 리뉴얼 후보다. 병존형은 기획세트·용량·컬러 변형이므로 "
                    "goodsNo 를 리뉴얼 경계로 쓰면 오탐이 압도적이다"
                ),
            },
            "productNameMarkers": {
                "renewalLabeled": name_renewal,
                "newLabeled": name_new,
                "distinctProductNames": len(names),
                "note": "NEW 표기는 전부 신컬러 출시다. 리뉴얼 경계로 쓸 수 없다",
            },
            "reviewTextMentions": {
                "pattern": MENTION_PATTERN,
                "reviews": len(mentions),
                "pctOfCorpus": pct(len(mentions), n),
                "productsWithMentions": len(mentions_by_product),
                "byYear": dict(
                    sorted(collections.Counter(r["reviewDate"][:4] for r in mentions).items())
                ),
                "note": (
                    "약한 신호다 — 구매 전 기대('리뉴얼 궁금하네요')와 타 제품 리뉴얼 언급이 "
                    "섞여 있다. 규모 확인용이고 컷 기준으로 승격하지 않는다"
                ),
            },
        },
        "skuSplitInfeasible": {
            "products": single_goods_with_mentions,
            "note": (
                "goodsNo 가 하나뿐인데 본문 리뉴얼 언급이 있는 제품. 세대 경계가 SKU 코드 "
                "안쪽에 있으므로 (goodsNo → productId) 를 아무리 쪼개도 가를 수 없다. "
                "별개 제품 정책을 실행하려면 키가 (goodsNo, reviewDate) 여야 한다"
            ),
        },
        "recencyCut": {
            "snapshotLatestMonth": SNAPSHOT_LATEST_MONTH,
            "windowMonths": RECENCY_WINDOW_MONTHS,
            "adoptedCutoffMonth": RECENCY_CUTOFF_MONTH,
            "sufficiencyNMin": SUFFICIENCY_N_MIN,
            "baselineCells": base_cells,
            "baselineCellsPassingNMin": base_pass,
            "simulations": simulations,
        },
        "renewalCandidateQueue": {
            "minMentions": 10,
            "products": candidates,
            "note": (
                "사람이 cutoverDate 를 확정할 대상 큐다. 여기 오른다고 리뉴얼이 확정된 것은 "
                "아니다 — 근거와 함께 확정한 제품만 카탈로그에서 separate 가 된다"
            ),
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    args = ap.parse_args()

    raw = args.input.read_bytes()
    reviews = json.loads(raw)
    result = measure(reviews)
    result["source"] = {
        "path": str(args.input.relative_to(ROOT)),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "records": len(reviews),
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")

    sig = result["renewalSignalCandidates"]
    print(f"[리뉴얼 신호] 멀티 goodsNo 제품 {sig['goodsNoReplacement']['multiGoodsNoProducts']}개 중 "
          f"교체형 {sig['goodsNoReplacement']['replacementType']}개 / "
          f"병존형 {sig['goodsNoReplacement']['coexistingType']}개")
    print(f"  상품명 리뉴얼 표기 {len(sig['productNameMarkers']['renewalLabeled'])}건 / "
          f"NEW 표기 {len(sig['productNameMarkers']['newLabeled'])}건(전부 신컬러)")
    print(f"  본문 언급 {sig['reviewTextMentions']['reviews']}건 "
          f"({sig['reviewTextMentions']['pctOfCorpus']}%)")
    print(f"  SKU 분할 불가 제품 {len(result['skuSplitInfeasible']['products'])}개")
    print(f"\n[리센시 컷] 채택 {RECENCY_CUTOFF_MONTH} ({RECENCY_WINDOW_MONTHS}개월)")
    for s in result["recencyCut"]["simulations"]:
        mark = " ←채택" if s["adopted"] else ""
        print(f"  {str(s['cutoffMonth'] or '컷 없음'):>8} 잔존 {s['reviewsKept']:6d}"
              f" ({s['reviewsKeptPct']:5.1f}%)  N>={SUFFICIENCY_N_MIN} 셀 {s['cellsPassingNMin']:3d}"
              f" (-{s['cellsLostVsNoCut']:2d}){mark}")
    print(f"\n→ {args.out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
