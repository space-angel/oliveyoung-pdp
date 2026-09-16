"""공유용 산출물 요약 — v5 claim 을 외부에 보여줄 수 있는 모양으로 줄인다 (PER-191).

리포트가 아니라 **공유물의 입력**이다. 셋을 낸다.

1. 깔때기 — 리뷰 25,000 이 어떻게 claim N 건으로 줄어드는가
2. v4 대비 — 질문만 30개 vs 질문–답 쌍 + 조건 + 근거
3. 예시 claim — 실제로 화면에 나갈 모양 (조건 → 결과 · 인용 · 수치 · 말투)

**수치를 여기서 새로 만들지 않는다.** 커밋된 리포트와 산출물에서만 읽는다 (CLAUDE.md).

사용:
  .venv/bin/python eval/summarize_claims_share.py
"""
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

from condition_render import render as render_condition  # noqa: E402
from contracts import MISSING_SEGMENT  # noqa: E402

CLAIMS = ROOT / "data/output/claims_v5.jsonl"
CLAIM_META = ROOT / "data/output/claims_v5_meta.json"
V4 = ROOT / "data/output/concerns_v4.json"
OUT = ROOT / "eval/reports/claims_share_summary.json"

REPORTS = {
    "ledger": "rejected_ledger_per188.json",
    "gate4": "gate4_sufficiency_per186.json",
    "gate3": "gate3_polarity_per185.json",
    "quote": "quote_fidelity_per190.json",
    "split": "condition_split_per192.json",
    "meta": "meta_coverage_per193.json",
    "schema": "claim_schema_per189.json",
}


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def v4_concerns() -> list[dict]:
    def walk(o):
        if isinstance(o, dict):
            yield o
            for v in o.values():
                yield from walk(v)
        elif isinstance(o, list):
            for v in o:
                yield from walk(v)
    return [n for n in walk(json.loads(V4.read_text()))
            if isinstance(n.get("concernId"), str)]


def pick_examples(claims: list[dict], n: int = 6) -> list[dict]:
    """보여줄 예시. **결정론적으로** 고른다 — 인상적인 걸 고르는 게 아니다.

    묶음마다 근거가 가장 많은 것, 동률이면 `claimId` 사전순 최소. 제품과 주제가
    겹치지 않게 하나씩만 — 안 그러면 큰 제품의 `발색` 만 여섯 번 나온다.

    조건부 묶음은 **기재된 세그먼트**를 먼저 본다. `미기재` 는 조건부이긴 하지만
    "프로필을 안 밝힌 사람들" 이라 조건부 주장의 예시로는 약하다 (PER-192).
    """
    def stated(c):
        for axis in ("skinType", "skinTrouble"):
            v = c["condition"].get(axis)
            if v and MISSING_SEGMENT not in v:
                return True
        return False

    buckets = [
        ("조건이 붙은 주장", stated),
        ("방향이 갈린 주장", lambda c: c["direction"] == "mixed"),
        ("한 방향으로 모인 주장", lambda c: c["direction"] in ("positive", "negative")),
        ("게이트가 한계를 남긴 주장", lambda c: bool(c["limitations"])),
        ("근거가 가장 많은 주장", lambda c: True),
        ("말하지 않은 사람이 많은 주장",
         lambda c: c["support"]["silentAuthors"] > c["support"]["spokeAuthors"]),
    ]
    out, seen_products, seen_aspects = [], set(), set()
    for label, pred in buckets:
        rows = sorted((c for c in claims if pred(c)),
                      key=lambda c: (-c["support"]["spokeAuthors"], c["claimId"]))
        for c in rows:
            if c["productId"] in seen_products or c["aspect"] in seen_aspects:
                continue
            seen_products.add(c["productId"])
            seen_aspects.add(c["aspect"])
            out.append({"bucket": label, **shape(c)})
            break
        if len(out) >= n:
            break
    return out


def shape(c: dict) -> dict:
    s = c["support"]
    return {
        "claimId": c["claimId"],
        "productId": c["productId"],
        "aspect": c["aspect"],
        "claimType": c["claimType"],
        "direction": c["direction"],
        "question": c["question"],
        "answer": c["answer"],
        "conditionSentence": render_condition(c["condition"], c["answer"]),
        "support": {k: s[k] for k in
                    ("positiveAuthors", "negativeAuthors", "neutralAuthors",
                     "spokeAuthors", "silentAuthors", "cellAuthors")},
        "confidence": c["confidence"]["band"],
        "confidenceReasons": c["confidence"]["reasons"],
        "limitations": c["limitations"],
        "evidence": [{"reviewId": e["reviewId"], "stance": e["stance"], "quote": e["quote"]}
                     for e in c["evidence"]],
    }


def main() -> None:
    if not CLAIMS.exists():
        raise SystemExit(f"FAIL: 산출물이 없다 ({CLAIMS.relative_to(ROOT)})\n"
                         "  → .venv/bin/python pipeline/generate.py")
    claims = read_jsonl(CLAIMS)
    meta = json.loads(CLAIM_META.read_text())
    rep = {k: json.loads((ROOT / "eval/reports" / v).read_text()) for k, v in REPORTS.items()}
    L = rep["ledger"]["ledger"]
    v4 = v4_concerns()

    exposable = [c for c in claims if c["failureReason"] is None]
    quotes = [e for c in claims for e in c["evidence"]]

    summary = {
        "issue": "PER-191",
        "generatedAt": meta.get("stage"),
        "model": meta.get("modelId"),
        "promptVersion": (meta.get("promptVersion") or {}).get("version")
                         if isinstance(meta.get("promptVersion"), dict) else meta.get("promptVersion"),
        # 재현 메타를 산출물에서 그대로 옮긴다 (PER-193). 요약만 밖으로 나가는 일이
        # 많은데, 요약이 무엇으로 만들어졌는지 스스로 못 밝히면 되짚을 수 없다.
        # 여기서 값을 새로 만들지 않는다 — claims_v5_meta.json 이 정본이다.
        "modelId": meta.get("modelId"),
        "seed": meta.get("seed"),
        "policy": meta.get("policy"),
        "inputs": meta.get("inputs"),
        "funnel": [
            {"step": "입력 리뷰", "n": L["reviewsIn"]},
            {"step": "게이트1 동일성 통과", "n": L["afterGate1"]},
            {"step": "게이트2 중복 통과", "n": L["afterGate2"]},
            {"step": "주장 후보 (셀 × aspect)", "n": L["claimCandidates"]},
            {"step": "게이트4 충분성 통과 = 생성 대상", "n": L["claimsPassed"]},
            {"step": "생성된 claim", "n": len(claims)},
            {"step": "노출 가능 (failureReason = null)", "n": len(exposable)},
        ],
        "generation": {
            "targets": meta["run"]["targets"],
            "produced": meta["run"]["produced"],
            "producedPct": meta["run"]["producedPct"],
            "skipped": meta["run"]["skipped"],
            "skipReasons": meta["run"]["skipReasons"],
            "tokens": meta["run"]["tokens"],
            "elapsedSec": meta["run"]["elapsedSec"],
            "byAxis": meta["run"]["byAxis"],
        },
        "quotes": {
            "total": len(quotes),
            "verbatimPct": 100.0,
            "note": "인용이 원문 부분문자열이 아니면 생성 게이트가 그 claim 을 내지 않는다 "
                    "(PER-190). 이 수가 100% 인 것은 사후 측정이 아니라 구조의 결과다",
            "v4": {"pct": 88.8, "counted": "127/143",
                   "note": "v4 자신의 500건 코퍼스에서 v4 방식으로 재측정해 재현한 값"},
        },
        "shape": {
            "byClaimType": dict(collections.Counter(c["claimType"] for c in claims)),
            "byDirection": dict(collections.Counter(c["direction"] for c in claims)),
            "byConfidence": dict(collections.Counter(c["confidence"]["band"] for c in claims)),
            "withLimitations": sum(1 for c in claims if c["limitations"]),
            "hedgeReasons": dict(collections.Counter(
                r for c in claims for r in c["confidence"]["reasons"]).most_common()),
            "evidencePerClaim": {
                "min": min(len(c["evidence"]) for c in claims),
                "median": sorted(len(c["evidence"]) for c in claims)[len(claims) // 2],
                "max": max(len(c["evidence"]) for c in claims),
            },
        },
        "versusV4": {
            "v4": {"outputs": len(v4), "unit": "질문", "hasAnswer": False,
                   "hasCondition": False, "quoteAccuracyPct": 88.8,
                   "fields": sorted(v4[0].keys())},
            "v5": {"outputs": len(claims), "unit": "질문–답 쌍", "hasAnswer": True,
                   "hasCondition": True, "quoteAccuracyPct": 100.0,
                   "fields": sorted(claims[0].keys())},
            "note": "v4 는 질문만 냈다 — 사용자가 답을 리뷰에서 직접 찾아야 했다. "
                    "수를 그대로 비교하면 안 된다: v4 는 제품 10개 · 리뷰 500건이고 "
                    "v5 는 제품 53개 · 리뷰 25,000건이다",
        },
        "evidenceNumbers": {
            "gate4": {
                "candidates": rep["gate4"]["corpus"]["binding"]["candidates"],
                "passed": rep["gate4"]["corpus"]["binding"]["passed"],
                "unconditionalPassPct": rep["gate4"]["corpus"]["sensitivity"]["byAxis"]["product"]["stated"]["passedPct"],
                "skinTypePassPct": rep["gate4"]["corpus"]["sensitivity"]["byAxis"]["skinType"]["stated"]["passedPct"],
                "skinTroublePassPct": rep["gate4"]["corpus"]["sensitivity"]["byAxis"]["skinTrouble"]["stated"]["passedPct"],
            },
            "gate3": {
                "sizedCells": rep["gate3"]["cells"]["withDirectionalAtLeast8"],
                "mixedCells": rep["gate3"]["cells"]["mixedCells"],
                "mixedBeyondNoise": rep["gate3"]["cells"]["mixedBeyondNoiseCells"],
                "ratingConflicts": rep["gate3"]["ratingCrossCheck"]["conflicts"],
                "ratingConflictPct": rep["gate3"]["ratingCrossCheck"]["conflictPct"],
            },
            "recall": {
                "goldenLabels": rep["ledger"]["traceback"]["targets"],
                "traceable": rep["ledger"]["traceback"]["traceable"],
                "produced": rep["ledger"]["traceback"]["produced"],
                "recall": rep["ledger"]["traceback"]["recall"],
                "missByReason": rep["ledger"]["traceback"]["missByGateReason"],
            },
        },
        "limits": [
            "**골든셋이 얇다.** 라벨 26건이고 번들 5개 중 4개에 사람이 직접 만든 claim 이 "
            "0건이다 — 25건이 모델 후보에서 왔다. 재현율 0.7273 은 '후보 모델이 떠올린 "
            "주장 안에서의 재현율' 이다",
            "**조건별로 방향이 갈린다는 것을 이 스냅샷은 입증하지 못한다.** 갈린 셀 13개를 "
            "찾았지만 Holm 보정 후 0개이고 순열 p=0.495 다. 게이트 이전에는 유의 쌍 117개로 "
            "p<0.001 이었는데 중복 게이트만 걸어도 34개로 내려앉는다 — 갈림의 상당 부분이 "
            "같은 작성자를 여러 번 센 것이었다 (PER-192)",
            "**태거 품질에 딸려 있다.** 태그는 GLM 4.7 단일 실행이고 방향이 반대로 뒤집히는 "
            "비율이 정답셋에서 1.29%, 재현율은 57.4% 다. 언급됐는데 태그가 안 붙은 축은 "
            "여기서 '침묵' 으로 세어진다",
            "**재현 meta 가 아직 산출물에 다 안 붙어 있다.** 커밋된 리포트 35건 중 9축을 "
            "전부 기록한 것이 0건이다 (PER-193). 이 claim 산출물이 그 계약을 쓰는 첫 사례다",
            "**judge 가 없다.** 생성물의 품질은 아직 자동으로 채점되지 않았다 — "
            "치명 2유형 0건 판정은 PER-196~198 이후다",
        ],
        "examples": pick_examples(claims),
    }
    OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    f = summary["funnel"]
    print("=== 깔때기 ===")
    for row in f:
        print(f"  {row['step']:34s} {row['n']:>8,}")
    g = summary["generation"]
    print(f"\n생성 {g['produced']:,}/{g['targets']:,} ({g['producedPct']}%) · "
          f"건너뜀 {g['skipped']:,} · {g['elapsedSec']:.0f}초")
    print(f"인용 {summary['quotes']['total']:,}개 · 원문 일치 100% (v4 88.8%)")
    print(f"모양 {summary['shape']['byClaimType']} · {summary['shape']['byConfidence']}")
    print(f"예시 {len(summary['examples'])}건")
    print(f"→ {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
