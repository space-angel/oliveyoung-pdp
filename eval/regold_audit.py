"""PER-175 v2 작업 기록: 블라인드 2패스 열람, 계약 감사, JSONL 조립.

v1 정답셋은 읽지 않는다. 생성/재라벨링 모델 호출도 하지 않는다.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))
from tag_contract import ASPECTS, SNIPPET_MAX_LEN, tagging_text, validate_tags

AUDIT = ROOT / "eval/reports/regold_v2"
SAMPLE = ROOT / "eval/gold/v5_tag_pilot_sample.jsonl"


def sample():
    return [json.loads(line) for line in SAMPLE.read_text().splitlines() if line]


def pass_rows(number):
    rows = []
    for chunk in range(40):
        path = AUDIT / f"pass{number}/chunk_{chunk:02}.json"
        current = json.loads(path.read_text())["results"]
        assert len(current) == 5, path
        rows.extend(current)
    assert [r["reviewId"] for r in rows] == [r["reviewId"] for r in sample()]
    return rows


def flatten(rows):
    return [{"reviewId": r["reviewId"], **t} for r in rows for t in r["aspects"]]


def violations(tags):
    reviews = {r["reviewId"]: r for r in sample()}
    errors, seen, counts = [], set(), collections.Counter()
    for i, t in enumerate(tags):
        reasons = []
        try:
            validate_tags([t], reviews)
        except ValueError as exc:
            reasons.append(str(exc))
        snippet = t.get("snippet", "")
        if len(snippet) > SNIPPET_MAX_LEN:
            reasons.append(f"snippet 길이 {len(snippet)} > {SNIPPET_MAX_LEN}")
        if "\n" in snippet or "\r" in snippet:
            reasons.append("줄바꿈을 넘는 인용")
        if t["reviewId"] in reviews and snippet not in tagging_text(reviews[t["reviewId"]]["raw"]["content"]):
            reasons.append("태거 입력 본문의 정확한 부분문자열이 아님")
        key = (t["reviewId"], t["aspect"])
        if key in seen:
            reasons.append("중복 reviewId/aspect")
        seen.add(key)
        counts[t["reviewId"]] += 1
        if counts[t["reviewId"]] > 5:
            reasons.append("리뷰당 5개 상한 초과")
        if reasons:
            errors.append({"index": i, "tag": t, "reasons": reasons})
    return errors


def main():
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument("--chunk", type=int)
    ap.add_argument("--check-pass", type=int, choices=[1, 2])
    ap.add_argument("--assemble", action="store_true")
    args = ap.parse_args()
    if args.chunk is not None:
        assert 0 <= args.chunk < 40
        rows = sample()[args.chunk * 5: (args.chunk + 1) * 5]
        prior = json.loads((AUDIT / f"pass1/chunk_{args.chunk:02}.json").read_text())["results"]
        print("이 리뷰가 말하고 있는데 위 목록에 빠진 축이 있는가? 14축을 다시 확인하라.")
        print(" / ".join(ASPECTS))
        for r, p in zip(rows, prior):
            assert r["reviewId"] == p["reviewId"]
            print(json.dumps({"reviewId": r["reviewId"], "content": tagging_text(r["raw"]["content"]),
                              "rating": r["raw"]["rating"], "pass1": p["aspects"]}, ensure_ascii=False))
    if args.check_pass:
        tags = flatten(pass_rows(args.check_pass))
        print(json.dumps({"tags": len(tags), "violations": violations(tags)}, ensure_ascii=False, indent=2))
    if args.assemble:
        first, second = (flatten(pass_rows(n)) for n in (1, 2))
        errors = violations(second)
        assert not errors, errors
        validate_tags(second, {r["reviewId"]: r for r in sample()})
        paths = [AUDIT / "pass1_tags.jsonl", ROOT / "eval/gold/v5_tags_pilot_gold_v2.jsonl"]
        for path, tags in zip(paths, [first, second]):
            path.write_text("".join(json.dumps(t, ensure_ascii=False) + "\n" for t in tags))
        a = {(t["reviewId"], t["aspect"]): t for t in first}
        b = {(t["reviewId"], t["aspect"]): t for t in second}
        summary = {
            "sampleSha256": hashlib.sha256(SAMPLE.read_bytes()).hexdigest(),
            "promptSha256": hashlib.sha256((ROOT / "eval/gold/regold_prompt_v1.md").read_bytes()).hexdigest(),
            "reviewCountPerPass": 200, "chunkSize": 5, "chunksPerPass": 40,
            "pass1Tags": len(first), "pass2Tags": len(second),
            "pass1Violations": violations(first), "pass2Violations": errors,
            "added": [b[k] for k in sorted(b.keys() - a.keys())],
            "removed": [a[k] for k in sorted(a.keys() - b.keys())],
            "changed": [{"old": a[k], "new": b[k]} for k in sorted(a.keys() & b.keys()) if a[k] != b[k]],
            "silentReviewIds": [r["reviewId"] for r in pass_rows(2) if not r["aspects"]],
        }
        (AUDIT / "audit.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({k: v for k, v in summary.items() if k not in {"added", "removed", "changed"}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
