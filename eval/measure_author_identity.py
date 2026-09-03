"""
PER-170 근거 측정 — 작성자 식별자 방침 (중복 게이트 vs PII 드롭).

세 옵션의 비용을 같은 스냅샷에서 실측한다.
  1) 입수 시점 솔트 해시 (authorHash)
  2) userName + profileImageUrl 조합
  3) 작성자 1표 포기 (본문 해시 중복만 처리)

측정 항목은 결정 문서 docs/DECISION_PER170_AUTHOR_IDENTIFIER.md 의 표와 1:1 대응한다.

사용:
  .venv/bin/python eval/measure_author_identity.py
  → eval/reports/author_identity_per170.json

주의: 입력 스냅샷에는 userName·profileImageUrl(PII)이 남아 있다.
이 스크립트는 집계 수치만 출력하고 원문 값은 리포트에 쓰지 않는다.
"""
import argparse
import collections
import hashlib
import hmac
import json
import statistics as st
import unicodedata
from pathlib import Path

ROOT = Path(__file__).parents[1]
DEFAULT_INPUT = ROOT / "data/input/reviews_50products.json"
DEFAULT_OUTPUT = ROOT / "eval/reports/author_identity_per170.json"

# 결정된 해시 규격의 참조 구현 (입수 코드는 PER-173에서 이 규격을 그대로 쓴다).
# 솔트는 저장소에 넣지 않는다 — 측정에서는 충돌 여부만 보므로 고정 더미로 충분하다.
MEASURE_SALT = b"PER170-MEASUREMENT-ONLY"
HASH_HEX_LEN = 16


def author_hash(user_name: str, salt: bytes = MEASURE_SALT, hex_len: int = HASH_HEX_LEN) -> str:
    """HMAC-SHA256(salt, NFC(userName)) 앞 16 hex. 원문은 저장하지 않는다."""
    msg = unicodedata.normalize("NFC", user_name).encode("utf-8")
    return hmac.new(salt, msg, hashlib.sha256).hexdigest()[:hex_len]


def content_hash(content: str) -> str:
    return hashlib.sha256(content.strip().encode("utf-8")).hexdigest()


def nonnull(values) -> set:
    return {v for v in values if v and str(v).strip()}


def pct(part: int, whole: int) -> float:
    return round(100 * part / whole, 2) if whole else 0.0


def measure(reviews: list[dict]) -> dict:
    n = len(reviews)
    by_name = collections.defaultdict(list)
    for r in reviews:
        by_name[r["userName"]].append(r)
    multi = {k: v for k, v in by_name.items() if len(v) > 1}

    # --- 1. 게이트가 필요한가: 중복 규모 ---
    pairs = collections.defaultdict(list)
    for r in reviews:
        pairs[(r["userName"], r["productKey"])].append(r)
    dup_pairs = {k: v for k, v in pairs.items() if len(v) > 1}
    excess_votes = sum(len(v) - 1 for v in dup_pairs.values())

    by_product = collections.defaultdict(list)
    for r in reviews:
        by_product[r["productKey"]].append(r)
    per_product = sorted(
        (
            {
                "productKey": pk,
                "reviews": len(v),
                "uniqueAuthors": len({x["userName"] for x in v}),
                "inflationPct": pct(len(v) - len({x["userName"] for x in v}), len(v)),
            }
            for pk, v in by_product.items()
        ),
        key=lambda x: -x["inflationPct"],
    )
    inflation = sorted(x["inflationPct"] for x in per_product)

    scale = {
        "reviews": n,
        "uniqueUserNames": len(by_name),
        "multiReviewAuthors": len(multi),
        "reviewsByMultiReviewAuthors": sum(len(v) for v in multi.values()),
        "reviewsByMultiReviewAuthorsPct": pct(sum(len(v) for v in multi.values()), n),
        "maxReviewsBySingleAuthor": max(len(v) for v in by_name.values()),
        "authorProductPairs": len(pairs),
        "duplicateAuthorProductPairs": len(dup_pairs),
        "excessVotes": excess_votes,
        "excessVotesPct": pct(excess_votes, n),
        "perProductInflationPct": {
            "min": inflation[0],
            "median": round(st.median(inflation), 2),
            "p95": inflation[int(0.95 * len(inflation))],
            "max": inflation[-1],
        },
        "worstInflationProducts": per_product[:3],
    }

    # --- 2. 옵션 3: 작성자 1표를 포기하면 얼마가 남는가 ---
    removed_by_content_hash = 0
    for rows in pairs.values():
        seen = set()
        for r in rows:
            h = content_hash(r["content"])
            if h in seen:
                removed_by_content_hash += 1
            else:
                seen.add(h)
    corpus_content_dupes = collections.Counter(content_hash(r["content"]) for r in reviews)
    option3 = {
        "identicalContentGroups": sum(1 for v in corpus_content_dupes.values() if v > 1),
        "removedByContentHashWithinProduct": removed_by_content_hash,
        "residualContamination": excess_votes - removed_by_content_hash,
        "residualContaminationPctOfCorpus": pct(excess_votes - removed_by_content_hash, n),
        "contentHashCoverageOfExcessVotesPct": pct(removed_by_content_hash, excess_votes),
    }

    # --- 3. 옵션 2: profileImageUrl 이 식별력을 보강하는가 ---
    multi_with_img = [k for k, v in multi.items() if nonnull(r["profileImageUrl"] for r in v)]
    multi_two_imgs = [k for k in multi_with_img if len(nonnull(r["profileImageUrl"] for r in multi[k])) > 1]
    img_to_names = collections.defaultdict(set)
    for r in reviews:
        if r["profileImageUrl"]:
            img_to_names[r["profileImageUrl"]].add(r["userName"])
    mixed_null = [k for k in multi_with_img if any(not r["profileImageUrl"] for r in multi[k])]
    masked = {k: v for k, v in by_name.items() if "*" in k}
    masked_multi = {k: v for k, v in masked.items() if len(v) > 1}
    masked_multi_with_img = [k for k in masked_multi if nonnull(r["profileImageUrl"] for r in masked_multi[k])]
    option2 = {
        "reviewsWithProfileImage": sum(1 for r in reviews if r["profileImageUrl"]),
        "reviewsWithProfileImagePct": pct(sum(1 for r in reviews if r["profileImageUrl"]), n),
        "uniqueProfileImageUrls": len(img_to_names),
        "multiReviewAuthorsWithImage": len(multi_with_img),
        "multiReviewAuthorsWithTwoOrMoreImages": len(multi_two_imgs),
        "profileImageUrlsSharedAcrossNames": sum(1 for v in img_to_names.values() if len(v) > 1),
        "authorsFalseSplitByCombinedKey": len(mixed_null),
        "maskedNames": len(masked),
        "maskedNameReviews": sum(len(v) for v in masked.values()),
        "maskedMultiReviewAuthors": len(masked_multi),
        "maskedMultiReviewAuthorsWithImage": len(masked_multi_with_img),
        "maskedMultiReviewAuthorsWithTwoOrMoreImages": sum(
            1 for k in masked_multi_with_img if len(nonnull(r["profileImageUrl"] for r in masked_multi[k])) > 1
        ),
    }

    # --- 4. 동명이인 위험 상한 ---
    # profileImageUrl 은 이름 간 공유가 0건이므로 "같은 계정" 증명에 쓸 수 있다.
    def verdict(rows):
        urls = nonnull(r["profileImageUrl"] for r in rows)
        if len(urls) > 1:
            return "different_account"
        if len(urls) == 1 and all(r["profileImageUrl"] for r in rows):
            return "same_account"
        return "unknown"

    pair_verdicts = collections.Counter(verdict(v) for v in dup_pairs.values())
    pair_skin_conflict = sum(1 for v in dup_pairs.values() if len(nonnull(r["skinType"] for r in v)) > 1)
    pair_tone_conflict = sum(1 for v in dup_pairs.values() if len(nonnull(r["skinTone"] for r in v)) > 1)

    conflict_authors = [k for k, v in multi.items() if len(nonnull(r["skinType"] for r in v)) > 1]
    conflict_verdicts = collections.Counter(verdict(multi[k]) for k in conflict_authors)

    def span_months(rows):
        ds = sorted(r["reviewDate"] for r in rows)
        to_m = lambda s: int(s[:4]) * 12 + int(s[5:7])
        return to_m(ds[-1]) - to_m(ds[0])

    spans = [span_months(multi[k]) for k in conflict_authors]
    same_month_conflict = sum(1 for s in spans if s <= 1)

    homonym = {
        "duplicatePairVerdicts": dict(pair_verdicts),
        "duplicatePairSameAccountPct": pct(pair_verdicts["same_account"], len(dup_pairs)),
        "duplicatePairSkinTypeConflict": pair_skin_conflict,
        "duplicatePairSkinTypeConflictPct": pct(pair_skin_conflict, len(dup_pairs)),
        "duplicatePairSkinToneConflict": pair_tone_conflict,
        "skinTypeConflictAuthors": len(conflict_authors),
        "skinTypeConflictAuthorsPct": pct(len(conflict_authors), len(multi)),
        "skinTypeConflictAuthorVerdicts": dict(conflict_verdicts),
        "skinTypeConflictSpanMonthsMedian": st.median(spans) if spans else None,
        "skinTypeConflictSpanMonthsMax": max(spans) if spans else None,
        "sameMonthSkinTypeConflictAuthors": same_month_conflict,
        "sameMonthSkinTypeConflictPctOfMultiAuthors": pct(same_month_conflict, len(multi)),
    }

    # --- 5. 해시 규격 검증 ---
    nfc_mismatch = [k for k in by_name if unicodedata.normalize("NFC", k) != k]
    collisions = {}
    for hex_len in (8, 12, 16, 32):
        hashes = {author_hash(k, hex_len=hex_len) for k in by_name}
        collisions[f"{hex_len}hex"] = len(by_name) - len(hashes)
    hash_spec = {
        "algorithm": "HMAC-SHA256(salt, NFC(userName))[:16]",
        "emptyUserNames": sum(1 for r in reviews if not (r["userName"] or "").strip()),
        "namesNotNfc": len(nfc_mismatch),
        "namesMergedByStripLower": len(by_name) - len({k.strip().lower() for k in by_name}),
        "collisionsByHexLength": collisions,
        "nameLength": {
            "min": min(len(k) for k in by_name),
            "median": st.median([len(k) for k in by_name]),
            "max": max(len(k) for k in by_name),
        },
        "namesUpToTwoChars": sum(1 for k in by_name if len(k) <= 2),
        "reviewsByNamesUpToTwoChars": sum(len(v) for k, v in by_name.items() if len(k) <= 2),
    }

    # --- 6. 작성자 1표의 하위 영향 (충분성 게이트 N_min=8) ---
    cells = collections.defaultdict(list)
    for r in reviews:
        cells[(r["productKey"], (r["skinType"] or "").strip() or "MISSING")].append(r)
    before = [len(v) for v in cells.values()]
    after = [len({r["userName"] for r in v}) for v in cells.values()]
    downstream = {
        "cellDefinition": "productKey x skinType (미기재는 MISSING 세그먼트로 별도 계산)",
        "cells": len(cells),
        "cellsAtLeast8Before": sum(1 for x in before if x >= 8),
        "cellsAtLeast8After": sum(1 for x in after if x >= 8),
        "cellsFallingBelow8": sum(1 for b, a in zip(before, after) if b >= 8 > a),
        "cellMedianBefore": st.median(before),
        "cellMedianAfter": st.median(after),
        "minUniqueAuthorsPerProduct": min(len({r["userName"] for r in v}) for v in by_product.values()),
    }

    return {
        "issue": "PER-170",
        "scale": scale,
        "option3_dropAuthorVote": option3,
        "option2_profileImageUrl": option2,
        "homonymRisk": homonym,
        "option1_hashSpec": hash_spec,
        "downstreamSufficiencyGate": downstream,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    args = ap.parse_args()

    raw = args.input.read_bytes()
    reviews = json.loads(raw)
    result = measure(reviews)
    result["source"] = {
        "path": str(args.input.relative_to(ROOT)),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "records": len(reviews),
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\n→ {args.out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
