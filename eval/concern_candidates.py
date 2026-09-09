"""
주장 골든셋 — 모델 후보 하네스 (PER-178, B안).

라벨러가 제로베이스에서 claim 을 짜내는 데 건당 30분이 걸렸다(B01 실측). 그래서 **1차 후보는 모델이
만들고 사람이 채택·수정·기각**한다. 단, 골든셋이 필요했던 이유를 지키기 위해 세 가지를 강제한다.

  출처 기록     라벨의 source / candidateId (golden_contract). judge 일치율·v4 비교를 출처별로 갈라 본다
  다른 모델     후보를 만든 모델 이름을 후보 파일에 남긴다. 파이프라인 생성기(PER-189)·judge(PER-196)는
               같은 모델·프롬프트를 쓰지 않는다 — 같으면 순환이다
  재현율 보존   번들마다 후보에 없는 claim 을 사람이 1개 이상 찾는다(source=human). stats 가 센다

API 를 부르지 않는다. 사람이 하네스 파일을 다른 모델(구독제)에 붙여 넣고, 출력 JSON 을 여기로 들여온다.

  export   번들마다 하네스 .md (지시문 + 리뷰 + 출력 규격)  → data/intermediate/v5_concern_golden_harness/B01.md
  import   모델 출력 JSON 을 검증해 후보 파일에 추가         → eval/gold/v5_concern_golden_candidates.jsonl (커밋)
  status   번들별 후보 수 · 계약 통과 수 · 사람 결정 수

사용:
  python3 eval/concern_candidates.py export --phase pilot          # B01~B16
  python3 eval/concern_candidates.py import B01 out.json --model "GPT-5 (ChatGPT, 2026-09)"
  python3 eval/concern_candidates.py status

후보 파일은 커밋한다 — 사람의 채택/기각 결정이 candidateId 로 이 파일을 가리키므로 고정물이다.
후보 자체는 정답이 아니다. 정답은 사람이 검수한 라벨(v5_concern_golden_labels.jsonl)이다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))
sys.path.insert(0, str(Path(__file__).parent))

from codebook import load_codebook  # noqa: E402
from contracts import MISSING_SEGMENT  # noqa: E402
from golden_contract import GoldenContractError, load_failure_taxonomy, validate_label  # noqa: E402
from label_concern_golden import _condition_line, load_labels  # noqa: E402
from sample_concern_golden import load_bundles  # noqa: E402
from tag_contract import fold_invisible  # noqa: E402

PROMPT_PATH = Path(__file__).parent / "prompts/concern_candidates_v2.md"  # v2: stance 절대값
HARNESS_DIR = ROOT / "data/intermediate/v5_concern_golden_harness"
RAW_DIR = ROOT / "data/intermediate/v5_concern_golden_candidates_raw"  # 다른 모델이 B01.json 을 여기에 쓴다
CANDIDATES_PATH = ROOT / "eval/gold/v5_concern_golden_candidates.jsonl"
CANDIDATE_FIELDS = ("aspect", "question", "answer", "condition", "direction", "evidence")


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- export ---


def render_harness(bundle: dict, prompt_template: str, codebook=None) -> str:
    """번들 하나 → 다른 모델에 통째로 붙여 넣는 텍스트. 파이프라인 결과는 들어가지 않는다."""
    codebook = codebook or load_codebook()
    scope = bundle["scope"]
    if scope["axis"]:
        seg = scope["segment"]
        seg_label = seg if scope["axis"] == "option" or seg == MISSING_SEGMENT else f"{codebook.label(scope['axis'], seg)}({seg})"
        scope_line = (
            f"**셀 번들** — `{scope['axis']}` = {seg_label}. 모든 후보의 `condition.{scope['axis']}` 는 "
            f"`{seg}` 이어야 합니다" + (" (skinTrouble 은 배열 안에 포함)" if scope["axis"] == "skinTrouble" else "")
        )
    else:
        scope_line = "**제품 번들** — 제품 전체 주장. 리뷰별 조건을 보고 조건부 주장을 만들 수도 있습니다 (근거는 그 세그먼트 리뷰만)"
    lines = [
        prompt_template.replace("{{BUNDLE_ID}}", bundle["bundleId"]).rstrip(),
        "",
        "---",
        "",
        f"# 리뷰 묶음 {bundle['bundleId']} — {bundle['displayName']}",
        "",
        f"- 카테고리 {bundle['category']} · 리뷰 {len(bundle['reviews'])}건",
        f"- {scope_line}",
        "- 리뷰 머리의 코드: 피부타입 A01 지성 · A02 건성 · A03 복합성 · A04 민감성 · A05 약건성 · A06 트러블성 · A07 중성 / "
        "고민 C01 잡티 · C02 미백 · C03 주름 · C04 각질 · C05 트러블 · C06 블랙헤드 · C07 피지과다 · C08 민감성 · C09 모공 · C10 탄력 · C11 붉은기 · C12 가려움 · C13 다크서클",
        "",
    ]
    for i, r in enumerate(bundle["reviews"], 1):
        lines.append(f"## [{r['reviewId']}] #{i:02d} · ★{r['raw']['rating']} · {_condition_line(r, codebook)}")
        lines.append("")
        lines.append(fold_invisible(r["raw"]["content"]).strip())
        lines.append("")
    return "\n".join(lines) + "\n"


def cmd_export(args) -> None:
    bundles = load_bundles()
    prompt = PROMPT_PATH.read_text()
    HARNESS_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    chosen = [b for b in bundles.values() if args.phase == "all" or b["phase"] == args.phase]
    if args.bundle:
        chosen = [bundles[args.bundle]]
    for b in chosen:
        (HARNESS_DIR / f"{b['bundleId']}.md").write_text(render_harness(b, prompt))
    readme = HARNESS_DIR / "README.md"
    readme.write_text(
        "# 후보 하네스 — 쓰는 법\n\n"
        "1. `B01.md` 를 통째로 복사해 모델에 붙여 넣는다 (지시문 + 리뷰 + 출력 규격이 한 파일).\n"
        "2. 모델이 낸 JSON 을 `B01.json` 으로 저장한다 (코드 블록 표시가 섞여도 임포터가 벗겨낸다).\n"
        f"3. `python3 eval/concern_candidates.py import B01 B01.json --model \"<모델 이름과 날짜>\"`\n"
        "4. 라벨 도구(웹)에서 후보를 채택·수정·기각한다. 번들마다 후보에 없는 claim 도 1개 이상 직접 만든다.\n\n"
        "모델(에이전트)이 파일 시스템에서 직접 작업하는 경우의 지침: `eval/gold/CANDIDATE_HARNESS_INSTRUCTIONS.md`. "
        f"출력은 `{rel(RAW_DIR)}/Bxx.json`, 들여오기는 `import-dir`.\n\n"
        f"프롬프트 정본: `{rel(PROMPT_PATH)}` (sha256 {sha256_text(prompt)[:12]}…). 프롬프트를 고치면 v2 로 파일을 새로 만든다.\n"
        "모델은 파이프라인 생성기(PER-189)·judge(PER-196)와 **다른 것**을 쓴다.\n"
    )
    print(f"[하네스] {len(chosen)}개 → {rel(HARNESS_DIR)}/  (프롬프트 {rel(PROMPT_PATH)} sha256 {sha256_text(prompt)[:12]})")
    for b in chosen:
        print(f"  {b['bundleId']}  {b['scope']['kind']:10s} {b['displayName']}  ({len(b['reviews'])}리뷰)")


# --- import ---


def parse_model_output(text: str) -> dict:
    """모델 출력에서 JSON 을 꺼낸다. 코드 블록·앞뒤 설명은 벗겨낸다. JSON 이 아니면 에러."""
    stripped = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", stripped, re.S)
    if fence:
        stripped = fence.group(1)
    start, end = stripped.find("{"), stripped.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("출력에 JSON 객체가 없다")
    data = json.loads(stripped[start : end + 1])
    if not isinstance(data.get("candidates"), list):
        raise ValueError("JSON 에 candidates 배열이 없다")
    return data


def load_candidates(path: Path = CANDIDATES_PATH) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def check_candidate(candidate: dict, bundle: dict, candidate_id: str, taxonomy) -> list[str]:
    """후보를 계약에 비춰 본다. 위반은 버리지 않고 **기록**한다 — 사람이 고치거나 기각 사유로 쓴다."""
    probe = {
        "labelId": candidate_id,
        "bundleId": bundle["bundleId"],
        "productId": bundle["productId"],
        "aspect": candidate.get("aspect"),
        "question": candidate.get("question"),
        "answer": candidate.get("answer"),
        "condition": candidate.get("condition"),
        "direction": candidate.get("direction"),
        "evidence": candidate.get("evidence"),
        "failureReasons": [],
        "evaluation": "complete",
        "notes": None,
        "minutesSpent": 1,
        "source": "candidate_accepted",
        "candidateId": candidate_id,
    }
    try:
        validate_label(probe, bundle, taxonomy)
        return []
    except GoldenContractError as e:
        return [str(e)]


# --- 사람 앞에 두는 규칙 점검 (토션 "규칙 기반 필터" 의 사상) ---

GENERIC_QUESTION_WORDS = ("좋은가요", "좋나요", "추천", "괜찮나요", "어떤가요", "만족")
MIN_SUPPORT_AUTHORS = 2


def auto_checks(candidate: dict, bundle: dict, siblings: list[dict]) -> list[dict]:
    """후보 하나에 대한 결정적 점검. **판정이 아니라 주의 표시**다 — 사람의 명시적 결정이 항상 우선한다.

    각 항목: {"key", "level": warn|info, "text"}. 계약 위반(contractErrors)은 따로 있다.
    """
    out: list[dict] = []
    authors = {r["reviewId"]: r["derived"]["authorKey"] for r in bundle["reviews"]}
    contents = {r["reviewId"]: fold_invisible(r["raw"]["content"]) for r in bundle["reviews"]}
    ev = candidate.get("evidence") or []
    pos = {authors.get(e.get("reviewId")) for e in ev if e.get("stance") == "positive"} - {None}
    neg = {authors.get(e.get("reviewId")) for e in ev if e.get("stance") == "negative"} - {None}
    if len(pos | neg) < MIN_SUPPORT_AUTHORS:
        out.append({"key": "thin_evidence", "level": "warn", "text": f"근거 고유 작성자 {len(pos | neg)}명 — 놓친 근거를 보태거나 과소 근거로 볼지 판단"})
    direction = candidate.get("direction")
    if pos and neg and min(len(pos), len(neg)) == 1 and max(len(pos), len(neg)) >= 3:
        out.append({"key": "mixed_single_dissent", "level": "info", "text": "한쪽 1명 — mixed 로 저장되되 소수 의견의 처리는 게이트(PER-186)가 정한다"})
    generalizers = ("대부분", "대체로", "거의", "보통", "다들", "모두", "누구나", "없다는 편", "문제 없", "문제없", "안 생긴다", "생기지 않는다")
    ans = candidate.get("answer") or ""
    if any(w in ans for w in generalizers) and len(pos | neg) < 5:
        out.append({"key": "generalizes_from_silence", "level": "warn",
                    "text": f"답에 일반화 표현이 있는데 언급한 작성자는 {len(pos | neg)}명 — 언급 없음(침묵)을 근거로 세면 unsupported_claim"})
    q = (candidate.get("question") or "").strip()
    if len(q) < 14 or any(w in q for w in GENERIC_QUESTION_WORDS):
        out.append({"key": "question_broad", "level": "warn", "text": "질문이 짧거나 일반적 — 무엇을 재는지 없으면 overbroad_question"})
    # 답의 숫자가 인용 어디에도 없으면 창작 의심
    import re as _re
    nums = set(_re.findall(r"\d+", candidate.get("answer") or ""))
    if nums:
        quoted = " ".join(e.get("quote") or "" for e in ev)
        missing = sorted(n for n in nums if n not in quoted)
        if missing:
            out.append({"key": "number_not_in_quotes", "level": "warn", "text": f"답의 숫자 {missing} 가 인용에 없다 — unsupported_claim 의심"})
    # 번들 안 같은 aspect+direction 후보
    twins = [c["candidateId"] for c in siblings if c["candidate"].get("aspect") == candidate.get("aspect") and c["candidate"].get("direction") == direction]
    if len(twins) > 1:
        out.append({"key": "sibling_same_topic", "level": "info", "text": f"같은 aspect·방향 후보 {len(twins)}개 ({', '.join(twins)}) — 답이 같으면 duplicate_claim"})
    # 인용이 리뷰 전체의 절반 이상이면 근거가 아니라 통째 복사
    for e in ev:
        c = contents.get(e.get("reviewId"))
        if c and e.get("quote") and len(e["quote"]) > max(80, len(c) * 0.6):
            out.append({"key": "quote_too_long", "level": "info", "text": f"reviewId {e['reviewId']} 인용이 본문의 대부분 — 핵심 문장으로 줄일지"})
            break
    if candidate.get("aspect") is None:
        out.append({"key": "aspect_null", "level": "info", "text": "aspect 없음 — 14종 밖 주제면 그대로 두고 notes 에 주제를 남긴다"})
    return out


def import_candidates(bundle_id: str, text: str, model: str, replace: bool = False) -> list[dict]:
    bundles = load_bundles()
    if bundle_id not in bundles:
        raise SystemExit(f"번들 {bundle_id!r} 이 없다")
    bundle = bundles[bundle_id]
    data = parse_model_output(text)
    if data.get("bundleId") not in (None, bundle_id):
        raise SystemExit(f"출력의 bundleId {data.get('bundleId')!r} 가 {bundle_id} 와 다르다 — 다른 번들의 출력을 넣었다")

    existing = load_candidates()
    already = [c for c in existing if c["bundleId"] == bundle_id]
    if already and not replace:
        raise SystemExit(
            f"{bundle_id} 에 후보 {len(already)}개가 이미 있다. 다시 넣으려면 --replace (사람 결정이 있으면 candidateId 가 어긋날 수 있다)"
        )
    decided = {l["candidateId"] for l in load_labels() if l.get("candidateId")}
    if replace and any(c["candidateId"] in decided for c in already):
        raise SystemExit(f"{bundle_id} 후보에 이미 사람 결정(라벨)이 붙어 있다. 라벨을 먼저 지우지 않으면 교체할 수 없다")

    taxonomy = load_failure_taxonomy()
    prompt = PROMPT_PATH.read_text()
    rows = []
    for i, cand in enumerate(data["candidates"], 1):
        if not isinstance(cand, dict):
            raise SystemExit(f"candidates[{i - 1}] 가 객체가 아니다")
        cid = f"{bundle_id}-c{i}"
        body = {k: cand.get(k) for k in CANDIDATE_FIELDS}
        rows.append(
            {
                "candidateId": cid,
                "bundleId": bundle_id,
                "productId": bundle["productId"],
                "model": model,
                "promptSha256": sha256_text(prompt),
                "candidate": body,
                "note": (cand.get("note") or "").strip() or None,
                "contractErrors": check_candidate(body, bundle, cid, taxonomy),
            }
        )
    kept = [c for c in existing if c["bundleId"] != bundle_id] + rows
    CANDIDATES_PATH.parent.mkdir(parents=True, exist_ok=True)
    CANDIDATES_PATH.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in kept))
    return rows


def cmd_import(args) -> None:
    text = Path(args.file).read_text()
    rows = import_candidates(args.bundle, text, args.model, replace=args.replace)
    ok = sum(1 for r in rows if not r["contractErrors"])
    print(f"[후보] {args.bundle}: {len(rows)}개 들여옴, 계약 통과 {ok} / 위반 {len(rows) - ok} → {rel(CANDIDATES_PATH)}")
    for r in rows:
        flag = "OK " if not r["contractErrors"] else "!! "
        q = (r["candidate"].get("question") or "")[:60]
        print(f"  {flag}{r['candidateId']}  {r['candidate'].get('aspect') or '-':8s} {r['candidate'].get('direction') or '-':8s} {q}")
        for e in r["contractErrors"]:
            print(f"       {e}")


def status(bundles: dict[str, dict] | None = None) -> list[dict]:
    bundles = bundles or load_bundles()
    cands = load_candidates()
    labels = load_labels()
    decided = {l["candidateId"] for l in labels if l.get("candidateId")}
    out = []
    for b in bundles.values():
        mine = [c for c in cands if c["bundleId"] == b["bundleId"]]
        out.append(
            {
                "bundleId": b["bundleId"],
                "phase": b["phase"],
                "candidates": len(mine),
                "valid": sum(1 for c in mine if not c["contractErrors"]),
                "decided": sum(1 for c in mine if c["candidateId"] in decided),
                "humanLabels": sum(1 for l in labels if l["bundleId"] == b["bundleId"] and l["source"] == "human"),
                "models": sorted({c["model"] for c in mine}),
            }
        )
    return out


def cmd_import_dir(args) -> None:
    """RAW_DIR(또는 지정 폴더)의 B??.json 을 전부 들여온다. 이미 있는 번들은 건너뛴다(--replace 없이)."""
    folder = Path(args.dir) if args.dir else RAW_DIR
    files = sorted(folder.glob("B[0-9][0-9].json"))
    if not files:
        raise SystemExit(f"{rel(folder)} 에 B??.json 이 없다")
    have = {c["bundleId"] for c in load_candidates()}
    done = skipped = 0
    for f in files:
        bid = f.stem
        if bid in have and not args.replace:
            skipped += 1
            continue
        try:
            rows = import_candidates(bid, f.read_text(), args.model, replace=args.replace)
        except (SystemExit, ValueError, json.JSONDecodeError) as e:
            print(f"  !! {bid}: {e}")
            continue
        ok = sum(1 for r in rows if not r["contractErrors"])
        print(f"  {bid}: {len(rows)}개 (계약 통과 {ok})")
        done += 1
    print(f"[후보] 들여옴 {done} · 건너뜀(이미 있음) {skipped} → {rel(CANDIDATES_PATH)}")


def cmd_status(args) -> None:
    rows = status()
    with_c = [r for r in rows if r["candidates"]]
    print(f"후보 있는 번들 {len(with_c)}/{len(rows)} · 후보 {sum(r['candidates'] for r in rows)} (계약 통과 {sum(r['valid'] for r in rows)}) · 결정 {sum(r['decided'] for r in rows)}")
    for r in rows:
        if r["candidates"] or r["humanLabels"]:
            warn = "  ← 사람 claim 0" if r["decided"] and not r["humanLabels"] else ""
            print(f"  {r['bundleId']} {r['phase']:5s} 후보 {r['candidates']:2d} (통과 {r['valid']:2d}) 결정 {r['decided']:2d} 사람 {r['humanLabels']:2d}  {','.join(r['models'])}{warn}")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("export")
    p.add_argument("--phase", choices=["pilot", "main", "all"], default="pilot")
    p.add_argument("--bundle")
    p.set_defaults(fn=cmd_export)
    p = sub.add_parser("import")
    p.add_argument("bundle")
    p.add_argument("file")
    p.add_argument("--model", required=True, help="후보를 만든 모델 이름과 날짜 — 생성기·judge 와 달라야 한다")
    p.add_argument("--replace", action="store_true")
    p.set_defaults(fn=cmd_import)
    p = sub.add_parser("import-dir", help="폴더의 B??.json 을 전부 들여온다")
    p.add_argument("--dir", help=f"기본 {RAW_DIR.relative_to(ROOT)}")
    p.add_argument("--model", required=True)
    p.add_argument("--replace", action="store_true")
    p.set_defaults(fn=cmd_import_dir)
    p = sub.add_parser("status")
    p.set_defaults(fn=cmd_status)
    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
