"""
게이트4 — 충분성 (PER-186 / PRD §4-4). **리뷰 수 ≠ 사람 수 ≠ 독립 관찰 수.**

게이트1~3이 "이 리뷰를 근거로 쓸 수 있는가"를 물었다면 여기서 묻는 것은 **"이 근거로
주장을 세울 수 있는가"** 다. 판정 단위가 리뷰가 아니라 주장이라 모듈이 따로다
(`gates.py` 는 리뷰 단위 게이트1·2를 소유한다).

## 통과 조건은 셋의 AND 다 — 낮은 쪽이 아니라 높은 쪽

    U ≥ N_min        방향을 명시한 고유 작성자의 **절대 하한**
    U/D ≥ R_min      **언급한 사람** 중 그 방향의 몫
    S ≥ S_min        셀 자체의 최소 관찰 수

단일 임계값(예: "15% 이상")으로 정의하면 소표본에서 무너진다. 언급자가 2명인데 둘 다
같은 방향이면 비율은 100% 다. 절대 하한이 없으면 리뷰가 적은 제품에서 1~2건짜리 주장이
"많이 궁금해하는 것"이라는 라벨을 달고 나간다.

## 분모는 D 다 — 침묵은 근거가 아니다 (PER-178)

    U+  긍정을 **명시**한 고유 작성자        U−  부정을 명시한 고유 작성자
    U0  말했지만 방향이 없는 작성자           D = U+ ∪ U− ∪ U0
    S   셀의 유효 고유 작성자 전체            S − D = 언급 없음

`D` 를 `S` 로 부풀리지 않는다. 구순염을 말하지 않은 37명은 "구순염이 안 생겼다"는
근거가 아니다 (`docs/DECISION_PER178_SILENCE_IS_NOT_EVIDENCE.md`). 부풀리면 U/D 가
비율이 아니라 **주제 언급률**이 되고, "40명 중 3명 언급"이 "40명 중 3명만 불만"으로
둔갑한다. 언급 없음은 지우지 않고 `silentAuthors` 로 따로 센다.

## 셀은 주장보다 먼저 판정한다 — §5-3 기본 폴백

`S ≥ S_min` 은 주장 없이도 물을 수 있는 질문이라 **생성 이전에** 건다
(`cell_sufficient`). 리뷰가 적은 제품은 주장을 적게 내거나 아무것도 내지 않는다.
현 스냅샷 실측(`eval/reports/gate4_sufficiency_per186.json`): 제품 53개 중 3개가
침묵한다 — 2개는 게이트1의 기간초과로 통과 리뷰가 0건, 1개는 고유 작성자 7명으로
S_min=8 미만이다.

## 탈락은 드롭이 아니라 사유가 붙은 행이다 (PER-188)

셋 다 PRD 가 말하는 "과소근거" 부류지만 사유 코드는 나눈다 — 무엇이 결속했는지 모르면
임계값을 바꿨을 때 달라진 몫을 귀속시킬 수 없다. 게이트1이 `옵션불일치` 와 `옵션미기재`
를 나눠 세는 것과 같은 이유다.

## 소수 의견은 지우지 않는다 (§4 결정)

방향이 갈리는데 한쪽이 1명이면 **다수 방향으로 뭉개지 않는다** — PER-178 규격 §9 가
기각한 대안이고, 갈린 걸 숨기는 것이 신뢰를 깨는 지점이다 (PER-185). 대신
`limitation="single_dissent"` 를 남겨 표기가 "1명"임을 드러내게 한다. 컷이 아니라
한계다. 골든셋 26건 중 8건이 이 경우다.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from contracts import MISSING_SEGMENT  # noqa: E402
from gates import independent_reviews  # noqa: E402
from policy import (  # noqa: E402
    DEFAULT_SUFFICIENCY,
    LIMIT_SINGLE_DISSENT,
    REJECT_INSUFFICIENT,
    REJECT_MINORITY_SHARE,
    REJECT_SEGMENT_TOO_SMALL,
    SufficiencyPolicy,
    sufficiency_gate,
)
# 게이트 이름과 사람이 읽는 표기는 레지스트리가 소유한다 (PER-188).
from reject_registry import (  # noqa: E402
    GATE_SUFFICIENCY,
    LIMIT_LABELS as _REGISTRY_LIMIT_LABELS,
    REJECT_LABELS,
    assert_rejectable,
    label_of,
)

ISSUE = "PER-186"

MULTI_AXES = ("skinTrouble",)
STANCES = ("positive", "negative", "neutral")

# 완료 조건의 "과소근거" 는 절대하한 미달에 붙는다 — 나머지 둘은 같은 부류지만 다른
# 이유이므로 표기도 나눈다. 표기 자체는 레지스트리의 한 벌에서 잘라 온다.
SUFFICIENCY_REJECT_LABELS = {
    code: REJECT_LABELS[code]
    for code in (REJECT_INSUFFICIENT, REJECT_MINORITY_SHARE, REJECT_SEGMENT_TOO_SMALL)
}

# 이 게이트가 **소유한** 한계의 표기만 잘라 온다. 레지스트리 전체(5종)를 그대로 재수출하면
# 게이트1·3 의 한계까지 여기 이름으로 보이고, 이 표를 훑는 호출부가 자기 게이트와 무관한
# 꼬리표를 렌더한다. 문자열은 한 벌이되 **범위는 소유자별로 자른다** — 사유 표기와 같은 규칙.
LIMIT_LABELS = {LIMIT_SINGLE_DISSENT: _REGISTRY_LIMIT_LABELS[LIMIT_SINGLE_DISSENT]}


class SufficiencyError(ValueError):
    """충분성 게이트 입력이 계약을 위반했다. 조용한 폴백 금지.

    `ValueError` 를 상속하는 이유는 `gates.GateError` 와 같다 — 호출부가 이미
    `ValueError` 를 잡고 있어도 위반이 통과 결과로 둔갑하지 않게 한다.
    """


def segments_of(record: dict, axis: str) -> tuple[str, ...]:
    """리뷰가 속한 세그먼트. 미기재도 세그먼트다 ('조건 없음'이 아니다, PER-173)."""
    condition = record.get("condition")
    if not isinstance(condition, dict) or axis not in condition:
        raise SufficiencyError(
            f"[reviewId={record.get('reviewId')!r}] condition.{axis} 가 없다 — "
            "입수(PER-173)를 거친 레코드를 넘겨라"
        )
    cell = condition[axis]
    if axis in MULTI_AXES:
        return tuple(cell["segments"])
    return (cell["segment"],)


def matches(record: dict, condition: dict) -> bool:
    """리뷰가 이 조건 셀에 속하는가. `None` 은 '무관'이고 `"미기재"` 는 세그먼트다.

    둘을 같게 취급하면 조건부 주장의 근거에 미기재 리뷰가 섞인다 (PER-177 §3).
    """
    for axis, want in condition.items():
        if want is None:
            continue
        if isinstance(want, (list, tuple)) and axis not in MULTI_AXES:
            # 코드를 AND 로 묶는 건 한 리뷰가 여러 코드를 갖는 다중 축에서만 뜻이 있다.
            # 단일 축에 리스트를 주면 만족할 리뷰가 없어 셀이 조용히 0명이 되고,
            # 그 결과가 '세그먼트과소' 탈락으로 둔갑한다 — 이 모듈이 막으려는 침묵이다
            raise SufficiencyError(
                f"단일 조건축 {axis!r} 에 코드 배열 {list(want)!r} 을 줬다. "
                f"배열은 다중 축 {MULTI_AXES} 에서만 쓴다 — 여러 세그먼트를 합치려면 "
                "셀을 나눠 각각 판정하라"
            )
        wants = want if isinstance(want, (list, tuple)) else [want]
        have = segments_of(record, axis)
        if any(w not in have for w in wants):
            return False
    return True


@dataclass(frozen=True)
class EvidenceCell:
    """주장이 놓인 셀 — `productId` × 조건 세그먼트. `authors` 의 수가 S 다.

    집계 단위는 `productId` 다 (`goodsNo` 도 행의 `productKey` 문자열도 아니다).
    """
    product_id: str
    condition: dict
    authors: frozenset[str]

    @property
    def size(self) -> int:
        return len(self.authors)

    @property
    def conditional(self) -> bool:
        return any(v is not None for v in self.condition.values())

    def as_dict(self) -> dict:
        return {
            "productId": self.product_id,
            "condition": {k: list(v) if isinstance(v, (list, tuple)) else v
                          for k, v in sorted(self.condition.items())},
            "cellAuthors": self.size,
        }

    @classmethod
    def of(cls, records: list[dict], product_id: str, condition: dict | None = None) -> "EvidenceCell":
        """**게이트2 통과분**에서 셀을 만든다. 통과분이 아니면 에러다.

        `independent_reviews` 를 그대로 불러 확인한다 — S 가 조용히 부풀 수 있는 경로가
        여기이기 때문이다. 같은 사람의 리뷰 2건이 남아 있으면 셀 크기가 1명 더 커지고,
        그 1명이 S_min 경계를 넘기면 말하지 말아야 할 셀이 말하게 된다.
        """
        condition = dict(condition or {})
        in_product = [r for r in records if r["productId"] == product_id]
        independent_reviews(in_product)
        authors = {r["derived"]["authorKey"] for r in in_product if matches(r, condition)}
        return cls(product_id=product_id, condition=condition, authors=frozenset(authors))


@dataclass(frozen=True)
class ClaimSupport:
    """주장 1건의 근거 — **리뷰 수가 아니라 고유 작성자 수** (PER-170).

    세 집합은 서로소여야 한다. 게이트2를 통과한 셀에서는 한 작성자가 한 리뷰이고
    (리뷰 × aspect) 태그도 aspect 당 1개이므로, 겹치면 게이트2 이전 묶음이거나
    태그 계약(PER-175)이 깨진 것이다.
    """
    aspect: str
    positive: frozenset[str] = frozenset()
    negative: frozenset[str] = frozenset()
    neutral: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        for a, b in (("positive", "negative"), ("positive", "neutral"), ("negative", "neutral")):
            overlap = getattr(self, a) & getattr(self, b)
            if overlap:
                raise SufficiencyError(
                    f"[{self.aspect}] 작성자 {sorted(overlap)} 가 {a} 와 {b} 양쪽에 있다. "
                    "게이트2 통과분이면 한 작성자는 한 표이고 aspect 당 방향도 하나다 (PER-175)"
                )

    @property
    def spoke(self) -> frozenset[str]:
        """D — 이 주제를 말한 작성자. 중립은 침묵이 아니므로 D 에 들어간다."""
        return self.positive | self.negative | self.neutral

    @property
    def direction(self) -> str:
        """U+/U− 만으로 계산한다. 중립(U0)과 침묵(S−D)은 방향에 영향을 주지 않는다.

        규칙은 골든셋의 `golden_contract.derive_direction` 과 같아야 한다 — 다르면
        judge 일치율이 게이트가 아니라 방향 정의의 차이를 재게 된다.
        """
        if self.positive and self.negative:
            return "mixed"
        if self.positive:
            return "positive"
        if self.negative:
            return "negative"
        return "neutral"

    @property
    def support(self) -> frozenset[str]:
        """U — 주장의 방향을 **명시한** 작성자. 중립은 어느 방향도 지지하지 않는다.

        `mixed` 의 U 는 U+ ∪ U− 다. "의견이 갈린다"는 주장을 지지하는 것은 양쪽에서
        방향을 말한 사람들이고, 그 합이 N_min 을 넘어야 갈렸다고 말할 수 있다.
        """
        if self.direction == "mixed":
            return self.positive | self.negative
        if self.direction == "positive":
            return self.positive
        if self.direction == "negative":
            return self.negative
        return frozenset()

    @property
    def minority(self) -> int | None:
        """방향이 갈릴 때 적은 쪽의 수. 갈리지 않으면 `None`."""
        if not (self.positive and self.negative):
            return None
        return min(len(self.positive), len(self.negative))

    def as_dict(self, cell: "EvidenceCell") -> dict:
        """표기(PER-190 §7-4)가 읽는 세 수를 따로 낸다 — 언급 · 긍정 · 부정.

        "만족 95%" 처럼 전체를 분모로 한 비율은 내지 않는다. `silentAuthors` 를 함께
        내는 것이 요점이다: 이 수가 크면 답이 일반화하면 안 된다.
        """
        spoke = self.spoke
        outside = spoke - cell.authors
        if outside:
            # 개수만 비교하면(U ≤ D ≤ S) 이게 안 잡힌다. silentAuthors 가 S − D 로
            # 계산되므로 셀 밖 작성자 1명이 섞이면 '말하지 않은 사람'이 1명 줄어든 채
            # 주장에 실린다 — 생성·judge 가 일반화 여부를 판단하는 바로 그 수다
            raise SufficiencyError(
                f"[{self.aspect}] 작성자 {sorted(outside)} 가 셀 "
                f"{cell.product_id}{cell.condition} 밖이다. 조건부 주장의 근거는 그 "
                "세그먼트 리뷰만이다 (PER-177 §3)"
            )
        return {
            "aspect": self.aspect,
            "direction": self.direction,
            "positiveAuthors": len(self.positive),
            "negativeAuthors": len(self.negative),
            "neutralAuthors": len(self.neutral),
            "spokeAuthors": len(spoke),
            "silentAuthors": cell.size - len(spoke),
            "cellAuthors": cell.size,
            "supportAuthors": len(self.support),
            "supportShare": round(len(self.support) / len(spoke), 4) if spoke else 0.0,
            "minorityAuthors": self.minority,
        }

    @classmethod
    def of(cls, tags: list[dict], cell_records: list[dict], aspect: str, cell: "EvidenceCell") -> "ClaimSupport":
        """(리뷰 × aspect) 태그 → 작성자 집합 (PER-175 태그 계약의 출력 형식).

        셀 밖 리뷰의 태그는 세지 않는다 — 조건부 주장의 근거는 그 세그먼트 리뷰만이다
        (PER-177 §3). 셀 안인데 `polarity` 가 3종 밖이면 조용히 넘기지 않고 에러다.
        """
        authors = {
            r["reviewId"]: r["derived"]["authorKey"]
            for r in cell_records
            if r["productId"] == cell.product_id
            and r["derived"]["authorKey"] in cell.authors
            and matches(r, cell.condition)
        }
        by: dict[str, set[str]] = {s: set() for s in STANCES}
        for tag in tags:
            if tag.get("aspect") != aspect:
                continue
            author = authors.get(tag.get("reviewId"))
            if author is None:
                continue
            polarity = tag.get("polarity")
            if polarity not in by:
                raise SufficiencyError(
                    f"[reviewId={tag.get('reviewId')!r}] polarity {polarity!r} 는 "
                    f"{STANCES} 중 하나여야 한다 (PER-175)"
                )
            by[polarity].add(author)
        return cls(
            aspect=aspect,
            positive=frozenset(by["positive"]),
            negative=frozenset(by["negative"]),
            neutral=frozenset(by["neutral"]),
        )


@dataclass(frozen=True)
class Claim:
    """충분성 판정의 입력 1건. `claim_id` 는 `rejected[]` 가 되짚을 키다."""
    claim_id: str
    cell: EvidenceCell
    support: ClaimSupport


@dataclass(frozen=True)
class RejectedClaim:
    """`rejected[]` 한 줄. 사유 없이 탈락시키지 않는다 (PER-188).

    수치를 함께 남기는 것이 게이트1·2의 `RejectedRow` 와 다른 점이다 — 임계값을
    바꿨을 때 어느 주장이 되살아나는지를 리포트를 다시 돌리지 않고 읽을 수 있어야
    민감도가 PER-199 의 입력이 된다.
    """
    claim_id: str
    reason: str
    support: dict
    detail: str | None = None

    def __post_init__(self) -> None:
        # 미등록 사유·다른 게이트의 사유로는 주장을 탈락시킬 수 없다 (PER-188).
        # 게이트1·2 의 `RejectedRow` 와 같은 자리에서 같은 레지스트리에 묻는다.
        assert_rejectable(GATE_SUFFICIENCY, self.reason)

    @property
    def label(self) -> str:
        return label_of(self.reason)

    def as_dict(self) -> dict:
        return {
            "claimId": self.claim_id,
            "gate": GATE_SUFFICIENCY,
            "reason": self.reason,
            "label": self.label,
            "detail": self.detail,
            "support": self.support,
        }


@dataclass
class SufficiencyResult:
    """게이트4 1회 실행 결과. 통과분과 탈락분을 **함께** 낸다."""
    gate: str = GATE_SUFFICIENCY
    issue: str = ISSUE
    policy: SufficiencyPolicy = DEFAULT_SUFFICIENCY
    passed: list[Claim] = field(default_factory=list)
    rejected: list[RejectedClaim] = field(default_factory=list)
    limitations: dict[str, list[str]] = field(default_factory=dict)

    def rejected_by_reason(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in self.rejected:
            counts[row.reason] = counts.get(row.reason, 0) + 1
        return dict(sorted(counts.items()))

    def as_dict(self) -> dict:
        return {
            "gate": self.gate,
            "issue": self.issue,
            "meta": {"sufficiency": self.policy.as_meta()},
            "passed": len(self.passed),
            "rejected": [r.as_dict() for r in self.rejected],
            "rejectedByReason": self.rejected_by_reason(),
            "limitations": {k: sorted(v) for k, v in sorted(self.limitations.items())},
        }


def cell_sufficient(cell: EvidenceCell, policy: SufficiencyPolicy = DEFAULT_SUFFICIENCY) -> bool:
    """셀이 말할 자격이 있는가 — 주장을 만들기 **전에** 묻는다 (§5-3 기본 폴백).

    주장 단위 판정(`run_sufficiency_gate`)도 같은 조건을 다시 보지만, 생성 비용을
    쓰기 전에 걸러야 "리뷰가 적은 제품은 아무것도 내지 않는다"가 실제로 성립한다.
    """
    return cell.size >= policy.s_min


def silent_cells(cells: list[EvidenceCell], policy: SufficiencyPolicy = DEFAULT_SUFFICIENCY) -> list[EvidenceCell]:
    """침묵해야 하는 셀. 빈 목록이 아니라 **셀 목록**을 낸다 — 무엇이 빠졌는지가 근거다."""
    return [c for c in cells if not cell_sufficient(c, policy)]


def run_sufficiency_gate(
    claims: list[Claim], policy: SufficiencyPolicy = DEFAULT_SUFFICIENCY
) -> SufficiencyResult:
    """주장 묶음에 게이트4를 건다. 탈락분은 버리지 않고 `rejected[]` 로 돌려준다."""
    result = SufficiencyResult(policy=policy)
    seen: set[str] = set()
    for claim in claims:
        if claim.claim_id in seen:
            raise SufficiencyError(
                f"claimId 중복: {claim.claim_id!r}. rejected[] 를 되짚을 키가 흔들린다"
            )
        seen.add(claim.claim_id)

        support = claim.support.as_dict(claim.cell)
        decision = sufficiency_gate(
            support_authors=support["supportAuthors"],
            spoke_authors=support["spokeAuthors"],
            cell_authors=support["cellAuthors"],
            minority_authors=claim.support.minority,
            policy=policy,
        )
        if not decision.passed:
            result.rejected.append(
                RejectedClaim(
                    claim_id=claim.claim_id,
                    reason=decision.reason,
                    support=support,
                    detail=_detail(decision.reason, support, policy),
                )
            )
            continue
        result.passed.append(claim)
        if decision.limitation:
            result.limitations.setdefault(decision.limitation, []).append(claim.claim_id)
    return result


def _detail(reason: str, support: dict, policy: SufficiencyPolicy) -> str:
    """왜 떨어졌는지를 수치로 적는다. "과소근거" 세 글자로는 임계값을 못 고친다."""
    if reason == REJECT_SEGMENT_TOO_SMALL:
        return f"S={support['cellAuthors']} < S_min={policy.s_min}"
    if reason == REJECT_INSUFFICIENT:
        return (
            f"U={support['supportAuthors']} < N_min={policy.n_min} "
            f"(D={support['spokeAuthors']}, 언급 없음 {support['silentAuthors']})"
        )
    return (
        f"U/D={support['supportShare']} < R_min={policy.r_min} "
        f"(U={support['supportAuthors']}, D={support['spokeAuthors']})"
    )


__all__ = [
    "GATE_SUFFICIENCY",
    "LIMIT_SINGLE_DISSENT",
    "MISSING_SEGMENT",
    "REJECT_INSUFFICIENT",
    "REJECT_MINORITY_SHARE",
    "REJECT_SEGMENT_TOO_SMALL",
    "SUFFICIENCY_REJECT_LABELS",
    "Claim",
    "ClaimSupport",
    "EvidenceCell",
    "RejectedClaim",
    "SufficiencyError",
    "SufficiencyPolicy",
    "SufficiencyResult",
    "cell_sufficient",
    "matches",
    "run_sufficiency_gate",
    "segments_of",
    "silent_cells",
]
