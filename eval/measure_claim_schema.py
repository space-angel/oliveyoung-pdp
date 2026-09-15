"""claim 스키마가 실제 주장을 담을 수 있는가 (PER-189).

스키마를 새로 만들면 "잘 만들었다"는 말밖에 할 게 없다. 그래서 **이미 있는 진짜 주장**
26건 — 사람이 번들을 읽고 만든 골든셋 라벨(PER-178·179) — 을 이 스키마로 옮겨 보고,
전부 계약을 통과하는지, 인용이 원문과 맞는지, 무엇이 노출되고 무엇이 보류되는지를 센다.

이 변환은 버리는 코드가 아니다. judge(PER-196·197)가 생성물과 골든셋을 **같은 모양**으로
놓고 비교해야 하므로, 골든셋 → claim 변환은 그때 그대로 쓰인다.

**여기서 생성하지 않는다.** LLM 호출이 없다 (PER-191).

사용:
  .venv/bin/python eval/measure_claim_schema.py
  .venv/bin/python eval/measure_claim_schema.py --check
"""
import argparse
import collections
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

from claim_contract import (  # noqa: E402
    CLAIM_SCHEMA_VERSION,
    CLAIM_TYPES,
    CONFIDENCE_BANDS,
    ClaimContractError,
    validate_claim,
)
from golden_contract import (  # noqa: E402
    GOLDEN_SCHEMA_VERSION,
    load_failure_taxonomy,
    support_counts,
)

LABELS_PATH = ROOT / "eval/gold/v5_concern_golden_labels.jsonl"
BUNDLES_PATH = ROOT / "eval/gold/v5_concern_golden_sample.jsonl"
REVIEWS_PATH = ROOT / "data/intermediate/v5_reviews.jsonl"
REPORT_PATH = ROOT / "eval/reports/claim_schema_per189.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def require(path: Path, how: str) -> None:
    if not path.exists():
        raise SystemExit(f"FAIL: {path.relative_to(ROOT)} 가 없다\n  → {how}")


def to_claim(label: dict, bundle: dict) -> dict:
    """골든셋 라벨 1건 → claim 스키마.

    **`support` 는 라벨이 아니라 번들에서 코드가 센다** (PER-178) — 라벨러는 근거를
    인용했을 뿐 몇 명이 말했는지 세지 않았고, 세는 것은 코드의 일이다.
    """
    counts = support_counts(label, bundle)
    counts["supportAuthors"] = counts["positiveAuthors"] + counts["negativeAuthors"]
    cond = label["condition"]
    return {
        "schemaVersion": CLAIM_SCHEMA_VERSION,
        "claimId": label["labelId"],
        "productId": label["productId"],
        "aspect": label["aspect"],
        "question": label["question"],
        "answer": label["answer"],
        "condition": {
            "skinType": [cond["skinType"]] if cond.get("skinType") else None,
            "skinTrouble": cond.get("skinTrouble") or None,
            "option": cond.get("option") or None,
            # 조건축이 아니다 — 데이터에 필드가 없다 (PER-187 §3). 항상 null 이다.
            "usagePeriod": None,
        },
        "direction": label["direction"],
        "evidence": [
            {"reviewId": e["reviewId"], "quote": e["quote"], "stance": e["stance"]}
            for e in label["evidence"]
        ],
        "support": counts,
        "rejected": [],
        "failureReasons": list(label["failureReasons"]),
        "failureReason": label["failureReasons"][0] if label["failureReasons"] else None,
        "limitations": [],
        "meta": {
            "origin": "golden",
            "goldenSchemaVersion": GOLDEN_SCHEMA_VERSION,
            "labelSource": label["source"],
            "bundleId": label["bundleId"],
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="재실행 결과가 커밋된 리포트와 같은지만 확인")
    args = ap.parse_args()

    require(REVIEWS_PATH, "python3 pipeline/ingest.py")
    labels = read_jsonl(LABELS_PATH)
    bundles = {b["bundleId"]: b for b in read_jsonl(BUNDLES_PATH)}
    reviews = {r["reviewId"]: r["raw"]["content"] for r in read_jsonl(REVIEWS_PATH)}
    taxonomy = load_failure_taxonomy()

    converted, failures = [], []
    for label in labels:
        payload = to_claim(label, bundles[label["bundleId"]])
        try:
            converted.append((label, payload, validate_claim(payload, reviews=reviews,
                                                             taxonomy=taxonomy)))
        except (ClaimContractError, ValueError) as exc:
            failures.append({"labelId": label["labelId"], "error": str(exc).splitlines()[0]})

    claims = [c for _l, _p, c in converted]
    exposable = [c for c in claims if c.exposable]
    by_source = collections.Counter(l["source"] for l, _p, _c in converted)
    hedge_reasons = collections.Counter(
        r for c in claims for r in c.confidence["reasons"]
    )
    quotes = sum(len(c.evidence) for c in claims)

    report = {
        "issue": "PER-189",
        "schemaVersion": CLAIM_SCHEMA_VERSION,
        "source": {
            "labels": {"path": str(LABELS_PATH.relative_to(ROOT)),
                       "sha256": sha256(LABELS_PATH), "rows": len(labels)},
            "bundles": {"path": str(BUNDLES_PATH.relative_to(ROOT)),
                        "sha256": sha256(BUNDLES_PATH), "rows": len(bundles)},
            "reviews": {"path": str(REVIEWS_PATH.relative_to(ROOT)),
                        "sha256": sha256(REVIEWS_PATH), "records": len(reviews)},
            "failureTaxonomyVersion": taxonomy.version,
            "note": "골든셋 라벨을 claim 스키마로 옮겨 계약을 건다. 생성기는 아직 없다 "
                    "(PER-191) — 여기서 LLM 을 부르지 않는다",
        },
        "conversion": {
            "labels": len(labels),
            "converted": len(converted),
            "failed": len(failures),
            "failures": failures,
            "bySource": dict(sorted(by_source.items())),
            "note": "support 수치는 라벨이 아니라 번들에서 코드가 센다 (PER-178) — "
                    "라벨러는 근거를 인용했을 뿐 몇 명이 말했는지 세지 않았다",
            "notExpressible": {
                "count": len(failures),
                "cause": "aspect 가 null 인 라벨. 골든셋은 택소노미 14종 **밖** 주장을 적을 수 "
                         "있게 허용하지만, 파이프라인의 단위가 (셀 × aspect) 라 claim 은 "
                         "aspect 없이 존재할 수 없다. 스키마 결함이 아니라 경계다",
                "sameAs": "PER-188 의 역추적이 untraceable 로 센 것과 같은 건들이다 — "
                          "재현율 분모가 26 이 아니라 22 인 이유가 여기서도 같은 수로 나온다",
                "implication": "이 4건은 임계값을 바꿔도 되살아나지 않는다. 되살리려면 "
                               "택소노미를 넓혀야 하고 그건 PER-212(어휘 확장)의 몫이다",
            },
        },
        "quotes": {
            "checked": quotes,
            "verbatim": quotes,
            "note": "전부 원문 부분문자열이어야 통과한다. 하나라도 아니면 위 conversion.failed "
                    "로 떨어진다 — 이 수가 quotes.checked 와 같다는 것이 곧 100% 다. "
                    "인용 원문성의 전수 강제와 측정은 PER-190 이 소유한다",
        },
        "exposure": {
            "exposable": len(exposable),
            "withdrawn": len(claims) - len(exposable),
            "byFailureReason": dict(sorted(collections.Counter(
                c.failure_reason for c in claims if c.failure_reason).items())),
            "note": "failureReason 이 그대로 노출 필터다 — null 인 것만 나간다. "
                    "골든셋의 candidate_rejected 는 음성 정답이라 보류로 잡히는 것이 맞다",
        },
        "claimType": {t: sum(1 for c in claims if c.claim_type == t) for t in CLAIM_TYPES},
        "confidence": {
            "byBand": {b: sum(1 for c in claims if c.confidence["band"] == b)
                       for b in CONFIDENCE_BANDS},
            "byReason": dict(sorted(hedge_reasons.items())),
            "note": "말투이지 컷이 아니다. 기본 정책값은 잠정이고 교정은 PER-200 이다. "
                    "**여기서 단정(assertive)이 0인 것을 정책의 실패로 읽으면 안 된다** — "
                    "support 가 번들 40건 기준이라 침묵(S−D)이 구조적으로 크고, 그래서 "
                    "silence_dominates 가 전건에 붙는다. 전수 셀에서 계산하는 생성기"
                    "(PER-191)에서 다시 봐야 의미가 있는 수다",
        },
        "limits": [
            "골든셋 26건은 파일럿이고(번들 5 · 제품 4) 최종 100건이 아니다 (PER-180). "
            "이 표는 '스키마가 실제 주장을 담는가' 를 보는 것이지 커버리지가 아니다",
            "번들 5개 중 4개에 사람이 직접 만든 claim(source=human)이 0건이다. 26건 중 25건이 "
            "모델 후보에서 왔으므로, 이 표가 보여주는 '담을 수 있는 주장의 모양' 도 "
            "그만큼 후보 모델의 모양이다",
            "support 는 **번들 40건** 기준이다. 파이프라인이 실제로 쓰는 수는 전수 셀에서 "
            "나오므로 (게이트4) 여기 수치를 커버리지나 충분성 판정으로 읽으면 안 된다",
            "rejected[] 와 limitations 는 비어 있다 — 골든셋 라벨은 게이트를 거친 산출물이 "
            "아니라 사람이 번들을 보고 만든 것이라 채울 값이 없다. 생성기(PER-191)가 "
            "게이트 원장(PER-188)에서 채운다",
        ],
    }

    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.check:
        if not REPORT_PATH.exists():
            raise SystemExit(f"FAIL: 리포트가 없다 ({REPORT_PATH.relative_to(ROOT)})")
        if REPORT_PATH.read_text() != payload:
            raise SystemExit(
                f"FAIL: claim 스키마 리포트가 재현되지 않는다 ({REPORT_PATH.relative_to(ROOT)})\n"
                "  → 스키마나 골든셋이 바뀌었다면 다시 생성해 함께 커밋한다")
        print(f"OK: claim 스키마 리포트 재현 일치 ({REPORT_PATH.relative_to(ROOT)})")
        return

    REPORT_PATH.write_text(payload)
    print(f"[변환] 골든셋 라벨 {len(labels)}건 → claim {len(converted)}건 "
          f"(실패 {len(failures)})")
    for f in failures:
        print(f"   - {f['labelId']}: {f['error']}")
    print(f"[인용] {quotes}개 전부 원문 부분문자열")
    print(f"[노출] {len(exposable)} · 보류 {len(claims) - len(exposable)} "
          f"({report['exposure']['byFailureReason']})")
    print(f"[말투] {report['confidence']['byBand']} · 사유 {report['confidence']['byReason']}")
    print(f"[종류] {report['claimType']}")
    print(f"→ {REPORT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
