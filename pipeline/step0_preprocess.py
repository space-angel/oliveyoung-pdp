"""
Step 0: Load and preprocess reviews.
Groups by product, filters short reviews, assigns sentiment_prior.
No LLM. Output: data/intermediate/step0_grouped.json
"""
import json
from pathlib import Path
from schemas import Review, Sentiment

INPUT_PATH = Path(__file__).parents[1] / "data/input/reviews_200_normalized.json"
OUTPUT_PATH = Path(__file__).parents[1] / "data/intermediate/step0_grouped.json"
MIN_CONTENT_LEN = 30


def load_reviews(path: Path) -> list[Review]:
    raw = json.loads(path.read_text())
    reviews = []
    for r in raw:
        if len(r["content"]) < MIN_CONTENT_LEN:
            continue
        reviews.append(Review(
            review_id=r["reviewId"],
            product_key=r["productKey"],
            content=r["content"],
            rating=r["rating"],
            likes=r.get("likes", 0),
            is_repurchase=r.get("isRepurchase", False),
            category=r.get("category", ""),
        ))
    return reviews


def group_by_product(reviews: list[Review]) -> dict:
    groups: dict[str, list] = {}
    for r in reviews:
        groups.setdefault(r.product_key, []).append(r)
    return groups


def run():
    reviews = load_reviews(INPUT_PATH)
    groups = group_by_product(reviews)

    output = {}
    for product_key, revs in groups.items():
        output[product_key] = {
            "reviews": [
                {
                    "reviewId": r.review_id,
                    "productKey": r.product_key,
                    "content": r.content,
                    "rating": r.rating,
                    "likes": r.likes,
                    "isRepurchase": r.is_repurchase,
                    "category": r.category,
                    "sentimentPrior": r.sentiment_prior.value,
                }
                for r in revs
            ],
            "stats": {
                "total": len(revs),
                "avgRating": round(sum(r.rating for r in revs) / len(revs), 2),
                "repurchaseCount": sum(1 for r in revs if r.is_repurchase),
            },
        }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    print(f"[Step 0] Grouped {len(reviews)} reviews into {len(groups)} products → {OUTPUT_PATH}")
    for pk, g in output.items():
        print(f"  {pk}: {g['stats']['total']}건")


if __name__ == "__main__":
    run()
