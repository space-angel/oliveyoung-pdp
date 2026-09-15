"""
태깅 — OpenAI 호환 엔드포인트 경로 (PER-175 후보 비교용).

`pipeline/tag.py` 가 Anthropic Batch API 경로라면 이 파일은 **OpenAI 호환 HTTP** 경로다
(NVIDIA NIM `integrate.api.nvidia.com` 등). 프롬프트·청크·계약 검증은 tag.py 와 같은
함수를 그대로 쓴다 — 두 경로가 다른 규칙으로 돌면 채점을 비교할 수 없다.

Batch API 가 없으므로 클라이언트가 동시성·재시도·재개를 맡는다.
  - 청크 결과를 `<label>_raw.jsonl` 에 즉시 적는다. 다시 돌리면 끝난 청크는 건너뛴다
  - 429/5xx 는 지수 백오프로 재시도, 그 외 4xx 는 즉시 멈춘다 (조용한 폴백 금지)

  본문에 보내는 것은 `reviewId` · `content` · `rating` 뿐이다 — `userName` 은 보내지 않는다.

AWS Bedrock 도 이 경로다 — Bedrock 은 OpenAI 호환 `/openai/v1` 을 같이 연다.
**MiniMax M2.5 는 Bedrock 배치 추론 지원 목록에 없다** (M2·M2.1 만 있다). 그래서 배치가
아니라 동기 호출 + `service_tier="flex"` (지연 허용 할인) 로 돌린다. 재개가 배치의 대역이다.

사용:
  # NVIDIA NIM
  export NVIDIA_API_KEY=nvapi-...
  python3 pipeline/tag_compat.py run --model moonshotai/kimi-k3 --pilot

  # AWS Bedrock (Flex 티어)
  echo 'AWS_BEARER_TOKEN_BEDROCK=...' >> .env
  python3 pipeline/tag_compat.py run --model minimax.minimax-m2.5 --pilot \
      --base-url https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1 \
      --env AWS_BEARER_TOKEN_BEDROCK --service-tier flex

  python3 pipeline/tag_compat.py collect --label pilot_minimaxm25
"""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import json
import os
import random
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from tag import (  # noqa: E402
    CHUNK_SIZE,
    PILOT_SAMPLE_PATH,
    PROMPT_PATH,
    REVIEWS_PATH,
    RUNS_DIR,
    ROOT,
    chunk_payload,
    chunks,
    load_reviews,
    parse_text,
    partition_by_contract,
    sha256,
    tagging_input_hash,
)

MAX_TOKENS = 8000
DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
RETRY_STATUS = (408, 409, 429, 500, 502, 503, 504)


def api_key(env: str) -> str:
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    key = os.environ.get(env)
    if not key:
        raise SystemExit(
            f"{env} 가 비어 있다.\n"
            f"  .env 에 넣거나 export 한다 — echo '{env}=...' >> .env"
        )
    return key


def post(base_url: str, key: str, body: dict, timeout: int, tries: int = 5) -> dict:
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code not in RETRY_STATUS or attempt == tries - 1:
                raise SystemExit(f"HTTP {e.code}: {e.read()[:400].decode(errors='replace')}")
        except (urllib.error.URLError, TimeoutError):
            if attempt == tries - 1:
                raise
        time.sleep(min(2**attempt + random.random(), 60))
    raise SystemExit("재시도 소진")


def incomplete_chunks(raw_path: Path, reviews: list[dict], chunk_size: int) -> set[int]:
    """받은 응답 중 **쓸 수 없는** 청크 번호. 파싱 실패와 리뷰 누락을 같이 본다.

    둘을 가르지 않는 이유는 결과가 같기 때문이다 — 그 리뷰들은 태그가 없는 채로
    남고, 그러면 "아무 aspect 도 말하지 않은 리뷰" 와 구별되지 않는다 (PER-178).
    """
    expected = {idx: {r["reviewId"] for r in batch} for idx, batch in chunks(reviews, chunk_size)}
    bad: set[int] = set()
    for line in raw_path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        idx = row["chunk"]
        try:
            results = parse_text(row["text"])["results"]
            seen = {r["reviewId"] for r in results}
        except (json.JSONDecodeError, KeyError, TypeError):
            bad.add(idx)
            continue
        if expected.get(idx, set()) - seen:
            bad.add(idx)
    return bad


def run(model: str, pilot: bool, label: str | None, base_url: str, env: str,
        concurrency: int, timeout: int, service_tier: str | None = None,
        chunk_size: int = CHUNK_SIZE, retry_failed: bool = False) -> None:
    reviews = load_reviews(pilot)
    input_path = PILOT_SAMPLE_PATH if pilot else REVIEWS_PATH
    label = label or f"{'pilot' if pilot else 'full'}_{model.split('/')[-1].replace('-', '').replace('.', '')}"
    key = api_key(env)
    system = PROMPT_PATH.read_text()

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = RUNS_DIR / f"{label}_raw.jsonl"

    # 재개는 label 로만 찾는다. 같은 label 을 다른 모델·프롬프트·입력으로 다시 돌리면
    # **남의 결과를 주워 재개**하고, 그게 조용히 그 모델의 성적표가 된다. 실제로 났던 사고다
    # (`minimax-m2.5` 와 `kimi-k2.5` 가 같은 label 로 접혔고, kimi 는 API 호출 없이
    # minimax 의 태그를 수거했다). 되풀이되지 않게 여기서 세운다.
    prev_path = RUNS_DIR / f"{label}.json"
    if prev_path.exists():
        prev = json.loads(prev_path.read_text())
        mismatch = [
            (k, prev_v, now_v)
            for k, prev_v, now_v in (
                ("model", prev.get("model"), model),
                ("prompt", (prev.get("prompt") or {}).get("sha256"), sha256(PROMPT_PATH)),
                ("input", (prev.get("input") or {}).get("taggingSha256"),
                 tagging_input_hash(reviews)),
            )
            if prev_v is not None and prev_v != now_v
        ]
        if mismatch:
            raise SystemExit(
                f"label '{label}' 은 이미 다른 조건으로 돌린 실행이다 — 재개하면 결과가 섞인다\n"
                + "".join(f"    {k}: 기존 {p!r} ≠ 지금 {n!r}\n" for k, p, n in mismatch)
                + "  다른 --label 을 쓰거나, 기존 실행을 data/intermediate/tag_runs/ 에서 치운다."
            )
    # 재개: 이미 받은 청크는 다시 부르지 않는다. 무료 엔드포인트에서 1,250청크를
    # 한 번에 끝내지 못하는 게 정상이라 이게 기능이다.
    done = set()
    if raw_path.exists():
        for line in raw_path.read_text().splitlines():
            if line.strip():
                done.add(json.loads(line)["chunk"])

    # 재개는 "응답을 받았는지" 만 본다. 그래서 **응답은 왔는데 내용이 깨진** 청크는
    # 영영 다시 부르지 않는다 — 잘린 JSON, 리뷰를 빼먹은 결과가 그렇다. 1,250청크
    # 실행에서 이건 재현율 손실로 조용히 남는다. --retry-failed 로 그것만 골라 비운다.
    if retry_failed and raw_path.exists():
        bad = incomplete_chunks(raw_path, reviews, chunk_size)
        if bad:
            kept = [
                line
                for line in raw_path.read_text().splitlines()
                if line.strip() and json.loads(line)["chunk"] not in bad
            ]
            raw_path.write_text("".join(l + "\n" for l in kept))
            done -= bad
            print(f"[run] 내용이 불완전한 청크 {len(bad)}개를 비웠다 — 다시 부른다: "
                  f"{sorted(bad)[:10]}{' …' if len(bad) > 10 else ''}")
        else:
            print("[run] 다시 부를 청크가 없다 (받은 응답이 전부 온전하다)")

    todo = [(i, b) for i, b in chunks(reviews, chunk_size) if i not in done]
    print(f"[run] {model} · 리뷰 {len(reviews):,}건 · 청크 {len(todo):,}개 남음 "
          f"(완료 {len(done):,}) · 동시 {concurrency}")
    if not todo:
        print("[run] 남은 청크가 없다. collect 로.")
        return

    lock = threading.Lock()
    fh = raw_path.open("a")
    counter = {"n": 0}

    def work(item):
        idx, batch = item
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": chunk_payload(batch)},
            ],
            "max_tokens": MAX_TOKENS,
            "temperature": 0,   # 같은 입력 → 같은 출력 (§5-2)
            "stream": False,
        }
        if service_tier:
            body["service_tier"] = service_tier
        data = post(base_url, key, body, timeout)
        choice = data["choices"][0]
        text = choice["message"]["content"]
        # 토큰 사용량은 청크마다 남긴다 — 비용을 추정이 아니라 실측으로 적기 위해서다.
        # finish_reason 도 같이 남긴다: 추론 모델이 max_tokens 에 걸려 잘리면 JSON 이
        # 깨지는데, 그때 "모델이 못 한다"와 "상한이 낮다"를 구별할 수 있어야 한다.
        row = {
            "chunk": idx,
            "text": text,
            "finishReason": choice.get("finish_reason"),
            "usage": data.get("usage"),
            "serviceTier": data.get("service_tier"),
        }
        with lock:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()
            counter["n"] += 1
            if counter["n"] % 10 == 0 or counter["n"] == len(todo):
                print(f"  {counter['n']}/{len(todo)}")

    with futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        list(pool.map(work, todo))
    fh.close()

    (RUNS_DIR / f"{label}.json").write_text(
        json.dumps(
            {
                "issue": "PER-175",
                "label": label,
                "provider": "openai-compatible",
                "baseUrl": base_url,
                "model": model,
                "serviceTier": service_tier,
                "pilot": pilot,
                "reviews": len(reviews),
                "chunkSize": chunk_size,
                "maxTokens": MAX_TOKENS,
                "temperature": 0,
                "raw": str(raw_path.relative_to(ROOT)),
                "prompt": {"path": str(PROMPT_PATH.relative_to(ROOT)), "sha256": sha256(PROMPT_PATH)},
                "input": {
                    "path": str(input_path.relative_to(ROOT)),
                    "sha256": sha256(input_path),
                    "taggingSha256": tagging_input_hash(reviews),
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    print(f"[다음]   python3 pipeline/tag_compat.py collect --label {label}")


def collect(label: str) -> None:
    man = json.loads((RUNS_DIR / f"{label}.json").read_text())
    reviews = {r["reviewId"]: r for r in load_reviews(man["pilot"])}

    tags, seen, failed = [], set(), []
    usage = {"input": 0, "output": 0, "cacheRead": 0, "chunks": 0}
    finish_reasons: dict[str, int] = {}
    for line in (ROOT / man["raw"]).read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        u = row.get("usage") or {}
        if u:
            usage["chunks"] += 1
            usage["input"] += u.get("prompt_tokens", 0)
            usage["output"] += u.get("completion_tokens", 0)
            details = u.get("prompt_tokens_details") or {}
            usage["cacheRead"] += details.get("cached_tokens", 0)
        if row.get("finishReason"):
            finish_reasons[row["finishReason"]] = finish_reasons.get(row["finishReason"], 0) + 1
        try:
            payload = parse_text(row["text"])
            results = payload["results"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            # 잘린 응답인지 모델이 형식을 어긴 것인지 구별할 수 있게 finish_reason 을 같이 남긴다
            failed.append(
                {"chunk": row["chunk"], "error": str(exc), "finishReason": row.get("finishReason")}
            )
            continue
        for r in results:
            seen.add(r["reviewId"])
            for a in r.get("aspects") or []:
                tags.append({"reviewId": r["reviewId"], **a})

    # 원문 산출물은 무조건 남긴다 — 계약에서 떨어져도 "무엇이 떨어졌나"가 근거다 (tag.py 와 같은 규칙).
    raw_out = ROOT / f"data/intermediate/v5_tags_{label}_raw.jsonl"
    raw_out.write_text("".join(json.dumps(t, ensure_ascii=False) + "\n" for t in tags))

    # 계약 통과분만 파이프라인으로 보낸다. 위반 하나에 수거 전체가 멈추면 25,000건을 다시 부른다.
    kept, violations = partition_by_contract(tags, reviews)

    out = ROOT / f"data/intermediate/v5_tags_{label}.jsonl"
    out.write_text("".join(json.dumps(t, ensure_ascii=False) + "\n" for t in kept))
    missing = sorted(set(reviews) - seen)
    man["output"] = {
        "path": str(out.relative_to(ROOT)),
        "rawPath": str(raw_out.relative_to(ROOT)),
        "tagsRaw": len(tags),
        "tags": len(kept),
        "contractViolations": len(violations),
        "violations": violations[:50],
        "reviewsTagged": len(seen),
        "missingCount": len(missing),
        "missingReviewIds": missing[:50],
        "failedChunks": failed,
        "usage": usage,
        "finishReasons": finish_reasons,
    }
    (RUNS_DIR / f"{label}.json").write_text(json.dumps(man, ensure_ascii=False, indent=2) + "\n")

    print(f"[collect] 태그 {len(tags):,}개 생성 · 계약 통과 {len(kept):,} · 위반 {len(violations):,}")
    print(f"[collect] 리뷰 {len(seen):,}/{len(reviews):,} → {out.relative_to(ROOT)}")
    if usage["chunks"]:
        print(
            f"[collect] 토큰 실측 입력 {usage['input']:,} · 출력 {usage['output']:,}"
            f" · 캐시읽기 {usage['cacheRead']:,} ({usage['chunks']:,}청크)"
        )
    if finish_reasons:
        print("[collect] finish_reason " + " · ".join(f"{k} {v}" for k, v in sorted(finish_reasons.items())))
    for v in violations[:10]:
        print(f"           - reviewId={v['reviewId']} {v['aspect']}: {v['violation']}")
    if failed:
        print(f"[collect] 파싱 실패 청크 {len(failed)}개 — 매니페스트의 failedChunks 참조")
    if missing:
        print(f"[collect] 결과에 없는 리뷰 {len(missing)}건 — 재실행 대상")
    print(
        f"[다음]   .venv/bin/python3 eval/validate_tags.py --tags {out.relative_to(ROOT)} "
        f"--label {label} --tagger {man['model']} "
        f"--against eval/gold/v5_tags_pilot_gold.jsonl --out eval/reports/v5_tag_{label}.json"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="OpenAI 호환 엔드포인트 태깅 (PER-175 후보 비교)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="태깅 실행 (재개 가능)")
    r.add_argument("--model", required=True, help="예: moonshotai/kimi-k3")
    r.add_argument("--pilot", action="store_true", help="정답셋 표본 200건만")
    r.add_argument("--label")
    r.add_argument("--base-url", default=DEFAULT_BASE_URL)
    r.add_argument("--env", default="NVIDIA_API_KEY", help="API 키 환경변수 이름")
    r.add_argument("--concurrency", type=int, default=4, help="무료 티어는 낮게 잡는다")
    r.add_argument("--timeout", type=int, default=180)
    r.add_argument(
        "--chunk-size",
        type=int,
        default=CHUNK_SIZE,
        help=f"요청당 리뷰 수 (기본 {CHUNK_SIZE}). 모델 비교는 같은 값으로 재야 한 표에 올라간다",
    )
    r.add_argument(
        "--retry-failed",
        action="store_true",
        help="받았지만 내용이 깨진 청크(파싱 실패·리뷰 누락)를 비우고 다시 부른다",
    )
    r.add_argument(
        "--service-tier",
        choices=("default", "flex", "priority"),
        help="Bedrock 서비스 티어. flex 는 지연을 허용하고 할인받는다 (배치가 없는 모델의 대역)",
    )

    c = sub.add_parser("collect", help="결과 수거 + 계약 검증")
    c.add_argument("--label", required=True)

    a = ap.parse_args()
    if a.cmd == "run":
        run(a.model, a.pilot, a.label, a.base_url, a.env, a.concurrency, a.timeout,
            a.service_tier, a.chunk_size, a.retry_failed)
    else:
        collect(a.label)


if __name__ == "__main__":
    main()
