"""
PER-184 근거 측정 — 로컬 한국어 임베딩 후보를 **우리 데이터로** 재본다.

공개 벤치(KorSTS·MIRACL-ko·AutoRAG)는 문서 검색 문제고 우리 문제가 아니다. 우리가
알아야 하는 것은 하나다 — **키워드 사전이 못 잡는 표현을 임베딩이 그 축으로 끌어오는가.**
이슈 코멘트가 든 실패 사례가 그것이다("플미주면서" · "지갑터는" · "헤프네요" ·
"날린돈"). `가격`·`저렴`·`세일`·`혜자` 어느 것도 걸리지 않는다.

정답은 사람이 만든 것만 쓴다 — `eval/gold/v5_tags_pilot_gold.jsonl` 432행
(리뷰 188건 × aspect, PER-175). 태거(LLM) 산출물인 `v5_tags.jsonl` 은 쓰지 않는다.
모델 산출물로 모델을 고르면 순환이다.

## 지표 정의

`키워드 가림`(blind)  gold 행의 **정답 aspect 키워드**(`eval/aspect_keywords.json`)가
                      스니펫 안에 부분문자열로 하나도 없는 행. 이 행은 키워드 사전이
                      구조적으로 못 잡는다 — 임베딩이 사야 할 값이 정확히 이것이다.

M1 `aspectKnnP1`      스니펫 하나를 빼고 나머지에서 코사인 최근접 1건을 찾아 aspect 가
                      같은지 본다. **같은 reviewId 는 후보에서 제외한다** — 한 리뷰가
                      같은 문장 조각을 여러 축으로 쪼갠 경우 문체가 그대로 새기 때문이다.
                      "(a) 같은 축 문장을 다른 축 문장보다 가깝게 두는가"에 답한다.

M2 `aspectQueryP1`    축마다 질문 틀(`aspect_keywords.json` 의 `questions`) 을 인코딩해
                      평균낸 것을 **축 프로토타입**으로 놓고, 스니펫을 14개 프로토타입 중
                      가장 가까운 축으로 분류한다. 정답률을 전체/가림/비가림으로 나눈다.
                      "(b) 키워드 밖 표현을 해당 축으로 끌어오는가"에 답한다.

M2b `recallAtK`       축 프로토타입으로 432행을 정렬해 그 축의 gold 행이 상위 k 에 몇 %
                      들어오는지. 축별로 재고 **macro 평균**(축 크기가 2~71 로 편중이라
                      micro 로 재면 큰 축이 다 먹는다). 가림 행만 따로도 낸다.

M3 `separation`       같은 축 쌍의 평균 코사인 − 다른 축 쌍의 평균 코사인. 같은 reviewId
                      쌍은 양쪽 모두에서 제외한다. 값이 클수록 축이 갈린다.

M4 `caseList`         이슈 코멘트가 든 4개 표현이 실제로 들어 있는 리뷰 문장. 정답 라벨이
                      없으므로 **지표가 아니라 사례 목록**이다(n=4). M2 방식으로 분류했을
                      때 `가성비` 가 나오는지와 14축 중 `가성비` 의 순위를 적는다.

키워드 기준선(모델 없이 계산):
  `anyMatchRecall`    정답 aspect 키워드가 걸리는 행 비율 = 1 − 가림률. 키워드 **탐지기**의
                      재현율 천장이다 (정밀도는 재지 않는다 — 여기서는 천장만 필요하다)
  `argmaxAccuracy`    키워드가 걸린 축 중 **가장 긴 매칭 키워드**를 가진 축으로 분류
                      (동률은 `tag_contract.ASPECTS` 순서). 하나도 안 걸리면 오답.
                      M2 와 같은 과제 위에서의 기준선이다

## `--check` 는 모델을 부르지 않는다

가중치 합계가 6GB 고 다운로드가 필요하다. `verify.sh` 의 `--check` 대열에 모델을 넣으면
클론 직후 게이트가 깨진다 (`measure_full_tagging.py`·`measure_gate3_polarity.py` 와 같은
규칙). 그래서 `--check` 는 **모델 없이 다시 계산되는 부분만** 대조한다 — 소스 sha256,
가림률, 키워드 기준선, 사례 목록의 원문 대조. 정답셋이나 키워드 사전이 바뀌면 여기서
걸린다. 모델이 낸 수치는 리포트 안에 실행 정체(모델 ID·revision·차원)와 함께 박아 둔다.

사용:
  python3 eval/measure_embedding_probe.py --models kure,bge-m3,e5-base,ko-sroberta
  python3 eval/measure_embedding_probe.py --check      # 모델 없이 재현 대조
  → eval/reports/embedding_probe_per184.json
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

from embedding_contract import load_embedding_config  # noqa: E402
from tag_contract import ASPECTS  # noqa: E402

GOLD_PATH = ROOT / "eval/gold/v5_tags_pilot_gold.jsonl"
KEYWORDS_PATH = ROOT / "eval/aspect_keywords.json"
REVIEWS_PATH = ROOT / "data/intermediate/v5_reviews.jsonl"
REPORT_PATH = ROOT / "eval/reports/embedding_probe_per184.json"

# 이슈 코멘트(2026-09-14)가 든 실패 사례. `(reviewId, 표현)` 만 적고 문장은 원문에서
# 꺼낸다 — 손으로 옮겨 적으면 인용이 원문 부분문자열이라는 규칙(CLAUDE.md)이 깨진다.
CASE_EXPRESSIONS = (
    (25837778, "플미"),
    (28077603, "지갑터"),
    (61572575, "헤프"),
    (61733979, "날린돈"),
)
CASE_ASPECT = "가성비"
SENTENCE_SPLIT = re.compile(r"(?<=[.!?~])\s+|\n+")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def require(path: Path, how: str) -> None:
    if not path.exists():
        raise SystemExit(f"입력이 없다: {path.relative_to(ROOT)}\n  {how}")


def pct(part: int, whole: int) -> float:
    return round(100 * part / whole, 2) if whole else 0.0


# --- 모델 없이 계산되는 부분 -------------------------------------------------

def load_keywords() -> dict[str, dict]:
    kw = json.loads(KEYWORDS_PATH.read_text())
    got = tuple(k for k in kw if not k.startswith("_"))
    if got != ASPECTS:
        raise SystemExit(
            "aspect_keywords.json 의 축이 tag_contract.ASPECTS 와 다르다.\n"
            f"  사전 {got}\n  계약 {ASPECTS}")
    return {a: kw[a] for a in ASPECTS}


def keyword_hits(snippet: str, kw: dict[str, dict]) -> dict[str, str]:
    """축 → 그 축에서 걸린 가장 긴 키워드. 안 걸린 축은 없다."""
    out: dict[str, str] = {}
    for aspect in ASPECTS:
        matched = [k for k in kw[aspect]["keywords"] if k in snippet]
        if matched:
            out[aspect] = max(matched, key=len)
    return out


def keyword_predict(snippet: str, kw: dict[str, dict]) -> str | None:
    """가장 긴 매칭 키워드를 가진 축. 동률은 ASPECTS 순서. 하나도 없으면 None."""
    hits = keyword_hits(snippet, kw)
    if not hits:
        return None
    return min(hits, key=lambda a: (-len(hits[a]), ASPECTS.index(a)))


def build_gold(kw: dict[str, dict]) -> list[dict]:
    rows = []
    for r in read_jsonl(GOLD_PATH):
        if r["aspect"] not in ASPECTS:
            raise SystemExit(f"정답셋에 계약 밖 aspect: {r['aspect']}")
        snippet = r["snippet"]
        rows.append({
            "reviewId": r["reviewId"],
            "aspect": r["aspect"],
            "snippet": snippet,
            "blind": not any(k in snippet for k in kw[r["aspect"]]["keywords"]),
            "kwPredicted": keyword_predict(snippet, kw),
        })
    rows.sort(key=lambda r: (r["reviewId"], ASPECTS.index(r["aspect"]), r["snippet"]))
    return rows


def keyword_baseline(gold: list[dict]) -> dict:
    blind = [r for r in gold if r["blind"]]
    visible = [r for r in gold if not r["blind"]]
    per_aspect = {}
    for aspect in ASPECTS:
        rows = [r for r in gold if r["aspect"] == aspect]
        b = [r for r in rows if r["blind"]]
        per_aspect[aspect] = {
            "rows": len(rows),
            "blind": len(b),
            "blindPct": pct(len(b), len(rows)),
            "argmaxCorrect": sum(1 for r in rows if r["kwPredicted"] == aspect),
            "argmaxAccuracyPct": pct(
                sum(1 for r in rows if r["kwPredicted"] == aspect), len(rows)),
        }

    def acc(rows: list[dict]) -> float:
        return pct(sum(1 for r in rows if r["kwPredicted"] == r["aspect"]), len(rows))

    return {
        "definition": {
            "anyMatchRecall": "정답 aspect 의 키워드가 스니펫에 부분문자열로 하나라도 "
                              "걸리는 행의 비율. 키워드 탐지기 재현율의 천장이다",
            "argmaxAccuracy": "가장 긴 매칭 키워드를 가진 축으로 분류했을 때의 정답률 "
                              "(동률은 ASPECTS 순서, 무매칭은 오답). M2 와 같은 과제다",
        },
        "rows": len(gold),
        "reviews": len({r["reviewId"] for r in gold}),
        "blindRows": len(blind),
        "blindPct": pct(len(blind), len(gold)),
        "anyMatchRecallPct": pct(len(visible), len(gold)),
        "argmaxAccuracyPct": acc(gold),
        "argmaxAccuracyBlindPct": acc(blind),
        "argmaxAccuracyVisiblePct": acc(visible),
        "byAspect": per_aspect,
    }


def build_cases(kw: dict[str, dict]) -> list[dict]:
    """이슈 코멘트의 표현이 실제로 들어 있는 리뷰 문장을 원문에서 꺼낸다."""
    wanted = dict(CASE_EXPRESSIONS)
    found: dict[int, dict] = {}
    for line in REVIEWS_PATH.open():
        rec = json.loads(line)
        rid = rec["reviewId"]
        if rid not in wanted:
            continue
        content = rec["raw"]["content"]
        expr = wanted[rid]
        sentences = [s.strip() for s in SENTENCE_SPLIT.split(content) if expr in s]
        if not sentences:
            raise SystemExit(f"리뷰 {rid} 본문에 '{expr}' 가 없다 — 사례 목록이 낡았다")
        sentence = sentences[0]
        if sentence not in content:
            raise SystemExit(f"리뷰 {rid}: 꺼낸 문장이 원문 부분문자열이 아니다")
        found[rid] = {
            "reviewId": rid,
            "productId": rec["productId"],
            "expression": expr,
            "sentence": sentence,
            "keywordHits": keyword_hits(sentence, kw),
            "keywordPredicted": keyword_predict(sentence, kw),
            "blindForCaseAspect": not any(
                k in sentence for k in kw[CASE_ASPECT]["keywords"]),
        }
    missing = [rid for rid, _ in CASE_EXPRESSIONS if rid not in found]
    if missing:
        raise SystemExit(f"사례 리뷰가 스냅샷에 없다: {missing}")
    return [found[rid] for rid, _ in CASE_EXPRESSIONS]


def corpus_fit(cands: dict) -> dict:
    """리뷰 본문 길이가 후보의 입력 상한 안에 들어오는가.

    토큰이 아니라 **글자 수**로 잰다 — 토크나이저가 후보마다 다르고, 글자 수는
    토큰 수의 하한이라 "상한을 넘는다"는 판정은 보수적으로만 틀린다(한국어는
    글자당 1 토큰 이상인 경우가 흔하므로 실제 초과율은 이보다 높다).
    """
    lengths = sorted(json.loads(l)["derived"]["contentLength"]
                     for l in REVIEWS_PATH.open())
    n = len(lengths)

    def q(p: float) -> int:
        return lengths[min(n - 1, int(n * p))]

    over = {}
    for key, c in sorted(cands.items()):
        limit = c["maxSeqLength"]
        over[key] = {
            "maxSeqLength": limit,
            "reviewsOverLimitPct": pct(sum(1 for x in lengths if x > limit), n),
        }
    return {
        "unit": "글자 수 (토큰 아님 — 토큰 수의 하한이다)",
        "reviews": n,
        "quantiles": {"p50": q(0.5), "p90": q(0.9), "p95": q(0.95),
                      "p99": q(0.99), "max": lengths[-1]},
        "byCandidate": over,
    }


def model_free_section(cands: dict) -> dict:
    require(GOLD_PATH, "eval/gold/ 는 손으로 만든 고정물이다 — 재생성되지 않는다")
    require(REVIEWS_PATH, "python3 pipeline/ingest.py")
    kw = load_keywords()
    gold = build_gold(kw)
    return {
        "source": {
            "gold": {
                "path": "eval/gold/v5_tags_pilot_gold.jsonl",
                "sha256": sha256(GOLD_PATH),
                "rows": len(gold),
                "reviews": len({r["reviewId"] for r in gold}),
            },
            "keywords": {
                "path": "eval/aspect_keywords.json",
                "sha256": sha256(KEYWORDS_PATH),
            },
            "reviews": {
                "path": "data/intermediate/v5_reviews.jsonl",
                "sha256": sha256(REVIEWS_PATH),
            },
            "note": "정답은 사람이 만든 gold 432행만 쓴다 — 태거(LLM) 산출물인 "
                    "v5_tags.jsonl 은 쓰지 않는다. 모델 산출물로 모델을 고르면 순환이다",
        },
        "corpusFit": corpus_fit(cands),
        "keywordBaseline": keyword_baseline(gold),
        "caseList": {
            "aspect": CASE_ASPECT,
            "note": "이슈 코멘트(2026-09-14)가 든 실패 사례. 정답 라벨이 없으므로 "
                    "지표가 아니라 사례 목록이다 (n=4). 문장은 원문 부분문자열이다",
            "cases": build_cases(kw),
        },
    }, gold


# --- 모델이 필요한 부분 -------------------------------------------------------

WEIGHT_FILES = ("model.safetensors", "pytorch_model.bin")


def cached_weights(model_id: str, cache_dir: Path | None) -> dict:
    """캐시에 실제로 내려받힌 가중치 파일이 **어느 커밋의 것인지** 적는다.

    설정에 박은 revision 만 믿으면 안 된다. `BAAI/bge-m3` 는 그 커밋에 `.bin` 만
    올라와 있어서 transformers 가 safetensors 변환본(다른 커밋)을 대신 가져온다.
    '무엇으로 잰 수치인가'를 되짚으려면 그 사실이 리포트에 남아야 한다.
    """
    from huggingface_hub.constants import HF_HUB_CACHE

    root = Path(cache_dir) if cache_dir else Path(HF_HUB_CACHE)
    repo = root / ("models--" + model_id.replace("/", "--")) / "snapshots"
    if not repo.is_dir():
        return {}
    out = {}
    for snap in sorted(repo.iterdir()):
        files = sorted(f.name for f in snap.iterdir() if f.name in WEIGHT_FILES)
        if files:
            out[snap.name] = files
    return out


def _unit(v):
    import numpy as np
    n = float(np.linalg.norm(v))
    return v / n if n else v


def encode_all(spec: dict, gold: list[dict], cases: list[dict],
               kw: dict[str, dict], cache_dir: Path | None):
    from sentence_transformers import SentenceTransformer

    kwargs = {"revision": spec["revision"], "trust_remote_code": False}
    if cache_dir:
        kwargs["cache_folder"] = str(cache_dir)
    model = SentenceTransformer(spec["model"], **kwargs)
    qp, pp = spec.get("queryPrefix", ""), spec.get("passagePrefix", "")

    def enc(texts: list[str], prefix: str):
        return model.encode([prefix + t for t in texts], batch_size=16,
                            normalize_embeddings=True, show_progress_bar=False,
                            convert_to_numpy=True)

    snippets = enc([r["snippet"] for r in gold], pp)
    # 프로토타입은 질문을 **따로 인코딩해 평균**낸다(정본). 이어붙이면 짧은 컨텍스트
    # 모델(ko-sroberta 는 128 토큰)에서 뒤쪽 질문이 잘려 후보가 불리해진다.
    # 이어붙인 변형도 함께 재서 이 선택이 순위를 바꾸는지 리포트에 남긴다
    import numpy as np
    protos = np.stack([
        _unit(enc(kw[a]["questions"], qp).mean(axis=0)) for a in ASPECTS])
    protos_concat = enc([" ".join(kw[a]["questions"]) for a in ASPECTS], qp)
    case_vecs = enc([c["sentence"] for c in cases], pp)
    dim = int(snippets.shape[1])
    if dim != spec["dimension"]:
        raise SystemExit(
            f"{spec['model']}: 실제 차원 {dim} 이 설정의 dimension {spec['dimension']} 과 다르다")
    weights = cached_weights(spec["model"], cache_dir)
    return snippets, protos, protos_concat, case_vecs, dim, weights


def score_model(spec: dict, gold: list[dict], cases: list[dict],
                kw: dict[str, dict], top_k: list[int], cache_dir: Path | None) -> dict:
    import numpy as np

    snip, protos, protos_concat, case_vecs, dim, weights = encode_all(
        spec, gold, cases, kw, cache_dir)
    n = len(gold)
    aspect_of = [r["aspect"] for r in gold]
    review_of = np.array([r["reviewId"] for r in gold])
    blind = np.array([r["blind"] for r in gold])

    # M1 — 같은 reviewId 는 후보에서 뺀다
    sim = snip @ snip.T
    same_review = review_of[:, None] == review_of[None, :]
    masked = np.where(same_review, -2.0, sim)
    nn = masked.argmax(axis=1)
    knn_hit = np.array([aspect_of[i] == aspect_of[nn[i]] for i in range(n)])
    knn_eligible = (~same_review).any(axis=1)

    def rate(mask) -> float:
        m = mask & knn_eligible
        return pct(int((knn_hit & m).sum()), int(m.sum()))

    # M2 — 축 프로토타입 분류
    qsim = snip @ protos.T
    pred = [ASPECTS[i] for i in qsim.argmax(axis=1)]
    q_hit = np.array([pred[i] == aspect_of[i] for i in range(n)])

    def qacc(mask) -> float:
        return pct(int((q_hit & mask).sum()), int(mask.sum()))

    # 프로토타입 구성 방식 민감도 — 이어붙인 변형
    cpred = [ASPECTS[i] for i in (snip @ protos_concat.T).argmax(axis=1)]
    c_hit = np.array([cpred[i] == aspect_of[i] for i in range(n)])

    def concat_acc(mask) -> float:
        return pct(int((c_hit & mask).sum()), int(mask.sum()))

    # M2b — 축별 Recall@k (macro)
    recall = {str(k): {} for k in top_k}
    per_aspect = {}
    for ai, aspect in enumerate(ASPECTS):
        order = np.argsort(-qsim[:, ai], kind="stable")
        is_a = np.array([a == aspect for a in aspect_of])
        a_blind = is_a & blind
        row = {"rows": int(is_a.sum()), "blindRows": int(a_blind.sum()),
               "queryP1Pct": qacc(is_a), "queryP1BlindPct": qacc(a_blind),
               "recallAtK": {}, "recallAtKBlind": {}, "recallCeilingAtK": {}}
        for k in top_k:
            top = order[:k]
            row["recallAtK"][str(k)] = pct(int(is_a[top].sum()), int(is_a.sum()))
            row["recallAtKBlind"][str(k)] = pct(
                int(a_blind[top].sum()), int(a_blind.sum()))
            # 축 크기가 k 보다 크면 R@k 는 구조적으로 100% 에 닿을 수 없다.
            # 천장을 함께 적어 축 간 비교로 잘못 읽히지 않게 한다
            row["recallCeilingAtK"][str(k)] = pct(min(k, int(is_a.sum())), int(is_a.sum()))
            recall[str(k)].setdefault("_all", []).append(row["recallAtK"][str(k)])
            if a_blind.sum():
                recall[str(k)].setdefault("_blind", []).append(
                    row["recallAtKBlind"][str(k)])
        per_aspect[aspect] = row

    macro = {
        str(k): {
            "allPct": round(float(np.mean(recall[str(k)]["_all"])), 2),
            "blindPct": round(float(np.mean(recall[str(k)]["_blind"])), 2),
            "blindAspects": len(recall[str(k)]["_blind"]),
        } for k in top_k
    }

    # M3 — 축 분리도. 같은 reviewId 쌍은 양쪽에서 제외
    iu = np.triu_indices(n, k=1)
    pair_same_aspect = np.array(
        [aspect_of[i] == aspect_of[j] for i, j in zip(*iu)])
    pair_same_review = same_review[iu]
    keep = ~pair_same_review
    vals = sim[iu]
    intra = vals[keep & pair_same_aspect]
    inter = vals[keep & ~pair_same_aspect]

    # M4 — 사례 목록
    case_sim = case_vecs @ protos.T
    case_out = []
    ci = ASPECTS.index(CASE_ASPECT)
    for row, s in zip(cases, case_sim):
        order = np.argsort(-s, kind="stable")
        case_out.append({
            "reviewId": row["reviewId"],
            "expression": row["expression"],
            "predictedAspect": ASPECTS[int(order[0])],
            "caseAspectRank": int(list(order).index(ci)) + 1,
            "caseAspectCos": round(float(s[ci]), 4),
            "topAspectCos": round(float(s[order[0]]), 4),
        })

    return {
        "model": spec["model"],
        "revision": spec["revision"],
        "dimension": dim,
        "maxSeqLength": spec["maxSeqLength"],
        "cachedWeights": weights,
        "queryPrefix": spec.get("queryPrefix", ""),
        "passagePrefix": spec.get("passagePrefix", ""),
        "aspectKnnP1": {
            "allPct": rate(np.ones(n, dtype=bool)),
            "blindPct": rate(blind),
            "visiblePct": rate(~blind),
        },
        "aspectQueryP1": {
            "allPct": qacc(np.ones(n, dtype=bool)),
            "blindPct": qacc(blind),
            "visiblePct": qacc(~blind),
        },
        "recallAtKMacro": macro,
        "separation": {
            "intraAspectCos": round(float(intra.mean()), 4),
            "interAspectCos": round(float(inter.mean()), 4),
            "marginCos": round(float(intra.mean() - inter.mean()), 4),
            "pairsIntra": int(intra.size),
            "pairsInter": int(inter.size),
        },
        "byAspect": per_aspect,
        "caseList": case_out,
        "protoSensitivity": {
            "note": "프로토타입을 '질문별 인코딩의 평균'(정본) 대신 '질문을 이어붙여 "
                    "한 번 인코딩'으로 만들었을 때의 같은 지표. 이 선택만으로 순위가 "
                    "바뀌므로 probe 수치를 절대값으로 읽지 않는다",
            "meanQueryP1Pct": qacc(np.ones(n, dtype=bool)),
            "meanQueryP1BlindPct": qacc(blind),
            "concatQueryP1Pct": concat_acc(np.ones(n, dtype=bool)),
            "concatQueryP1BlindPct": concat_acc(blind),
        },
    }


# --- 실행 --------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="",
                    help="쉼표로 구분한 후보 키 (embedding_config.json 의 candidates). "
                         "비우면 measured=true 인 후보 전부")
    ap.add_argument("--cache-dir", default="",
                    help="모델 가중치를 둘 곳. 저장소 안에 두지 않는다")
    ap.add_argument("--check", action="store_true",
                    help="모델 없이 재현 대조 — 소스 sha256·가림률·키워드 기준선·사례 원문")
    args = ap.parse_args()

    cfg = load_embedding_config()
    cands = cfg["candidates"]
    free, gold = model_free_section(cands)

    if args.check:
        if not REPORT_PATH.exists():
            raise SystemExit(f"FAIL: 리포트가 없다 ({REPORT_PATH.relative_to(ROOT)})")
        have = json.loads(REPORT_PATH.read_text())
        for key in ("source", "corpusFit", "keywordBaseline", "caseList"):
            if have.get(key) != free[key]:
                raise SystemExit(
                    f"FAIL: 임베딩 probe 의 모델 없는 부분이 재현되지 않는다 ({key})\n"
                    "  → 정답셋·키워드 사전·스냅샷이 바뀌었다면 모델을 다시 돌려 "
                    "리포트를 통째로 갱신한다 (부분 갱신 금지)")
        if have.get("selected", {}).get("embeddingVersion") != cfg["selected"]["embeddingVersion"]:
            raise SystemExit(
                "FAIL: 선정 모델의 embeddingVersion 이 리포트와 다르다\n"
                "  → 모델을 바꿨으면 벡터·어휘·정답셋을 한 작업으로 갱신한다 (PER-184)")
        print(f"OK: 임베딩 probe 재현 일치 — 모델 없는 부분만 대조 "
              f"({REPORT_PATH.relative_to(ROOT)})")
        return

    kw = load_keywords()
    cases = free["caseList"]["cases"]
    keys = ([k.strip() for k in args.models.split(",") if k.strip()]
            or [k for k, v in cands.items() if v.get("measured")])
    unknown = [k for k in keys if k not in cands]
    if unknown:
        raise SystemExit(f"모르는 후보 키: {unknown}\n  있는 키: {sorted(cands)}")

    cache_dir = Path(args.cache_dir) if args.cache_dir else None
    top_k = cfg["probe"]["topK"]
    measured = {}
    for key in keys:
        print(f"[{key}] {cands[key]['model']} 인코딩 중…", flush=True)
        measured[key] = score_model(cands[key], gold, cases, kw, top_k, cache_dir)

    report = {
        "issue": "PER-184",
        "scope": "모델 선정까지. 벡터 생성·게이트2 부착·전수 임베딩은 사이클 2 (PER-212)",
        **free,
        "probe": {
            "topK": top_k,
            "definitions": {
                "blind": "정답 aspect 의 키워드가 스니펫에 하나도 없는 행",
                "aspectKnnP1": "같은 reviewId 를 뺀 최근접 1건의 aspect 일치율",
                "aspectQueryP1": "축 질문 틀 평균을 프로토타입으로 둔 14지 분류 정답률",
                "recallAtKMacro": "축 프로토타입 정렬 상위 k 안의 그 축 gold 행 비율, "
                                  "축별로 재서 macro 평균 (축 크기 2~71 편중 때문)",
                "separation": "같은 축 쌍 평균 코사인 − 다른 축 쌍 평균 코사인 "
                              "(같은 reviewId 쌍 제외)",
            },
            "note": "모델 수치는 이 리포트에 박는다 — 가중치 6GB 다운로드가 필요해 "
                    "verify.sh --check 대열에 넣지 않는다 (measure_gate3_polarity.py 와 "
                    "같은 규칙). --check 는 모델 없는 부분만 대조한다",
        },
        "models": measured,
        "selected": {
            "embeddingVersion": cfg["selected"]["embeddingVersion"],
            "model": cfg["selected"]["model"],
            "revision": cfg["selected"]["revision"],
            "vocabVersion": cfg["selected"]["vocabVersion"],
        },
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    b = free["keywordBaseline"]
    print(f"\n[키워드 기준선] gold {b['rows']}행 (리뷰 {b['reviews']}건) · "
          f"가림 {b['blindRows']}행 ({b['blindPct']}%)")
    print(f"  탐지 천장 {b['anyMatchRecallPct']}% · 14지 분류 {b['argmaxAccuracyPct']}% "
          f"(가림 {b['argmaxAccuracyBlindPct']}% / 비가림 {b['argmaxAccuracyVisiblePct']}%)")
    print(f"  {CASE_ASPECT} 가림률 {b['byAspect'][CASE_ASPECT]['blindPct']}% "
          f"({b['byAspect'][CASE_ASPECT]['blind']}/{b['byAspect'][CASE_ASPECT]['rows']}행)")
    print(f"\n{'후보':16s} {'차원':>5s} {'knnP1':>7s} {'queryP1':>8s} {'가림':>7s} "
          f"{'R@5가림':>8s} {'분리도':>7s} {'사례':>5s}")
    for key, m in measured.items():
        hit = sum(1 for c in m["caseList"] if c["predictedAspect"] == CASE_ASPECT)
        r5 = m["recallAtKMacro"].get("5", {}).get("blindPct", "-")
        print(f"{key:16s} {m['dimension']:5d} {m['aspectKnnP1']['allPct']:7.2f} "
              f"{m['aspectQueryP1']['allPct']:8.2f} {m['aspectQueryP1']['blindPct']:7.2f} "
              f"{r5:8} {m['separation']['marginCos']:7.4f} {hit:3d}/4")
    print(f"\n→ {REPORT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
