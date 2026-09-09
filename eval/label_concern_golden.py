"""
주장 골든셋 라벨 도구 (PER-178). 100건을 감당하는 CLI.

라벨러의 작업 순서:

  1. python3 eval/label_concern_golden.py show B01          # 워크시트 (리뷰 전문) 를 만들어 읽는다
  2. python3 eval/label_concern_golden.py add B01           # 대화식으로 claim 1건 입력 → 검증 → 저장
     (또는 JSON 파일을 직접 써서  add B01 --from label.json)
  3. python3 eval/label_concern_golden.py validate          # 라벨 파일 전체 검증 (게이트가 이걸 돌린다)
  4. python3 eval/label_concern_golden.py stats             # 진척·층·소요 시간 (PER-179 의 일정 근거)

## 블라인드 규칙 — 이 도구가 지킨다

워크시트에는 **원문 리뷰와 그 조건 코드만** 나온다. 파이프라인 결과(태그·concern·v4 산출물)는
읽지도 보여주지도 않는다. 별점은 원문 데이터라 보이지만, 방향(`direction`)은 별점이 아니라
근거 문장으로 정한다 — 한 리뷰 안에서 방향이 갈리는 비율이 20.5% 다 (PER-175 실측).

## 파일

  eval/gold/v5_concern_golden_sample.jsonl   번들 (pipeline/sample_concern_golden.py, 고정물)
  eval/gold/v5_concern_golden_labels.jsonl   라벨 — **손으로 만든 것, 재생성되지 않는다. 커밋한다**
  data/intermediate/v5_concern_golden_worksheets/B01.md   워크시트 (재생성 가능)

라벨의 모양과 위반 규칙은 `pipeline/golden_contract.py` 가 소유한다. 이 파일은 입력과 표시만 한다.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

from codebook import load_codebook  # noqa: E402
from contracts import CONDITION_AXES, MISSING_SEGMENT  # noqa: E402
from golden_contract import (  # noqa: E402
    DIRECTIONS,
    EVALUATIONS,
    STANCES,
    GoldenContractError,
    load_failure_taxonomy,
    support_counts,
    validate_label,
    validate_labels,
)
from policy import SUFFICIENCY_N_MIN  # noqa: E402
from sample_concern_golden import LABELS_PATH, PILOT_BUNDLES, SAMPLE_PATH, load_bundles  # noqa: E402
from tag_contract import ASPECTS, fold_invisible  # noqa: E402

WORKSHEET_DIR = ROOT / "data/intermediate/v5_concern_golden_worksheets"


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def load_labels(path: Path = LABELS_PATH) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def append_label(label: dict, path: Path = LABELS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(label, ensure_ascii=False) + "\n")


# --- 워크시트 ---


def _condition_line(review: dict, codebook) -> str:
    parts = []
    st = review["condition"]["skinType"]
    parts.append(f"피부타입 {codebook.label('skinType', st['code'])}({st['code']})" if st["stated"] else f"피부타입 {MISSING_SEGMENT}")
    tr = review["condition"]["skinTrouble"]
    if tr["stated"]:
        parts.append("고민 " + "·".join(f"{codebook.label('skinTrouble', c)}({c})" for c in tr["codes"]))
    else:
        parts.append(f"고민 {MISSING_SEGMENT}")
    op = review["condition"]["option"]
    parts.append(f"옵션 {op['segment']}" if op["stated"] else f"옵션 {MISSING_SEGMENT}")
    return " · ".join(parts)


def render_worksheet(bundle: dict, codebook=None, taxonomy=None) -> str:
    """번들 하나를 사람이 읽는 마크다운으로. 파이프라인 결과는 들어가지 않는다."""
    codebook = codebook or load_codebook()
    taxonomy = taxonomy or load_failure_taxonomy()
    scope = bundle["scope"]
    if scope["axis"]:
        seg = scope["segment"]
        seg_label = seg if scope["axis"] == "option" or seg == MISSING_SEGMENT else f"{codebook.label(scope['axis'], seg)}({seg})"
        scope_line = f"**셀 번들** — `{scope['axis']}` = {seg_label}. 이 번들의 주장은 `condition.{scope['axis']}` 에 이 값을 가져야 한다"
    else:
        scope_line = "**제품 번들** — 제품 전체 주장. 리뷰별 조건을 보고 조건부 주장을 만들 수도 있다 (근거는 그 세그먼트 리뷰만)"

    strata = " · ".join(f"{s['name']} {s['sampled']}/{s['population']}" for s in bundle["strata"])
    lines = [
        f"# {bundle['bundleId']} — {bundle['displayName']}",
        "",
        f"- 카테고리 {bundle['category']} · productId `{bundle['productId']}` · 계보 `{bundle['lineageId']}` · 리뉴얼 `{bundle['renewalPolicy']}` · 단계 {bundle['phase']}",
        f"- {scope_line}",
        f"- 리뷰 {len(bundle['reviews'])}건 / 모집단 {bundle['population']}건 (리센시 통과 · 작성자 1건). 층: {strata}",
        f"- **번들 밖 리뷰를 보지 않는다. 파이프라인 결과·v4 산출물을 보지 않는다.** 방향은 별점이 아니라 문장으로 정한다.",
        f"- 근거 카운트는 고유 작성자 수다. 충분성 절대하한(실험값) N_min={SUFFICIENCY_N_MIN} 은 참고값이고 운영 확정은 PER-186 이다",
        "",
        "## 리뷰",
        "",
    ]
    for i, r in enumerate(bundle["reviews"], 1):
        raw = r["raw"]
        lines.append(
            f"### #{i:02d} · reviewId `{r['reviewId']}` · ★{raw['rating']} · {r['derived']['reviewYearMonth']} · "
            f"{_condition_line(r, codebook)} · 작성자 `{r['derived']['authorKey']}`"
        )
        lines.append("")
        for para in fold_invisible(raw["content"]).split("\n"):
            lines.append(f"> {para}" if para.strip() else ">")
        lines.append("")

    lines += [
        "## 라벨 어휘 (복사용)",
        "",
        f"- aspect (14종, 동결): {' · '.join(ASPECTS)}",
        f"- direction: {' · '.join(DIRECTIONS)}   ·   evidence.stance: {' · '.join(STANCES)}",
        f"- condition 축: {' · '.join(CONDITION_AXES)} — 값은 null(무관) / `{MISSING_SEGMENT}` / 코드",
        f"- failureReasons ({taxonomy.version}): " + " · ".join(f"`{t['key']}`({t['label']}, {t['severity']})" for t in taxonomy.types),
        f"- evaluation: {' · '.join(EVALUATIONS)}",
        "",
        f"입력: `python3 eval/label_concern_golden.py add {bundle['bundleId']}`",
    ]
    return "\n".join(lines) + "\n"


def cmd_show(args) -> None:
    bundles = load_bundles()
    bundle = bundles.get(args.bundle)
    if bundle is None:
        raise SystemExit(f"번들 {args.bundle!r} 이 없다. 있는 번들: {', '.join(sorted(bundles))}")
    text = render_worksheet(bundle)
    WORKSHEET_DIR.mkdir(parents=True, exist_ok=True)
    out = WORKSHEET_DIR / f"{bundle['bundleId']}.md"
    out.write_text(text)
    if args.stdout:
        print(text)
    else:
        print(f"→ {rel(out)}  ({len(bundle['reviews'])}리뷰)")


# --- 대화식 입력 ---


def _prompt(text: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default is not None else ""
    value = input(f"{text}{suffix}: ").strip()
    return value if value else (default or "")


def _parse_condition_value(axis: str, raw: str):
    """빈 값 → null(무관). '미기재' → 미기재 세그먼트. 나머지는 코드 (skinTrouble 은 쉼표 구분)."""
    raw = raw.strip()
    if not raw or raw.lower() == "null":
        return None
    if axis == "skinTrouble":
        return [v.strip() for v in raw.split(",") if v.strip()]
    return raw


def parse_evidence_line(line: str) -> dict:
    """`reviewId | support|oppose | 인용문` 한 줄."""
    parts = [p.strip() for p in line.split("|", 2)]
    if len(parts) != 3:
        raise GoldenContractError(f"근거 줄은 'reviewId | support|oppose | 인용문' 꼴이어야 한다: {line!r}")
    rid, stance, quote = parts
    if not rid.isdigit():
        raise GoldenContractError(f"reviewId 가 정수가 아니다: {rid!r}")
    return {"reviewId": int(rid), "stance": stance, "quote": quote}


def interactive_label(bundle: dict, existing: list[dict]) -> dict:
    n = sum(1 for l in existing if l.get("bundleId") == bundle["bundleId"]) + 1
    print(f"\n[{bundle['bundleId']} {bundle['displayName']}] claim #{n}. 빈 줄은 기본값/생략. Ctrl-C 로 취소.\n")
    label_id = _prompt("labelId", f"{bundle['bundleId']}-{n}")
    print("aspect: " + "  ".join(f"{i + 1}.{a}" for i, a in enumerate(ASPECTS)))
    aspect_raw = _prompt("aspect (번호 또는 이름, 없으면 빈 줄)", "")
    aspect = None
    if aspect_raw:
        aspect = ASPECTS[int(aspect_raw) - 1] if aspect_raw.isdigit() else aspect_raw
    question = _prompt("question (구매자의 질문)")
    answer = _prompt("answer (리뷰가 주는 답)")
    condition = {}
    scope = bundle["scope"]
    for axis in CONDITION_AXES:
        default = ""
        if scope["axis"] == axis:
            default = scope["segment"]
        hint = f"null=무관 / {MISSING_SEGMENT} / 코드" + (" (쉼표 구분)" if axis == "skinTrouble" else "")
        condition[axis] = _parse_condition_value(axis, _prompt(f"condition.{axis} ({hint})", default))
    direction = _prompt(f"direction ({'/'.join(DIRECTIONS)})", "positive")
    print("evidence: 한 줄에 하나, 'reviewId | support|oppose | 원문 인용'. 빈 줄로 끝.")
    evidence = []
    while True:
        line = input(f"  evidence[{len(evidence)}]: ").strip()
        if not line:
            break
        evidence.append(parse_evidence_line(line))
    reasons_raw = _prompt("failureReasons (쉼표 구분, 정상 claim 은 빈 줄)", "")
    reasons = [k.strip() for k in reasons_raw.split(",") if k.strip()]
    evaluation = _prompt(f"evaluation ({'/'.join(EVALUATIONS)})", "complete")
    notes = _prompt("notes", "") or None
    minutes_raw = _prompt("minutesSpent (이 claim 에 쓴 분)")
    minutes = float(minutes_raw) if "." in minutes_raw else int(minutes_raw)
    return {
        "labelId": label_id,
        "bundleId": bundle["bundleId"],
        "productId": bundle["productId"],
        "aspect": aspect,
        "question": question,
        "answer": answer,
        "condition": condition,
        "direction": direction,
        "evidence": evidence,
        "failureReasons": reasons,
        "evaluation": evaluation,
        "notes": notes,
        "minutesSpent": minutes,
        "source": "human",  # CLI 입력은 사람이 직접 만든 것. 후보 검수는 웹 UI 에서 한다
        "candidateId": None,
    }


def cmd_add(args) -> None:
    bundles = load_bundles()
    bundle = bundles.get(args.bundle)
    if bundle is None:
        raise SystemExit(f"번들 {args.bundle!r} 이 없다")
    taxonomy = load_failure_taxonomy()
    existing = load_labels()
    if args.from_file:
        raw = json.loads(Path(args.from_file).read_text())
        raw.setdefault("bundleId", bundle["bundleId"])
        raw.setdefault("productId", bundle["productId"])
        raw.setdefault("source", "human")
        raw.setdefault("candidateId", None)
    else:
        try:
            raw = interactive_label(bundle, existing)
        except KeyboardInterrupt:
            print("\n취소. 저장하지 않았다.")
            return
    # 정렬은 도구가 해준다 — 사람이 severity 순서를 외울 필요는 없다
    raw["failureReasons"] = taxonomy.sort(list(raw.get("failureReasons") or []))
    try:
        label = validate_label(raw, bundle, taxonomy)
        if any(l.get("labelId") == label["labelId"] for l in existing):
            raise GoldenContractError(f"labelId 중복: {label['labelId']!r}")
    except GoldenContractError as e:
        raise SystemExit(f"저장하지 않았다 — 계약 위반:\n  {e}")
    counts = support_counts(label, bundle)
    append_label(label)
    print(
        f"저장 → {rel(LABELS_PATH)}  ({label['labelId']}, {label['direction']}, "
        f"support 작성자 {counts['supportAuthors']} / oppose {counts['opposeAuthors']}, "
        f"failureReasons={label['failureReasons'] or 'none'})"
    )


# --- 검증 · 통계 ---


def cmd_validate(args) -> None:
    bundles = load_bundles()
    labels = load_labels()
    if not labels:
        print(f"OK: 라벨 0건 ({rel(LABELS_PATH)} 없음 또는 비어 있음)")
        return
    try:
        normalized = validate_labels(labels, bundles)
    except GoldenContractError as e:
        raise SystemExit(f"FAIL: 골든셋 라벨 계약 위반\n  {e}")
    # 정규화 결과가 파일과 다르면(공백·null 표기 등) 알려준다 — 조용히 고쳐 쓰지 않는다
    drift = sum(1 for a, b in zip(labels, normalized) if a != b)
    msg = f"OK: 라벨 {len(normalized)}건 계약 통과"
    if drift:
        msg += f" (정규화와 표기가 다른 라벨 {drift}건 — `validate --rewrite` 로 정규 표기로 다시 쓸 수 있다)"
    if args.rewrite and drift:
        LABELS_PATH.write_text("".join(json.dumps(l, ensure_ascii=False) + "\n" for l in normalized))
        msg += f" → 다시 씀 {rel(LABELS_PATH)}"
    print(msg)


def summarize(labels: list[dict], bundles: dict[str, dict]) -> dict:
    """진척과 분포. PER-179 가 요구하는 건당 소요 시간 포함."""
    normalized = validate_labels(labels, bundles) if labels else []
    by_phase = collections.Counter(bundles[l["bundleId"]]["phase"] for l in normalized)
    touched = {l["bundleId"] for l in normalized}
    minutes = [l["minutesSpent"] for l in normalized]
    supports = [support_counts(l, bundles[l["bundleId"]])["supportAuthors"] for l in normalized]
    return {
        "labels": len(normalized),
        "byPhase": dict(by_phase),
        "target": {"pilot": 40, "total": 100},
        "bundlesTouched": len(touched),
        "bundlesTotal": len(bundles),
        "pilotBundlesTouched": len([b for b in touched if bundles[b]["phase"] == "pilot"]),
        "pilotBundles": PILOT_BUNDLES,
        "byDirection": dict(collections.Counter(l["direction"] for l in normalized)),
        "byScopeKind": dict(collections.Counter(bundles[l["bundleId"]]["scope"]["kind"] for l in normalized)),
        "byCategory": dict(collections.Counter(bundles[l["bundleId"]]["category"] for l in normalized)),
        "byAspect": dict(collections.Counter(l["aspect"] or "(없음)" for l in normalized)),
        "conditional": sum(1 for l in normalized if any(v is not None for v in l["condition"].values())),
        "withFailure": sum(1 for l in normalized if l["failureReasons"]),
        "byFailureReason": dict(collections.Counter(k for l in normalized for k in l["failureReasons"])),
        "notEvaluable": sum(1 for l in normalized if l["evaluation"] == "not_evaluable"),
        "bySource": dict(collections.Counter(l["source"] for l in normalized)),
        # 재현율 보존 장치 (B안): 후보 결정만 있고 사람이 만든 claim 이 0인 번들
        "bundlesWithoutHumanLabel": sorted(
            b for b in touched
            if not any(l["source"] == "human" for l in normalized if l["bundleId"] == b)
        ),
        "supportAuthors": {
            "min": min(supports) if supports else None,
            "median": sorted(supports)[len(supports) // 2] if supports else None,
            "belowNmin": sum(1 for s in supports if s < SUFFICIENCY_N_MIN),
            "nMin": SUFFICIENCY_N_MIN,
        },
        "minutesPerLabel": {
            "total": sum(minutes),
            "mean": round(sum(minutes) / len(minutes), 1) if minutes else None,
            "median": sorted(minutes)[len(minutes) // 2] if minutes else None,
        },
    }


def cmd_stats(args) -> None:
    bundles = load_bundles()
    labels = load_labels()
    try:
        s = summarize(labels, bundles)
    except GoldenContractError as e:
        raise SystemExit(f"FAIL: 통계 전에 계약 위반부터 고쳐라\n  {e}")
    if args.json:
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return
    print(f"라벨 {s['labels']}건  (파일럿 {s['byPhase'].get('pilot', 0)}/{s['target']['pilot']} · 전체 목표 {s['target']['total']})")
    print(f"번들 {s['bundlesTouched']}/{s['bundlesTotal']} 착수 (파일럿 번들 {s['pilotBundlesTouched']}/{s['pilotBundles']})")
    print(f"방향 {s['byDirection']} · 조건부 {s['conditional']} · 실패 라벨 {s['withFailure']} {s['byFailureReason']} · 평가불가 {s['notEvaluable']}")
    print(f"출처 {s['bySource']} · 사람 claim 없는 번들 {len(s['bundlesWithoutHumanLabel'])} {s['bundlesWithoutHumanLabel'] or ''}")
    print(f"종류 {s['byScopeKind']} · 카테고리 {s['byCategory']}")
    print(f"aspect {s['byAspect']}")
    sa = s["supportAuthors"]
    print(f"support 고유 작성자 min {sa['min']} / 중앙 {sa['median']} · N_min({sa['nMin']}) 미만 {sa['belowNmin']}건")
    m = s["minutesPerLabel"]
    if m["mean"] is not None:
        remaining = max(0, s["target"]["total"] - s["labels"])
        print(f"소요 시간 건당 평균 {m['mean']}분 / 중앙 {m['median']}분 · 누적 {m['total']}분 · 남은 {remaining}건 추정 {round(remaining * m['mean'])}분")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("show", help="번들 워크시트를 만든다")
    p.add_argument("bundle")
    p.add_argument("--stdout", action="store_true", help="파일 대신 표준출력")
    p.set_defaults(fn=cmd_show)

    p = sub.add_parser("add", help="claim 1건을 입력·검증·저장")
    p.add_argument("bundle")
    p.add_argument("--from", dest="from_file", help="대화식 대신 JSON 파일에서 읽는다")
    p.set_defaults(fn=cmd_add)

    p = sub.add_parser("validate", help="라벨 파일 전체 검증 (게이트)")
    p.add_argument("--rewrite", action="store_true", help="정규 표기로 다시 쓴다")
    p.set_defaults(fn=cmd_validate)

    p = sub.add_parser("stats", help="진척·분포·소요 시간")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_stats)

    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
