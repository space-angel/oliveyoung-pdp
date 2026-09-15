"""
태그 파일 두 개를 (리뷰 × aspect) 단위로 대조한다 (PER-175).

정답셋을 다시 만들었을 때 **무엇이 달라졌는지 사람이 판정**하기 위한 도구다.
`eval/validate_tags.py --against` 는 정밀도·재현율 수치를 내지만 목록을 50개에서 자른다.
이 스크립트는 전부 내고, 판정에 필요한 원문·인용을 함께 붙인다.

  python3 eval/diff_tags.py --old eval/gold/v5_tags_pilot_gold.jsonl \
                            --new eval/gold/v5_tags_pilot_gold_v2.jsonl \
                            --out eval/reports/gold_v1_v2_diff.md

판정하는 것은 사람이다. 이 스크립트는 어느 쪽도 정답으로 치지 않는다.
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
SAMPLE_PATH = ROOT / "eval/gold/v5_tag_pilot_sample.jsonl"


def read(path: Path) -> dict[tuple[int, str], dict]:
    out: dict[tuple[int, str], dict] = {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        t = json.loads(line)
        key = (t["reviewId"], t["aspect"])
        if key in out:
            raise SystemExit(f"{path.name}: 같은 리뷰에 같은 aspect 가 두 번 — {key}")
        out[key] = t
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="태그 파일 두 개를 (리뷰×aspect) 로 대조")
    ap.add_argument("--old", required=True, help="기준 파일 (예: 현 정답셋)")
    ap.add_argument("--new", required=True, help="비교 파일 (예: 새로 만든 정답셋)")
    ap.add_argument("--sample", default=str(SAMPLE_PATH), help="원문 표본")
    ap.add_argument("--context", type=int, default=400, help="원문을 몇 자까지 붙일지")
    ap.add_argument("--out", help="마크다운 출력 경로. 생략하면 stdout")
    a = ap.parse_args()

    old, new = read(Path(a.old)), read(Path(a.new))
    reviews = {}
    for line in Path(a.sample).read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            reviews[r["reviewId"]] = r

    only_old = sorted(set(old) - set(new))
    only_new = sorted(set(new) - set(old))
    both = sorted(set(old) & set(new))
    pol = [k for k in both if old[k]["polarity"] != new[k]["polarity"]]
    snip = [k for k in both if old[k]["snippet"] != new[k]["snippet"]]
    hint = [k for k in both if old[k].get("skinTypeHint") != new[k].get("skinTypeHint")]

    L: list[str] = []
    w = L.append
    w(f"# 정답셋 대조 — `{Path(a.old).name}` vs `{Path(a.new).name}`\n")
    w("| | 태그 |")
    w("|---|---:|")
    w(f"| 기준(old) | {len(old)} |")
    w(f"| 비교(new) | {len(new)} |")
    w(f"| 양쪽 공통 | {len(both)} |")
    w(f"| **old 에만** — 새 정답셋이 뺀 것 | **{len(only_old)}** |")
    w(f"| **new 에만** — 새 정답셋이 더한 것 | **{len(only_new)}** |")
    w(f"| 방향이 바뀐 것 | {len(pol)} |")
    w(f"| 인용만 바뀐 것 | {len(snip)} |")
    w(f"| 피부힌트만 바뀐 것 | {len(hint)} |")
    w("")
    w("**이 표의 어느 줄도 자동으로 채택되지 않는다.** 아래 목록을 사람이 하나씩 본다.\n")

    if only_new:
        w("## 새 정답셋이 더한 태그 — aspect 분포\n")
        w("| aspect | 추가 |")
        w("|---|---:|")
        for asp, n in collections.Counter(k[1] for k in only_new).most_common():
            w(f"| {asp} | {n} |")
        w("")
    if only_old:
        w("## 새 정답셋이 뺀 태그 — aspect 분포\n")
        w("| aspect | 제거 |")
        w("|---|---:|")
        for asp, n in collections.Counter(k[1] for k in only_old).most_common():
            w(f"| {asp} | {n} |")
        w("")

    def block(title: str, keys: list, mode: str) -> None:
        if not keys:
            return
        w(f"## {title} ({len(keys)}건)\n")
        for rid, asp in keys:
            content = reviews.get(rid, {}).get("raw", {}).get("content", "")
            w(f"### `{rid}` · {asp}")
            if mode == "old":
                t = old[(rid, asp)]
                w(f"- old: **{t['polarity']}** — `{t['snippet']}`")
                w("- new: (없음)")
            elif mode == "new":
                t = new[(rid, asp)]
                w("- old: (없음)")
                w(f"- new: **{t['polarity']}** — `{t['snippet']}`")
            else:
                o, n = old[(rid, asp)], new[(rid, asp)]
                w(f"- old: **{o['polarity']}** — `{o['snippet']}` · hint `{o.get('skinTypeHint')}`")
                w(f"- new: **{n['polarity']}** — `{n['snippet']}` · hint `{n.get('skinTypeHint')}`")
            w(f"- 원문: {content[:a.context]}{'…' if len(content) > a.context else ''}")
            w("")

    block("새 정답셋이 뺀 태그", only_old, "old")
    block("새 정답셋이 더한 태그", only_new, "new")
    block("방향이 바뀐 태그", pol, "both")
    block("인용이 바뀐 태그", snip, "both")
    block("피부힌트가 바뀐 태그", hint, "both")

    text = "\n".join(L) + "\n"
    if a.out:
        Path(a.out).write_text(text)
        print(f"[diff] old {len(old)} · new {len(new)} · 뺀 것 {len(only_old)} · 더한 것 {len(only_new)} "
              f"· 방향 {len(pol)} · 인용 {len(snip)} → {a.out}")
    else:
        print(text)


if __name__ == "__main__":
    main()
