"""
태깅 — OpenAI 호환 엔드포인트 경로 (PER-175 후보 비교용).

`pipeline/tag.py` 가 Anthropic Batch API 경로라면 이 파일은 **OpenAI 호환 HTTP** 경로다
(NVIDIA NIM `integrate.api.nvidia.com` 등). 프롬프트·청크·계약 검증은 tag.py 와 같은
함수를 그대로 쓴다 — 두 경로가 다른 규칙으로 돌면 채점을 비교할 수 없다.

Batch API 가 없으므로 클라이언트가 동시성·재시도·재개를 맡는다.
  - 청크 결과를 `<label>_raw.jsonl` 에 즉시 적는다. 다시 돌리면 끝난 청크는 건너뛴다
  - 429/5xx 는 지수 백오프로 재시도, 그 외 4xx 는 즉시 멈춘다 (조용한 폴백 금지)

  본문에 보내는 것은 `reviewId` · `content` · `rating` 뿐이다 — `userName` 은 보내지 않는다.

사용:
  export NVIDIA_API_KEY=nvapi-...
  python3 pipeline/tag_compat.py run --model moonshotai/kimi-k3 --pilot
  python3 pipeline/tag_compat.py collect --label pilot_kimik3
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
    PROMPT_PATH,
    RUNS_DIR,
    ROOT,
    chunk_payload,
    chunks,
    load_reviews,
    parse_text,
    sha256,
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


def run(model: str, pilot: bool, label: str | None, base_url: str, env: str,
        concurrency: int, timeout: int) -> None:
    reviews = load_reviews(pilot)
    label = label or f"{'pilot' if pilot else 'full'}_{model.split('/')[-1].replace('-', '')}"
    key = api_key(env)
    system = PROMPT_PATH.read_text()

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = RUNS_DIR / f"{label}_raw.jsonl"
    # 재개: 이미 받은 청크는 다시 부르지 않는다. 무료 엔드포인트에서 1,250청크를
    # 한 번에 끝내지 못하는 게 정상이라 이게 기능이다.
    done = set()
    if raw_path.exists():
        for line in raw_path.read_text().splitlines():
            if line.strip():
                done.add(json.loads(line)["chunk"])

    todo = [(i, b) for i, b in chunks(reviews) if i not in done]
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
        data = post(base_url, key, body, timeout)
        text = data["choices"][0]["message"]["content"]
        with lock:
            fh.write(json.dumps({"chunk": idx, "text": text}, ensure_ascii=False) + "\n")
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
                "pilot": pilot,
                "reviews": len(reviews),
                "chunkSize": len(reviews) and len(next(chunks(reviews))[1]),
                "maxTokens": MAX_TOKENS,
                "temperature": 0,
                "raw": str(raw_path.relative_to(ROOT)),
                "prompt": {"path": str(PROMPT_PATH.relative_to(ROOT)), "sha256": sha256(PROMPT_PATH)},
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    print(f"[다음]   python3 pipeline/tag_compat.py collect --label {label}")


def collect(label: str) -> None:
    from tag_contract import validate_tags

    man = json.loads((RUNS_DIR / f"{label}.json").read_text())
    reviews = {r["reviewId"]: r for r in load_reviews(man["pilot"])}

    tags, seen, failed = [], set(), []
    for line in (ROOT / man["raw"]).read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        try:
            payload = parse_text(row["text"])
            results = payload["results"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            failed.append({"chunk": row["chunk"], "error": str(exc)})
            continue
        for r in results:
            seen.add(r["reviewId"])
            for a in r.get("aspects") or []:
                tags.append({"reviewId": r["reviewId"], **a})

    validate_tags(tags, reviews)

    out = ROOT / f"data/intermediate/v5_tags_{label}.jsonl"
    out.write_text("".join(json.dumps(t, ensure_ascii=False) + "\n" for t in tags))
    missing = sorted(set(reviews) - seen)
    man["output"] = {
        "path": str(out.relative_to(ROOT)),
        "tags": len(tags),
        "reviewsTagged": len(seen),
        "missingCount": len(missing),
        "failedChunks": failed,
    }
    (RUNS_DIR / f"{label}.json").write_text(json.dumps(man, ensure_ascii=False, indent=2) + "\n")

    print(f"[collect] 태그 {len(tags):,}개 · 리뷰 {len(seen):,}/{len(reviews):,} → {out.relative_to(ROOT)}")
    if failed:
        print(f"[collect] 파싱 실패 청크 {len(failed)}개")
    if missing:
        print(f"[collect] 결과에 없는 리뷰 {len(missing)}건")
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

    c = sub.add_parser("collect", help="결과 수거 + 계약 검증")
    c.add_argument("--label", required=True)

    a = ap.parse_args()
    if a.cmd == "run":
        run(a.model, a.pilot, a.label, a.base_url, a.env, a.concurrency, a.timeout)
    else:
        collect(a.label)


if __name__ == "__main__":
    main()
