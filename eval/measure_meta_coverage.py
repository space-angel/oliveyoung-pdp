"""지금 커밋된 리포트들이 재현 meta 계약을 얼마나 만족하는가 (PER-193).

계약(`pipeline/run_meta.py`)만 만들고 끝내면 "잘 만들었다"는 말밖에 할 게 없다. 그래서
**이미 커밋된 리포트 전부**를 그 계약으로 훑어서, 어느 리포트가 어느 축을 빠뜨렸는지를
센다. 이 표가 곧 PER-201 이 "무엇을 바꿔서 좋아졌는가"를 귀속할 수 있는지의 현재 상태다.

**여기서 리포트를 고치지 않는다.** 채우는 것은 각 이슈의 몫이고 이 이슈는 계약과 현황까지다
(2026-09-14 이슈 코멘트의 범위 축소). 낮게 나와도 그대로 적는다 — 빈칸을 지금 메우면
"원래 있었다"가 되고, 그러면 무엇을 고쳐야 하는지가 사라진다.

## 어떻게 세는가

리포트마다 JSON 트리를 전부 훑어 **축별 증거 키**를 찾는다. 위치는 리포트마다 다르므로
(`source` · `policy` · `meta` · 최상위) 경로를 고정하지 않고 키 이름으로 찾고, 찾은
위치를 함께 적는다. 값이 비었거나 자리표시자면 없는 것으로 센다 — PER-193 계약이
`''`·`0`·`'unknown'` 을 값으로 치지 않는 것과 같은 기준이다.

느슨하게 세는 쪽을 골랐다: `modelId` 는 `model`·`tagsModel` 도 인정하고, `inputs` 는
64자 sha256 이 하나라도 있으면 인정한다. **현황을 실제보다 나쁘게 보이게 만들지 않기
위해서다** — 그렇게 세고도 낮으면 그 수치는 방법론 탓이 아니다.

사용:
  .venv/bin/python eval/measure_meta_coverage.py
  .venv/bin/python eval/measure_meta_coverage.py --check
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

from run_meta import (  # noqa: E402
    COVERAGE_FIELDS,
    RUN_META_SCHEMA_VERSION,
    UNAVAILABLE,
    embedding_fields,
    failure_taxonomy_version,
    file_input,
    prompt_ref,
    stamp,
    validate,
)

REPORTS_DIR = ROOT / "eval/reports"
REPORT_PATH = REPORTS_DIR / "meta_coverage_per193.json"
SELF = REPORT_PATH.name

#: 이슈 리포트 — 파일명이 `_perNNN.json` 인 것. PER-201 의 귀속 대상이 이 묶음이다.
ISSUE_RE = re.compile(r"_per\d+\.json$")

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PLACEHOLDER = frozenset({"", "unknown", "n/a", "na", "none", "null", "tbd", "-", "?"})

#: 축별 증거 키. 값은 (키 이름 후보, 판정 함수 이름).
EVIDENCE_KEYS = {
    "promptVersion": ("promptVersion", "prompt", "promptSha256", "promptPath"),
    "modelId": ("modelId", "model", "tagsModel", "judgeModel", "taggerModel"),
    "policy": ("policy", "thresholds", "sufficiency"),
    "seed": ("seed", "randomSeed"),
    "inputs": ("sha256", "tagsSha256", "goldenLabelsSha256", "inputSha256",
               "taggingInputSha256", "aspectsSha256", "sourceSha256"),
    "failureTaxonomyVersion": ("failureTaxonomyVersion",),
    "embeddingModel": ("embeddingModel",),
    "embeddingVersion": ("embeddingVersion",),
    "vocabVersion": ("vocabVersion",),
}


def is_value(v) -> bool:
    """비었거나 자리표시자면 값이 아니다 — 계약과 같은 기준."""
    if v is None or v is False:
        return False
    if isinstance(v, str):
        return v.strip().lower() not in _PLACEHOLDER
    if isinstance(v, (dict, list)):
        return len(v) > 0
    if isinstance(v, bool):
        return False
    return True


def walk(node, path="$"):
    """(경로, 키, 값) 전부. 리스트 인덱스도 경로에 남긴다."""
    if isinstance(node, dict):
        for k, v in node.items():
            child = f"{path}.{k}"
            yield child, k, v
            yield from walk(v, child)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            child = f"{path}[{i}]"
            yield from walk(v, child)


def scan(doc) -> dict:
    """리포트 1건의 축별 충족. 찾은 위치를 함께 남긴다 (사전순 첫 위치)."""
    hits: dict[str, list[str]] = {f: [] for f in COVERAGE_FIELDS}
    for path, key, value in walk(doc):
        for field, keys in EVIDENCE_KEYS.items():
            if key not in keys or not is_value(value):
                continue
            # `prompt` 는 파일 해시를 든 객체일 때만 프롬프트 버전으로 친다.
            if field == "promptVersion" and key == "prompt":
                if not (isinstance(value, dict) and value.get("sha256")):
                    continue
            # 해시로 치려면 실제 sha256 모양이어야 한다. 'sha256' 키에 숫자가 들어 있는
            # 경우까지 입력 스냅샷으로 세지 않는다.
            if field == "inputs" and not (
                    isinstance(value, str) and _SHA256.match(value)):
                continue
            if field == "modelId" and not isinstance(value, (str, dict, list)):
                continue
            hits[field].append(path)
    return {f: sorted(paths) for f, paths in hits.items()}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="리포트가 재현되는지만 확인하고 쓰지 않는다")
    args = ap.parse_args()

    files = sorted(p for p in REPORTS_DIR.glob("*.json") if p.name != SELF)
    if not files:
        raise SystemExit(f"FAIL: 훑을 리포트가 없다 ({REPORTS_DIR.relative_to(ROOT)})")

    rows = []
    for path in files:
        try:
            doc = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            raise SystemExit(f"FAIL: {path.relative_to(ROOT)} 를 읽을 수 없다: {e}")
        hits = scan(doc)
        present = [f for f in COVERAGE_FIELDS if hits[f]]
        rows.append({
            "report": path.relative_to(ROOT).as_posix(),
            "isIssueReport": bool(ISSUE_RE.search(path.name)),
            "present": present,
            "missing": [f for f in COVERAGE_FIELDS if not hits[f]],
            "satisfied": len(present),
            "foundAt": {f: hits[f][0] for f in COVERAGE_FIELDS if hits[f]},
        })

    issue_rows = [r for r in rows if r["isIssueReport"]]

    def tally(subset):
        return {
            "reports": len(subset),
            "byField": {
                f: {
                    "present": sum(1 for r in subset if f in r["present"]),
                    "missing": sum(1 for r in subset if f in r["missing"]),
                } for f in COVERAGE_FIELDS
            },
            "fullySatisfying": sum(1 for r in subset
                                   if len(r["present"]) == len(COVERAGE_FIELDS)),
            "satisfiedHistogram": {
                str(n): sum(1 for r in subset if r["satisfied"] == n)
                for n in range(len(COVERAGE_FIELDS) + 1)
                if any(r["satisfied"] == n for r in subset)
            },
        }

    # 계약이 실제로 만들어지는지 — PER-191 이 복사해 쓸 기준 meta.
    reference = stamp(
        stage="measure:meta-coverage",
        policy={
            "issue": "PER-193",
            "coverageFields": list(COVERAGE_FIELDS),
            "note": "이 실측 자체에는 임계값이 없다. policy 는 무엇을 축으로 셌는지를 적는다",
        },
        inputs=[
            file_input("reviews", ROOT / "data/input/reviews_50products.json"),
            file_input("catalog", ROOT / "data/input/product_catalog.json"),
            file_input("codebook", ROOT / "data/input/skin_codebook.json"),
        ],
        model_id=UNAVAILABLE,
        prompt=UNAVAILABLE,
        seed=UNAVAILABLE,
        failure_taxonomy=failure_taxonomy_version(),
        embedding=embedding_fields(),
        unavailable={
            "modelId": "이 실측은 LLM 을 부르지 않는다 — 커밋된 JSON 만 읽는다",
            "promptVersion": "프롬프트가 없는 단계다",
            "seed": "결정적 단계 — 난수를 쓰지 않는다. 0 으로 깔지 않는다 (PER-193)",
        },
    )
    validate(reference)

    report = {
        "issue": "PER-193",
        "schemaVersion": RUN_META_SCHEMA_VERSION,
        "scope": "커밋된 eval/reports/*.json 이 재현 meta 계약을 얼마나 만족하는가. "
                 "**여기서 리포트를 고치지 않는다** — 채우는 것은 각 이슈의 몫이다 "
                 "(2026-09-14 이슈 코멘트의 범위 축소)",
        "method": {
            "fields": list(COVERAGE_FIELDS),
            "evidenceKeys": {k: list(v) for k, v in EVIDENCE_KEYS.items()},
            "note": "위치를 고정하지 않고 키 이름으로 JSON 트리를 훑는다 — 지금 리포트들은 "
                    "source·policy·최상위에 제각각 적고 있고 meta 라는 자리가 없다. "
                    "느슨한 쪽으로 셌다(model·tagsModel 도 modelId 로 인정, sha256 이 "
                    "하나라도 있으면 inputs 로 인정). 그렇게 세고도 낮으면 방법론 탓이 아니다",
            "notCounted": "digestKind — 지금 리포트 중 해시 종류를 적은 것은 0건이다. "
                          "전부 sha256 이라 암묵적으로 맞지만 meta 가 그렇다고 말하지는 않는다",
        },
        "referenceMeta": reference,
        "summary": {
            "all": tally(rows),
            "issueReports": tally(issue_rows),
        },
        "reports": rows,
        "todo": {
            "note": "리포트별로 무엇을 더 적어야 계약을 만족하는가. 이 목록은 각 이슈로 "
                    "돌린다 — 여기서 고치면 '원래 있었다'가 되고 무엇을 고쳐야 하는지가 사라진다",
            "byReport": {r["report"]: r["missing"] for r in issue_rows if r["missing"]},
        },
        "limits": [
            "키 이름으로 찾으므로 **있는데 다른 이름으로 적은 것**은 놓친다. 반대로 축과 "
            "무관한 곳에 우연히 같은 키가 있으면 과대평가한다 — foundAt 의 경로가 그 판단의 근거다",
            "`present` 는 '그 축을 어딘가 적었다'는 뜻이지 '계약을 통과한다'가 아니다. "
            "run_meta.validate 를 통과하는 리포트는 **0건**이다 — meta 라는 자리 자체가 없다",
            "리포트 파일 해시를 여기 담지 않았다. 담으면 다른 이슈가 리포트를 고칠 때마다 "
            "이 --check 가 깨져 그 이슈의 게이트를 막는다. 대신 파일 목록과 축 충족이 "
            "바뀌면 깨진다 — 새 리포트가 들어오면 다시 돌려야 한다",
            "eval_report_v4*.json 은 v4 산출물이라 v5 계약을 만족할 이유가 없다. 분모에 "
            "들어 있지만 issueReports 묶음에는 빠진다 (파일명이 _perNNN 이 아니다)",
        ],
    }

    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.check:
        if not REPORT_PATH.exists():
            raise SystemExit(f"FAIL: 리포트가 없다 ({REPORT_PATH.relative_to(ROOT)})")
        if REPORT_PATH.read_text() != payload:
            raise SystemExit(
                f"FAIL: meta 충족 실측이 재현되지 않는다 ({REPORT_PATH.relative_to(ROOT)})\n"
                "  → 리포트가 늘거나 meta 필드가 바뀌었다. 다시 생성해 함께 커밋한다")
        print(f"OK: meta 충족 실측 재현 일치 ({REPORT_PATH.relative_to(ROOT)})")
        return

    REPORT_PATH.write_text(payload)
    a, i = report["summary"]["all"], report["summary"]["issueReports"]
    print(f"[대상] 리포트 {a['reports']}건 (이슈 리포트 {i['reports']}건) "
          f"· 축 {len(COVERAGE_FIELDS)}개")
    print(f"[전체 충족] {a['fullySatisfying']}건 / {a['reports']}건")
    for f in COVERAGE_FIELDS:
        b = i["byField"][f]
        print(f"   {f:<24} 이슈 리포트 {b['present']:>2}/{i['reports']}")
    print(f"→ {REPORT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
