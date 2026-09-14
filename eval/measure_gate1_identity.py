"""
PER-182 근거 측정 — 게이트1 동일성(옵션 정규화 · 컷 비용).

게이트1이 실제로 무엇을 걸러내는지 같은 스냅샷에서 잰다. 두 가지를 확인한다.

  1) **옵션 정규화가 근거를 얼마나 되살리는가** — 797개 옵션 문자열이 몇 개의 색상으로
     정리되고, 색상 셀 중 몇 개가 충분성 하한(N_min=8, PER-186)을 넘는가.
     정규화 전에는 문자열 하나가 곧 한 색이므로 셀이 잘게 쪼개진다
  2) **컷이 얼마나 걸리는가** — 리뉴얼·리센시 컷으로 탈락하는 리뷰 수. 현 스냅샷의
     `renewalPolicy` 는 `unobserved` 45 · `separate` 6 · `single` 2 이고, `separate` 6개는
     세대가 `goodsNo` 로 이미 갈려 있어(구간이 열림) 날짜 컷이 걸리지 않는다 — 즉
     **리뉴얼 컷의 실효는 0이어야 한다.** 0이 아니면 카탈로그의 세대 구간이 바뀐 것이다

과대병합(다른 색이 합쳐짐)은 치명 오류(§7-1 '잘못된 귀속')이므로 자동으로 안전하다고
선언하지 않는다. 대신 **사람이 볼 수 있는 형태**로 남긴다 — 색상 키별 원본 문자열
목록(`mergedForms`)과, 이름이 포함관계인 키 쌍(`containmentPairs`)을 리포트에 싣는다.

사용:
  .venv/bin/python eval/measure_gate1_identity.py
  → eval/reports/gate1_identity_per182.json
  .venv/bin/python eval/measure_gate1_identity.py --check   # 커밋본과 일치 확인 (병합 게이트)
"""
import argparse
import collections
import hashlib
import json
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

from catalog import load_catalog  # noqa: E402
from gates import REJECT_LABELS, IdentityScope, run_identity_gate  # noqa: E402
from option_norm import KIND_PACK, KIND_SHADE, OptionIndex, markers  # noqa: E402
from policy import (  # noqa: E402
    RECENCY_CUTOFF_MONTH,
    RECENCY_WINDOW_MONTHS,
    SNAPSHOT_LATEST_MONTH,
    SUFFICIENCY_N_MIN,
)

INPUT_PATH = ROOT / "data/input/reviews_50products.json"
REPORT_PATH = ROOT / "eval/reports/gate1_identity_per182.json"


def load_records(path: Path) -> tuple[list[dict], object]:
    """입수 레코드 모양으로 최소 변환 — 게이트가 읽는 필드만 채운다.

    `pipeline/ingest.py` 출력을 쓰지 않는 이유는 그 산출물이 gitignore 라
    이 측정이 클론 직후에도 재현돼야 하기 때문이다. 제품 동일성은 동일하게
    카탈로그에 묻는다 (행의 `productKey` 문자열을 쓰지 않는다).
    """
    catalog = load_catalog()
    records = []
    for row in json.loads(path.read_text()):
        records.append({
            "reviewId": row["reviewId"],
            "productId": catalog.resolve_goods_no(row["goodsNo"], row["reviewDate"]),
            "raw": {"reviewDate": row["reviewDate"], "option": row.get("option")},
        })
    return records, catalog


def option_profile(records: list[dict], index: OptionIndex, catalog) -> dict:
    """옵션 정규화 전/후. '문자열 수 → 색상 수' 가 이 이슈의 핵심 수치다."""
    forms = collections.defaultdict(collections.Counter)
    kinds = collections.Counter()
    for r in records:
        raw = unicodedata.normalize("NFC", (r["raw"].get("option") or "").strip())
        if raw:
            forms[r["productId"]][raw] += 1
        _, kind = index.shade_key(r["productId"], r["raw"].get("option"))
        kinds[kind] += 1

    per_product = {}
    merged_forms = {}
    containment = []
    for product in catalog.products:
        pid = product.product_id
        keys = index.shade_keys(pid)
        if not forms[pid]:
            continue
        grouped = collections.defaultdict(list)
        for raw, n in forms[pid].most_common():
            key, kind = index.shade_key(pid, raw)
            if kind == KIND_SHADE:
                grouped[key].append({"option": raw, "reviews": n})

        # 병합 전/후를 **같은 모집단**에서 비교한다 — 색상으로 읽히는 문자열만.
        # 용량/구성(`pack`)은 애초에 색상 셀이 아니므로 양쪽에서 뺀다
        shade_forms = {
            f["option"]: f["reviews"] for forms_of_key in grouped.values() for f in forms_of_key
        }
        per_product[pid] = {
            "displayName": product.display_name,
            "category": product.category,
            "statedForms": len(forms[pid]),
            "shadeForms": len(shade_forms),
            "shadeKeys": len(keys),
            "hasShadeAxis": index.has_shade_axis(pid),
            "wrappers": sorted(index.wrappers(pid)),
            "cellsAtLeastNmin": sum(1 for v in keys.values() if v >= SUFFICIENCY_N_MIN),
            "formsAtLeastNmin": sum(1 for v in shade_forms.values() if v >= SUFFICIENCY_N_MIN),
            "reviewsAtLeastNminBefore": sum(
                v for v in shade_forms.values() if v >= SUFFICIENCY_N_MIN),
            "reviewsAtLeastNminAfter": sum(
                v for v in keys.values() if v >= SUFFICIENCY_N_MIN),
        }
        merged_forms[pid] = {k: v for k, v in sorted(grouped.items()) if len(v) > 1}
        # 이름이 포함관계인 키 쌍 — 과대/과소병합을 사람이 훑어보는 단서다
        for a in keys:
            for b in keys:
                if a != b and a.split("|")[-1] in b.split("|")[-1]:
                    containment.append({"productId": pid, "narrow": a, "wide": b,
                                        "narrowReviews": keys[a], "wideReviews": keys[b]})

    # 카테고리별 요약 — `hasShadeAxis` 는 "옵션 열이 갈린다"는 뜻이지 "색상이 있다"는
    # 뜻이 아니다. 스킨케어에서 참으로 나오는 건 용기·구성이 갈리는 경우다.
    # 그 한계가 리포트에서 바로 보이도록 카테고리로 나눠 싣는다
    by_category = collections.defaultdict(lambda: collections.Counter())
    for pid, row in per_product.items():
        bucket = by_category[row["category"]]
        bucket["products"] += 1
        bucket["shadeAxis"] += 1 if row["hasShadeAxis"] else 0
        bucket["statedForms"] += row["statedForms"]
        bucket["shadeKeys"] += row["shadeKeys"]

    total_forms = sum(len(v) for v in forms.values())
    total_keys = sum(len(index.shade_keys(p.product_id)) for p in catalog.products)
    return {
        "markersVersion": markers().version,
        "statedReviews": kinds[KIND_SHADE] + kinds[KIND_PACK],
        "reviewsByKind": dict(sorted(kinds.items())),
        "distinctFormsNote": "제품별로 센 뒤 합산한다 — 같은 문자열이 두 제품에 나오면 2로 센다",
        "distinctForms": total_forms,
        "distinctShadeKeys": total_keys,
        "formsPerShadeKey": round(total_forms / total_keys, 3) if total_keys else 0.0,
        "productsWithShadeAxis": sum(
            1 for p in catalog.products if index.has_shade_axis(p.product_id)),
        # 충분성 하한(PER-186)을 넘는 색상 셀 — 정규화의 실익이 여기서 보인다.
        # **셀 수가 아니라 그 셀에 담긴 리뷰 수**가 핵심이다: 병합하면 셀은 줄고
        # 셀마다 커진다. 하한을 넘는 셀에 담긴 리뷰가 실제로 쓸 수 있는 근거다
        "atLeastNmin": {
            "nMin": SUFFICIENCY_N_MIN,
            "cellsBefore": sum(p["formsAtLeastNmin"] for p in per_product.values()),
            "cellsAfter": sum(p["cellsAtLeastNmin"] for p in per_product.values()),
            "reviewsBefore": sum(p["reviewsAtLeastNminBefore"] for p in per_product.values()),
            "reviewsAfter": sum(p["reviewsAtLeastNminAfter"] for p in per_product.values()),
        },
        "byCategory": {k: dict(v) for k, v in sorted(by_category.items())},
        "byProduct": per_product,
        "mergedForms": merged_forms,
        "containmentPairs": containment,
    }


def cut_profile(records: list[dict], catalog) -> dict:
    """리뉴얼·리센시 컷 비용. 옵션 범위는 걸지 않는다 (색상 질문이 아닌 기본 경로)."""
    by_product = collections.defaultdict(list)
    for r in records:
        by_product[r["productId"]].append(r)

    reasons = collections.Counter()
    limitations = collections.Counter()
    passed = 0
    per_product = {}
    for pid, rows in sorted(by_product.items()):
        result = run_identity_gate(rows, IdentityScope(pid), catalog)
        passed += len(result.passed)
        for reason, n in result.rejected_by_reason().items():
            reasons[reason] += n
        for lim in result.limitations:
            limitations[lim] += 1
        per_product[pid] = {
            "reviews": len(rows),
            "passed": len(result.passed),
            "rejected": result.rejected_by_reason(),
        }
    return {
        "reviews": len(records),
        "passed": passed,
        "rejected": {
            reason: {"reviews": n, "label": REJECT_LABELS.get(reason, reason)}
            for reason, n in sorted(reasons.items())
        },
        "limitations": dict(limitations),
        "byProduct": per_product,
    }


def option_cut_profile(records: list[dict], index: OptionIndex, catalog) -> dict:
    """색상 범위를 걸었을 때의 비용 — 색상축이 있는 제품의 **가장 큰 색상**을 범위로 잡는다.

    "색상 질문이면 같은 옵션 리뷰만"의 실제 값을 보여주는 수치다. 미기재 탈락이
    불일치 탈락과 따로 세어져야 한다 (기재율 68.1%의 반대편).
    """
    by_product = collections.defaultdict(list)
    for r in records:
        by_product[r["productId"]].append(r)

    rows = {}
    totals = collections.Counter()
    for product in catalog.products:
        pid = product.product_id
        keys = index.shade_keys(pid)
        if not index.has_shade_axis(pid):
            continue
        top_key = max(keys, key=lambda k: (keys[k], k))
        result = run_identity_gate(
            by_product[pid], IdentityScope(pid, top_key), catalog, index)
        rejected = result.rejected_by_reason()
        rows[pid] = {
            "displayName": product.display_name,
            "scope": top_key,
            "reviews": len(by_product[pid]),
            "passed": len(result.passed),
            "rejected": rejected,
        }
        totals["reviews"] += len(by_product[pid])
        totals["passed"] += len(result.passed)
        for reason, n in rejected.items():
            totals[reason] += n
    return {
        "note": "제품별로 가장 큰 색상 1개를 범위로 걸었을 때",
        "products": len(rows),
        "totals": dict(sorted(totals.items())),
        "byProduct": rows,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, default=INPUT_PATH)
    ap.add_argument("--check", action="store_true",
                    help="재실행 결과가 커밋된 리포트와 같은지만 확인 (§5-2 재현성)")
    args = ap.parse_args()

    records, catalog = load_records(args.input)
    index = OptionIndex.from_records(records)

    report = {
        "issue": "PER-182",
        "source": {
            "path": str(args.input.relative_to(ROOT)),
            "sha256": hashlib.sha256(args.input.read_bytes()).hexdigest(),
            "reviews": len(records),
        },
        "policy": {
            "snapshotLatestMonth": SNAPSHOT_LATEST_MONTH,
            "recencyWindowMonths": RECENCY_WINDOW_MONTHS,
            "recencyCutoffMonth": RECENCY_CUTOFF_MONTH,
            "sufficiencyNMin": SUFFICIENCY_N_MIN,
        },
        "optionNormalization": option_profile(records, index, catalog),
        "cuts": cut_profile(records, catalog),
        "optionScopedCut": option_cut_profile(records, index, catalog),
    }
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.check:
        if not REPORT_PATH.exists():
            raise SystemExit(f"FAIL: 리포트가 없다 ({REPORT_PATH.relative_to(ROOT)})")
        if REPORT_PATH.read_text() != payload:
            raise SystemExit(
                "FAIL: 게이트1 리포트가 재현되지 않는다 "
                f"({REPORT_PATH.relative_to(ROOT)})\n"
                "  → 옵션 어휘·정규화 규칙이 바뀌었다면 리포트를 다시 생성해 함께 커밋한다"
            )
        print(f"OK: 게이트1 리포트 재현 일치 ({REPORT_PATH.relative_to(ROOT)})")
        return

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(payload)

    opt = report["optionNormalization"]
    cuts = report["cuts"]
    print(f"[게이트1] 리뷰 {report['source']['reviews']}건")
    print(f"  옵션 문자열 {opt['distinctForms']} → 색상 {opt['distinctShadeKeys']} "
          f"(문자열/색상 {opt['formsPerShadeKey']})")
    cells = opt["atLeastNmin"]
    print(f"  N>={cells['nMin']} 셀에 담긴 근거 {cells['reviewsBefore']} → {cells['reviewsAfter']}건"
          f"  (셀 {cells['cellsBefore']} → {cells['cellsAfter']})")
    print(f"  색상축 있는 제품 {opt['productsWithShadeAxis']}개")
    print(f"  컷: 통과 {cuts['passed']} / " +
          ", ".join(f"{v['label']} {v['reviews']}" for v in cuts["rejected"].values()))
    scoped = report["optionScopedCut"]["totals"]
    print(f"  색상 범위를 걸면: 통과 {scoped.get('passed', 0)} / {scoped.get('reviews', 0)}")
    print(f"→ {REPORT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
