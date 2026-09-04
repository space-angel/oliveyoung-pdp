"""
리뷰 신뢰도 사전 점수 (PER-174 / PRD §3-4).

모든 리뷰가 같은 무게일 수 없다. 그런데 **무게는 필터가 아니다** — 점수가 낮은 리뷰를
버리지 않고 근거 정렬에서 뒤로 보낸다. 버리는 판단은 게이트(#4, PER-182~188)에서만
하고, 버릴 때는 `rejected[]` 에 사유가 남는다.

이 점수가 쓰이는 곳은 두 군데다.
  1) 근거 정렬 — 같은 주장을 받치는 리뷰 중 무엇을 먼저 보여줄지
  2) 중복 게이트의 1표 선택 — `(작성자, 제품)` 중복에서 어느 1건을 남길지
     (PER-170: 점수 최고 → `reviewDate` 최신 → `reviewId` 최소)

## 6개 신호를 실측에 비춰본 결과

PRD §3-4 가 신호 6개와 방향을 제시한다. 그 방향을 그대로 상수로 박지 않고
골든셋 200건(`eval/gold/v5_tags_pilot_gold.jsonl`)의 aspect 태그 수를
"이 리뷰가 실제로 담은 근거의 양"으로 놓고 신호별 효과를 쟀다
(`eval/reports/trust_signals_per174.json`).

| PRD 신호 | 이 모듈에서 | 실측 |
| -- | -- | -- |
| 본문 길이 ↑(상한) | `contentLength` **0.6** | Δ평균태그 +1.31 [+0.98,+1.62] · 무태그 3.7% vs 17.8% |
| 동일 본문 존재 ↓↓ | `uniqueContent` **0.4** | 587그룹 1,433건(5.73%). 결정론적 |
| 배송·포장만 언급 ↓ | `onTopic` **0.5** (입수 시점 미가용) | 어휘 규칙은 정밀도 25% · 재현율 4.2% → 채택 안 함 |
| 이미지 첨부 ↑ | `hasPhoto` **0.0** | Δ+0.18 [-0.18,+0.54] — 0을 배제하지 못한다 |
| 사용 기간 표기 ↑ | `usagePeriod` **0.0** | Δ-0.26 [-0.61,+0.09] — **부호가 PRD 주장과 반대** |
| 좋아요 수 ↑ | `likes` **0.0** | 길이를 통제하면 효과 소멸 (Δ+0.02 / -0.13) |

가중치 0.0 은 "신호를 뺐다"가 아니라 **"측정에서 효과가 0과 구별되지 않았다"** 는
기록이다. 신호는 계속 계산되고 `signals` 에 남으므로, 근거가 생기면 코드가 아니라
`pipeline/trust_weights.json` 만 고치면 된다.

### `usefulPoint` 는 좋아요 수가 아니다 — 쓰지 않는다

PRD 표는 좋아요 신호로 `usefulPoint` / `recommendCount` 를 함께 적었지만 둘은
같은 것이 아니다. 실측에서 두 값의 순위상관은 **0.008** 이다.

  - `usefulPoint` 는 올리브영의 자체 정렬 점수다. 크롤러가 이 값 내림차순으로
    수집했고 153/153 goodsNo 그룹이 파일 순서대로 단조 비증가다. 즉 **표본 자체가
    이 변수 위에서 잘려 있다**(제품당 상위 500건). 게다가 제품별 중앙값이
    8.0~396.0 으로 50배 차이라 제품 간 비교가 성립하지 않는다
  - 이 값을 신뢰도로 쓰면 우리가 재는 것은 리뷰의 근거력이 아니라
    **올리브영의 랭킹을 그대로 다시 들여오는 것**이 된다

그래서 좋아요 신호는 순수 카운트인 `recommendCount` 로만 계산한다. 다만 이 값도
리뷰 나이와 교란돼 있어(순위상관 -0.343, 2026년 리뷰의 84.7% 가 0) **제품 안에서
백분위로 정규화**한 뒤 쓴다 — 원값을 쓰면 리센시 컷(PER-172)으로 남긴 최신 리뷰를
바로 뒤로 밀어버린다.

## 순수 함수 (§5-2)

`score(record, context, weights)` 는 같은 인자에 항상 같은 값을 준다. 중복 본문
그룹 크기와 제품별 좋아요 백분위는 리뷰 1건만 봐서는 알 수 없으므로 `ScoringContext`
로 **명시해서 넘긴다** — 전역 상태를 읽지 않는다. context 는 스냅샷에서 결정론적으로
만들어지므로 같은 스냅샷이면 같은 점수다.

점수는 `signals` 값(가중치와 무관)과 함께 남긴다. 하위 단계가 다른 가중치로 다시
매기고 싶으면 입수를 다시 돌리지 않고 `rescore()` 만 부르면 된다.

## 가용하지 않은 신호는 조용히 0점이 아니다

`onTopic` 은 aspect 태깅(PER-175) 결과가 있어야 판정된다. 입수 시점에는 없다.
없는 신호를 0점으로 깔면 "근거가 없다"와 "아직 안 쟀다"가 구별되지 않는다
(PER-172 의 `unobserved` 와 같은 이유). 그래서 가용한 신호의 가중치 합으로만
정규화하고, 무엇이 빠졌는지 `unavailable` 에 남긴다.
"""
from __future__ import annotations

import collections
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).parents[1]
WEIGHTS_PATH = Path(__file__).parent / "trust_weights.json"

TRUST_SCHEMA_VERSION = "trust-v1"
SCORE_DECIMALS = 4

# 신호 이름. 여기 없는 이름이 설정에 있으면 오타이므로 에러다.
SIGNALS: tuple[str, ...] = (
    "contentLength",
    "uniqueContent",
    "onTopic",
    "hasPhoto",
    "usagePeriod",
    "likes",
)
# 입수 시점에 계산할 수 없는 신호와 그 이유.
DEFERRED_SIGNALS = {
    "onTopic": "aspect 태깅 결과가 있어야 판정된다 (PER-175). 입수 시점에는 미가용",
}


class TrustConfigError(ValueError):
    """가중치 설정이 계약을 위반했다. 조용히 기본값으로 돌아가지 않는다."""


@dataclass(frozen=True)
class TrustWeights:
    """신호별 가중치와 길이 램프 파라미터. 코드 상수가 아니라 설정 파일이 정본이다."""

    weights: dict[str, float]
    floor_chars: int
    cap_chars: int
    shape: str
    version: str
    source_sha256: str

    @classmethod
    def load(cls, path: Path = WEIGHTS_PATH) -> "TrustWeights":
        if not path.exists():
            raise TrustConfigError(f"가중치 설정이 없다: {path}")
        body = path.read_bytes()
        return cls.from_dict(json.loads(body), source_sha256=hashlib.sha256(body).hexdigest())

    @classmethod
    def from_dict(cls, cfg: dict, source_sha256: str = "") -> "TrustWeights":
        raw = cfg.get("weights")
        if not isinstance(raw, dict):
            raise TrustConfigError("설정에 weights 객체가 없다")
        unknown = sorted(set(raw) - set(SIGNALS))
        if unknown:
            raise TrustConfigError(f"알 수 없는 신호 {unknown}. 쓸 수 있는 신호: {list(SIGNALS)}")
        missing = sorted(set(SIGNALS) - set(raw))
        if missing:
            raise TrustConfigError(
                f"신호 {missing} 의 가중치가 없다. 안 쓰기로 했으면 0.0 을 명시하라 "
                "— 빠뜨린 것과 0 으로 정한 것을 구별할 수 없게 된다"
            )
        weights = {}
        for name, value in raw.items():
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
                raise TrustConfigError(f"가중치 {name}={value!r} 는 0 이상의 수여야 한다")
            weights[name] = float(value)
        if not any(weights[s] > 0 for s in SIGNALS if s not in DEFERRED_SIGNALS):
            raise TrustConfigError(
                "입수 시점에 가용한 신호의 가중치가 전부 0 이다. 그러면 모든 리뷰가 같은 점수가 된다"
            )

        ramp = cfg.get("contentLength") or {}
        floor_chars, cap_chars = int(ramp.get("floorChars", 0)), int(ramp.get("capChars", 0))
        if cap_chars <= floor_chars:
            raise TrustConfigError(f"capChars({cap_chars}) 는 floorChars({floor_chars}) 보다 커야 한다")
        shape = ramp.get("shape", "sqrt")
        if shape not in RAMPS:
            raise TrustConfigError(f"알 수 없는 shape {shape!r}. 쓸 수 있는 값: {sorted(RAMPS)}")

        return cls(
            weights=weights,
            floor_chars=floor_chars,
            cap_chars=cap_chars,
            shape=shape,
            version=cfg.get("version", TRUST_SCHEMA_VERSION),
            source_sha256=source_sha256,
        )

    def as_dict(self) -> dict:
        """meta 에 남기는 형태 — 점수를 이 설정에 귀속시키기 위한 신원."""
        return {
            "version": self.version,
            "path": str(WEIGHTS_PATH.relative_to(ROOT)),
            "sha256": self.source_sha256,
            "weights": dict(self.weights),
            "contentLength": {
                "floorChars": self.floor_chars,
                "capChars": self.cap_chars,
                "shape": self.shape,
            },
        }


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


# 길이 → [0,1] 램프. 골든셋 200건과의 피어슨 상관: sqrt 0.563 / log 0.557 / linear 0.537
# (원값 그대로는 0.464). 상한을 두는 것만으로 0.464 → 0.537 이 오르므로 상한이 요점이고,
# 곡선 모양의 차이는 그 다음이다.
RAMPS = {
    "linear": lambda t: t,
    "sqrt": math.sqrt,
    "log": lambda t: math.log1p(t * (math.e - 1)),
}


@dataclass(frozen=True)
class ScoringContext:
    """리뷰 1건만 봐서는 알 수 없는 값. 스냅샷에서 결정론적으로 만든다.

    `content_group_size`  같은 본문 해시를 가진 리뷰 수 (1 이면 유일)
    `like_percentile`     제품 안에서의 `recommendCount` 백분위 [0,1]
    `aspect_counts`       reviewId → aspect 태그 수. **None 이면 `onTopic` 미가용**
    """

    content_group_size: dict[str, int]
    like_percentile: dict[int, float]
    aspect_counts: dict[int, int] | None = None

    @classmethod
    def from_records(
        cls, records: list[dict], aspect_counts: dict[int, int] | None = None
    ) -> "ScoringContext":
        group = collections.Counter(r["derived"]["contentHash"] for r in records)

        # 좋아요는 제품 안에서만 비교한다. 제품마다 노출량이 달라 원값은 섞이지 않는다.
        by_product: dict[str, list[int]] = collections.defaultdict(list)
        for r in records:
            by_product[r["productId"]].append(int(r["raw"].get("recommendCount") or 0))
        sorted_likes = {p: sorted(v) for p, v in by_product.items()}

        percentile: dict[int, float] = {}
        for r in records:
            values = sorted_likes[r["productId"]]
            n = len(values)
            likes = int(r["raw"].get("recommendCount") or 0)
            if n <= 1:
                percentile[r["reviewId"]] = 0.0
                continue
            # 자기보다 **낮은** 값의 비율. 0 이 71.5% 라 동점이 많고, 동점끼리는 같은 값을
            # 받아야 한다 (동점을 순서로 가르면 파일 순서가 점수에 새 든다).
            below = _bisect_left(values, likes)
            percentile[r["reviewId"]] = round(below / (n - 1), 6)
        return cls(
            content_group_size=dict(group),
            like_percentile=percentile,
            aspect_counts=dict(aspect_counts) if aspect_counts is not None else None,
        )


def _bisect_left(values: list[int], target: int) -> int:
    lo, hi = 0, len(values)
    while lo < hi:
        mid = (lo + hi) // 2
        if values[mid] < target:
            lo = mid + 1
        else:
            hi = mid
    return lo


def signal_values(record: dict, context: ScoringContext, weights: TrustWeights) -> dict[str, float]:
    """신호별 값 [0,1] — 클수록 믿을 만하다. **가중치와 무관하다.**

    가중치와 분리해 두는 것이 요점이다. 하위 단계가 가중치를 바꿔도 입수를 다시
    돌릴 필요가 없고, 가중치 0 인 신호의 값도 리포트에 남아 나중에 재측정할 수 있다.
    """
    raw = record["raw"]
    span = weights.cap_chars - weights.floor_chars
    length = int(record["derived"].get("contentLength") or 0)
    ramp = RAMPS[weights.shape]

    values = {
        "contentLength": round(ramp(_clamp01((length - weights.floor_chars) / span)), 6),
        # ↓↓ — 같은 본문이 n 개면 1/n. 587그룹 중 최대 그룹까지 같은 규칙으로 내려간다
        "uniqueContent": round(1.0 / context.content_group_size.get(record["derived"]["contentHash"], 1), 6),
        "hasPhoto": 1.0 if raw.get("hasPhoto") else 0.0,
        "usagePeriod": 1.0 if raw.get("isMonthOverReview") else 0.0,
        "likes": context.like_percentile.get(record["reviewId"], 0.0),
    }
    if context.aspect_counts is not None:
        values["onTopic"] = 1.0 if context.aspect_counts.get(record["reviewId"], 0) > 0 else 0.0
    return values


def rescore(values: dict[str, float], weights: TrustWeights) -> dict:
    """신호 값 + 가중치 → 점수. 입수를 다시 돌리지 않고 가중치만 바꿀 때 쓴다.

    가용한 신호의 가중치 합으로만 정규화한다 — 미가용 신호를 0점으로 깔면
    "근거가 없다"와 "아직 안 쟀다"가 같은 점수가 된다.
    """
    unknown = sorted(set(values) - set(SIGNALS))
    if unknown:
        raise TrustConfigError(f"알 수 없는 신호 {unknown}")
    unavailable = sorted(set(SIGNALS) - set(values))
    total = sum(weights.weights[s] for s in values)
    if total <= 0:
        raise TrustConfigError(
            f"가용한 신호 {sorted(values)} 의 가중치 합이 0 이다. 점수가 모든 리뷰에서 같아진다"
        )
    score = sum(weights.weights[s] * values[s] for s in values) / total
    out = {
        "score": round(score, SCORE_DECIMALS),
        "signals": {s: values[s] for s in SIGNALS if s in values},
    }
    if unavailable:
        out["unavailable"] = unavailable
    return out


def trust_prior(record: dict, context: ScoringContext, weights: TrustWeights) -> dict:
    """리뷰 1건의 사전 점수. 순수 함수 — 같은 인자면 항상 같은 값이다 (§5-2)."""
    return rescore(signal_values(record, context, weights), weights)


def score_all(
    records: list[dict],
    weights: TrustWeights | None = None,
    aspect_counts: dict[int, int] | None = None,
) -> dict[int, dict]:
    """레코드 전체에 점수를 매긴다. context 를 한 번만 만들어 재사용한다."""
    weights = weights or TrustWeights.load()
    context = ScoringContext.from_records(records, aspect_counts)
    return {r["reviewId"]: trust_prior(r, context, weights) for r in records}


def rank_key(record: dict) -> tuple:
    """근거 정렬 키 (PER-170 §중복 1표 선택과 같은 규칙).

    점수 내림차순 → `reviewDate` 최신 → `reviewId` 최소. 점수만으로는 동점이 많아
    (가중치 0 인 신호가 많고 길이가 같은 리뷰가 흔하다) 결정적 tiebreak 가 필요하다.
    `sorted(records, key=rank_key)` 의 앞쪽이 상위다.
    """
    prior = record["derived"].get("trustPrior") or {}
    return (-float(prior.get("score", 0.0)), _neg_date(record["raw"].get("reviewDate")), record["reviewId"])


def _neg_date(review_date) -> str:
    """최신이 앞에 오도록 날짜를 뒤집는다. 문자열 비교라 자릿수를 맞춰 둔다."""
    text = (review_date or "").strip().replace(".", "-")
    return "".join(chr(ord("9") - int(c)) if c.isdigit() else c for c in text)
