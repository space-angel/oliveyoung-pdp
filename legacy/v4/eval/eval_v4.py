"""
EVAL — concerns_v4.json 자동 평가 스크립트

04_success_criteria.md 기준을 그대로 구현한다 (기준 임의 변경 금지).

실행:
  python3 eval_v4.py                  # Gate + 인용정확도 + 3차검증(결정론적 항목만)
  python3 eval_v4.py --llm            # 위 항목 + Sonnet 기반 구체성/적합도/polarity 평가
  python3 eval_v4.py --golden         # 위 항목 + golden set 일치율 (legacy/v4/eval/golden_set.json)
  python3 eval_v4.py --all --output reports/eval_report_v4.json
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[3] / ".env")
except ImportError:
    pass

ROOT = Path(__file__).resolve().parents[3]
CONCERNS_PATH = ROOT / "data/output/concerns_v4.json"
REVIEWS_PATH = ROOT / "data/input/reviews_200_normalized.json"
GOLDEN_PATH = Path(__file__).resolve().parent / "golden_set.json"

VALID_CATEGORIES = {"적합성", "리스크", "실사용", "비교"}
MODEL = "claude-sonnet-5"  # DEV는 Haiku 사용 → cross-model 검증 (docs/v4/eval/CLAUDE.md 제약)
MAX_RETRIES = 3
RETRY_DELAY = 2


# ─── 로딩 ───

def load_concerns(path=CONCERNS_PATH):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_reviews(path=REVIEWS_PATH):
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    lst = raw if isinstance(raw, list) else raw.get("results", [])
    return {r["reviewId"]: r for r in lst}


def all_concerns(data):
    """products 배열을 순회하며 (product, concern) 튜플을 낸다."""
    for product in data["products"]:
        for concern in product["concerns"]:
            yield product, concern


# ─── 1차 합격선: Gate (구조 검증) ───

def run_gate(data, review_map):
    checks = []

    products = data["products"]
    checks.append({
        "item": "제품 커버리지",
        "criterion": "len(products) == 5",
        "pass": len(products) == 5,
        "detail": f"{len(products)}개 제품",
    })

    range_fail = [p["productKey"] for p in products if not (3 <= len(p["concerns"]) <= 7)]
    checks.append({
        "item": "질문 수 범위",
        "criterion": "모든 제품 3 <= len(concerns) <= 7",
        "pass": len(range_fail) == 0,
        "detail": f"위반: {range_fail}" if range_fail else "전 제품 범위 내",
    })

    support_fail = []
    for product, concern in all_concerns(data):
        if len(concern.get("supportingReviewIds", [])) < 5:
            support_fail.append(concern["concernId"])
    checks.append({
        "item": "최소 근거 리뷰",
        "criterion": "len(supportingReviewIds) >= 5",
        "pass": len(support_fail) == 0,
        "detail": f"위반: {support_fail}" if support_fail else "전 concern 5건 이상",
    })

    invalid_id_fail = []
    for product, concern in all_concerns(data):
        bad_ids = [rid for rid in concern.get("supportingReviewIds", []) if rid not in review_map]
        if bad_ids:
            invalid_id_fail.append((concern["concernId"], bad_ids))
    checks.append({
        "item": "유효한 reviewId",
        "criterion": "근거 ID가 입력 데이터에 존재",
        "pass": len(invalid_id_fail) == 0,
        "detail": f"위반: {invalid_id_fail}" if invalid_id_fail else "전 ID 유효",
    })

    question_fail = [c["concernId"] for _, c in all_concerns(data) if not c.get("question", "").strip().endswith("?")]
    checks.append({
        "item": "의문형 질문",
        "criterion": "question.endswith('?')",
        "pass": len(question_fail) == 0,
        "detail": f"위반: {question_fail}" if question_fail else "전 질문 '?' 종결",
    })

    category_fail = [c["concernId"] for _, c in all_concerns(data) if c.get("category") not in VALID_CATEGORIES]
    checks.append({
        "item": "카테고리 유효값",
        "criterion": f"category in {VALID_CATEGORIES}",
        "pass": len(category_fail) == 0,
        "detail": f"위반: {category_fail}" if category_fail else "전 카테고리 유효",
    })

    return checks


# ─── 인용 정확도 (quote가 원문에 실제 존재하는가) ───

def _normalize(text):
    return re.sub(r"\s+", "", text or "")


def run_citation_accuracy(data, review_map):
    results = []
    total_quotes = 0
    matched_quotes = 0

    for product, concern in all_concerns(data):
        supporting_content = "".join(
            _normalize(review_map[rid]["content"])
            for rid in concern.get("supportingReviewIds", [])
            if rid in review_map
        )
        for polarity, snippets in (("positive", concern.get("positiveSnippets", [])),
                                    ("negative", concern.get("negativeSnippets", []))):
            for snippet in snippets:
                total_quotes += 1
                norm_snippet = _normalize(snippet)
                is_match = bool(norm_snippet) and norm_snippet in supporting_content
                if is_match:
                    matched_quotes += 1
                else:
                    results.append({
                        "concernId": concern["concernId"],
                        "productKey": product["productKey"],
                        "polarity": polarity,
                        "snippet": snippet,
                    })

    accuracy = round(matched_quotes / total_quotes * 100, 1) if total_quotes else 0.0
    return {
        "totalQuotes": total_quotes,
        "matchedQuotes": matched_quotes,
        "accuracyPct": accuracy,
        "mismatches": results,
    }


# ─── 3차 검증: 감성 다양성 / 카테고리 분포 / 리스크 존재 ───

def run_coverage_diversity(data):
    per_product = []
    mixed_ok_count = 0
    category_ok_count = 0
    risk_present_count = 0

    for product in data["products"]:
        concerns = product["concerns"]
        has_mixed = any(c.get("positiveCount", 0) >= 2 and c.get("negativeCount", 0) >= 2 for c in concerns)
        categories_present = {c.get("category") for c in concerns}
        has_risk = "리스크" in categories_present

        if has_mixed:
            mixed_ok_count += 1
        if len(categories_present & VALID_CATEGORIES) >= 3:
            category_ok_count += 1
        if has_risk:
            risk_present_count += 1

        per_product.append({
            "productKey": product["productKey"],
            "hasMixedSentimentConcern": has_mixed,
            "categoriesPresent": sorted(categories_present),
            "categoryCount": len(categories_present & VALID_CATEGORIES),
            "hasRiskConcern": has_risk,
        })

    n = len(data["products"])
    return {
        "perProduct": per_product,
        "summary": {
            "감성혼재_제품수": f"{mixed_ok_count}/{n}",
            "카테고리분포_충족_제품수(>=3/4)": f"{category_ok_count}/{n}",
            "리스크질문_존재_제품수": f"{risk_present_count}/{n}",
            "gate_감성혼재_전제품충족": mixed_ok_count == n,
            "gate_카테고리분포_전제품충족": category_ok_count == n,
            "gate_리스크질문_기준충족(>=4/5)": risk_present_count >= 4,
        },
    }


# ─── LLM 기반 Quality 평가: Specificity + Relevance + Polarity 일관성 (Sonnet) ───

EVAL_SYSTEM_PROMPT = """당신은 이커머스 플랫폼의 '리뷰 기반 구매 고민 질문 생성 파이프라인'을 평가하는 객관적이고 엄격한 QA 평가자입니다.
아래 [질문]과 그에 매핑된 [긍정 리뷰 수 / 부정 리뷰 수 / 샘플 리뷰 목록]을 보고 세 지표를 루브릭에 따라 평가하세요.

1. 구체성 지수 (Specificity) - 질문 1개에 대해 평가
- 2점: 특정 상황, 조건, 피부 타입, 사용 맥락 등 구매 결정에 도움되는 디테일이 명확히 포함됨.
- 1점: 특정 측면은 언급하지만 사용 맥락 디테일이 부족함.
- 0점: 지나치게 포괄적이거나 단순 찬반을 묻는 무의미한 질문.

2. 리뷰-질문 적합도 (Relevance) - 샘플 리뷰마다 개별 평가
- 5점: 리뷰가 질문에 대한 명확하고 직접적인 답변을 제공함.
- 4점: 직접적 키워드는 없으나 맥락상 충분히 유추 가능함.
- 3점: 같은 주제(카테고리)이나 명확한 답변으로는 약간 부족함.
- 2점: 카테고리만 겹칠 뿐 질문의 구체적 맥락과 무관함.
- 1점: 전혀 상관없음.

3. Polarity 일관성 (polarityConsistency) - 질문 전체에 대해 평가
- 질문의 어조/뉘앙스(우려형인지, 확인형인지, 긍정 확인형인지)가 실제 긍정({pos}건)/부정({neg})건 리뷰 분포와 모순되지 않는지 true/false로 판단.
- 예: 질문이 "번들거림 걱정 없나요?"처럼 부정 소지를 묻는데 부정 리뷰가 0건이면 false.

반드시 아래 JSON 포맷으로만 응답하고 부연 설명을 추가하지 마세요.

{{
  "specificity": {{"score": 0, "reason": "..."}},
  "polarityConsistency": {{"value": true, "reason": "..."}},
  "relevance": [{{"reviewId": "...", "score": 3, "reason": "..."}}]
}}"""


def build_user_prompt(question, pos_count, neg_count, reviews):
    review_lines = "\n\n".join(
        f"{i + 1}. reviewId: {r['reviewId']}\ncontent: {r['content']}"
        for i, r in enumerate(reviews)
    )
    return f"[질문]\n{question}\n\n[긍정 리뷰 수: {pos_count} / 부정 리뷰 수: {neg_count}]\n\n[샘플 리뷰 목록]\n{review_lines}"


def call_eval(client, question, pos_count, neg_count, reviews):
    system = EVAL_SYSTEM_PROMPT.format(pos=pos_count, neg=neg_count)
    user_msg = build_user_prompt(question, pos_count, neg_count, reviews)
    text = ""
    for attempt in range(MAX_RETRIES):
        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=4096,
                system=system,
                messages=[{"role": "user", "content": user_msg}],
            )
            text_block = next(b for b in resp.content if b.type == "text")
            text = text_block.text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            return json.loads(text)
        except json.JSONDecodeError as e:
            print(f"    ⚠️ JSON 파싱 실패 (시도 {attempt + 1}): {e}")
            time.sleep(RETRY_DELAY)
        except Exception as e:
            print(f"    ❌ API 오류 (시도 {attempt + 1}): {e}")
            time.sleep(RETRY_DELAY * (attempt + 1))
    return None


def run_llm_quality(data, review_map, max_reviews=10):
    import anthropic
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("❌ ANTHROPIC_API_KEY 필요")
        sys.exit(1)
    client = anthropic.Anthropic(api_key=api_key)

    results = []
    for product, concern in all_concerns(data):
        question = concern["question"]
        pos_count = concern.get("positiveCount", 0)
        neg_count = concern.get("negativeCount", 0)
        review_ids = concern.get("supportingReviewIds", [])[:max_reviews]
        reviews = [{"reviewId": rid, "content": review_map[rid]["content"]} for rid in review_ids if rid in review_map]

        print(f"  [{concern['concernId']}] {question[:40]}...", end=" ", flush=True)
        out = call_eval(client, question, pos_count, neg_count, reviews)
        if not out:
            print("실패")
            continue

        relevance_list = out.get("relevance", [])
        avg_relevance = round(sum(r.get("score", 0) for r in relevance_list) / len(relevance_list), 2) if relevance_list else 0
        print(f"구체성={out.get('specificity', {}).get('score')} 적합도={avg_relevance}")

        results.append({
            "concernId": concern["concernId"],
            "productKey": product["productKey"],
            "question": question,
            "specificity": out.get("specificity", {}),
            "polarityConsistency": out.get("polarityConsistency", {}),
            "relevance": relevance_list,
            "avgRelevance": avg_relevance,
        })
        time.sleep(0.3)

    avg_spec = round(sum(r["specificity"].get("score", 0) for r in results) / len(results), 2) if results else 0
    avg_rel = round(sum(r["avgRelevance"] for r in results) / len(results), 2) if results else 0
    polarity_fail = [r["concernId"] for r in results if r["polarityConsistency"].get("value") is False]

    return {
        "results": results,
        "summary": {
            "avgSpecificity": avg_spec,
            "avgRelevance": avg_rel,
            "specificity_target_1.3": avg_spec >= 1.3,
            "relevance_target_3.5": avg_rel >= 3.5,
            "polarityInconsistentConcerns": polarity_fail,
        },
    }


# ─── Golden Set 일치율 ───

def run_golden_match(data, golden_path=GOLDEN_PATH):
    if not golden_path.exists():
        return {"available": False, "reason": f"{golden_path} 없음"}

    with open(golden_path, encoding="utf-8") as f:
        golden = json.load(f)

    generated_by_product = {}
    for product, concern in all_concerns(data):
        generated_by_product.setdefault(product["productKey"], []).append(concern)

    matched = 0
    total = 0
    detail = []
    for item in golden["goldenSet"]:
        total += 1
        product_key = item["productKey"]
        candidates = generated_by_product.get(product_key, [])
        support_set = set(item["supportingReviewIds"])
        best = None
        best_overlap = 0
        for c in candidates:
            overlap = len(support_set & set(c.get("supportingReviewIds", [])))
            if c.get("category") == item.get("category") and overlap > best_overlap:
                best = c
                best_overlap = overlap
        is_match = best is not None and best_overlap >= 3
        if is_match:
            matched += 1
        detail.append({
            "goldenId": item["goldenId"],
            "productKey": product_key,
            "goldenQuestion": item["question"],
            "matchedConcernId": best["concernId"] if best else None,
            "matchedQuestion": best["question"] if best else None,
            "reviewOverlap": best_overlap,
            "isMatch": is_match,
        })

    match_rate = round(matched / total * 100, 1) if total else 0.0
    return {
        "available": True,
        "totalGoldenItems": total,
        "matched": matched,
        "matchRatePct": match_rate,
        "detail": detail,
    }


# ─── main ───

def main():
    parser = argparse.ArgumentParser(description="concerns_v4.json 자동 평가")
    parser.add_argument("--llm", action="store_true", help="Sonnet 기반 구체성/적합도/polarity 평가 실행 (API 비용 발생)")
    parser.add_argument("--golden", action="store_true", help="golden set 일치율 계산")
    parser.add_argument("--all", action="store_true", help="--llm --golden 모두 실행")
    parser.add_argument("--output", "-o", default="", help="결과 JSON 저장 경로")
    parser.add_argument("--max-reviews", type=int, default=10)
    args = parser.parse_args()

    if args.all:
        args.llm = True
        args.golden = True

    data = load_concerns()
    review_map = load_reviews()

    print("=" * 70)
    print("1차 Gate 검증")
    print("=" * 70)
    gate_results = run_gate(data, review_map)
    for c in gate_results:
        mark = "✅" if c["pass"] else "❌"
        print(f"{mark} {c['item']}: {c['detail']}")
    gate_pass = all(c["pass"] for c in gate_results)

    print("\n" + "=" * 70)
    print("인용 정확도 (quote-in-source)")
    print("=" * 70)
    citation = run_citation_accuracy(data, review_map)
    print(f"정확도: {citation['accuracyPct']}% ({citation['matchedQuotes']}/{citation['totalQuotes']})")
    if citation["mismatches"]:
        for m in citation["mismatches"][:10]:
            print(f"  ⚠️ [{m['concernId']}] {m['polarity']}: \"{m['snippet'][:40]}...\"")

    print("\n" + "=" * 70)
    print("3차 검증: 감성 다양성 / 카테고리 분포 / 리스크 존재")
    print("=" * 70)
    diversity = run_coverage_diversity(data)
    for k, v in diversity["summary"].items():
        print(f"  {k}: {v}")

    llm_quality = None
    if args.llm:
        print("\n" + "=" * 70)
        print(f"Quality 평가 (LLM: {MODEL})")
        print("=" * 70)
        llm_quality = run_llm_quality(data, review_map, args.max_reviews)
        for k, v in llm_quality["summary"].items():
            print(f"  {k}: {v}")

    golden = None
    if args.golden:
        print("\n" + "=" * 70)
        print("Golden Set 일치율")
        print("=" * 70)
        golden = run_golden_match(data)
        if golden["available"]:
            print(f"  일치율: {golden['matchRatePct']}% ({golden['matched']}/{golden['totalGoldenItems']})")
        else:
            print(f"  ⚠️ {golden['reason']}")

    report = {
        "gate": {"checks": gate_results, "pass": gate_pass},
        "citationAccuracy": citation,
        "coverageDiversity": diversity,
        "llmQuality": llm_quality,
        "goldenSet": golden,
    }

    if args.output:
        out_path = ROOT / "eval" / args.output if not os.path.isabs(args.output) else Path(args.output)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\n💾 결과 저장 → {out_path}")


if __name__ == "__main__":
    main()
