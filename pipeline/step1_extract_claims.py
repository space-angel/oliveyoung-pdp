"""
Step 1: Per-review aspect+sentiment extraction via LLM (batched).
Uses cache to avoid re-calling for already processed reviews.
Output: data/intermediate/step1_claims.json
"""
import json
import os
import sys
from pathlib import Path

import anthropic
from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[1] / ".env")

INPUT_PATH = Path(__file__).parents[1] / "data/intermediate/step0_grouped.json"
OUTPUT_PATH = Path(__file__).parents[1] / "data/intermediate/step1_claims.json"
CACHE_PATH = Path(__file__).parents[1] / "data/cache/step1_cache.json"
PROMPT_PATH = Path(__file__).parent / "prompts/step1/v1.md"

BATCH_SIZE = 20
MODEL = "claude-haiku-4-5-20251001"


def load_cache() -> dict:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text())
    return {}


def save_cache(cache: dict):
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2))


def extract_claims_batch(client: anthropic.Anthropic, prompt_system: str, batch: list[dict]) -> list[dict]:
    user_content = json.dumps(batch, ensure_ascii=False)
    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=prompt_system,
        messages=[{"role": "user", "content": user_content}],
    )
    text = response.content[0].text.strip()
    # strip markdown fences if present
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text).get("results", [])


def run():
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set. Add it to .env", file=sys.stderr)
        sys.exit(1)

    grouped = json.loads(INPUT_PATH.read_text())
    prompt_system = PROMPT_PATH.read_text()
    cache = load_cache()
    client = anthropic.Anthropic()

    all_claims: dict[str, list] = {}  # product_key -> list of claim dicts
    total_api_calls = 0

    for product_key, group in grouped.items():
        reviews = group["reviews"]
        print(f"\n[Step 1] {product_key} ({len(reviews)}건)")
        product_claims = []

        # split into uncached / cached
        uncached = [r for r in reviews if str(r["reviewId"]) not in cache]
        cached_ids = [r["reviewId"] for r in reviews if str(r["reviewId"]) in cache]

        # restore from cache
        for review_id in cached_ids:
            cached_result = cache[str(review_id)]
            for claim in cached_result:
                claim["productKey"] = product_key
            product_claims.extend(cached_result)

        # process uncached in batches
        for i in range(0, len(uncached), BATCH_SIZE):
            batch = uncached[i: i + BATCH_SIZE]
            batch_input = [
                {
                    "reviewId": r["reviewId"],
                    "content": r["content"],
                    "rating": r["rating"],
                    "sentimentPrior": r["sentimentPrior"],
                }
                for r in batch
            ]
            print(f"  batch {i // BATCH_SIZE + 1}: {len(batch)}건 LLM 호출 중...")
            results = extract_claims_batch(client, prompt_system, batch_input)
            total_api_calls += 1

            # index results by reviewId
            result_map = {r["reviewId"]: r.get("claims", []) for r in results}
            for r in batch:
                rid = r["reviewId"]
                claims = result_map.get(rid, [])
                cache[str(rid)] = claims
                for claim in claims:
                    claim["reviewId"] = rid
                    claim["productKey"] = product_key
                product_claims.extend(claims)

        save_cache(cache)
        all_claims[product_key] = product_claims
        print(f"  → {len(product_claims)}개 claim 추출")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(all_claims, ensure_ascii=False, indent=2))
    print(f"\n[Step 1] 완료 — LLM 호출: {total_api_calls}회 → {OUTPUT_PATH}")


if __name__ == "__main__":
    run()
