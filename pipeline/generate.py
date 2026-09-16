"""question–answer 쌍 생성 (PER-191).

PRD §6 — *"질문만 만들면 사용자가 답을 리뷰에서 직접 찾아야 한다. **질문–답 쌍이 가치 단위(0-1)**."*
v4 는 질문만 만들었다. 이게 v4→v5 의 가장 큰 산출물 변화다.

## 이 모듈이 하는 일은 좁다

게이트 1~4 를 통과한 `(셀 × aspect)` 마다 **5단 배치를 그대로 보여 주고**(PER-187),
모델에게 `question`·`answer`·`evidence[]` **셋만** 받는다. 나머지는 전부 코드가 채운다.

    모델    question · answer · evidence[{reviewId, quote, stance}]
    코드    claimId · condition · direction · support · rejected · confidence
            · limitations · meta            ← PER-189 가 정한 경계

받은 초안은 세 관문을 지난다. 하나라도 못 지나면 **폐기하고 최대 2회 다시 부르며**
그래도 안 되면 그 주장을 만들지 않는다 (PRD §5-3).

    1. `claim_contract.assert_model_draft`  모델이 코드 몫을 침범했는가
    2. `quote_gate.gate_claim_payload`      인용이 그 리뷰의 원문인가 (PER-190)
    3. `claim_contract.validate_claim`      스키마·택소노미·조건축 (PER-189)

생성 모델은 **골든셋 후보를 만든 모델과 달라야 한다** — 같으면 재현율이 자기 자신을
채점하는 수가 된다 (PER-189 코멘트).

사용:
  .venv/bin/python pipeline/generate.py --limit 12          # 파일럿
  .venv/bin/python pipeline/generate.py                     # 전수
  .venv/bin/python pipeline/generate.py --check             # 재현 확인 (LLM 안 부름)
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures as futures
import json
import os
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(Path(__file__).parent))

from catalog import load_catalog  # noqa: E402
from claim_contract import (  # noqa: E402
    CLAIM_SCHEMA_VERSION,
    ClaimContractError,
    assert_model_draft,
    validate_claim,
)
from codebook import load_codebook  # noqa: E402
from condition_render import render as render_condition  # noqa: E402
from context_layout import layout_context, render_layout  # noqa: E402
from contracts import MISSING_SEGMENT  # noqa: E402
from ledger import load_inputs, run_gates  # noqa: E402
from workspace import CANONICAL, Workspace  # noqa: E402
from polarity import aspect_support  # noqa: E402
from quote_gate import gate_claim_payload, summarize as quote_summary  # noqa: E402
import run_meta  # noqa: E402
from sufficiency import matches  # noqa: E402

PROMPT_PATH = Path(__file__).parent / "prompts/claim/v2.md"
OUT_PATH = ROOT / "data/output/claims_v5.jsonl"
META_PATH = ROOT / "data/output/claims_v5_meta.json"
RAW_PATH = ROOT / "data/intermediate/claim_runs"

# 3단 인용 상한. PER-187 과 같은 수를 쓴다 — 충분성의 절대 하한만큼은 순위 앞쪽에 둔다.
MAX_QUOTES = 8
# 재시도 2회까지 (PRD §5-3). claim_contract.MAX_RETRIES 와 같은 값이다.
MAX_RETRIES = 2
# 질문이 특정 조건을 단정하는 어법. 셀이 그 조건이 아닐 때만 문제가 된다.
CONDITION_CLAIM_RE = re.compile(
    r"(건성|지성|복합성|민감성|약건성|중성|트러블성|트러블 피부|여드름성|"
    r"잡티|모공|주름|각질|블랙헤드|미백)\s*(인데|이라|이라서|라서|인 사람|이신 분)")
DEFAULT_MODEL = "claude-sonnet-5"
# 추출 작업이라 thinking 을 명시적으로 끈다 — 안 끄면 adaptive 로 돌고 <thinking> 이 본문에 샌다
MODEL_PROFILES = {
    "claude-haiku-4-5": {},
    "claude-sonnet-5": {"thinking": {"type": "disabled"}},
    "claude-opus-5": {"output_config": {"effort": "low"}},
}


class GenerateError(RuntimeError):
    """생성 경로가 계약을 위반했다. 조용한 폴백 금지."""


def client():
    """SDK 기본 해석 순서를 그대로 쓴다 (`pipeline/tag.py` 와 같은 방식)."""
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    import anthropic

    return anthropic.Anthropic()


# =============================================================================
# 생성 대상 — 게이트 1~4 를 통과한 (셀 × aspect)
# =============================================================================

@dataclass
class Target:
    """생성 대상 1건. 게이트가 이미 정한 것을 들고 있다."""
    claim_id: str
    axis: str
    product_id: str
    aspect: str
    condition: dict
    members: list[dict]
    support: object          # sufficiency.ClaimSupport
    decision: object         # policy.GateDecision
    cell: object             # sufficiency.EvidenceCell
    limitations: tuple[str, ...] = ()


def axis_of(condition: dict) -> tuple[str, str | None]:
    for axis in ("skinType", "skinTrouble"):
        if condition.get(axis) is not None:
            return axis, condition[axis]
    return "product", None


def targets_from(run, catalog) -> list[Target]:
    """`run_gates` 의 통과 주장을 생성 대상으로 옮긴다. 판정을 다시 하지 않는다."""
    out: list[Target] = []
    for claim in run.passed_claims:
        cell = claim.cell
        axis, segment = axis_of(cell.condition)
        members = [r for r in run.kept.get(cell.product_id, []) if matches(r, cell.condition)]
        out.append(Target(
            claim_id=claim.claim_id,
            axis=axis,
            product_id=cell.product_id,
            aspect=claim.support.aspect,
            condition=dict(cell.condition),
            members=members,
            support=claim.support,
            decision=claim.decision if hasattr(claim, "decision") else None,
            cell=cell,
        ))
    return out


def layout_for(target: Target, tags_by_review, catalog, codebook, decision):
    cell_tags = [t for r in target.members for t in tags_by_review.get(r["reviewId"], ())
                 if t["aspect"] == target.aspect]
    polarity = aspect_support(target.members, cell_tags, target.aspect)
    from sufficiency import Claim as SClaim

    return layout_context(
        product=catalog.product(target.product_id),
        claim=SClaim(target.claim_id, target.cell, target.support),
        polarity=polarity,
        decision=decision,
        records=target.members,
        tags=cell_tags,
        codebook=codebook,
        max_quotes=MAX_QUOTES,
    )


# =============================================================================
# 모델 호출
# =============================================================================

def parse_draft(text: str) -> dict:
    """모델 응답 → 초안. 코드블록·머리말이 붙어도 객체만 꺼낸다."""
    body = text.strip()
    if body.startswith("```"):
        body = body.split("```")[1]
        if body.lstrip().startswith("json"):
            body = body.lstrip()[4:]
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        raise GenerateError(f"응답에서 JSON 객체를 찾지 못했다: {text[:120]!r}")
    return json.loads(body[start:end + 1])


def ask(c, model: str, system: str, layout_text: str) -> tuple[dict, dict]:
    profile = MODEL_PROFILES.get(model, {})
    r = c.messages.create(
        model=model, max_tokens=1024, system=system,
        messages=[{"role": "user", "content": layout_text}], **profile)
    text = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
    usage = {"input": r.usage.input_tokens, "output": r.usage.output_tokens}
    return parse_draft(text), usage


# =============================================================================
# 코드가 채우는 필드
# =============================================================================

def code_fields(target: Target, layout, draft: dict) -> dict:
    """모델 초안 + 게이트 판정 → claim 스키마. **수치는 전부 게이트에서 온다.**"""
    stages = layout.as_dict()["stages"]
    suff, direction = stages["sufficiency"], stages["direction"]
    condition = {
        "skinType": _axis_value(target.condition, "skinType"),
        "skinTrouble": _axis_value(target.condition, "skinTrouble"),
        "option": None,
        "usagePeriod": None,   # 조건축이 아니다 (PER-187 §3 · PER-189 §5-1)
    }
    return {
        "schemaVersion": CLAIM_SCHEMA_VERSION,
        "claimId": target.claim_id,
        "productId": target.product_id,
        "aspect": target.aspect,
        "decisionAxis": draft["decisionAxis"],
        "question": draft["question"],
        "verdict": draft["verdict"],
        "answer": draft["answer"],
        "condition": condition,
        "direction": stages["direction"]["verdict"],
        "evidence": draft["evidence"],
        # 수치는 전부 게이트3·4 판정에서 온다. 모델이 쓴 값을 쓰지 않는다 (PER-178·189)
        "support": {
            "positiveAuthors": direction["positiveAuthors"],
            "negativeAuthors": direction["negativeAuthors"],
            "neutralAuthors": suff["neutralAuthors"],
            "spokeAuthors": suff["spokeAuthors"],
            "silentAuthors": suff["silentAuthors"],
            "supportAuthors": suff["supportAuthors"],
            "cellAuthors": suff["cellAuthors"],
        },
        "rejected": [],
        "failureReasons": [],
        "failureReason": None,
        # 게이트3·4 가 남긴 한계를 합친다 — 완곡한 말투의 사유가 된다 (PER-189 confidence)
        "limitations": sorted(set(suff.get("limitations", [])) | set(direction.get("limitations", []))),
        "meta": {"origin": "generated"},
    }


def _axis_value(condition: dict, axis: str):
    value = condition.get(axis)
    if value is None:
        return None
    return [value] if isinstance(value, str) else list(value)


# =============================================================================
# 한 건 생성
# =============================================================================

@dataclass
class Outcome:
    claim_id: str
    claim: dict | None = None
    skipped: str | None = None
    attempts: int = 0
    usage: dict = field(default_factory=lambda: {"input": 0, "output": 0})
    discards: list[str] = field(default_factory=list)
    quote_result: object = None


def generate_one(c, model: str, system: str, target: Target, layout, reviews: dict) -> Outcome:
    out = Outcome(claim_id=target.claim_id)
    layout_text = render_layout(layout.as_dict())
    for attempt in range(MAX_RETRIES + 1):
        out.attempts = attempt + 1
        try:
            draft, usage = ask(c, model, system, layout_text)
        except Exception as exc:                      # 네트워크·형식 실패
            out.discards.append(f"호출 실패: {type(exc).__name__} {exc}"[:160])
            time.sleep(1.5 * (attempt + 1))
            continue
        out.usage["input"] += usage["input"]
        out.usage["output"] += usage["output"]

        if isinstance(draft, dict) and draft.get("skip"):
            out.skipped = f"모델 판단: {draft['skip']}"[:200]
            return out
        try:
            from claim_contract import DRAFT_FIELDS
            assert_model_draft({**{k: draft.get(k) for k in DRAFT_FIELDS},
                                "aspect": draft.get("aspect") or target.aspect})
        except ClaimContractError as exc:
            out.discards.append(f"초안 계약 위반: {exc}"[:200])
            continue

        # 미기재 셀에 특정 조건을 단정한 질문은 근거가 없는 연결이다 — `미기재` 는
        # "프로필을 안 밝힌 사람들" 이지 그 조건의 사람들이 아니다 (CLAUDE.md · PER-192).
        # 택소노미의 `missing_condition` 에 해당하므로 폐기하고 다시 부른다.
        stated_axes = [a for a in ("skinType", "skinTrouble")
                       if target.condition.get(a) and target.condition[a] != MISSING_SEGMENT]
        if not stated_axes and CONDITION_CLAIM_RE.search(draft.get("question", "")):
            out.discards.append(
                "미기재 셀인데 질문이 조건을 단정한다 — missing_condition")
            continue
        payload = code_fields(target, layout, draft)
        gated, qres = gate_claim_payload(payload, reviews)
        out.quote_result = qres
        if gated is None:
            out.discards.append(f"인용 게이트: {qres.outcome}")
            continue
        try:
            claim = validate_claim(gated, reviews=reviews)
        except (ClaimContractError, ValueError) as exc:
            out.discards.append(f"스키마 위반: {exc}"[:200])
            continue
        # 파생 필드(claimType · confidence · failureReason)를 함께 저장한다 — 화면과
        # judge 가 같은 값을 다시 계산하면 규칙이 두 곳에 살게 된다 (PER-189)
        out.claim = claim.as_dict()
        return out
    out.skipped = f"{MAX_RETRIES + 1}회 모두 실패 — 생성하지 않는다 (PRD §5-3)"
    return out


# =============================================================================
# 실행
# =============================================================================

def check_only() -> None:
    """러너(`run_v5.py --steps claims`)용 진입점.

    **이 단계는 생성을 대신 돌리지 않는다.** 태깅(`tag:ensure_current`)과 같은
    이유다 — 외부 API 호출이고 실비가 든다. 여기서 하는 일은 하나다:
    *지금 있는 산출물이 계약을 통과하는가.* 없으면 **멈추고** 무엇을 돌려야
    하는지 알려준다.

    생성하려면 인자를 골라야 해서(제품·모델·동시성) 러너가 대신 정할 수 없다.
    """
    if not OUT_PATH.exists():
        raise SystemExit(
            f"FAIL: 산출물이 없다 ({OUT_PATH.relative_to(ROOT)})\n"
            "  → .venv/bin/python pipeline/generate.py --products p005,p009,p011,p033,p044\n"
            "     (LLM 호출 · 실비가 든다. ANTHROPIC_API_KEY 필요)")
    records, _tags, _catalog = load_inputs()
    reviews = {r["reviewId"]: r["raw"]["content"] for r in records}
    rows = [json.loads(l) for l in OUT_PATH.read_text().splitlines() if l.strip()]
    for row in rows:
        validate_claim(row, reviews=reviews)
    print(f"OK: claim {len(rows)}건이 계약을 통과한다 ({OUT_PATH.relative_to(ROOT)})")


def main() -> None:
    ap = argparse.ArgumentParser(description="question–answer 쌍 생성 (PER-191)")
    ap.add_argument("--model", default=DEFAULT_MODEL, choices=sorted(MODEL_PROFILES))
    ap.add_argument("--limit", type=int, default=0, help="앞에서 N건만 (파일럿). 0 은 전수")
    ap.add_argument("--products", default="", help="쉼표로 구분한 productId. 비우면 전 제품")
    ap.add_argument("--concurrency", type=int, default=12)
    ap.add_argument("--seed", type=int, default=20260916, help="대상 정렬 시드 기록용")
    ap.add_argument("--check", action="store_true", help="LLM 없이 산출물 계약만 다시 검증")
    ap.add_argument("--run", default="",
                    help="런 ID. 주면 data/runs/<id>/ 로 격리한다 (정본을 건드리지 않는다)")
    args = ap.parse_args()

    # 인자를 안 주면 정본이고 지금까지와 완전히 같다 (PER-194).
    ws = Workspace.for_run(args.run) if args.run else CANONICAL
    ws.assert_isolated()
    ws.ensure_dirs()
    out_path, meta_path = ws.claims, ws.claims_meta
    raw_path = RAW_PATH if ws.is_canonical else ws.base / "claim_runs"

    records, tags_by_review_pairs, catalog = load_inputs(
        ws.reviews, ws.tags, None if ws.is_canonical else ws.catalog)
    reviews = {r["reviewId"]: r["raw"]["content"] for r in records}

    if args.check:
        if not out_path.exists():
            raise SystemExit(f"FAIL: 산출물이 없다 ({out_path.relative_to(ROOT)})")
        rows = [json.loads(l) for l in out_path.read_text().splitlines() if l.strip()]
        for row in rows:
            validate_claim(row, reviews=reviews)
        print(f"OK: claim {len(rows)}건이 계약을 통과한다 ({out_path.relative_to(ROOT)})")
        return

    codebook = load_codebook()
    system = PROMPT_PATH.read_text()
    run = run_gates(records, tags_by_review_pairs, catalog)
    print(f"[게이트] 리뷰 {len(records):,} → 통과 주장 {len(run.passed_claims):,}")

    # 태그 원본(dict) 이 필요하다 — run_gates 는 (aspect, polarity) 쌍만 쓴다
    tags_raw = collections.defaultdict(list)
    for line in (ROOT / "data/intermediate/v5_tags.jsonl").read_text().splitlines():
        if line.strip():
            t = json.loads(line)
            tags_raw[t["reviewId"]].append(t)

    from policy import sufficiency_gate
    targets = sorted(targets_from(run, catalog), key=lambda t: t.claim_id)
    if args.products:
        want = {p.strip() for p in args.products.split(",") if p.strip()}
        unknown = want - {t.product_id for t in targets}
        if unknown:
            raise SystemExit(f"게이트를 통과한 주장이 없는 productId: {sorted(unknown)}")
        targets = [t for t in targets if t.product_id in want]
    if args.limit:
        targets = targets[:args.limit]
    print(f"[대상] {len(targets):,}건 · 모델 {args.model} · 동시 {args.concurrency}")

    c = client()
    lock = threading.Lock()
    done = {"n": 0}
    outcomes: list[Outcome] = []

    def work(target: Target) -> Outcome:
        counts = target.support.as_dict(target.cell)
        decision = sufficiency_gate(
            support_authors=counts["supportAuthors"], spoke_authors=counts["spokeAuthors"],
            cell_authors=counts["cellAuthors"], minority_authors=target.support.minority)
        layout = layout_for(target, tags_raw, catalog, codebook, decision)
        res = generate_one(c, args.model, system, target, layout, reviews)
        with lock:
            done["n"] += 1
            if done["n"] % 50 == 0 or done["n"] == len(targets):
                print(f"  {done['n']:,}/{len(targets):,}", flush=True)
        return res

    started = time.time()
    with futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        outcomes = list(pool.map(work, targets))
    elapsed = time.time() - started

    claims = [o.claim for o in outcomes if o.claim]
    skipped = [o for o in outcomes if not o.claim]
    tok_in = sum(o.usage["input"] for o in outcomes)
    tok_out = sum(o.usage["output"] for o in outcomes)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    # 빈 결과로 기존 산출물을 덮지 않는다. 호출이 통째로 막히면(사용 한도·네트워크)
    # produced 가 0 이 되는데, 그때 그냥 쓰면 **지난 실행 결과가 사라진다.**
    # 2026-09-16 에 실제로 그렇게 997건을 잃었다. 산출물은 근거이므로 덮어쓰기 전에 막는다.
    if not claims and out_path.exists() and out_path.stat().st_size > 0:
        raise SystemExit(
            f"FAIL: 생성된 claim 이 0 건인데 기존 산출물이 있다 ({out_path.relative_to(ROOT)}).\n"
            f"  폐기 사유: {json.dumps(dict(collections.Counter((o.discards or [''])[0].split(':')[0] for o in skipped)), ensure_ascii=False)}\n"
            "  → 덮어쓰지 않았다. 호출이 막힌 원인을 먼저 보고, 정말 비우려면 파일을 직접 지워라")
    # 부분 실패도 덮어쓰기 전에 알린다 — 한도에 걸려 절반만 나온 실행이 전수인 척하면 안 된다
    if out_path.exists() and out_path.stat().st_size > 0:
        before = sum(1 for l in out_path.read_text().splitlines() if l.strip())
        if before > len(claims):
            print(f"  ! 기존 {before:,}건 → 이번 {len(claims):,}건 으로 줄어든다 "
                  f"(--products 로 일부만 돌렸다면 정상이다)")
    out_path.write_text("".join(json.dumps(c_, ensure_ascii=False) + "\n" for c_ in claims))
    raw_path.mkdir(parents=True, exist_ok=True)
    (raw_path / "skipped.jsonl").write_text("".join(
        json.dumps({"claimId": o.claim_id, "skipped": o.skipped,
                    "attempts": o.attempts, "discards": o.discards},
                   ensure_ascii=False) + "\n" for o in skipped))

    meta = run_meta.stamp(
        stage="claims",
        policy={**run.policy.as_meta(), "maxQuotes": MAX_QUOTES, "maxRetries": MAX_RETRIES},
        inputs=[run_meta.file_input("reviews", ROOT / "data/intermediate/v5_reviews.jsonl"),
                run_meta.file_input("tags", ROOT / "data/intermediate/v5_tags.jsonl"),
                run_meta.file_input("catalog", ROOT / "data/input/product_catalog.json")],
        model_id=args.model,
        prompt=run_meta.prompt_ref(PROMPT_PATH, "claim-v2"),
        seed=args.seed,
        failure_taxonomy=run_meta.failure_taxonomy_version(),
    )
    meta["run"] = {
        "targets": len(targets), "produced": len(claims), "skipped": len(skipped),
        "producedPct": round(len(claims) / len(targets) * 100, 2) if targets else 0.0,
        "tokens": {"input": tok_in, "output": tok_out},
        "elapsedSec": round(elapsed, 1),
        "quotes": quote_summary([o.quote_result for o in outcomes if o.quote_result]),
        "skipReasons": dict(collections.Counter(
            (o.skipped or "").split(":")[0] for o in skipped).most_common()),
        "byAxis": dict(collections.Counter(t.axis for t in targets)),
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n")

    print(f"\n[생성] {len(claims):,}/{len(targets):,} "
          f"({meta['run']['producedPct']}%) · 건너뜀 {len(skipped):,}")
    print(f"[토큰] 입력 {tok_in:,} · 출력 {tok_out:,} · {elapsed:.0f}초")
    print(f"[인용] {meta['run']['quotes']}")
    print(f"→ {out_path.relative_to(ROOT)} · {meta_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
