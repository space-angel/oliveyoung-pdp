#!/usr/bin/env python3
"""코드북(data/input/skin_codebook.json)이 실제 수집 데이터의 코드를 전부 덮는지 검증한다.

  python crawler/verify_skin_codebook.py [reviews.json ...]

기본 대상은 data/input/reviews_50products.json. 미커버 코드가 있으면 exit 1.
"""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODEBOOK = ROOT / "data/input/skin_codebook.json"
DEFAULT = [ROOT / "data/input/reviews_50products.json"]

FIELDS = {"skinType": False, "skinTone": False, "skinTrouble": True}  # value: is_list


def main(paths):
    book = json.loads(CODEBOOK.read_text(encoding="utf-8"))
    rows = []
    for p in paths:
        rows += json.loads(Path(p).read_text(encoding="utf-8"))

    ok = True
    print(f"코드북: {CODEBOOK.relative_to(ROOT)}  (출처: {book['_meta']['source']}, {book['_meta']['verifiedAt']})")
    print(f"검증 대상: {len(rows)}건\n")

    for field, is_list in FIELDS.items():
        labels = book[field]
        counts = Counter()
        filled = 0
        for r in rows:
            v = r.get(field)
            if is_list:
                v = v or []
                if v:
                    filled += 1
                counts.update(v)
            elif v:
                filled += 1
                counts[v] += 1

        unknown = sorted(c for c in counts if c not in labels)
        unused = sorted(c for c in labels if c not in counts)
        print(f"[{field}] 기재 {filled}/{len(rows)} ({filled / len(rows) * 100:.1f}%) · 관측 코드 {len(counts)} / 코드북 {len(labels)}")
        for code in sorted(counts, key=lambda c: int(c[1:])):
            print(f"  {code} {labels.get(code, '???'):>6}  {counts[code]:6,}건  {counts[code] / len(rows) * 100:5.1f}%")
        if unknown:
            ok = False
            print(f"  ❌ 코드북에 없는 코드: {unknown}")
        if unused:
            print(f"  ⚠️  데이터에 없는 코드: {unused}")
        print()

    print("✅ 전 코드 커버됨" if ok else "❌ 미커버 코드 있음 — 코드북 갱신 필요")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or DEFAULT))
