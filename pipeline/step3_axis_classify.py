"""
Step 3: LLM-based concern category classification.
One LLM call per product to assign 적합성/리스크/실사용/비교 to each cluster.
Output: data/intermediate/step3_classified.json
"""
import json
import os
import sys
from pathlib import Path

import anthropic
from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[2] / ".env")

INPUT_PATH = Path(__file__).parents[2] / "data/intermediate/step2_clusters.json"
OUTPUT_PATH = Path(__file__).parents[2] / "data/intermediate/step3_classified.json"
PROMPT_PATH = Path(__file__).parents[1] / "prompts/step3/v1.md"

MODEL = "claude-haiku-4-5-20251001"
VALID_CATEGORIES = {"적합성", "리스크", "실사용", "비교"}


def classify_product(client: anthropic.Anthropic, prompt_system: str, product_key: str, clusters: list) -> dict:
    payload = {
        "productKey": product_key,
        "clusters": [
            {
                "clusterId": c["clusterId"],
                "aspectLabel": c["aspectLabel"],
                "posCount": c["posCount"],
                "negCount": c["negCount"],
                "positiveSnippets": c["positiveSnippets"][:2],
                "negativeSnippets": c["negativeSnippets"][:2],
            }
            for c in clusters
        ],
    }
    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=prompt_system,
        messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
    )
    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return {item["clusterId"]: item["category"] for item in json.loads(text).get("classifications", [])}


def run():
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set.", file=sys.stderr)
        sys.exit(1)

    clusters_data = json.loads(INPUT_PATH.read_text())
    prompt_system = PROMPT_PATH.read_text()
    client = anthropic.Anthropic()

    output = {}
    total_api_calls = 0

    for product_key, clusters in clusters_data.items():
        print(f"[Step 3] {product_key}: {len(clusters)}개 클러스터 분류 중...")
        category_map = classify_product(client, prompt_system, product_key, clusters)
        total_api_calls += 1

        classified = []
        for c in clusters:
            cat = category_map.get(c["clusterId"], "실사용")
            if cat not in VALID_CATEGORIES:
                cat = "실사용"
            classified.append({**c, "concernCategory": cat})

        output[product_key] = classified

        cats = [c["concernCategory"] for c in classified]
        print(f"  → {dict((k, cats.count(k)) for k in set(cats))}")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    print(f"\n[Step 3] 완료 — LLM 호출: {total_api_calls}회 → {OUTPUT_PATH}")


if __name__ == "__main__":
    run()
