"""
Step 2: Rule-based aspect normalization and clustering.
Groups synonymous aspects, counts pos/neg, filters by min support.
No LLM. Output: data/intermediate/step2_clusters.json
"""
import json
from pathlib import Path
from collections import defaultdict

INPUT_CLAIMS = Path(__file__).parents[1] / "data/intermediate/step1_claims.json"
INPUT_GROUPED = Path(__file__).parents[1] / "data/intermediate/step0_grouped.json"
OUTPUT_PATH = Path(__file__).parents[1] / "data/intermediate/step2_clusters.json"

MIN_SUPPORT = 5          # minimum reviews per cluster
MAX_SNIPPETS = 3         # top snippets per sentiment

# Step 1 출력은 이미 아래 14종 enum 중 하나여야 한다.
# SYNONYM_MAP은 안전망: 프롬프트 이탈 시 최대한 흡수.
VALID_ASPECTS = {
    "보습감", "흡수력", "발림감/텍스처", "지속성", "향",
    "광택/윤기", "커버력", "발색", "유분/번들거림", "트러블/자극",
    "탄력/탱탱함", "분사력", "밀착력", "가성비",
}

SYNONYM_MAP = {
    "보습감": ["보습감", "보습력", "촉촉함", "촉촉", "수분감", "수분", "보습"],
    "흡수력": ["흡수력", "흡수", "흡수되", "스며"],
    "트러블/자극": ["트러블", "여드름", "자극", "뒤집어", "뒤집", "올라오", "뾰루지", "붉어", "따갑", "따가운", "자극감", "피부 이상", "반응"],
    "발림감/텍스처": ["발림", "텍스처", "질감", "제형", "발리", "도포", "퍼지"],
    "향": ["향", "냄새", "향기", "향이"],
    "지속성": ["지속", "지속성", "유지", "오래", "버티"],
    "광택/윤기": ["광", "윤기", "윤광", "글로우", "빛나", "광택"],
    "커버력": ["커버", "커버력", "가려"],
    "발색": ["발색", "색감", "색"],
    "유분/번들거림": ["유분", "번들", "기름", "T존"],
    "가성비": ["가성비", "가격", "금액", "비싸", "저렴", "돈값"],
    "분사력": ["분사", "스프레이", "뿌림", "미세"],
    "탄력/탱탱함": ["탄력", "탱탱", "탄탄", "탄력/탱탱함"],
    "밀착력": ["밀착", "들뜸", "뜨지"],
}

_unknown_log: set[str] = set()


def normalize_aspect(raw_aspect: str) -> str | None:
    if raw_aspect in VALID_ASPECTS:
        return raw_aspect
    lower = raw_aspect.lower()
    for canonical, variants in SYNONYM_MAP.items():
        if any(v in lower for v in variants):
            return canonical
    if raw_aspect not in _unknown_log:
        print(f"[Step 2] WARNING: enum 외 aspect 감지 → '{raw_aspect}' (건너뜀)")
        _unknown_log.add(raw_aspect)
    return None


def run():
    claims_data = json.loads(INPUT_CLAIMS.read_text())
    grouped_data = json.loads(INPUT_GROUPED.read_text())

    # Build likes lookup: reviewId -> likes
    likes_lookup: dict[int, int] = {}
    for group in grouped_data.values():
        for r in group["reviews"]:
            likes_lookup[r["reviewId"]] = r.get("likes", 0)

    output: dict[str, list] = {}

    for product_key, claims in claims_data.items():
        # cluster_key -> {reviewIds, pos_snippets, neg_snippets, pos_count, neg_count}
        clusters: dict[str, dict] = defaultdict(lambda: {
            "review_ids": set(),
            "pos_snippets": [],  # (likes, snippet)
            "neg_snippets": [],
            "pos_count": 0,
            "neg_count": 0,
            "neutral_count": 0,
        })

        for claim in claims:
            aspect = normalize_aspect(claim.get("aspect", ""))
            if aspect is None:
                continue
            sentiment = claim.get("sentiment", "neutral")
            snippet = claim.get("snippet", "")
            review_id = claim.get("reviewId")
            likes = likes_lookup.get(review_id, 0)

            c = clusters[aspect]
            c["review_ids"].add(review_id)

            if sentiment == "positive":
                c["pos_count"] += 1
                c["pos_snippets"].append((likes, snippet))
            elif sentiment == "negative":
                c["neg_count"] += 1
                c["neg_snippets"].append((likes, snippet))
            else:
                c["neutral_count"] += 1

        product_clusters = []
        for idx, (aspect_label, c) in enumerate(clusters.items()):
            review_ids = list(c["review_ids"])
            if len(review_ids) < MIN_SUPPORT:
                continue

            # sort snippets by likes desc, take top N
            top_pos = [s for _, s in sorted(c["pos_snippets"], key=lambda x: -x[0])][:MAX_SNIPPETS]
            top_neg = [s for _, s in sorted(c["neg_snippets"], key=lambda x: -x[0])][:MAX_SNIPPETS]

            product_clusters.append({
                "clusterId": f"{product_key[:4]}_{idx:03d}",
                "aspectLabel": aspect_label,
                "reviewIds": review_ids,
                "posCount": c["pos_count"],
                "negCount": c["neg_count"],
                "neutralCount": c["neutral_count"],
                "positiveSnippets": top_pos,
                "negativeSnippets": top_neg,
                "isMixed": c["pos_count"] >= 2 and c["neg_count"] >= 2,
                "concernCategory": None,  # filled in Step 3
            })

        # sort: mixed first, then by total count desc
        product_clusters.sort(key=lambda x: (-int(x["isMixed"]), -(x["posCount"] + x["negCount"])))
        output[product_key] = product_clusters
        print(f"[Step 2] {product_key}: {len(product_clusters)}개 클러스터 (min_support={MIN_SUPPORT})")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    print(f"\n[Step 2] 완료 → {OUTPUT_PATH}")


if __name__ == "__main__":
    run()
