"""
주장 골든셋 라벨링 번들 추출 (PER-178 → PER-179 파일럿 40건 → PER-180 100건).

골든셋의 라벨 단위는 **claim** 이지만, 라벨러가 읽는 단위는 **번들**이다 — 제품(또는 제품 × 조건 셀)
하나에 대한 리뷰 묶음. 라벨러는 번들 하나를 읽고 그 안에서 claim 을 여러 개 만든다.
파이프라인 결과는 보지 않는다 (블라인드).

## 왜 층화인가

평점 5점이 85%, 1~2점은 407건(1.6%)이다. 번들을 무작위로 채우면 부정 신호가 번들당 0~1건
들어오고, 리스크 질문("자극이 있나요?")의 정답을 만들 근거가 없다. v4 골든셋도 리스크 질문이
15개 중 2개였다. 그래서 번들 안을 **평점으로 층화**하고 층별 가중치를 기록한다 — "번들의 30%가
부정"을 코퍼스 수치로 오독하지 않게.

## 번들의 모양

    제품 번들   scope.axis = null      제품 전체 리뷰에서 층화. 제품 단위 주장 + 리뷰별 조건을 보고 만드는 조건부 주장
    셀 번들     scope.axis = skinType / skinTrouble / option    그 세그먼트 리뷰만. 조건부 주장을 **강제**
    미기재 번들 scope.axis = skinType, segment = "미기재"       미기재 세그먼트. "조건 없음"이 아니라는 규칙을 라벨에서 검증

셀은 고유 작성자 N ≥ `policy.SUFFICIENCY_N_MIN`(8) 인 것만 쓴다 — 그 아래 셀은 파이프라인이 침묵해야
하므로 정답을 만들어도 채점 대상이 없다.

## 모집단에서 미리 빼는 것 (라벨 후 되돌릴 수 없으므로 여기서 정한다)

- 리센시 컷 밖 리뷰 (`policy.recency_gate`). 파이프라인이 근거로 쓸 수 없는 리뷰로 정답을 만들면
  커버리지 미달이 정답셋 탓인지 생성기 탓인지 가를 수 없다.
- 같은 (작성자, 제품) 의 두 번째 이후 리뷰. 근거 카운트가 고유 작성자 수라서(PER-170) 라벨러가
  중복 작성자를 세지 않게 하나만 남긴다.
- v4 골든셋의 5제품 (`legacy/v4/eval/golden_set.json`). v4 concern 을 이미 본 제품이라 블라인드가
  성립하지 않는다 (PER-177 §5 도 같은 이유로 v4 사례를 골든셋에서 뺐다).

  입력  data/intermediate/v5_reviews.jsonl          (PER-173 입수 결과)
        data/input/product_catalog.json             (제품·카테고리·세대)
        legacy/v4/eval/golden_set.json              (제외할 v4 제품)
  출력  eval/gold/v5_concern_golden_sample.jsonl    (번들 40개, 리뷰 전문 — **커밋한다**)
        eval/gold/v5_concern_golden_meta.json       (시드·할당·층별 가중치·입력 해시 — 커밋)

사용:
  python3 pipeline/sample_concern_golden.py
  python3 pipeline/sample_concern_golden.py --check   # 재실행이 같은 표본을 주는지
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

from catalog import load_catalog  # noqa: E402
from contracts import MISSING_SEGMENT  # noqa: E402
from policy import RECENCY_CUTOFF_MONTH, SUFFICIENCY_N_MIN, recency_gate  # noqa: E402
from sample_tag_pilot import load_reviews, rel  # noqa: E402

ROOT = Path(__file__).parents[1]
REVIEWS_PATH = ROOT / "data/intermediate/v5_reviews.jsonl"
CATALOG_PATH = ROOT / "data/input/product_catalog.json"
V4_GOLDEN_PATH = ROOT / "legacy/v4/eval/golden_set.json"
SAMPLE_PATH = ROOT / "eval/gold/v5_concern_golden_sample.jsonl"
META_PATH = ROOT / "eval/gold/v5_concern_golden_meta.json"
LABELS_PATH = ROOT / "eval/gold/v5_concern_golden_labels.jsonl"

SEED = 20260909
BUNDLES = 40
PILOT_BUNDLES = 16  # PER-179 파일럿 40건 ≈ 번들당 2.5 claim
LABEL_TARGET = {"pilot": 40, "total": 100}

# 번들 종류별 개수. 합 = BUNDLES.
SCOPE_QUOTA = collections.OrderedDict(
    [
        ("product", 16),
        ("skinType", 10),
        ("option", 6),
        ("skinTrouble", 4),
        ("missing", 4),  # skinType = 미기재 셀
    ]
)

# 번들 안 평점 층. (층 이름, 평점 조건, 목표 건수). 합 40.
STRATA = (
    ("rating_1_2", lambda r: r <= 2, 12),
    ("rating_3", lambda r: r == 3, 8),
    ("rating_4", lambda r: r == 4, 8),
    ("rating_5", lambda r: r == 5, 12),
)
BUNDLE_SIZE = sum(want for _, _, want in STRATA)


def v4_product_ids(catalog) -> list[str]:
    """v4 골든셋이 다룬 제품의 **계보 전체**를 뺀다 — 세대만 달라도 같은 concern 을 봤다."""
    golden = json.loads(V4_GOLDEN_PATH.read_text())["goldenSet"]
    excluded: set[str] = set()
    for key in sorted({g["productKey"] for g in golden}):
        product = catalog.by_display_name(key)
        excluded.update(p.product_id for p in catalog.lineage_of(product.product_id))
    return sorted(excluded)


def eligible_population(reviews: list[dict], rng: random.Random) -> tuple[list[dict], dict]:
    """리센시 통과 + (작성자, 제품) 1건. 뺀 규모를 메타에 남긴다."""
    recent = [r for r in reviews if recency_gate(r["raw"]["reviewDate"]).passed]
    by_author: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
    for r in recent:
        by_author[(r["derived"]["authorKey"], r["productId"])].append(r)
    kept: list[dict] = []
    for key in sorted(by_author):
        group = sorted(by_author[key], key=lambda r: r["reviewId"])
        kept.append(rng.choice(group))
    kept.sort(key=lambda r: r["reviewId"])
    return kept, {
        "corpus": len(reviews),
        "recencyPassed": len(recent),
        "recencyCutoffMonth": RECENCY_CUTOFF_MONTH,
        "afterAuthorDedup": len(kept),
    }


def largest_remainder(weights: dict[str, int], total: int) -> dict[str, int]:
    """카테고리별 번들 수를 제품 수에 비례 배분한다 (최대 잉여법, 결정적)."""
    denom = sum(weights.values())
    raw = {k: total * v / denom for k, v in weights.items()}
    alloc = {k: int(raw[k]) for k in weights}
    left = total - sum(alloc.values())
    for k in sorted(weights, key=lambda k: (-(raw[k] - alloc[k]), k))[:left]:
        alloc[k] += 1
    return alloc


def _segments(review: dict, axis: str) -> tuple[str, ...]:
    cond = review["condition"][axis]
    return tuple(cond["segments"]) if axis == "skinTrouble" else (cond["segment"],)


def cell_options(rows: list[dict]) -> dict[str, list[str]]:
    """제품 하나에서 셀 번들이 가능한 세그먼트. N ≥ N_MIN 인 것만."""
    out: dict[str, list[str]] = {}
    for axis in ("skinType", "skinTrouble", "option"):
        counts: collections.Counter = collections.Counter()
        for r in rows:
            for seg in _segments(r, axis):
                counts[seg] += 1
        ok = sorted(seg for seg, n in counts.items() if n >= SUFFICIENCY_N_MIN)
        stated = [s for s in ok if s != MISSING_SEGMENT]
        if stated:
            out[axis] = stated
        if axis == "skinType" and MISSING_SEGMENT in ok:
            out["missing"] = [MISSING_SEGMENT]
    return out


def _stratify(pool: list[dict], rng: random.Random) -> tuple[list[dict], list[dict]]:
    """평점 층화. 모자란 층은 나머지 층에서 채워 BUNDLE_SIZE 까지 맞춘다."""
    picked: list[dict] = []
    strata_meta: list[dict] = []
    leftovers: list[dict] = []
    for name, predicate, want in STRATA:
        members = [r for r in pool if predicate(r["raw"]["rating"])]
        rng.shuffle(members)
        take = members[:want]
        leftovers.extend(members[want:])
        picked.extend(take)
        strata_meta.append({"name": name, "population": len(members), "sampled": len(take)})
    short = BUNDLE_SIZE - len(picked)
    if short > 0 and leftovers:
        # 채우는 순서: 낮은 평점부터 (부정 신호를 먼저 살린다)
        leftovers.sort(key=lambda r: (r["raw"]["rating"], rng.random()))
        fill = leftovers[:short]
        picked.extend(fill)
        for r in fill:
            for s, (_, predicate, _) in zip(strata_meta, STRATA):
                if predicate(r["raw"]["rating"]):
                    s["sampled"] += 1
    for s in strata_meta:
        s["weight"] = round(s["population"] / s["sampled"], 4) if s["sampled"] else None
    picked.sort(key=lambda r: r["reviewId"])
    return picked, strata_meta


def plan_bundles(products: dict[str, list[dict]], catalog, rng: random.Random) -> list[dict]:
    """어느 제품에 어떤 종류의 번들을 붙일지. 제품당 최대 1개.

    희소한 종류(missing → skinTrouble → option → skinType)부터 배정하고 제품 번들은 남는 제품에서
    고른다. 카테고리 배분은 제품 수 비례(최대 잉여법)라 5카테고리가 다 들어간다 — v4 카테고리
    분포 FAIL 의 재발을 표본에서부터 막는다.
    """
    by_category: dict[str, list[str]] = collections.defaultdict(list)
    for pid in sorted(products):
        by_category[catalog.product(pid).category].append(pid)
    alloc = largest_remainder({c: len(p) for c, p in by_category.items()}, BUNDLES)

    # 카테고리마다 배정 수만큼 제품을 고른다 (시드 셔플)
    chosen: list[str] = []
    for category in sorted(by_category):
        pids = list(by_category[category])
        rng.shuffle(pids)
        chosen.extend(pids[: alloc[category]])

    options = {pid: cell_options(products[pid]) for pid in chosen}
    remaining = set(chosen)
    plan: list[dict] = []
    segment_use: collections.Counter = collections.Counter()
    for kind in ("missing", "skinTrouble", "option", "skinType"):
        want = SCOPE_QUOTA[kind]
        candidates = sorted(pid for pid in remaining if kind in options[pid])
        rng.shuffle(candidates)
        for pid in candidates[:want]:
            axis = "skinType" if kind == "missing" else kind
            # 세그먼트는 아직 덜 쓰인 것부터 — 건성만 열 번 나오지 않게
            segs = sorted(options[pid][kind], key=lambda s: (segment_use[(axis, s)], rng.random()))
            segment = segs[0]
            segment_use[(axis, segment)] += 1
            plan.append({"productId": pid, "kind": kind, "axis": axis, "segment": segment})
            remaining.discard(pid)
        if len(candidates) < want:
            raise SystemExit(
                f"{kind} 번들을 {want}개 만들 제품이 {len(candidates)}개뿐이다. SCOPE_QUOTA 를 다시 정하라"
            )
    for pid in sorted(remaining):
        plan.append({"productId": pid, "kind": "product", "axis": None, "segment": None})
    return plan, alloc


def _round_robin(groups: dict[str, list[dict]]) -> list[dict]:
    """그룹을 번갈아 하나씩. 앞부분을 잘라도 그룹이 고루 들어간다."""
    queues = [list(groups[k]) for k in sorted(groups)]
    ordered: list[dict] = []
    while any(queues):
        for q in queues:
            if q:
                ordered.append(q.pop(0))
    return ordered


def _interleave(plan: list[dict], catalog) -> list[dict]:
    """파일럿(앞 16개)이 종류·카테고리를 고루 덮도록 두 겹 라운드로빈으로 섞는다.

    종류 안에서 카테고리를 번갈아 놓고, 그 다음 종류를 번갈아 뽑는다. 한 겹만 하면
    (카테고리 정렬 → 종류 라운드로빈) 앞 16개가 사전순 첫 카테고리에 몰린다 — 실측 16개 중 6개.
    """
    by_kind: dict[str, dict[str, list[dict]]] = {k: collections.defaultdict(list) for k in SCOPE_QUOTA}
    for item in sorted(plan, key=lambda x: x["productId"]):
        by_kind[item["kind"]][catalog.product(item["productId"]).category].append(item)
    queues = collections.OrderedDict((k, _round_robin(by_kind[k])) for k in SCOPE_QUOTA)
    ordered: list[dict] = []
    while any(queues.values()):
        for kind, q in queues.items():
            if q:
                ordered.append(q.pop(0))
    return ordered


def sample() -> tuple[list[dict], dict]:
    reviews = load_reviews(REVIEWS_PATH)
    catalog = load_catalog(CATALOG_PATH)
    rng = random.Random(SEED)

    population, pop_meta = eligible_population(reviews, rng)
    excluded_v4 = v4_product_ids(catalog)
    products: dict[str, list[dict]] = collections.defaultdict(list)
    for r in population:
        if r["productId"] not in excluded_v4:
            products[r["productId"]].append(r)
    # 고유 작성자 N_MIN 미만 제품은 파이프라인이 침묵해야 하는 제품이다 — 정답을 만들어도 채점 대상이 없다
    too_small = sorted(pid for pid, rows in products.items() if len(rows) < SUFFICIENCY_N_MIN)
    for pid in too_small:
        del products[pid]

    plan, alloc = plan_bundles(products, catalog, rng)
    plan = _interleave(plan, catalog)

    bundles: list[dict] = []
    for i, item in enumerate(plan):
        pid = item["productId"]
        rows = products[pid]
        if item["axis"]:
            rows = [r for r in rows if item["segment"] in _segments(r, item["axis"])]
        picked, strata_meta = _stratify(rows, rng)
        product = catalog.product(pid)
        bundles.append(
            {
                "bundleId": f"B{i + 1:02d}",
                "phase": "pilot" if i < PILOT_BUNDLES else "main",
                "productId": pid,
                "displayName": product.display_name,
                "category": product.category,
                "lineageId": product.lineage_id,
                "renewalPolicy": product.renewal_policy,
                "scope": {"kind": item["kind"], "axis": item["axis"], "segment": item["segment"]},
                "population": len(rows),
                "strata": strata_meta,
                "reviews": picked,
            }
        )

    meta = {
        "issue": "PER-178",
        "purpose": "주장 골든셋(PER-179 파일럿 40건 → PER-180 100건) 라벨링 번들. 라벨러는 번들만 보고 claim 을 만든다",
        "schemaVersion": "concern-golden-v1",
        "seed": SEED,
        "bundles": len(bundles),
        "pilotBundles": PILOT_BUNDLES,
        "labelTarget": LABEL_TARGET,
        "bundleSize": BUNDLE_SIZE,
        "strataQuota": {name: want for name, _, want in STRATA},
        "scopeQuota": dict(SCOPE_QUOTA),
        "cellMinAuthors": SUFFICIENCY_N_MIN,
        "population": pop_meta,
        "excludedProducts": {
            "v4GoldenLineages": excluded_v4,
            "reason": "v4 골든셋·concern 을 이미 본 제품 — 블라인드가 성립하지 않는다 (legacy/v4/eval/golden_set.json)",
            "belowCellMinAuthors": too_small,
            "belowCellMinAuthorsReason": f"리센시·작성자 1건 후 고유 작성자 {SUFFICIENCY_N_MIN}명 미만 — 파이프라인이 침묵해야 하는 제품",
        },
        "eligibleProducts": len(products),
        "categoryAllocation": alloc,
        "source": {
            "reviews": {"path": rel(REVIEWS_PATH), "sha256": hashlib.sha256(REVIEWS_PATH.read_bytes()).hexdigest()},
            "catalog": {"path": rel(CATALOG_PATH), "sha256": hashlib.sha256(CATALOG_PATH.read_bytes()).hexdigest()},
        },
        "reviewsInBundles": sum(len(b["reviews"]) for b in bundles),
        "note": "번들 안 평점 비율은 코퍼스와 다르다 (1~2점 과표집). 층별 weight 로 되돌린다. 번들 밖 리뷰로 라벨을 만들지 않는다",
    }
    return bundles, meta


def write(bundles: list[dict], meta: dict) -> None:
    SAMPLE_PATH.parent.mkdir(parents=True, exist_ok=True)
    SAMPLE_PATH.write_text("".join(json.dumps(b, ensure_ascii=False) + "\n" for b in bundles))
    META_PATH.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n")


def load_bundles(path: Path = SAMPLE_PATH) -> dict[str, dict]:
    if not path.exists():
        raise SystemExit(f"번들 표본이 없다 ({rel(path)}). 먼저 python3 {rel(Path(__file__))} 를 돌려라")
    bundles = [json.loads(line) for line in path.read_text().splitlines() if line]
    return {b["bundleId"]: b for b in bundles}


def diagnose(new: list[dict], pinned: list[dict]) -> str:
    """재실행과 고정물의 차이를 치명 / 양성으로 가른다 (sample_tag_pilot.diagnose 와 같은 사상).

      치명  번들의 (제품, scope) 나 reviewId 집합이 바뀜 → 그 번들의 라벨이 무효
      양성  리뷰 집합은 같고 파생 필드만 바뀜 → 재생성하면 된다
    """
    old = {b["bundleId"]: b for b in pinned}
    new_by = {b["bundleId"]: b for b in new}
    if set(old) != set(new_by):
        return f"치명 — 번들 ID 집합이 바뀌었다 (기존 {len(old)} / 재실행 {len(new_by)}). 표본을 덮어쓰지 마라."
    broken: list[str] = []
    for bid, b in old.items():
        n = new_by[bid]
        if (b["productId"], b["scope"]) != (n["productId"], n["scope"]):
            broken.append(f"{bid}: {b['productId']} {b['scope']} → {n['productId']} {n['scope']}")
        elif {r["reviewId"] for r in b["reviews"]} != {r["reviewId"] for r in n["reviews"]}:
            broken.append(f"{bid}: reviewId 집합이 바뀜")
    if broken:
        dead = 0
        if LABELS_PATH.exists():
            hit = {line.split('"bundleId": "')[1].split('"')[0] for line in LABELS_PATH.read_text().splitlines() if '"bundleId"' in line}
            dead = len([b for b in broken if b.split(":")[0] in hit])
        lines = [f"치명 — 번들 {len(broken)}개의 내용이 바뀌었다. 라벨이 붙은 번들 {dead}개가 무효가 된다 ({rel(LABELS_PATH)})."]
        lines += [f"    {b}" for b in broken[:5]]
        lines.append("  표본을 덮어쓰지 마라. 입수 결과가 왜 달라졌는지부터 확인하라.")
        return "\n".join(lines)
    return (
        "양성 — 번들·리뷰 집합은 같고 파생 필드만 바뀌었다. 라벨은 (bundleId, reviewId) 로 대조하므로 "
        f"무효화되지 않는다. 사유를 확인했으면 재생성하라: python3 {rel(Path(__file__))}"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="재실행이 같은 표본을 주는지만 확인")
    args = ap.parse_args()

    bundles, meta = sample()
    payload = "".join(json.dumps(b, ensure_ascii=False) + "\n" for b in bundles)

    if args.check:
        if not SAMPLE_PATH.exists():
            raise SystemExit(f"FAIL: 표본이 없다 ({rel(SAMPLE_PATH)})")
        if SAMPLE_PATH.read_text() != payload:
            pinned = [json.loads(line) for line in SAMPLE_PATH.read_text().splitlines() if line]
            raise SystemExit(f"FAIL: 재실행 표본이 고정물과 다르다.\n  {diagnose(bundles, pinned)}")
        print(f"OK: 골든셋 번들 재현 확인 ({meta['bundles']}번들 / {meta['reviewsInBundles']}리뷰)")
        return

    write(bundles, meta)
    kinds = collections.Counter(b["scope"]["kind"] for b in bundles)
    cats = collections.Counter(b["category"] for b in bundles)
    print(f"[모집단] {meta['population']['corpus']:,} → 리센시 {meta['population']['recencyPassed']:,} → 작성자 1건 {meta['population']['afterAuthorDedup']:,}")
    print(f"[제외] v4 골든셋 제품 {len(meta['excludedProducts']['v4GoldenLineages'])}개 → 후보 제품 {meta['eligibleProducts']}")
    print(f"[번들] {meta['bundles']}개 (파일럿 {PILOT_BUNDLES}) · 종류 {dict(kinds)} · 카테고리 {dict(cats)}")
    neg = sum(s["sampled"] for b in bundles for s in b["strata"] if s["name"] == "rating_1_2")
    print(f"       리뷰 {meta['reviewsInBundles']}건, 그중 1~2점 {neg}건 → {rel(SAMPLE_PATH)}")


if __name__ == "__main__":
    main()
