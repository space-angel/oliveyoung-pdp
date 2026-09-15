"""
v5 Step 3 — 25K 전수 aspect/polarity 태깅 (PER-175 / PRD §3-2).

입수 레코드를 Batch API 로 태깅한다. 단위는 리뷰가 아니라 `(리뷰 × aspect)` 다.
프롬프트 정본은 `pipeline/prompts/tag/v1.md`, 계약은 `pipeline/tag_contract.py`.

  입력  data/intermediate/v5_reviews.jsonl          (ingest 산출물)
        eval/gold/v5_tag_pilot_sample.jsonl         (--pilot 일 때)
  출력  data/intermediate/tag_runs/<label>.json     (실행 매니페스트 — batchId·모델·해시)
        data/intermediate/v5_tags_<label>.jsonl     (태그)

사용:
  python3 pipeline/tag.py submit  --model claude-haiku-4-5 --pilot
  python3 pipeline/tag.py poll    --label pilot_haiku45
  python3 pipeline/tag.py collect --label pilot_haiku45
  python3 pipeline/tag.py runs                       # 이 저장소가 아는 실행 목록

멈추는 조건 (조용한 폴백 금지)
  - 모델 응답이 JSON 이 아님            → 그 청크를 failedChunks 에 남기고 계속, 마지막에 요약
  - 계약 위반 태그 (aspect·polarity·인용) → tag_contract.validate_tags 가 에러
  - 입력 리뷰가 결과에 없음              → 누락 reviewId 를 매니페스트에 남긴다

비용: 실행 전에 `eval/reports/v5_tag_cost_estimate.json` 을 보고 모델을 정한다.
채점: `eval/validate_tags.py --tags <출력> --against eval/gold/v5_tags_pilot_gold.jsonl`
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

ROOT = Path(__file__).parents[1]
REVIEWS_PATH = ROOT / "data/intermediate/v5_reviews.jsonl"
PILOT_SAMPLE_PATH = ROOT / "eval/gold/v5_tag_pilot_sample.jsonl"
PROMPT_PATH = ROOT / "pipeline/prompts/tag/v1.md"
RUNS_DIR = ROOT / "data/intermediate/tag_runs"

CHUNK_SIZE = 20          # 리뷰/요청. 비용 추정치(1,250배치)가 이 값 기준이다
MAX_TOKENS = 8000        # 측정 출력 2,617토큰/요청의 3배 여유


# 모델별 요청 파라미터. thinking 설정이 모델마다 다르다 — 태깅은 추출 작업이라
# 사고 토큰이 비용만 늘리고 품질을 올리지 않을 가능성이 높다. 그래서 최소로 깐다.
MODEL_PROFILES = {
    # Haiku 4.5: thinking 은 budget_tokens 방식이고 effort 는 에러다. 둘 다 안 쓴다.
    "claude-haiku-4-5": {},
    # Sonnet 5: thinking 을 안 주면 adaptive 로 돈다. 추출 작업이라 명시적으로 끈다.
    "claude-sonnet-5": {"thinking": {"type": "disabled"}},
    # Opus 5: thinking 이 기본 on 이고, disabled 는 effort high 이하에서만 받는다.
    # 끄는 대신 effort 를 낮춘다 — 끄면 <thinking> 태그가 본문에 새는 사례가 있다.
    "claude-opus-5": {"output_config": {"effort": "low"}},
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tagging_input_hash(reviews: list[dict]) -> str:
    """**태거가 실제로 본 것**의 해시 — `reviewId` · 접힌 본문 · 별점뿐이다.

    파일 해시를 쓰면 파생층만 바뀌어도 태그가 낡은 것으로 판정된다. 특히
    `trustPrior` 재점수(PER-174 인계)는 태그 수를 읽어 레코드를 고치므로,
    파일 해시로 묶으면 **재점수 → 태그 무효 → 재태깅**이라는 순환이 생긴다.
    태깅 투입물이 같으면 태그는 유효하다 — 그 사실만 해시로 고정한다.
    """
    from tag_contract import tagging_text

    h = hashlib.sha256()
    for r in reviews:
        h.update(str(r["reviewId"]).encode())
        h.update(b"\x1f")
        h.update(tagging_text(r["raw"]["content"]).encode())
        h.update(b"\x1f")
        h.update(str(r["raw"]["rating"]).encode())
        h.update(b"\x1e")
    return h.hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def client():
    """SDK 기본 해석 순서(ANTHROPIC_API_KEY → ANTHROPIC_AUTH_TOKEN → ant 프로필)를 그대로 쓴다."""
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    import anthropic

    return anthropic.Anthropic()


def load_reviews(pilot: bool) -> list[dict]:
    path = PILOT_SAMPLE_PATH if pilot else REVIEWS_PATH
    if not path.exists():
        raise SystemExit(
            f"입력이 없다: {path.relative_to(ROOT)}\n"
            "  ingest 를 먼저 돌린다 — python3 pipeline/ingest.py"
        )
    return read_jsonl(path)


def chunks(reviews: list[dict], size: int = CHUNK_SIZE):
    for i in range(0, len(reviews), size):
        yield i // size, reviews[i : i + size]


def chunk_payload(batch: list[dict]) -> str:
    """태거에게 주는 본문. 대조와 같은 접기를 쓴다 — 안 접으면 정상 인용이 탈락한다 (PER-175)."""
    from tag_contract import tagging_text

    return json.dumps(
        [
            {
                "reviewId": r["reviewId"],
                "content": tagging_text(r["raw"]["content"]),
                "rating": r["raw"]["rating"],
            }
            for r in batch
        ],
        ensure_ascii=False,
    )


def build_requests(reviews: list[dict], model: str) -> list[dict]:
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    system = PROMPT_PATH.read_text()
    profile = MODEL_PROFILES[model]
    requests = []
    for idx, batch in chunks(reviews):
        params = {
            "model": model,
            "max_tokens": MAX_TOKENS,
            # 프롬프트는 1,250요청 내내 같다 — 캐시가 붙으면 입력 비용이 떨어진다.
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": chunk_payload(batch)}],
            **profile,
        }
        requests.append(
            Request(
                custom_id=f"chunk-{idx:05d}",
                params=MessageCreateParamsNonStreaming(**params),
            )
        )
    return requests


# --------------------------------------------------------------------------- submit


def submit(model: str, pilot: bool, label: str | None) -> None:
    if model not in MODEL_PROFILES:
        raise SystemExit(
            f"모르는 모델: {model}\n  아는 모델: {', '.join(MODEL_PROFILES)}\n"
            "  새 모델을 쓰려면 MODEL_PROFILES 에 thinking/effort 규칙을 먼저 적는다."
        )
    reviews = load_reviews(pilot)
    label = label or f"{'pilot' if pilot else 'full'}_{model.replace('claude-', '').replace('-', '')}"
    requests = build_requests(reviews, model)

    print(f"[submit] {model} · 리뷰 {len(reviews):,}건 · 요청 {len(requests):,}개 (청크 {CHUNK_SIZE})")
    batch = client().messages.batches.create(requests=requests)

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {
        "issue": "PER-175",
        "label": label,
        "batchId": batch.id,
        "model": model,
        "pilot": pilot,
        "reviews": len(reviews),
        "requests": len(requests),
        "chunkSize": CHUNK_SIZE,
        "maxTokens": MAX_TOKENS,
        "params": MODEL_PROFILES[model],
        "prompt": {"path": str(PROMPT_PATH.relative_to(ROOT)), "sha256": sha256(PROMPT_PATH)},
        "input": {
            "path": str((PILOT_SAMPLE_PATH if pilot else REVIEWS_PATH).relative_to(ROOT)),
            "sha256": sha256(PILOT_SAMPLE_PATH if pilot else REVIEWS_PATH),
            # 파생층(trustPrior 재점수 등)이 바뀌어도 태그가 낡지 않게 하는 축
            "taggingSha256": tagging_input_hash(reviews),
        },
        "createdAt": batch.created_at.isoformat(),
    }
    (RUNS_DIR / f"{label}.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(f"[submit] batchId={batch.id}  →  {(RUNS_DIR / f'{label}.json').relative_to(ROOT)}")
    print(f"[다음]   python3 pipeline/tag.py poll --label {label}")


# ----------------------------------------------------------------------------- poll


def manifest_of(label: str) -> dict:
    path = RUNS_DIR / f"{label}.json"
    if not path.exists():
        known = sorted(p.stem for p in RUNS_DIR.glob("*.json")) if RUNS_DIR.exists() else []
        raise SystemExit(f"모르는 실행: {label}\n  아는 실행: {', '.join(known) or '(없음)'}")
    return json.loads(path.read_text())


def poll(label: str, watch: bool, interval: int = 60) -> None:
    man = manifest_of(label)
    c = client()
    while True:
        b = c.messages.batches.retrieve(man["batchId"])
        counts = b.request_counts
        print(
            f"[{b.processing_status}] 처리중 {counts.processing} · 성공 {counts.succeeded} · "
            f"실패 {counts.errored} · 취소 {counts.canceled} · 만료 {counts.expired}"
            f"  ({counts.succeeded}/{man['requests']})"
        )
        if b.processing_status == "ended":
            print(f"[다음]   python3 pipeline/tag.py collect --label {label}")
            return
        if not watch:
            return
        time.sleep(interval)


# -------------------------------------------------------------------------- collect


# 추론 모델(gpt-oss 계열)은 사고 과정을 별도 필드가 아니라 `content` 안에
# `<reasoning>…</reasoning>` 로 섞어 보낸다. **닫힌 블록만** 걷어낸다 —
# 열린 채 끝났다는 건 max_tokens 에 걸려 잘렸다는 뜻이고, 그건 조용히 넘어가면
# 안 되는 사건이라 파싱 실패로 남겨 failedChunks 에 finish_reason 과 함께 기록한다.
REASONING_BLOCK = re.compile(r"<reasoning>.*?</reasoning>", re.DOTALL)


def parse_text(text: str) -> dict:
    """모델이 코드펜스나 추론 블록을 붙이는 경우가 있다. 그것만 벗기고 나머지는 그대로 파싱한다."""
    t = REASONING_BLOCK.sub("", text).strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        t = t.rsplit("```", 1)[0]
    return json.loads(t)


def partition_by_contract(tags: list[dict], reviews: dict) -> tuple[list[dict], list[dict]]:
    """계약 통과분과 위반분을 가른다.

    규칙은 `tag_contract.validate_tags` 가 단독으로 소유한다 — 여기서 검사를 다시 쓰지
    않는다. 위반 태그를 하나씩 빼면서 계약을 다시 세워, 규칙이 바뀌면 이 진단도 같이 바뀐다.
    파이프라인 경로(첫 위반에서 정지)는 그대로 두고, **모델을 비교할 때 위반이 몇 건인지**
    세기 위한 것이다.
    """
    from tag_contract import TagContractError, validate_tags

    import re

    kept = list(tags)
    violations: list[dict] = []
    while True:
        try:
            validate_tags(kept, reviews)
            return kept, violations
        except TagContractError as exc:
            m = re.search(r"tags\[(\d+)\]", str(exc))
            if not m:
                raise
            idx = int(m.group(1))
            bad = kept.pop(idx)
            violations.append({**bad, "violation": str(exc).split(": ", 1)[-1]})


def collect(label: str) -> None:
    man = manifest_of(label)
    reviews = {r["reviewId"]: r for r in load_reviews(man["pilot"])}

    tags: list[dict] = []
    seen: set[int] = set()
    failed: list[dict] = []

    # 클라이언트를 인라인으로 만들면 결과 스트림을 다 읽기 전에 GC 되면서 소켓이 닫힌다.
    # 반드시 참조를 붙잡고 순회한다.
    c = client()
    for result in c.messages.batches.results(man["batchId"]):
        if result.result.type != "succeeded":
            failed.append({"customId": result.custom_id, "type": result.result.type})
            continue
        text = "".join(b.text for b in result.result.message.content if b.type == "text")
        try:
            payload = parse_text(text)
            rows = payload["results"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            failed.append({"customId": result.custom_id, "type": "unparseable", "error": str(exc)})
            continue
        for row in rows:
            rid = row["reviewId"]
            seen.add(rid)
            for a in row.get("aspects") or []:
                tags.append({"reviewId": rid, **a})

    # 원문 산출물은 무조건 남긴다 — 계약에서 떨어져도 "무엇이 떨어졌나"가 근거다.
    raw_out = ROOT / f"data/intermediate/v5_tags_{label}_raw.jsonl"
    raw_out.write_text("".join(json.dumps(t, ensure_ascii=False) + "\n" for t in tags))

    # 계약 통과분만 파이프라인으로 보낸다. 위반 태그를 조용히 흘려보내면 게이트가 근거로 쓴다.
    kept, violations = partition_by_contract(tags, reviews)

    missing = sorted(set(reviews) - seen)
    out = ROOT / f"data/intermediate/v5_tags_{label}.jsonl"
    out.write_text("".join(json.dumps(t, ensure_ascii=False) + "\n" for t in kept))

    man["output"] = {
        "path": str(out.relative_to(ROOT)),
        "rawPath": str(raw_out.relative_to(ROOT)),
        "tagsRaw": len(tags),
        "tags": len(kept),
        "contractViolations": len(violations),
        "violations": violations[:50],
        "reviewsTagged": len(seen),
        "missingReviewIds": missing[:50],
        "missingCount": len(missing),
        "failedChunks": failed,
    }
    (RUNS_DIR / f"{label}.json").write_text(json.dumps(man, ensure_ascii=False, indent=2) + "\n")

    print(f"[collect] 태그 {len(tags):,}개 생성 · 계약 통과 {len(kept):,} · 위반 {len(violations):,}")
    print(f"[collect] 리뷰 {len(seen):,}/{len(reviews):,} → {out.relative_to(ROOT)}")
    for v in violations[:10]:
        print(f"           - reviewId={v['reviewId']} {v['aspect']}: {v['violation']}")
    if failed:
        print(f"[collect] 실패 청크 {len(failed)}개 — 매니페스트의 failedChunks 참조")
    if missing:
        print(f"[collect] 결과에 없는 리뷰 {len(missing)}건 — 재실행 대상")
    gold = "eval/gold/v5_tags_pilot_gold.jsonl"
    print(
        f"[다음]   .venv/bin/python3 eval/validate_tags.py --tags {out.relative_to(ROOT)} "
        f"--label {label} --tagger {man['model']} --against {gold} "
        f"--out eval/reports/v5_tag_{label}.json"
    )


CANONICAL_TAGS_PATH = ROOT / "data/intermediate/v5_tags.jsonl"
CANONICAL_META_PATH = ROOT / "data/intermediate/v5_tags_meta.json"


def ensure_current() -> None:
    """러너(`run_v5.py --steps tag`)용 진입점.

    **이 단계는 태깅을 대신 돌리지 않는다.** 외부 API 호출이고 실비가 들어서,
    러너가 알아서 재실행하면 안 되는 종류의 일이다. 여기서 하는 일은 하나다 —
    *지금 입력으로 만든 전수 태그가 있는가.* 없거나 낡았으면 **멈추고** 무엇을
    돌려야 하는지 알려준다 (미구현 단계와 같은 규칙).

    통과하면 실행 하나를 정본으로 못박아 `data/intermediate/v5_tags.jsonl` 로
    복사한다. 뒤 단계(게이트3 방향성)는 이 경로만 본다 — 어느 실행을 썼는지는
    `v5_tags_meta.json` 에 남는다.
    """
    if not REVIEWS_PATH.exists():
        raise SystemExit(
            f"입수 산출물이 없다: {REVIEWS_PATH.relative_to(ROOT)}\n"
            "  먼저 입수를 돌린다 — .venv/bin/python3 pipeline/run_v5.py --steps ingest"
        )
    input_hash = tagging_input_hash(read_jsonl(REVIEWS_PATH))

    manifests = []
    if RUNS_DIR.exists():
        for path in sorted(RUNS_DIR.glob("*.json")):
            man = json.loads(path.read_text())
            if not man.get("pilot") and man.get("output"):
                manifests.append(man)

    current = [
        m for m in manifests if (m.get("input") or {}).get("taggingSha256") == input_hash
    ]
    if not current:
        stale = [m["label"] for m in manifests]
        hint = f"\n  입력이 바뀐 실행: {', '.join(stale)} — 스냅샷이 달라졌으면 재태깅이다" if stale else ""
        raise SystemExit(
            "[tag] 지금 입력으로 만든 전수 태그가 없다 (PER-175)\n"
            f"  입력 {REVIEWS_PATH.relative_to(ROOT)} sha256={input_hash[:12]}…{hint}\n"
            "  태깅을 먼저 돌린다 —\n"
            "    .venv/bin/python3 pipeline/tag.py submit --model claude-haiku-4-5\n"
            "    .venv/bin/python3 pipeline/tag.py collect --label full_haiku45\n"
            "  태그 없이 게이트3(방향성)을 돌리면 근거 없는 결과가 나온다."
        )
    if len(current) > 1:
        raise SystemExit(
            "[tag] 지금 입력에 해당하는 전수 실행이 여러 개다 — 어느 것이 정본인지 정해야 한다\n"
            + "".join(f"    {m['label']:<24} {m['model']}\n" for m in current)
            + "  쓰지 않을 실행의 매니페스트를 data/intermediate/tag_runs/ 에서 치운다."
        )

    man = current[0]
    out = man["output"]
    # 부분 수거를 정본으로 삼으면 태깅 안 된 리뷰가 '아무 aspect도 말하지 않은 리뷰'와
    # 구별되지 않는다. 침묵은 근거가 아니라는 규칙(PER-178)이 여기서 무너진다.
    if out.get("missingCount") or out.get("failedChunks"):
        raise SystemExit(
            f"[tag] 실행 '{man['label']}' 이 부분 수거다 — 정본으로 쓸 수 없다\n"
            f"  결과에 없는 리뷰 {out.get('missingCount', 0)}건 · 실패 청크 "
            f"{len(out.get('failedChunks') or [])}개\n"
            "  같은 label 로 다시 돌리면 끝난 청크는 건너뛰고 빠진 것만 채운다."
        )

    tags_path = ROOT / out["path"]
    if not tags_path.exists():
        raise SystemExit(f"[tag] 태그 파일이 없다: {out['path']} — collect 를 다시 돌린다")

    CANONICAL_TAGS_PATH.write_text(tags_path.read_text())
    CANONICAL_META_PATH.write_text(
        json.dumps(
            {
                "issue": "PER-175",
                "label": man["label"],
                "model": man["model"],
                "prompt": man["prompt"],
                "input": man["input"],
                "tags": out["tags"],
                "tagsRaw": out.get("tagsRaw"),
                "contractViolations": out.get("contractViolations"),
                "reviewsTagged": out["reviewsTagged"],
                "source": out["path"],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    print(
        f"[tag] 정본 = '{man['label']}' ({man['model']}) · 태그 {out['tags']:,}개 · "
        f"리뷰 {out['reviewsTagged']:,}건"
    )
    print(f"       → {CANONICAL_TAGS_PATH.relative_to(ROOT)}")


def runs() -> None:
    if not RUNS_DIR.exists() or not any(RUNS_DIR.glob("*.json")):
        print("아는 실행이 없다. submit 부터 — python3 pipeline/tag.py submit --model claude-haiku-4-5 --pilot")
        return
    for path in sorted(RUNS_DIR.glob("*.json")):
        m = json.loads(path.read_text())
        done = m.get("output", {}).get("tags")
        print(
            f"{m['label']:<24} {m['model']:<20} 리뷰 {m['reviews']:>6,}  "
            f"{'태그 ' + format(done, ',') if done else '미수거'}  {m['batchId']}"
        )


def main() -> None:
    ap = argparse.ArgumentParser(description="25K 전수 aspect/polarity 태깅 (PER-175)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("submit", help="Batch API 에 제출")
    s.add_argument("--model", required=True, choices=sorted(MODEL_PROFILES))
    s.add_argument("--pilot", action="store_true", help="정답셋 표본 200건만 (모델 비교용)")
    s.add_argument("--label", help="실행 이름. 생략하면 모델명으로 만든다")

    p = sub.add_parser("poll", help="진행 상황")
    p.add_argument("--label", required=True)
    p.add_argument("--watch", action="store_true", help="끝날 때까지 주기적으로 확인")
    p.add_argument("--interval", type=int, default=60)

    c = sub.add_parser("collect", help="결과 수거 + 계약 검증")
    c.add_argument("--label", required=True)

    sub.add_parser("runs", help="이 저장소가 아는 실행 목록")

    args = ap.parse_args()
    if args.cmd == "submit":
        submit(args.model, args.pilot, args.label)
    elif args.cmd == "poll":
        poll(args.label, args.watch, args.interval)
    elif args.cmd == "collect":
        collect(args.label)
    else:
        runs()


if __name__ == "__main__":
    main()
