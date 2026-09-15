"""
PER-187 근거 측정 — 컨텍스트 정렬 5단 배치.

**이 리포트는 태그 파일이 있어야 돈다.** 태그는 LLM 산출물이고 `data/intermediate/`
라 gitignore 대상이다 (`measure_gate3_polarity.py` · `measure_gate4_sufficiency.py` 와
같은 사정). 대신 리포트 안에 실행 정체(입력 해시·태그 모델·프롬프트 해시)를 박아
어떤 실행에서 나온 배치인지 남긴다.

확인하는 것은 네 가지다.

  1) **전수 데이터에서 배치가 실제로 만들어지는가** — 게이트1~4 를 통과한 셀 하나를
     골라 5단 배치의 스냅샷을 낸다. 무조건부 1건 · 조건부(skinType) 1건
  2) **같은 입력이면 같은 배치인가** — 레코드·태그 입력 순서를 섞어 여러 번 배치하고
     payload 의 sha256 이 같은지 본다. 다르면 에러다
  3) **인용이 원문 부분문자열인가** — 배치가 실은 모든 인용을 원문과 다시 대조한다
     (v4 88.8% 였던 지점). 배치 함수가 이미 에러를 내지만, 몇 건을 확인했는지 센다
  4) **5단 순서가 강제되는가** — 순서를 뒤섞은 payload 가 에러를 내는지 확인한다
  5) **상한이 방향을 지우지 않는가** — 신뢰도 상위 N 건만 그대로 자르면 어떤 방향이
     사라지는지, 방향 보존 후 무엇이 실렸는지를 함께 낸다 (`truncation`)

## 셀을 고르는 규칙 (결정론적)

"아무거나 하나"가 아니다. 배치가 **실제로 일하는 자리**는 방향이 갈린 셀이다 —
한 방향뿐이면 3단 정렬이 뒤집혀도 4단이 같아 보인다. 그래서:

  게이트4를 통과한 `(셀 × aspect)` 중 `direction == mixed` 인 것에서
  **D(언급 작성자)가 가장 큰 것**, 동률이면 `claimId` 사전순 최소

를 축(무조건부 · skinType 기재)마다 하나씩 고른다. 같은 스냅샷이면 같은 셀이 뽑힌다.

사용:
  .venv/bin/python eval/measure_context_layout.py
  → eval/reports/context_layout_per187.json
  .venv/bin/python eval/measure_context_layout.py --check   # 커밋본과 일치 확인
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

from catalog import load_catalog  # noqa: E402
from codebook import load_codebook  # noqa: E402
from context_layout import (  # noqa: E402
    EXCLUDED_AXES,
    LAYOUT_CONDITION_AXES,
    LAYOUT_SCHEMA_VERSION,
    STAGES,
    ContextLayoutError,
    assert_stage_order,
    layout_context,
    render_layout,
)
from contracts import MISSING_SEGMENT  # noqa: E402
from gates import IdentityScope, run_duplicate_gate, run_identity_gate  # noqa: E402
from polarity import aspect_support  # noqa: E402
from policy import (  # noqa: E402
    RECENCY_CUTOFF_MONTH,
    SNAPSHOT_LATEST_MONTH,
    SUFFICIENCY_N_MIN,
    DEFAULT_SUFFICIENCY,
    sufficiency_gate,
)
from sufficiency import (  # noqa: E402
    Claim,
    ClaimSupport,
    EvidenceCell,
    matches,
)
from tag_contract import ASPECTS, is_verbatim  # noqa: E402

REVIEWS_PATH = ROOT / "data/intermediate/v5_reviews.jsonl"
TAGS_PATH = ROOT / "data/intermediate/v5_tags.jsonl"
TAGS_META_PATH = ROOT / "data/intermediate/v5_tags_meta.json"
REPORT_PATH = ROOT / "eval/reports/context_layout_per187.json"

# 3단 인용 상한. N_min(=8) 과 같은 수를 쓴다 — 충분성의 절대 하한이 "이 주장을 세우는
# 데 필요한 최소 작성자 수"이므로, 상위 8건이면 그 하한만큼의 근거가 순위 앞쪽에 있다.
# 자른 몫은 감추지 않고 `omittedByRank` 로 남는다.
MAX_QUOTES = SUFFICIENCY_N_MIN

# 배치가 입력 순서에 흔들리지 않는지 보는 횟수. 시드를 고정해 --check 가 성립하게 한다.
SHUFFLE_RUNS = 20
SHUFFLE_SEED = 20260915


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def require(path: Path, how: str) -> None:
    if not path.exists():
        raise SystemExit(f"입력이 없다: {path.relative_to(ROOT)}\n  {how}")


def gate12(records: list[dict], catalog) -> dict[str, list[dict]]:
    """게이트1 → 게이트2 를 제품별로 건다. 배치의 입력은 그 통과분이다.

    옵션 범위는 걸지 않는다 — 옵션은 질문이 요구할 때만 거는 컷이다 (PER-182).
    """
    grouped: dict[str, list[dict]] = collections.defaultdict(list)
    for record in records:
        grouped[record["productId"]].append(record)

    kept: dict[str, list[dict]] = {}
    for product_id, rows in sorted(grouped.items()):
        first = run_identity_gate(rows, IdentityScope(product_id), catalog)
        kept[product_id] = run_duplicate_gate(first.passed).passed
    return kept


def candidates(kept: dict[str, list[dict]], by_review: dict[int, list[tuple[str, str]]]) -> list[dict]:
    """게이트4를 통과한 `(셀 × aspect)` 후보. 축은 무조건부와 `skinType` 기재뿐이다.

    `measure_gate4_sufficiency.py` 의 빠른 경로와 같은 방식으로 집합을 한 번에 모은다
    — 셀마다 `ClaimSupport.of()` 를 부르면 태그 38,251개를 매번 훑는다. 뽑힌 셀에
    대해서는 아래에서 정본 경로로 다시 만들어 대조한다.
    """
    out: list[dict] = []
    for product_id, rows in sorted(kept.items()):
        if not rows:
            continue
        buckets: list[tuple[str, dict]] = [("product", {})]
        segments = sorted({r["condition"]["skinType"]["segment"] for r in rows})
        for segment in segments:
            if segment != MISSING_SEGMENT:  # 미기재 세그먼트는 조건부 예시로 쓰지 않는다
                buckets.append(("skinType", {"skinType": segment}))

        for axis, condition in buckets:
            members = [r for r in rows if matches(r, condition)]
            authors = frozenset(r["derived"]["authorKey"] for r in members)
            cell = EvidenceCell(product_id, condition, authors)
            by_aspect: dict[str, dict[str, set]] = collections.defaultdict(
                lambda: {"positive": set(), "negative": set(), "neutral": set()})
            for row in members:
                author = row["derived"]["authorKey"]
                for aspect, polarity in by_review.get(row["reviewId"], ()):
                    by_aspect[aspect][polarity].add(author)

            for aspect in ASPECTS:
                stances = by_aspect.get(aspect)
                if not stances:
                    continue
                support = ClaimSupport(
                    aspect=aspect,
                    positive=frozenset(stances["positive"]),
                    negative=frozenset(stances["negative"]),
                    neutral=frozenset(stances["neutral"]),
                )
                counts = support.as_dict(cell)
                decision = sufficiency_gate(
                    support_authors=counts["supportAuthors"],
                    spoke_authors=counts["spokeAuthors"],
                    cell_authors=counts["cellAuthors"],
                    minority_authors=support.minority,
                )
                if not decision.passed:
                    continue
                claim_id = f"{product_id}|{axis}={condition.get('skinType')}|{aspect}"
                out.append({
                    "axis": axis,
                    "claimId": claim_id,
                    "direction": support.direction,
                    "spokeAuthors": counts["spokeAuthors"],
                    "cell": cell,
                    "members": members,
                    "support": support,
                    "decision": decision,
                })
    return out


def pick(rows: list[dict], axis: str) -> dict:
    """축마다 하나. **방향이 갈린 셀 중 D 가 가장 큰 것**, 동률이면 claimId 최소."""
    mixed = [r for r in rows if r["axis"] == axis and r["direction"] == "mixed"]
    if not mixed:
        raise SystemExit(f"[context-layout] {axis} 축에 mixed 후보가 없다")
    return sorted(mixed, key=lambda r: (-r["spokeAuthors"], r["claimId"]))[0]


def build_layout(entry: dict, tags_by_review: dict[int, list[dict]], catalog, codebook,
                 records: list[dict] | None = None, tags: list[dict] | None = None,
                 max_quotes: int | None = MAX_QUOTES):
    """뽑힌 후보 하나를 5단으로 배치한다. 정본 경로(`aspect_support`)로 판정을 다시 만든다."""
    members = records if records is not None else entry["members"]
    aspect = entry["support"].aspect
    if tags is None:
        tags = [t for r in members for t in tags_by_review.get(r["reviewId"], ())]
    cell_tags = [t for t in tags if t["aspect"] == aspect]
    polarity = aspect_support(members, cell_tags, aspect)
    return layout_context(
        product=catalog.product(entry["cell"].product_id),
        claim=Claim(entry["claimId"], entry["cell"], entry["support"]),
        polarity=polarity,
        decision=entry["decision"],
        records=members,
        tags=cell_tags,
        codebook=codebook,
        max_quotes=max_quotes,
    )


def determinism(entry: dict, tags_by_review: dict, catalog, codebook) -> dict:
    """입력 순서를 섞어도 배치가 같은가. 다르면 리포트를 내지 않고 **에러다.**"""
    members = entry["members"]
    aspect = entry["support"].aspect
    tags = [t for r in members for t in tags_by_review.get(r["reviewId"], ())
            if t["aspect"] == aspect]

    rng = random.Random(SHUFFLE_SEED)
    digests = set()
    for _ in range(SHUFFLE_RUNS):
        shuffled_records = members[:]
        shuffled_tags = tags[:]
        rng.shuffle(shuffled_records)
        rng.shuffle(shuffled_tags)
        layout = build_layout(entry, tags_by_review, catalog, codebook,
                              records=shuffled_records, tags=shuffled_tags)
        payload = json.dumps(layout.as_dict(), ensure_ascii=False, sort_keys=False)
        digests.add(hashlib.sha256(payload.encode()).hexdigest())
    if len(digests) != 1:
        raise SystemExit(
            f"[context-layout] {entry['claimId']}: 입력 순서를 섞었더니 배치가 "
            f"{len(digests)}종으로 갈렸다. 배치가 결정론적이지 않다"
        )
    return {
        "shuffledRuns": SHUFFLE_RUNS,
        "seed": SHUFFLE_SEED,
        "distinctPayloads": len(digests),
        "payloadSha256": digests.pop(),
    }


def truncation_profile(layout, full_layout) -> dict:
    """상한을 그냥 잘랐으면 어떤 방향이 사라졌는가. 방향 보존의 근거 수치다.

    4단이 "부정 6명"이라고 적어 놓고 3단에 부정 인용이 한 줄도 없으면, 생성기가
    인용할 수 있는 반대 근거가 화면에서 사라진다 (PER-185 §4-3 결정 3).
    """
    all_quotes = full_layout.as_dict()["stages"]["evidence"]["quotes"]
    shown = layout.as_dict()["stages"]["evidence"]
    plain = all_quotes[:MAX_QUOTES]
    return {
        "maxQuotes": MAX_QUOTES,
        "quotesTotal": len(all_quotes),
        "directionsInCell": sorted({q["polarity"] for q in all_quotes}),
        "directionsInPlainTopN": sorted({q["polarity"] for q in plain}),
        "directionsShown": shown["directionsShown"],
        "reservedForDirection": shown["reservedForDirection"],
        "reservedRanks": [q["rank"] for q in shown["quotes"]
                          if q["reviewId"] in set(shown["reservedForDirection"])],
    }


def verbatim_check(layout, members: list[dict]) -> dict:
    """배치가 낸 인용이 정말 원문 부분문자열인가. 배치가 이미 막지만 **다시 센다.**"""
    content = {r["reviewId"]: r["raw"]["content"] for r in members}
    quotes = layout.as_dict()["stages"]["evidence"]["quotes"]
    verbatim = sum(1 for q in quotes if is_verbatim(q["quote"], content[q["reviewId"]]))
    return {"quotes": len(quotes), "verbatim": verbatim,
            "verbatimPct": round(100 * verbatim / len(quotes), 2) if quotes else 0.0}


def order_enforced(layout) -> dict:
    """5단 순서를 뒤섞은 payload 가 에러를 내는가 (완료 조건)."""
    payload = layout.as_dict()
    checks = {}
    for name, broken in (
        ("reversed", {"stages": {k: payload["stages"][k] for k in reversed(STAGES)}}),
        ("dropped", {"stages": {k: v for k, v in payload["stages"].items() if k != "evidence"}}),
    ):
        candidate = dict(payload)
        candidate.update(broken)
        try:
            assert_stage_order(candidate)
        except ContextLayoutError:
            checks[name] = "에러"
        else:
            raise SystemExit(f"[context-layout] 5단 순서 위반({name})이 통과했다")
    return checks


def spot_check_support(entry: dict, tags_raw: list[dict], kept: dict) -> bool:
    """빠른 경로가 `ClaimSupport.of()` 와 같은 값을 내는지 뽑힌 셀에서 확인한다."""
    cell = entry["cell"]
    reference = ClaimSupport.of(
        tags_raw, kept[cell.product_id], entry["support"].aspect, cell)
    if reference != entry["support"]:
        raise SystemExit(f"[context-layout] 빠른 경로가 ClaimSupport.of() 와 다르다: {entry['claimId']}")
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="재실행 결과가 커밋된 리포트와 같은지만 확인 (§5-2 재현성)")
    args = ap.parse_args()

    require(REVIEWS_PATH, "python3 pipeline/ingest.py")
    require(TAGS_PATH, "python3 pipeline/run_v5.py --steps tag (전수 태깅은 $4 다 — 복사해 쓴다)")

    records = read_jsonl(REVIEWS_PATH)
    tags_raw = read_jsonl(TAGS_PATH)
    tags_meta = json.loads(TAGS_META_PATH.read_text())
    catalog = load_catalog()
    codebook = load_codebook()

    kept = gate12(records, catalog)
    by_review: dict[int, list[tuple[str, str]]] = collections.defaultdict(list)
    tags_by_review: dict[int, list[dict]] = collections.defaultdict(list)
    for tag in tags_raw:
        by_review[tag["reviewId"]].append((tag["aspect"], tag["polarity"]))
        tags_by_review[tag["reviewId"]].append(tag)

    rows = candidates(kept, by_review)
    picks = [("unconditional", pick(rows, "product")), ("skinType", pick(rows, "skinType"))]

    layouts = []
    for name, entry in picks:
        spot_check_support(entry, tags_raw, kept)
        layout = build_layout(entry, tags_by_review, catalog, codebook)
        full_layout = build_layout(entry, tags_by_review, catalog, codebook, max_quotes=None)
        payload = layout.as_dict()
        rendered = render_layout(payload)
        layouts.append({
            "selector": name,
            "claimId": entry["claimId"],
            "axis": entry["axis"],
            "determinism": determinism(entry, tags_by_review, catalog, codebook),
            "quoteCheck": verbatim_check(layout, entry["members"]),
            "truncation": truncation_profile(layout, full_layout),
            "stageOrderViolations": order_enforced(layout),
            "layout": payload,
            "render": rendered.splitlines(),
            "renderSha256": hashlib.sha256(rendered.encode()).hexdigest(),
        })

    by_axis = collections.Counter(r["axis"] for r in rows)
    mixed_by_axis = collections.Counter(r["axis"] for r in rows if r["direction"] == "mixed")

    report = {
        "issue": "PER-187",
        "schemaVersion": LAYOUT_SCHEMA_VERSION,
        "stageOrder": list(STAGES),
        "source": {
            "reviews": {
                "path": str(REVIEWS_PATH.relative_to(ROOT)),
                "sha256": sha256_of(REVIEWS_PATH),
                "records": len(records),
                "afterGate1And2": sum(len(v) for v in kept.values()),
                "products": len(kept),
            },
            "tags": {
                "path": str(TAGS_PATH.relative_to(ROOT)),
                "sha256": sha256_of(TAGS_PATH),
                "tags": len(tags_raw),
                "label": tags_meta["label"],
                "model": tags_meta["model"],
                "prompt": tags_meta["prompt"],
            },
            "catalog": {"products": len(kept)},
        },
        "policy": {
            **DEFAULT_SUFFICIENCY.as_meta(),
            "snapshotLatestMonth": SNAPSHOT_LATEST_MONTH,
            "recencyCutoffMonth": RECENCY_CUTOFF_MONTH,
            "maxQuotes": MAX_QUOTES,
        },
        "conditionAxes": {
            "used": list(LAYOUT_CONDITION_AXES),
            "excluded": [dict(a) for a in EXCLUDED_AXES],
            "note": (
                "이슈 설명문의 '사용기간 · 계절' 은 데이터에 없다. 빼기로 한 사실을 "
                "주석이 아니라 배치 자료에 실어 낸다 (PER-174 의 unavailable 과 같은 규칙)"
            ),
        },
        "selection": {
            "rule": (
                "게이트4 통과 (셀 × aspect) 중 direction='mixed' 이고 D 가 가장 큰 것, "
                "동률이면 claimId 사전순 최소. 축마다 하나 (무조건부 · skinType 기재)"
            ),
            "passedCandidates": len(rows),
            "byAxis": dict(sorted(by_axis.items())),
            "mixedByAxis": dict(sorted(mixed_by_axis.items())),
            "picked": [{"selector": n, "claimId": e["claimId"],
                        "spokeAuthors": e["spokeAuthors"]} for n, e in picks],
        },
        "layouts": layouts,
        "limits": [
            "이 리포트의 후보 수는 무조건부 셀과 skinType **기재** 셀만 센 것이다. "
            "skinTrouble 축과 미기재 세그먼트는 배치 예시로 쓰지 않았을 뿐 배치가 "
            "못 다루는 것이 아니다 — 커버리지 수치는 PER-186 리포트가 정본이다",
            "인용은 태거(zai.glm-4.7) 가 뽑은 snippet 이다. 배치는 그것이 원문 "
            "부분문자열인지만 강제하고 **주제에 맞는 문장인지는 판정하지 않는다** — "
            "태거 방향 오류는 게이트3 의 limitation 으로 따라나간다 (PER-185)",
            "3단 상한 8건은 N_min 과 같은 수를 쓴 것이지 '8건이면 충분하다'는 측정이 "
            "아니다. 상한이 생성 품질에 미치는 영향은 재지 않았다 (PER-189 이후)",
            "방향 보존은 각 방향의 1위 한 건만 채워 넣는다. 부정이 6명이어도 3단에 "
            "실리는 부정 인용은 1건이라 생성기가 '몇 명이 그랬는지' 를 3단이 아니라 "
            "4단 수치에서 읽어야 한다",
            "배치 순서를 바꿨을 때 **출력이 달라지는지**는 이 이슈에서 재지 않았다 — "
            "LLM 호출 실험은 09-16 판정 수치에 들어가지 않아 범위 밖으로 뺐다",
        ],
    }

    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.check:
        if not REPORT_PATH.exists():
            raise SystemExit(f"FAIL: 리포트가 없다 ({REPORT_PATH.relative_to(ROOT)})")
        if REPORT_PATH.read_text() != payload:
            raise SystemExit(
                f"FAIL: 5단 배치 리포트가 재현되지 않는다 ({REPORT_PATH.relative_to(ROOT)})\n"
                "  → 배치 규칙·정렬 키·게이트 판정이 바뀌었다면 리포트를 다시 생성해 함께 커밋한다"
            )
        print(f"OK: 5단 배치 리포트 재현 일치 ({REPORT_PATH.relative_to(ROOT)})")
        return

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(payload)

    print(f"[PER-187] 게이트4 통과 후보 {len(rows)}건 (mixed {sum(mixed_by_axis.values())}건)")
    for row in layouts:
        stages = row["layout"]["stages"]
        print(
            f"  {row['selector']:<14} {row['claimId']}\n"
            f"    3단 인용 {stages['evidence']['quotesShown']}/{stages['evidence']['quotesTotal']}건 "
            f"(전부 원문 부분문자열 {row['quoteCheck']['verbatimPct']}%)\n"
            f"    4단 {stages['direction']['label']} — 긍정 {stages['direction']['positiveAuthors']} · "
            f"부정 {stages['direction']['negativeAuthors']} / 분모 {stages['direction']['denominator']['value']}\n"
            f"    5단 U={stages['sufficiency']['supportAuthors']} D={stages['sufficiency']['spokeAuthors']} "
            f"S={stages['sufficiency']['cellAuthors']} (언급 없음 {stages['sufficiency']['silentAuthors']})\n"
            f"    상한 {row['truncation']['maxQuotes']} — 그냥 자르면 방향 "
            f"{row['truncation']['directionsInPlainTopN']} · 보존 후 {row['truncation']['directionsShown']}\n"
            f"    입력 순서 {row['determinism']['shuffledRuns']}회 셔플 → payload {row['determinism']['distinctPayloads']}종"
        )
    print(f"→ {REPORT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
