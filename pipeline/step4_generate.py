"""
Step 4: Generate purchase concern questions via LLM.
One call per product. Outputs final concerns_v4.json.
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[2] / ".env")

INPUT_PATH = Path(__file__).parents[2] / "data/intermediate/step3_classified.json"
INPUT_GROUPED = Path(__file__).parents[2] / "data/intermediate/step0_grouped.json"
OUTPUT_PATH = Path(__file__).parents[2] / "data/output/concerns_v4.json"
PROMPT_PATH = Path(__file__).parents[1] / "prompts/step4/v1.md"

MODEL = "claude-haiku-4-5-20251001"
TARGET_MIN = 3
TARGET_MAX = 7


def generate_concerns(client: anthropic.Anthropic, prompt_system: str,
                       product_key: str, product_category: str,
                       clusters: list, target_count: int) -> list[dict]:
    # send top clusters only (sorted already in step2)
    payload = {
        "productKey": product_key,
        "productCategory": product_category,
        "clusters": [
            {
                "clusterId": c["clusterId"],
                "aspectLabel": c["aspectLabel"],
                "concernCategory": c["concernCategory"],
                "posCount": c["posCount"],
                "negCount": c["negCount"],
                "isMixed": c["isMixed"],
                "positiveSnippets": c["positiveSnippets"][:2],
                "negativeSnippets": c["negativeSnippets"][:2],
            }
            for c in clusters[:target_count]
        ],
        "targetCount": target_count,
    }
    response = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=prompt_system,
        messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
    )
    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text).get("concerns", [])


def run():
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set.", file=sys.stderr)
        sys.exit(1)

    classified = json.loads(INPUT_PATH.read_text())
    grouped = json.loads(INPUT_GROUPED.read_text())
    prompt_system = PROMPT_PATH.read_text()
    client = anthropic.Anthropic()

    # Build cluster index: clusterId -> cluster data
    all_products = []
    total_api_calls = 0
    total_concerns = 0

    for product_key, clusters in classified.items():
        if not clusters:
            print(f"[Step 4] {product_key}: 클러스터 없음, 스킵")
            continue

        product_info = grouped.get(product_key, {})
        reviews = product_info.get("reviews", [])
        product_category = reviews[0]["category"] if reviews else ""
        total_reviews = len(reviews)

        target_count = min(TARGET_MAX, max(TARGET_MIN, len(clusters)))
        print(f"[Step 4] {product_key}: {len(clusters)}개 클러스터 → {target_count}개 질문 생성 중...")

        generated = generate_concerns(client, prompt_system, product_key, product_category, clusters, target_count)
        total_api_calls += 1

        # Build cluster lookup by clusterId
        cluster_map = {c["clusterId"]: c for c in clusters}

        product_concerns = []
        for i, gen in enumerate(generated):
            cluster_id = gen.get("clusterId")
            cluster = cluster_map.get(cluster_id, {})
            concern = {
                "concernId": f"{product_key[:4]}_{i+1:02d}",
                "question": gen.get("question", ""),
                "category": cluster.get("concernCategory", "실사용"),
                "supportingReviewIds": cluster.get("reviewIds", []),
                "positiveCount": cluster.get("posCount", 0),
                "negativeCount": cluster.get("negCount", 0),
                "positiveSnippets": cluster.get("positiveSnippets", [])[:3],
                "negativeSnippets": cluster.get("negativeSnippets", [])[:3],
                "confidence": gen.get("confidence", 0.7),
            }
            product_concerns.append(concern)

        all_products.append({
            "productKey": product_key,
            "productName": product_key,
            "category": product_category,
            "totalReviews": total_reviews,
            "concerns": product_concerns,
        })
        total_concerns += len(product_concerns)
        print(f"  → {len(product_concerns)}개 concern 생성")

    output = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "version": "v4",
        "totalProducts": len(all_products),
        "totalConcerns": total_concerns,
        "products": all_products,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    print(f"\n[Step 4] 완료 — LLM 호출: {total_api_calls}회, concern 총 {total_concerns}개 → {OUTPUT_PATH}")


if __name__ == "__main__":
    run()
