"""인용 원문성 — v4 재측정과 v5 현재분 (PER-190).

CLAUDE.md 서술 규칙 — *"실행 로그·리포트 파일로 확인되지 않은 수치는 쓰지 않는다."*
이 스크립트가 PER-190 의 수치를 만든다. **LLM 호출이 없다.**

## 무엇을 재는가

| 축 | 대상 | 코퍼스 | 판정 |
|---|---|---|---|
| A | v4 concern 인용 143개 | v4 500건 | **v4 가 쓴 방식 그대로** — 공백 전체 squeeze + 근거 리뷰 이어붙이기 |
| B | v4 concern 인용 143개 | v4 500건 | v5 규칙 — `fold_invisible` 부분문자열, 인용한 리뷰 기준 |
| C | v5 골든셋 라벨 인용 107개 | 25,000건 스냅샷 | v5 규칙 |
| D | v5 claim 변환분 인용 96개 | 25,000건 스냅샷 | v5 규칙 |
| E | v5 claim 변환분 인용 96개 | 25,000건 스냅샷 | v4 방식 (비교 가능성 확인용) |

**A 가 있는 이유**가 이 리포트의 요점이다. 88.8% 라는 수를 우리 규칙으로 다시 재면
다른 수가 나오는데, 그 차이가 규칙 차이인지 데이터 차이인지 구별되지 않으면 v5 의
개선분이 귀속되지 않는다 (PER-201). 그래서 **같은 입력에 두 규칙을 다 걸어** 본다.

## 기준이 되는 코퍼스가 다르다 — 이걸 먼저 밝힌다

v4 는 자기 입력 500건에서 쟀다 (`data/input/reviews_200_normalized.json`, 파일명과
달리 500행). v5 는 25,000건 스냅샷을 쓴다. **두 코퍼스는 거의 겹치지 않는다** —
v4 의 500건 중 25,000건 스냅샷에 있는 것은 `corpora.overlap` 에 실린 수뿐이다. 그래서
v4 인용을 25,000건에서 다시 재는 것은 의미가 없고, **v4 는 v4 코퍼스에서 다시 잰다.**

## "100%" 의 분모

v5 쪽 수치는 **생성기 산출물이 아니다.** 생성기는 PER-191 이고 아직 없다. 지금
존재하는 claim 은 사람이 만든 골든셋 라벨의 변환분뿐이고, 사람이 번들 화면에서
복사해 만든 인용이므로 **이 100% 는 게이트의 효과가 아니라 라벨의 품질이다.**
게이트가 생성물에서 무엇을 막는지는 PER-191 이 붙은 뒤에야 잴 수 있다.

사용:
  python3 eval/measure_quote_fidelity.py
  python3 eval/measure_quote_fidelity.py --check
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
sys.path.insert(0, str(ROOT / "eval"))

from quote_gate import (  # noqa: E402
    DEFAULT_POLICY,
    STATUSES,
    STATUS_LABELS,
    STATUS_VERBATIM,
    classify_quote,
    enforce_evidence,
    registry_entries,
    summarize,
)
from claim_contract import ClaimContractError, validate_claim  # noqa: E402
from golden_contract import load_failure_taxonomy  # noqa: E402
from measure_claim_schema import to_claim  # noqa: E402

CONCERNS_V4_PATH = ROOT / "data/output/concerns_v4.json"
REVIEWS_V4_PATH = ROOT / "data/input/reviews_200_normalized.json"
LABELS_PATH = ROOT / "eval/gold/v5_concern_golden_labels.jsonl"
BUNDLES_PATH = ROOT / "eval/gold/v5_concern_golden_sample.jsonl"
REVIEWS_V5_PATH = ROOT / "data/intermediate/v5_reviews.jsonl"
REPORT_PATH = ROOT / "eval/reports/quote_fidelity_per190.json"

#: v4 가 통과 기준으로 쓴 정규화 (`legacy/v4/eval/eval_v4.py:_normalize`).
#: 공백을 전부 지운다 — 우리 규칙으로는 쓸 수 없다 (CLAUDE.md).
V4_NORMALIZE = r're.sub(r"\s+", "", text)'


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def require(path: Path, how: str) -> None:
    if not path.exists():
        raise SystemExit(f"FAIL: {path.relative_to(ROOT)} 가 없다\n  → {how}")


def v4_squeeze(text: str) -> str:
    """v4 의 `_normalize` 를 그대로 옮긴 것. 재현을 위해 우리 함수로 바꾸지 않는다."""
    return re.sub(r"\s+", "", text or "")


def pct(matched: int, total: int) -> float:
    return round(matched / total * 100, 1) if total else 0.0


# --- 축 A·E : v4 방식 --------------------------------------------------------

def measure_v4_method(items: list[dict], reviews: dict[int, str]) -> dict:
    """v4 방식 — 근거 리뷰 본문을 **이어붙여** 공백 없앤 문자열에서 부분문자열을 찾는다.

    두 가지가 우리 규칙과 다르다.

      1. 공백을 전부 지운다 → 띄어쓰기를 지운 편집이 통과한다
      2. 인용을 **어느 리뷰**에서 가져왔는지 보지 않는다 → 출처가 틀려도 통과한다.
         v4 산출물에는 인용별 reviewId 가 아예 없어서 그 이상을 잴 수 없었다
    """
    total = matched = 0
    mismatches = []
    for item in items:
        haystack = "".join(v4_squeeze(reviews[rid]) for rid in item["supportingReviewIds"]
                           if rid in reviews)
        for quote in item["quotes"]:
            total += 1
            needle = v4_squeeze(quote["quote"])
            if needle and needle in haystack:
                matched += 1
            else:
                mismatches.append({"id": item["id"], "polarity": quote.get("polarity"),
                                   "quote": quote["quote"]})
    return {
        "method": "v4 — 근거 리뷰 이어붙이기 + 공백 전체 제거, 인용별 출처 대조 없음",
        "normalize": V4_NORMALIZE,
        "quotes": total,
        "matched": matched,
        "accuracyPct": pct(matched, total),
        "mismatches": mismatches,
    }


# --- 축 B·C·D : v5 규칙 ------------------------------------------------------

def measure_v5_rule(items: list[dict], reviews: dict[int, str],
                    corpus: dict[int, str] | None = None) -> dict:
    """v5 규칙 — `fold_invisible` 부분문자열을 **인용이 가리킨 리뷰**에서 찾는다.

    인용별 reviewId 가 없는 산출물(v4)에서는 근거 리뷰 목록 전체를 후보로 본다.
    후보 중 하나라도 원문 일치면 통과이고, 그때의 등급은 가장 좋은 등급이다 —
    v4 를 우리 규칙으로 다시 잴 때 v4 가 가지지 않은 정보를 요구하지 않기 위해서다.
    """
    total = 0
    by_status: collections.Counter = collections.Counter()
    failures = []
    for item in items:
        candidates = [rid for rid in item["supportingReviewIds"] if rid in reviews]
        for quote in item["quotes"]:
            total += 1
            verdicts = [classify_quote(quote["quote"], rid, reviews, corpus)
                        for rid in candidates] or [
                classify_quote(quote["quote"], -1, reviews, corpus)]
            best = min(verdicts, key=lambda v: STATUSES.index(v.status))
            by_status[best.status] += 1
            if not best.ok:
                failures.append({
                    "id": item["id"],
                    "polarity": quote.get("polarity"),
                    "quote": quote["quote"],
                    "status": best.status,
                    "statusLabel": STATUS_LABELS[best.status],
                    "matchedReviewId": best.matched_review_id,
                })
    verbatim = by_status[STATUS_VERBATIM]
    return {
        "method": "v5 — fold_invisible 부분문자열, 인용이 가리킨 리뷰 기준",
        "normalize": "tag_contract.fold_invisible (보이지 않는 문자만: CRLF·Zs·폭 없는 공백)",
        "quotes": total,
        "verbatim": verbatim,
        "verbatimPct": pct(verbatim, total),
        "byStatus": {s: by_status[s] for s in STATUSES if by_status[s]},
        "failures": failures,
    }


# --- 입력 모양 맞추기 --------------------------------------------------------

def v4_items(concerns: dict) -> list[dict]:
    """v4 concern → 공통 모양. 인용별 reviewId 가 없다는 사실이 여기서 드러난다."""
    items = []
    for product in concerns["products"]:
        for concern in product["concerns"]:
            quotes = [{"quote": s, "polarity": polarity}
                      for polarity, key in (("positive", "positiveSnippets"),
                                            ("negative", "negativeSnippets"))
                      for s in concern.get(key, [])]
            items.append({
                "id": concern["concernId"],
                "supportingReviewIds": list(concern.get("supportingReviewIds", [])),
                "quotes": quotes,
            })
    return items


def claim_items(claims: list[dict]) -> list[dict]:
    """v5 claim → 공통 모양. 인용마다 reviewId 가 있어 출처까지 대조된다."""
    return [{
        "id": c["claimId"],
        "supportingReviewIds": [e["reviewId"] for e in c["evidence"]],
        "quotes": [{"quote": e["quote"], "polarity": e["stance"],
                    "reviewId": e["reviewId"]} for e in c["evidence"]],
    } for c in claims]


def measure_attributed(claims: list[dict], reviews: dict[int, str]) -> dict:
    """인용별 reviewId 가 있는 산출물 전용 — **출처까지** 대조한다 (v5 의 정본 판정)."""
    total = 0
    by_status: collections.Counter = collections.Counter()
    failures = []
    for claim in claims:
        for e in claim["evidence"]:
            total += 1
            verdict = classify_quote(e["quote"], e["reviewId"], reviews)
            by_status[verdict.status] += 1
            if not verdict.ok:
                failures.append({"id": claim["claimId"], "reviewId": e["reviewId"],
                                 "quote": e["quote"], "status": verdict.status})
    verbatim = by_status[STATUS_VERBATIM]
    return {
        "method": "v5 — fold_invisible 부분문자열을 evidence[].reviewId 본문에서 찾는다",
        "quotes": total,
        "verbatim": verbatim,
        "verbatimPct": pct(verbatim, total),
        "byStatus": {s: by_status[s] for s in STATUSES if by_status[s]},
        "failures": failures,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="인용 원문성 측정 (PER-190)")
    ap.add_argument("--check", action="store_true",
                    help="재실행 결과가 커밋된 리포트와 같은지만 확인")
    args = ap.parse_args()

    require(REVIEWS_V5_PATH, "python3 pipeline/ingest.py")

    # --- v4 쪽 ---
    concerns = json.loads(CONCERNS_V4_PATH.read_text())
    reviews_v4 = {r["reviewId"]: r["content"]
                  for r in json.loads(REVIEWS_V4_PATH.read_text())}
    items_v4 = v4_items(concerns)
    axis_a = measure_v4_method(items_v4, reviews_v4)
    axis_b = measure_v5_rule(items_v4, reviews_v4)

    # v4 방식으로는 통과하는데 v5 규칙으로는 떨어지는 것. 규칙 차이의 실체다.
    fail_keys = {(f["id"], f["quote"]) for f in axis_b["failures"]}
    miss_keys = {(m["id"], m["quote"]) for m in axis_a["mismatches"]}
    divergence = sorted(fail_keys - miss_keys)
    only_v4_fails = sorted(miss_keys - fail_keys)

    # --- v5 쪽 ---
    labels = read_jsonl(LABELS_PATH)
    bundles = {b["bundleId"]: b for b in read_jsonl(BUNDLES_PATH)}
    reviews_v5 = {r["reviewId"]: r["raw"]["content"] for r in read_jsonl(REVIEWS_V5_PATH)}
    taxonomy = load_failure_taxonomy()

    label_items = [{
        "id": lab["labelId"],
        "supportingReviewIds": [e["reviewId"] for e in lab["evidence"]],
        "quotes": [{"quote": e["quote"], "polarity": e["stance"]} for e in lab["evidence"]],
    } for lab in labels]
    label_quote_total = sum(len(lab["evidence"]) for lab in labels)

    claims, not_expressible = [], []
    for label in labels:
        payload = to_claim(label, bundles[label["bundleId"]])
        try:
            validate_claim(payload, reviews=reviews_v5, taxonomy=taxonomy)
            claims.append(payload)
        except (ClaimContractError, ValueError) as exc:
            not_expressible.append({"labelId": label["labelId"],
                                    "quotes": len(label["evidence"]),
                                    "error": str(exc).splitlines()[0]})

    axis_c = measure_attributed(
        [{"claimId": lab["labelId"], "evidence": lab["evidence"]} for lab in labels],
        reviews_v5)
    axis_d = measure_attributed(claims, reviews_v5)
    axis_e = measure_v4_method(claim_items(claims), reviews_v5)

    # --- 게이트를 실제로 걸어 본다 ---
    gate_results = [
        enforce_evidence(c["claimId"], c["evidence"], reviews_v5, policy=DEFAULT_POLICY)
        for c in claims
    ]
    gate_summary = summarize(gate_results)

    overlap = len(set(reviews_v4) & set(reviews_v5))

    report = {
        "issue": "PER-190",
        "what": "인용이 그 리뷰의 원문 부분문자열인가. v4 를 v4 방식으로 재현하고, "
                "같은 입력에 v5 규칙을 걸어 규칙 차이를 분리한다",
        "source": {
            "concernsV4": {"path": str(CONCERNS_V4_PATH.relative_to(ROOT)),
                           "sha256": sha256(CONCERNS_V4_PATH),
                           "concerns": len(items_v4)},
            "reviewsV4": {"path": str(REVIEWS_V4_PATH.relative_to(ROOT)),
                          "sha256": sha256(REVIEWS_V4_PATH), "records": len(reviews_v4),
                          "note": "파일명은 200 이지만 500행이다. v4 평가가 쓴 파일 그대로 "
                                  "(legacy/v4/eval/eval_v4.py:REVIEWS_PATH)"},
            "goldenLabels": {"path": str(LABELS_PATH.relative_to(ROOT)),
                             "sha256": sha256(LABELS_PATH), "rows": len(labels)},
            "bundles": {"path": str(BUNDLES_PATH.relative_to(ROOT)),
                        "sha256": sha256(BUNDLES_PATH), "rows": len(bundles)},
            "reviewsV5": {"path": str(REVIEWS_V5_PATH.relative_to(ROOT)),
                          "sha256": sha256(REVIEWS_V5_PATH), "records": len(reviews_v5)},
            "failureTaxonomyVersion": taxonomy.version,
            "note": "LLM 호출이 없다. 생성기는 PER-191 이고 아직 없다",
        },
        "corpora": {
            "v4Reviews": len(reviews_v4),
            "v5Snapshot": len(reviews_v5),
            "overlap": overlap,
            "note": "겹치지 않으므로 v4 인용을 25,000건에서 다시 재는 것은 의미가 없다. "
                    "v4 는 v4 코퍼스에서, v5 는 스냅샷에서 잰다 — 코퍼스가 다르다는 "
                    "사실을 수치 옆에 붙여 읽어야 한다",
        },
        "rule": {
            "pass": "fold_invisible(quote) 가 fold_invisible(그 리뷰 원문)의 부분문자열",
            "normalize": "tag_contract.fold_invisible — CRLF·\\r → \\n, Zs 공백 → ASCII 공백, "
                         "폭 없는 공백(U+200B·U+FEFF) 제거. 글자 수와 띄어쓰기 수는 그대로다",
            "forbidden": "공백 전체 squeeze. 띄어쓰기를 지운 편집까지 통과시킨다 (CLAUDE.md). "
                         f"v4 는 이것을 통과 기준으로 썼다: {V4_NORMALIZE}",
            "sameAs": "pipeline/claim_contract.assert_quotes_verbatim 과 글자 그대로 같은 기준. "
                      "pipeline/test_quote_gate.GateMatchesClaimContract 가 양방향으로 고정한다",
            "statuses": {s: STATUS_LABELS[s] for s in STATUSES},
        },
        "v4": {
            "axisA_asMeasuredByV4": {
                **axis_a,
                "reportedInV4": {"accuracyPct": 88.8, "matched": 127, "quotes": 143,
                                 "path": "eval/reports/eval_report_v4.md §5"},
                "reproduced": (axis_a["quotes"] == 143 and axis_a["matched"] == 127
                               and axis_a["accuracyPct"] == 88.8),
            },
            "axisB_underV5Rule": axis_b,
            "divergence": {
                "v4PassV5Fail": [{"id": i, "quote": q} for i, q in divergence],
                "v5PassV4Fail": [{"id": i, "quote": q} for i, q in only_v4_fails],
                "note": "v4 방식이 통과시킨 것 중 v5 규칙이 떨어뜨리는 것 = 공백 전체 "
                        "squeeze 의 몫이다. 반대 방향(v5 통과·v4 탈락)은 비어 있다 — "
                        "v5 규칙은 v4 방식보다 순수하게 엄격하다",
            },
            "v4ReportCorrection": {
                "claimInV4Report": "불일치 16건은 '500건 전체 리뷰 원문에서 재검색해도 "
                                   "매칭되는 곳이 없었다' (eval_report_v4.md §5)",
                "found": [
                    {"id": f["id"], "quote": f["quote"], "matchedReviewId": f["matchedReviewId"]}
                    for f in axis_b["failures"] if f["status"] == "misattributed"
                ],
                "note": "재측정하면 그 중 일부는 코퍼스에 **그대로 있다.** 다만 그 concern 의 "
                        "supportingReviewIds 에 없는 다른 리뷰의 문장이다. 즉 파라프레이즈가 "
                        "아니라 misattribution 이고, 고칠 곳이 프롬프트가 아니라 근거 연결이다. "
                        "v4 가 왜 못 찾았는지는 재검색 절차가 코드로 남아 있지 않아 "
                        "재구성할 수 없다 — 못 쟀다",
            },
        },
        "v5": {
            "axisC_goldenLabels": {
                **axis_c,
                "denominator": {
                    "labels": len(labels),
                    "quotes": label_quote_total,
                    "what": "골든셋 라벨 전부의 인용. claim 으로 표현 가능한지와 무관하게 센다",
                },
            },
            "axisD_claimConvertible": {
                **axis_d,
                "denominator": {
                    "labels": len(labels),
                    "claims": len(claims),
                    "quotes": axis_d["quotes"],
                    "what": "claim 스키마로 표현되는 라벨의 인용만. "
                            "eval/reports/claim_schema_per189.json 의 96/96 과 같은 분모다",
                    "excluded": {
                        "labels": len(not_expressible),
                        "quotes": label_quote_total - axis_d["quotes"],
                        "rows": not_expressible,
                        "why": "aspect 가 null 인 라벨 — 택소노미 14종 밖이라 claim 이 될 수 "
                               "없다 (PER-189 conversion.notExpressible). 인용 자체는 "
                               "axisC 에서 세어진다",
                    },
                },
            },
            "axisE_underV4Method": {
                **axis_e,
                "note": "v4 와 같은 축에서 보기 위한 것. v4 방식은 출처를 보지 않으므로 "
                        "우리 판정보다 느슨하고, 이 수가 axisD 보다 높아도 개선이 아니다",
            },
        },
        "gate": {
            "policy": DEFAULT_POLICY.as_dict(),
            "appliedTo": "v5 claim 변환분",
            "summary": gate_summary,
            "registry": registry_entries(),
            "note": "게이트를 실제로 걸어 본 결과다. 폐기가 0 인 것은 게이트가 잘 막아서가 "
                    "아니라 **입력이 사람이 만든 라벨이라 막을 것이 없어서**다 — "
                    "생성기(PER-191)가 붙어야 게이트의 효과를 잴 수 있다",
        },
        "limits": [
            "**v5 쪽 수치는 생성기 산출물이 아니다.** 생성기는 PER-191 이고 아직 없다. "
            "지금 존재하는 claim 은 사람이 번들 화면을 보고 만든 골든셋 라벨의 변환분이라, "
            "여기의 100% 는 게이트의 효과가 아니라 라벨의 품질이다",
            "v4 와 v5 는 **코퍼스가 다르다** — v4 500건, v5 25,000건 스냅샷이고 겹치는 "
            f"리뷰는 {overlap}건이다. 두 수치를 그대로 빼서 개선폭이라고 말할 수 없다",
            "v4 산출물에는 **인용별 reviewId 가 없다.** concern 단위의 "
            "supportingReviewIds 만 있어서 v4 축에서는 출처불일치를 "
            "'근거 리뷰 목록 안에 있는가' 수준까지만 잴 수 있다. v5 는 인용마다 "
            "reviewId 가 있어 misattribution 이 판정된다",
            "linebreak_only 등급은 통과가 아니다. fold_invisible 을 넓히면 통과시킬 수 "
            "있지만 그 함수는 태깅 입력·중복 판정·게이트3 이 함께 쓴다 — 규칙을 한 "
            "이슈에서 조용히 넓히지 않는다 (docs/DECISION_PER190_QUOTE_FIDELITY.md §3)",
            "게이트 사유가 reject_registry(PER-188)에 등록돼 있지 않아 claim.rejected[] "
            "행이 되지 못한다. 지금은 quote_gate 의 자기 원장과 로그에만 남는다 "
            "(gate.registry.reasons 에 등록할 항목이 그대로 적혀 있다)",
        ],
    }

    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.check:
        if not REPORT_PATH.exists():
            raise SystemExit(f"FAIL: 리포트가 없다 ({REPORT_PATH.relative_to(ROOT)})")
        if REPORT_PATH.read_text() != payload:
            raise SystemExit(
                f"FAIL: 인용 원문성 리포트가 재현되지 않는다 ({REPORT_PATH.relative_to(ROOT)})\n"
                "  → 정규화 규칙·게이트·골든셋이 바뀌었다면 다시 생성해 함께 커밋한다")
        print(f"OK: 인용 원문성 리포트 재현 일치 ({REPORT_PATH.relative_to(ROOT)})")
        return

    REPORT_PATH.write_text(payload)
    a, b = axis_a, axis_b
    print(f"[v4·v4방식] {a['matched']}/{a['quotes']} = {a['accuracyPct']}% "
          f"(리포트 88.8% 재현: {report['v4']['axisA_asMeasuredByV4']['reproduced']})")
    print(f"[v4·v5규칙] {b['verbatim']}/{b['quotes']} = {b['verbatimPct']}% "
          f"· 등급 {b['byStatus']}")
    print(f"[규칙차이] v4통과·v5탈락 {len(divergence)}건 · v5통과·v4탈락 "
          f"{len(only_v4_fails)}건")
    print(f"[v5·라벨] {axis_c['verbatim']}/{axis_c['quotes']} = {axis_c['verbatimPct']}% "
          f"(라벨 {len(labels)}건)")
    print(f"[v5·claim] {axis_d['verbatim']}/{axis_d['quotes']} = {axis_d['verbatimPct']}% "
          f"(claim {len(claims)}건 · 표현불가 라벨 {len(not_expressible)}건 "
          f"인용 {label_quote_total - axis_d['quotes']}개 제외)")
    print(f"[게이트] {gate_summary['byOutcome']} · 원장 {gate_summary['ledgerRows']}행")
    print(f"→ {REPORT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
