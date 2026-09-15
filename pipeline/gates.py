"""
근거 선별 게이트 — 1 동일성 (PER-182) · 2 중복 (PER-183) / PRD §4.

넓게 모은 리뷰를 검문소에 순서대로 통과시킨다. 순서가 규격이다 — **이 제품의 리뷰가
맞는가**(게이트1)를 먼저 정해야 **같은 사람을 두 번 세고 있지 않은가**(게이트2)를
물을 수 있다. 두 게이트 모두 결정론적이고, 탈락은 드롭이 아니라 `rejected[]` 행이다.

# 게이트1 — 동일성 (PER-182 / PRD §4-1).

"이 리뷰가 **정말 이 제품·이 옵션**에 대한 것인가." 근거를 고르기 전에 통과해야 하는
첫 관문이고, **전부 결정론적이다 — LLM 호출이 없다.** PRD §2 가 못 박은 대로
같은 제품인가는 카탈로그의 책임이지 모델에게 물을 것이 아니다.

## 네 개의 판정

  제품   `goodsNo` → 카탈로그가 정한 `productId` 가 대상과 같은가 (PER-171)
  세대   리뷰가 그 제품 **세대**의 것인가 — 리뉴얼 컷 (PER-172)
  시점   리뷰가 리센시 윈도우 안인가 — 스냅샷 최신 월 기준 24개월 (PER-172)
  옵션   색상·호수 질문이면 **같은 색상**의 리뷰인가 (PER-182, `pipeline/option_norm.py`)

세대·시점 판정은 `pipeline/policy.py` 가 소유한다. 이 모듈은 그 둘을 옵션·제품
판정과 한 관문으로 묶고, 탈락을 `rejected[]` 행으로 만든다.

## 옵션 판정은 질문이 요구할 때만 건다

옵션은 조건축이지 항상 거는 컷이 아니다. "지속력 어때요" 에 색상 컷을 걸면 근거의
68.1%(기재분)만 남고 나머지는 이유 없이 사라진다. 그래서 **호출부가 옵션 범위를
명시할 때만** 건다 — 그게 §4-1 의 "색상·호수 질문이면" 이다.

범위를 건 질문에서 옵션 미기재 리뷰는 **불일치가 아니라 미기재**로 따로 센다.
둘을 한 사유로 뭉치면 "색상을 안 적어서 못 쓴 근거"(31.9%)와 "다른 색이라 못 쓴
근거"를 구분할 수 없고, 옵션 정규화를 개선했을 때 무엇이 좋아졌는지 귀속되지 않는다.

## 탈락은 드롭이 아니다 (PER-188)

판정은 리뷰를 지우지 않는다. `rejected[]` 에 사유와 함께 남는다 — 통과분만 남기면
정밀도는 측정되지만 **재현율은 영영 측정되지 않는다** (PRD §6).

사유 코드는 `policy.py` 와 같은 영문 snake_case 를 쓰고(`renewal_cut` …), 사람이 읽는
리포트·UI 표기는 `REJECT_LABELS` 가 한글로 옮긴다 (`리뉴얼이전` · `옵션불일치`).
코드와 표기를 같은 문자열로 두면 표기를 고칠 때 판정 코드가 같이 흔들린다.

## 통과했다고 한계가 없는 것은 아니다

`renewalPolicy='unobserved'` 는 통과하되 `limitation` 을 남긴다. 현 스냅샷은 전 제품
`unobserved` 라 **리뉴얼 컷의 실효는 0**이고, 그 사실이 주장의 한계로 따라나가야 한다.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from option_norm import (  # noqa: E402
    KIND_SHADE,
    KIND_UNSTATED,
    OptionIndex,
    UnknownOptionScopeError,
)
from policy import (  # noqa: E402
    REJECT_RECENCY,
    REJECT_RENEWAL,
    GateDecision,
    recency_gate,
    renewal_gate,
)
# 사유 코드·게이트 이름·한글 표기는 레지스트리가 소유한다 (PER-188). 이 모듈은
# **어느 조건에서 그 사유가 붙는가**만 정한다 — 두 벌로 들면 표기를 고칠 때 판정
# 코드가 같이 흔들리고, 미등록 사유로 탈락시켜도 아무도 못 잡는다.
from reject_registry import (  # noqa: E402
    GATE_DUPLICATE,
    GATE_IDENTITY,
    REJECT_DUPLICATE_CONTENT,
    REJECT_LABELS,
    REJECT_OPTION,
    REJECT_OPTION_UNSTATED,
    REJECT_PRODUCT,
    REJECT_SAME_AUTHOR,
    assert_rejectable,
)
from trust import rank_key  # noqa: E402

# 게이트2 중복 (PER-183)의 두 축은 서로 대체하지 않으므로 사유도 나눠 센다 — 본문
# 해시가 잡는 건 초과 표의 12.1% 뿐이고(PER-170 §2), 무엇이 어느 축에서 걸렸는지
# 모르면 의미 유사 클러스터링(PER-184)의 증분을 나중에 귀속시킬 수 없다.


class GateError(ValueError):
    """게이트 입력이 계약을 위반했다. 조용한 폴백 금지.

    `ValueError` 를 상속한다 — 호출부가 이미 `ValueError` 를 잡고 있어도 위반이
    통과 결과로 둔갑하지 않게 하려는 것이다 (`contracts.ContractError` 와 같은 이유).
    """


@dataclass(frozen=True)
class IdentityScope:
    """근거를 고르는 대상. 옵션 범위는 **색상·호수 질문일 때만** 채운다.

    `option_key` 는 정규화된 색상 키여야 한다. 자연어 표기는
    `OptionIndex.resolve_scope()` 로 먼저 옮긴다 — 리뷰와 같은 정규화를 거쳐야
    비교가 대칭이다.
    """
    product_id: str
    option_key: str | None = None


@dataclass(frozen=True)
class RejectedRow:
    """`rejected[]` 한 줄 (PER-188). 사유 없이, 또 **미등록 사유로도** 탈락시키지 않는다.

    생성 시점에 레지스트리에 묻는다 — `rejected[]` 가 쌓이고 난 뒤에 검사하면 어느
    호출부가 만든 행인지 되짚을 수 없다. 사유가 게이트와 1:1 이 아니면 그 행은
    집계에서 어느 검문소에도 귀속되지 않는다.
    """
    review_id: int
    gate: str
    reason: str
    detail: str | None = None

    def __post_init__(self) -> None:
        assert_rejectable(self.gate, self.reason)

    @property
    def label(self) -> str:
        return REJECT_LABELS[self.reason]

    def as_dict(self) -> dict:
        return {
            "reviewId": self.review_id,
            "gate": self.gate,
            "reason": self.reason,
            "label": self.label,
            "detail": self.detail,
        }


@dataclass
class GateResult:
    """게이트 1회 실행 결과. 통과분과 탈락분을 **함께** 낸다.

    `gate`/`issue` 를 값으로 받는다 — 게이트가 여럿이 되면서 결과만 보고 어느 검문소의
    판정인지 알 수 있어야 `rejected[]` 를 게이트별로 되짚을 수 있다 (PER-188).
    """
    gate: str
    issue: str
    passed: list[dict] = field(default_factory=list)
    rejected: list[RejectedRow] = field(default_factory=list)
    limitations: set[str] = field(default_factory=set)

    def as_dict(self) -> dict:
        return {
            "gate": self.gate,
            "issue": self.issue,
            "passed": len(self.passed),
            "rejected": [r.as_dict() for r in self.rejected],
            "rejectedByReason": self.rejected_by_reason(),
            "limitations": sorted(self.limitations),
        }

    def rejected_by_reason(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in self.rejected:
            counts[row.reason] = counts.get(row.reason, 0) + 1
        return dict(sorted(counts.items()))


def identity_gate(
    record: dict,
    scope: IdentityScope,
    catalog,
    option_index: OptionIndex | None = None,
) -> tuple[GateDecision, str | None]:
    """리뷰 1건의 동일성 판정 → (`GateDecision`, 탈락 상세).

    판정 순서는 **좁은 것부터**다 — 제품이 다르면 세대·옵션을 볼 이유가 없고,
    사유가 하나로 정해져야 `rejected[]` 가 재현율의 단서가 된다.
    """
    if record["productId"] != scope.product_id:
        return (
            GateDecision(passed=False, reason=REJECT_PRODUCT),
            f"{record['productId']} != {scope.product_id}",
        )

    product = catalog.product(scope.product_id)
    review_date = record["raw"]["reviewDate"]

    decision = renewal_gate(product, review_date)
    if not decision.passed:
        return decision, f"{review_date} / 세대 {product.renewal_from_month}~{product.renewal_to_month}"

    limitation = decision.limitation
    recency = recency_gate(review_date)
    if not recency.passed:
        return GateDecision(passed=False, reason=REJECT_RECENCY), review_date

    if scope.option_key is None:
        return GateDecision(passed=True, limitation=limitation), None

    if option_index is None:
        raise ValueError(
            "옵션 범위를 걸었는데 OptionIndex 가 없다. 색상 판정은 코퍼스 어휘가 필요하다"
        )
    key, kind = option_index.shade_key(scope.product_id, record["raw"].get("option"))
    if kind == KIND_UNSTATED:
        return GateDecision(passed=False, reason=REJECT_OPTION_UNSTATED), None
    if kind != KIND_SHADE or key != scope.option_key:
        return GateDecision(passed=False, reason=REJECT_OPTION), f"{key!r} != {scope.option_key!r}"
    return GateDecision(passed=True, limitation=limitation), None


def run_identity_gate(
    records: list[dict],
    scope: IdentityScope,
    catalog,
    option_index: OptionIndex | None = None,
) -> GateResult:
    """레코드 묶음에 게이트1을 건다. 탈락분은 버리지 않고 `rejected[]` 로 돌려준다."""
    result = GateResult(gate=GATE_IDENTITY, issue="PER-182")
    for record in records:
        decision, detail = identity_gate(record, scope, catalog, option_index)
        if decision.passed:
            result.passed.append(record)
            if decision.limitation:
                result.limitations.add(decision.limitation)
        else:
            result.rejected.append(
                RejectedRow(
                    review_id=record["reviewId"],
                    gate=GATE_IDENTITY,
                    reason=decision.reason,
                    detail=detail,
                )
            )
    return result



# =============================================================================
# 게이트2 — 중복 (PER-183 / PRD §4-2)
# =============================================================================
#
# 같은 말이 열 번 나왔다고 열 배 중요한 게 아니다. **여기서 중요한 건 제거 자체가
# 아니라 카운트를 오염시키지 않는 것이다** — 이 게이트를 통과한 수가 곧 화면의
# "리뷰 N건"(`support.independentReviews`)이기 때문이다. 안 걸면 그 문장이 거짓이 된다:
# 25K 스냅샷에서 초과 표가 22.4%, 최악 제품은 리뷰 500건에 고유 작성자 291명이다
# (`eval/reports/author_identity_per170.json`).
#
# ## 두 축은 서로 대체하지 않는다 — 둘 다 건다
#
#   본문 완전일치  `contentHash` 가 같은 리뷰. 템플릿·복붙
#   작성자 1표     `(authorKey, productId)` 가 같은 두 번째 이후 리뷰
#
# 같은 사람이 같은 제품에 쓴 복수 리뷰는 대부분 본문이 다르다(재구매·옵션별·기간 경과
# 후 추가 작성). 그래서 해시로 잡히는 건 초과 표의 **12.1%** 뿐이고, 작성자 축을
# 빼면 코퍼스의 19.7% 가 오염으로 남는다 (PER-170 §2). 반대로 작성자 축만 걸면 서로
# 다른 사람이 같은 템플릿을 붙여 넣은 경우가 독립 근거로 남는다.
#
# ## 판정 순서는 좁은 것부터 — 본문 해시 → 작성자
#
# 게이트1과 같은 원칙이다. 사유가 하나로 정해져야 `rejected[]` 가 재현율의 단서가
# 된다. 순서를 바꿔도 **통과 수는 같고 사유 귀속만 달라진다** — 그 사실을 가정하지
# 않고 쟀다 (`eval/reports/gate2_duplicate_per183.json` 의 `orderSensitivity`).
# 좁은 판정(완전히 같은 글)을 먼저 붙이는 쪽이 "왜 빠졌나"를 읽기 쉽다.
#
# ## 묶는 단위는 `productId` 다
#
# 제품을 넘어서는 합치지 않는다 — 같은 사람이 다른 제품에 쓴 리뷰는 서로 독립 근거다.
# `goodsNo` 로 묶지 않는 이유는 중복 쌍 3,506개 중 **428개가 서로 다른 변형 SKU 에
# 걸쳐 있어서**다 (PER-170 §5). SKU 로 묶으면 그 428쌍이 두 표로 남는다.
#
# ## 1표로 남길 리뷰는 결정적으로 고른다
#
# `trust.rank_key` 를 그대로 쓴다 — 신뢰도 사전 점수 최고 → `reviewDate` 최신 →
# `reviewId` 최소 (PER-170 §5 · PER-174). 점수만으로는 동점이 흔해서(25,000건에 서로
# 다른 점수 757개) tiebreak 없이는 파일 순서가 결과에 새 든다.
#
# 의미 유사 클러스터링은 이 게이트가 하지 않는다 — PER-184 가 같은 `rejected[]` 에
# `의미중복` 을 추가한다.


def _dedup_keys(record: dict) -> tuple[str, str]:
    """중복 판정에 필요한 파생 필드를 꺼낸다. 없으면 조용히 넘기지 않고 에러다.

    세 필드 모두 없을 때의 실패가 **조용하다**는 게 요점이다.
      - `authorKey` 가 비면 서로 다른 사람이 한 사람으로 합쳐진다 (과소 계수)
      - `contentHash` 가 없으면 완전일치 축이 통째로 꺼진 채 통과율만 올라간다
      - `trustPrior` 가 없으면 1표 선택이 날짜순으로 밀려 같은 입력에 다른 대표가 남는다
    """
    derived = record.get("derived") or {}
    author = derived.get("authorKey")
    content = derived.get("contentHash")
    prior = derived.get("trustPrior")
    rid = record.get("reviewId")
    if not isinstance(author, str) or not author.strip():
        raise GateError(
            f"[reviewId={rid!r}] authorKey 가 없다. 빈 키로 묶으면 서로 다른 사람이 "
            "한 사람이 된다 (PER-170)"
        )
    if not isinstance(content, str) or not content.strip():
        raise GateError(f"[reviewId={rid!r}] contentHash 가 없다. 본문 완전일치 축을 걸 수 없다")
    if not isinstance(prior, dict) or not isinstance(prior.get("score"), (int, float)):
        raise GateError(
            f"[reviewId={rid!r}] trustPrior.score 가 없다. 1표 선택이 날짜순으로 "
            "조용히 밀린다 — 입수(PER-173)를 거친 레코드를 넘겨라"
        )
    return author, content


def run_duplicate_gate(records: list[dict]) -> GateResult:
    """게이트2 — 같은 근거를 두 번 세지 않는다. 통과분의 수가 곧 독립 근거 수다.

    입력은 게이트1 통과분(같은 제품·세대·시점)이다. 다른 제품이 섞여 들어와도
    `productId` 를 키에 넣어 합치지 않지만, 그건 방어일 뿐 순서를 바꿔도 된다는
    뜻은 아니다 — 게이트1 이 먼저 걸려야 컷된 리뷰가 대표로 남지 않는다.

    통과분은 **정렬 순서**로 낸다 (`trust.rank_key`). 남은 1건이 곧 근거 정렬의 앞이고,
    입력 순서를 섞어도 결과가 같아야 재현된다 (§5-2).
    """
    result = GateResult(gate=GATE_DUPLICATE, issue="PER-183")
    kept_content: dict[tuple[str, str], int] = {}
    kept_author: dict[tuple[str, str], int] = {}

    for record in sorted(records, key=rank_key):
        author, content = _dedup_keys(record)
        product_id = record["productId"]
        review_id = record["reviewId"]

        content_key = (product_id, content)
        if content_key in kept_content:
            result.rejected.append(RejectedRow(
                review_id=review_id,
                gate=GATE_DUPLICATE,
                reason=REJECT_DUPLICATE_CONTENT,
                detail=f"reviewId {kept_content[content_key]} 과 본문 완전일치",
            ))
            continue

        author_key_ = (product_id, author)
        if author_key_ in kept_author:
            result.rejected.append(RejectedRow(
                review_id=review_id,
                gate=GATE_DUPLICATE,
                reason=REJECT_SAME_AUTHOR,
                detail=f"reviewId {kept_author[author_key_]} 과 같은 작성자 (1표)",
            ))
            continue

        kept_content[content_key] = review_id
        kept_author[author_key_] = review_id
        result.passed.append(record)
    return result


def independent_reviews(records: list[dict]) -> int:
    """`support.independentReviews` — 화면의 "리뷰 N건"이 여기서 나온다.

    **게이트2 통과분에만 쓴다.** 통과분이 아닌 목록을 넘기면 에러다 — N 이 조용히
    부풀 수 있는 유일한 경로가 여기이기 때문이다. 리뷰 수를 세는 함수가 아니라
    "이 묶음이 정말 서로 독립인가"를 확인하고 그 수를 내는 함수다.
    """
    authors: dict[tuple[str, str], int] = {}
    contents: dict[tuple[str, str], int] = {}
    for record in records:
        author, content = _dedup_keys(record)
        product_id, review_id = record["productId"], record["reviewId"]
        for seen, key, what in (
            (authors, (product_id, author), "같은 작성자"),
            (contents, (product_id, content), "같은 본문"),
        ):
            if key in seen:
                raise GateError(
                    f"게이트2를 통과하지 않은 묶음이다 — reviewId {seen[key]} 와 "
                    f"{review_id} 가 {what}다. 이 수를 그대로 쓰면 '리뷰 N건'이 부푼다"
                )
            seen[key] = review_id
    return len(records)


__all__ = [
    "GATE_DUPLICATE",
    "GATE_IDENTITY",
    "REJECT_DUPLICATE_CONTENT",
    "REJECT_LABELS",
    "REJECT_OPTION",
    "REJECT_OPTION_UNSTATED",
    "REJECT_PRODUCT",
    "REJECT_SAME_AUTHOR",
    "GateError",
    "GateResult",
    "IdentityScope",
    "RejectedRow",
    "UnknownOptionScopeError",
    "identity_gate",
    "independent_reviews",
    "run_duplicate_gate",
    "run_identity_gate",
]
