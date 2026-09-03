"""
Step 0: Load and preprocess reviews.
Groups by product, filters short reviews, assigns sentiment_prior.
No LLM. Output: data/intermediate/step0_grouped.json

제품 동일성은 이 단계에서 카탈로그(PER-171)가 결정한다. 리뷰 행의
`productKey` 문자열은 읽지 않고 `goodsNo` 를 카탈로그에 물어 `productId` 를
받는다. 카탈로그에 없는 `goodsNo` 는 조용히 폴백하지 않고 에러다.
"""
import argparse
import json
from pathlib import Path

from catalog import Product, load_catalog
from schemas import Review, Sentiment

INPUT_PATH = Path(__file__).parents[1] / "data/input/reviews_200_normalized.json"
OUTPUT_PATH = Path(__file__).parents[1] / "data/intermediate/step0_grouped.json"
MIN_CONTENT_LEN = 30


def load_reviews(path: Path, catalog) -> list[tuple[Product, Review]]:
    """리뷰를 읽고 카탈로그로 제품을 확정한다. 미등록 goodsNo 는 UnknownGoodsNoError."""
    raw = json.loads(path.read_text())
    rows = []
    for r in raw:
        if len(r["content"]) < MIN_CONTENT_LEN:
            continue
        product = catalog.product_of_goods_no(r["goodsNo"])
        rows.append((
            product,
            Review(
                review_id=r["reviewId"],
                product_key=product.display_name,
                content=r["content"],
                rating=r["rating"],
                likes=r.get("likes", 0),
                is_repurchase=r.get("isRepurchase", False),
                category=product.category,
            ),
        ))
    return rows


def group_by_product(rows: list[tuple[Product, Review]]) -> dict:
    groups: dict[str, dict] = {}
    for product, review in rows:
        g = groups.setdefault(product.display_name, {"product": product, "reviews": []})
        g["reviews"].append(review)
    return groups


def run(input_path: Path = INPUT_PATH, output_path: Path = OUTPUT_PATH):
    catalog = load_catalog()
    rows = load_reviews(input_path, catalog)
    groups = group_by_product(rows)

    output = {}
    for product_key, g in groups.items():
        product, revs = g["product"], g["reviews"]
        output[product_key] = {
            "productId": product.product_id,
            "displayName": product.display_name,
            "reviews": [
                {
                    "reviewId": r.review_id,
                    "productId": product.product_id,
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

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    print(f"[Step 0] Grouped {len(rows)} reviews into {len(groups)} products → {output_path}")
    print(f"         제품 동일성: data/input/product_catalog.json ({len(catalog)}제품)")
    for pk, g in output.items():
        print(f"  {g['productId']} {pk}: {g['stats']['total']}건")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, default=INPUT_PATH)
    ap.add_argument("--out", type=Path, default=OUTPUT_PATH)
    run(ap.parse_args().input, ap.parse_args().out)
