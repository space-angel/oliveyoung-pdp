"""
탈락 사유 레지스트리 (PER-188 / PRD §3-2 · §4 · §6).

**버린 것을 왜 버렸는지 남기는 자리가 여기다.** 게이트 1~4 가 쓰는 사유 코드·한글
표기·판정 소유 모듈·판정 단위를 **이 파일 하나가 소유한다.** 다른 모듈은 여기서
가져다 쓰고, 자기 문자열을 따로 들지 않는다.

이 모듈은 **잎(leaf)이다** — `policy` · `gates` · `polarity` · `sufficiency` 를 임포트하지
않는다. 반대 방향이라야 순환이 안 생기고, 사유 어휘가 판정 로직보다 먼저 존재한다는
사실이 구조로 남는다.

## 왜 한 곳인가

사유 코드는 원래 세 파일에 흩어져 있었다 — `gates.py` 의 제품·옵션·중복,
`policy.py` 의 시점·세대·충분성, 그리고 한글 표기는 `gates.REJECT_LABELS` 와
`sufficiency.SUFFICIENCY_REJECT_LABELS` 두 군데였다. 흩어져 있으면 세 가지가 조용히
깨진다.

  1. **미등록 사유로 탈락시켜도 아무도 못 잡는다.** 오타 하나면 `rejected[]` 에
     아무 문자열이나 들어가고, 집계에서 그 행은 어느 게이트에도 귀속되지 않는다
  2. **게이트와 사유의 1:1 대응이 깨진다.** 게이트2 의 사유로 게이트4 를 탈락시켜도
     통과 수는 그대로라 리포트만 보고는 알 수 없다
  3. **표기가 판정을 흔든다.** 코드와 한글을 같은 문자열로 두면 표기를 고칠 때
     판정 코드가 같이 흔들린다 (`gates.py` 가 이미 분리해 둔 이유)

그래서 등록되지 않은 사유로 탈락시키면 **에러**다 (`assert_rejectable`). CLAUDE.md 의
"도메인을 코드 상수로 들지 말고 물어라"(코드북·카탈로그)와 같은 규칙을, 조건 어휘가
아니라 **탈락 사유 어휘**에 적용한 것이다.

## 게이트3 에는 사유가 없다 — 비어 있는 게 아니라 **결정이다**

PER-188 이슈 설명문은 `게이트3 → 방향불일치` 를 요구했다. **그 요구를 기각한다.**
PER-185 가 이미 반대로 결정했고(`DECISION_PER185_POLARITY_GATE.md` §1), 그 결정이
`test_polarity.EvidenceIsNotDiscarded` 로 고정돼 있다.

> 부정 근거는 틀린 근거가 아니다. 방향이 갈렸다고 한쪽을 버리면 근거 선별이 아니라
> 검열이다.

게이트1·2 는 "이 근거가 **틀렸다**"를 판정한다 — 다른 제품이거나, 같은 사람을 두 번
세고 있다. 방향은 그런 종류의 판정이 아니다. `방향불일치` 로 리뷰를 버리면 전수에서
`혼재` 로 판정된 셀 343개(N≥8 셀 395개의 86.8%)의 소수 측이 화면에서 사라진다
(`eval/reports/gate3_polarity_per185.json`).

그래서 게이트3 은 **사유 0개로 등록된다.** 등록에서 빠진 것과 사유가 없다고 등록된
것은 다르다 — 앞의 것은 누락이고 뒤의 것은 결정이다. 게이트3 으로 무엇이든
탈락시키려 하면 `RejectRegistryError` 가 그 결정을 가리키며 멈춘다. 게이트3 의 산출은
탈락이 아니라 **한계**(`LIMITATIONS`)와 별점 불일치 플래그다.

## `의미중복` 은 자리만 열려 있다 (PER-184 미적용)

이슈 설명문의 `게이트2 → 의미중복` 은 임베딩 클러스터링(PER-184)이 채울 자리다.
**지금은 미적용이다.** 코드는 등록하되 `status="reserved"` 이고, 이 사유로 탈락시키면
에러다 — 없는 걸 있는 척하면 `rejected[]` 의 사유 분포가 거짓이 된다.

자리를 미리 여는 이유는 PER-184 가 들어올 때 **기존 사유 분포와 비교 가능해야**
하기 때문이다. 나중에 코드를 새로 만들면 그 전후를 같은 축에서 못 센다.
"""
from __future__ import annotations

from dataclasses import dataclass

# --- 게이트 이름 ---
#
# `gates.GATE_IDENTITY` · `polarity.GATE_POLARITY` · `sufficiency.GATE_SUFFICIENCY` 가
# 이 값을 가져다 쓴다. 게이트 이름이 두 벌이면 원장의 `gate` 가 모듈마다 달라진다.
GATE_IDENTITY = "identity"
GATE_DUPLICATE = "duplicate"
GATE_POLARITY = "polarity"
GATE_SUFFICIENCY = "sufficiency"
GATES = (GATE_IDENTITY, GATE_DUPLICATE, GATE_POLARITY, GATE_SUFFICIENCY)

GATE_ORDER = {name: i + 1 for i, name in enumerate(GATES)}
GATE_ISSUES = {
    GATE_IDENTITY: "PER-182",
    GATE_DUPLICATE: "PER-183",
    GATE_POLARITY: "PER-185",
    GATE_SUFFICIENCY: "PER-186",
}

# --- 판정 단위 ---
#
# 게이트1·2 는 리뷰를, 게이트4 는 주장을 탈락시킨다. 단위가 섞이면 원장의 행 수를
# 서로 더하게 되고 "리뷰 N건이 떨어졌다"가 주장 수와 합산된다.
UNIT_REVIEW = "review"
UNIT_CLAIM = "claim"
UNITS = (UNIT_REVIEW, UNIT_CLAIM)

# --- 사유의 상태 ---
#
# `reserved` 는 "코드는 정했고 구현은 아직"이다. 이 사유로 실제 탈락시키면 에러다.
STATUS_ACTIVE = "active"
STATUS_RESERVED = "reserved"
STATUSES = (STATUS_ACTIVE, STATUS_RESERVED)

# --- 사유 코드 (정본) ---
#
# 영문 snake_case 다. 한글은 표기이고 판정이 아니다 — 아래 `Reason.label`.

# 게이트1 동일성 (PER-182 / PER-171 / PER-172)
REJECT_PRODUCT = "product_mismatch"
REJECT_OPTION = "option_mismatch"
REJECT_OPTION_UNSTATED = "option_unstated"
REJECT_RENEWAL = "renewal_cut"
REJECT_RECENCY = "recency_cut"

# 게이트2 중복 (PER-183 / PER-170)
REJECT_DUPLICATE_CONTENT = "duplicate_content"
REJECT_SAME_AUTHOR = "same_author"
REJECT_DUPLICATE_SEMANTIC = "duplicate_semantic"  # PER-184 미적용 — 자리만

# 게이트4 충분성 (PER-186 / PER-178)
REJECT_INSUFFICIENT = "insufficient_support"
REJECT_MINORITY_SHARE = "minority_share"
REJECT_SEGMENT_TOO_SMALL = "segment_too_small"

# --- 한계 코드 ---
#
# 탈락이 아니라 **통과에 따라붙는 꼬리표**다. 통과했는데 한계가 있는 경우가 실재하고
# (`renewal_unobserved` 는 현 스냅샷 전 제품), 그 사실이 주장까지 따라나가야 한다.
LIMIT_RENEWAL_UNOBSERVED = "renewal_unobserved"
LIMIT_TAGGER_DIRECTION_ERROR = "tagger_direction_error"
LIMIT_ORDER_CHOSEN_DIRECTION = "order_chosen_direction"
LIMIT_MINORITY_WITHIN_NOISE = "minority_within_tagger_noise"
LIMIT_SINGLE_DISSENT = "single_dissent"


class RejectRegistryError(ValueError):
    """등록되지 않은 사유로 탈락시켰거나, 사유를 엉뚱한 게이트에 걸었다.

    `ValueError` 를 상속한다 — 호출부가 이미 `ValueError` 를 잡고 있어도 위반이
    통과 결과로 둔갑하지 않게 하려는 것이다 (`gates.GateError` 와 같은 이유).
    """


@dataclass(frozen=True)
class Reason:
    """탈락 사유 1종.

    `owner` 는 **판정을 내리는 모듈**이지 사유를 등록한 모듈이 아니다. 왜 떨어졌는지를
    코드에서 되짚을 때 첫 걸음이 이 필드다.
    """
    code: str
    gate: str
    label: str
    owner: str
    unit: str
    status: str
    note: str

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "gate": self.gate,
            "gateOrder": GATE_ORDER[self.gate],
            "issue": GATE_ISSUES[self.gate],
            "label": self.label,
            "owner": self.owner,
            "unit": self.unit,
            "status": self.status,
            "note": self.note,
        }


REASONS: tuple[Reason, ...] = (
    # --- 게이트1 동일성 ---
    Reason(
        REJECT_PRODUCT, GATE_IDENTITY, "제품불일치", "pipeline/gates.py", UNIT_REVIEW,
        STATUS_ACTIVE,
        "카탈로그가 정한 productId 가 대상과 다르다. 행의 productKey 문자열이 아니다 (PER-171)",
    ),
    Reason(
        REJECT_OPTION, GATE_IDENTITY, "옵션불일치", "pipeline/gates.py", UNIT_REVIEW,
        STATUS_ACTIVE,
        "색상 범위를 건 질문에서 다른 색상의 리뷰다 (PER-182)",
    ),
    Reason(
        REJECT_OPTION_UNSTATED, GATE_IDENTITY, "옵션미기재", "pipeline/gates.py", UNIT_REVIEW,
        STATUS_ACTIVE,
        "색상 범위를 걸었는데 리뷰가 색상을 밝히지 않았다. 불일치와 뭉치면 "
        "'색을 안 적어서 못 쓴 근거'와 '다른 색이라 못 쓴 근거'를 구분할 수 없다 (PER-182)",
    ),
    Reason(
        REJECT_RENEWAL, GATE_IDENTITY, "리뉴얼이전", "pipeline/policy.py", UNIT_REVIEW,
        STATUS_ACTIVE,
        "리뷰가 이 제품 세대의 것이 아니다. 키는 goodsNo 가 아니라 (goodsNo, reviewDate) 다 "
        "(PER-172). 현 스냅샷은 전 제품 unobserved 라 실효 0",
    ),
    Reason(
        REJECT_RECENCY, GATE_IDENTITY, "기간초과", "pipeline/policy.py", UNIT_REVIEW,
        STATUS_ACTIVE,
        "스냅샷 최신 월 기준 24개월 윈도우 밖이다. today 롤링이 아니다 (PER-172)",
    ),
    # --- 게이트2 중복 ---
    Reason(
        REJECT_DUPLICATE_CONTENT, GATE_DUPLICATE, "중복", "pipeline/gates.py", UNIT_REVIEW,
        STATUS_ACTIVE,
        "같은 제품에 contentHash 가 같은 리뷰가 이미 남았다 (PER-183)",
    ),
    Reason(
        REJECT_SAME_AUTHOR, GATE_DUPLICATE, "동일작성자", "pipeline/gates.py", UNIT_REVIEW,
        STATUS_ACTIVE,
        "(authorKey, productId) 가 같은 두 번째 이후 리뷰다 — 한 사람은 1표 (PER-170)",
    ),
    Reason(
        REJECT_DUPLICATE_SEMANTIC, GATE_DUPLICATE, "의미중복", "pipeline/embedding (PER-184)",
        UNIT_REVIEW, STATUS_RESERVED,
        "임베딩 의미 유사 클러스터링. **미적용이다** — 자리만 열어 둔다. 지금 이 사유로 "
        "탈락시키면 rejected[] 의 사유 분포가 거짓이 된다",
    ),
    # --- 게이트4 충분성 ---
    Reason(
        REJECT_SEGMENT_TOO_SMALL, GATE_SUFFICIENCY, "세그먼트과소", "pipeline/policy.py",
        UNIT_CLAIM, STATUS_ACTIVE,
        "S < S_min — 셀 자체가 말할 자격이 없다. 주장 이전에 걸린다 (PER-186 §5-3)",
    ),
    Reason(
        REJECT_INSUFFICIENT, GATE_SUFFICIENCY, "과소근거", "pipeline/policy.py",
        UNIT_CLAIM, STATUS_ACTIVE,
        "U < N_min — 방향을 명시한 고유 작성자가 절대 하한 미만이다 (PER-186)",
    ),
    Reason(
        REJECT_MINORITY_SHARE, GATE_SUFFICIENCY, "소수방향", "pipeline/policy.py",
        UNIT_CLAIM, STATUS_ACTIVE,
        "U/D < R_min — 언급한 사람 중 그 방향의 몫이 하한 미만이다. 분모는 셀 크기 S 가 "
        "아니라 언급 작성자 D 다 (PER-178)",
    ),
)

BY_CODE: dict[str, Reason] = {r.code: r for r in REASONS}
if len(BY_CODE) != len(REASONS):
    raise RejectRegistryError("사유 코드가 중복 등록됐다")

# 사람이 읽는 표기. `gates.REJECT_LABELS` · `sufficiency.SUFFICIENCY_REJECT_LABELS` 가
# 이 표에서 자기 몫을 잘라 쓴다 — 두 벌로 들지 않는다.
REJECT_LABELS: dict[str, str] = {r.code: r.label for r in REASONS}

# 게이트 → 사유 집합. **게이트3 은 빈 튜플이고 그게 결정이다** (모듈 문서 참조).
REASONS_BY_GATE: dict[str, tuple[str, ...]] = {
    gate: tuple(r.code for r in REASONS if r.gate == gate) for gate in GATES
}

# 게이트3 이 사유 0개인 이유. 에러 메시지가 결정 문서를 가리키게 한다.
GATE_POLARITY_NO_REJECT = (
    "게이트3(방향성)은 근거를 탈락시키지 않는다 — 부정 근거는 틀린 근거가 아니다. "
    "PER-185 결정(docs/DECISION_PER185_POLARITY_GATE.md §1)이고 "
    "test_polarity.EvidenceIsNotDiscarded 가 고정한다. PER-188 이슈 설명문의 "
    "`게이트3 → 방향불일치` 는 이 결정과 충돌하므로 기각했다 — 방향이 갈린 근거는 "
    "탈락이 아니라 `혼재` 판정과 한계로 남는다"
)


@dataclass(frozen=True)
class Limitation:
    """통과에 따라붙는 한계 1종. 탈락이 아니므로 `rejected[]` 가 아니라 주장에 실린다."""
    code: str
    gate: str
    label: str
    note: str

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "gate": self.gate,
            "issue": GATE_ISSUES[self.gate],
            "label": self.label,
            "note": self.note,
        }


LIMITATIONS: tuple[Limitation, ...] = (
    Limitation(
        LIMIT_RENEWAL_UNOBSERVED, GATE_IDENTITY, "리뉴얼미확정",
        "renewalPolicy 가 unobserved 다. 컷을 걸지 않되 세대가 섞였을 수 있다는 사실이 "
        "주장까지 따라간다 (PER-172). 현 스냅샷은 전 제품이 여기 해당한다",
    ),
    Limitation(
        LIMIT_TAGGER_DIRECTION_ERROR, GATE_POLARITY, "태거방향오류",
        "방향 판정에는 언제나 태거의 방향 오류가 얹힌다 (정답셋 200건에서 6.5%) (PER-185)",
    ),
    Limitation(
        LIMIT_ORDER_CHOSEN_DIRECTION, GATE_POLARITY, "출력순서결정",
        "태거가 같은 축에 긍·부정을 둘 다 뱉어 방향이 출력 순서로 정해졌다 (PER-185)",
    ),
    Limitation(
        LIMIT_MINORITY_WITHIN_NOISE, GATE_POLARITY, "잡음범위소수",
        "갈리긴 했는데 소수가 태거 뒤집힘만으로 설명된다. **판정은 그대로 mixed** 이고 "
        "소수도 그대로 세어진다 — 컷이 아니라 한계다 (PER-185)",
    ),
    Limitation(
        LIMIT_SINGLE_DISSENT, GATE_SUFFICIENCY, "반대1명",
        "반대가 1명이어도 다수 방향으로 뭉개지 않는다. 컷이 아니라 한계다 (PER-186 §4)",
    ),
)

LIMITATIONS_BY_CODE: dict[str, Limitation] = {lim.code: lim for lim in LIMITATIONS}
LIMIT_LABELS: dict[str, str] = {lim.code: lim.label for lim in LIMITATIONS}


def reason(code: str) -> Reason:
    """사유 1종을 꺼낸다. 미등록이면 조용히 폴백하지 않고 에러다."""
    try:
        return BY_CODE[code]
    except KeyError:
        raise RejectRegistryError(
            f"등록되지 않은 탈락 사유 {code!r}. 등록된 사유: {sorted(BY_CODE)}\n"
            "  → 사유를 새로 만들려면 pipeline/reject_registry.py 에 등록하고 "
            "어느 게이트의 것인지 적어라. 미등록 사유로 버리면 그 행은 어느 게이트에도 "
            "귀속되지 않는다"
        ) from None


def label_of(code: str) -> str:
    """한글 표기. 표기를 모르는 사유는 애초에 탈락시킬 수 없다."""
    return reason(code).label


def reasons_for(gate: str) -> tuple[str, ...]:
    """그 게이트가 쓸 수 있는 사유 전부 (`reserved` 포함)."""
    if gate not in REASONS_BY_GATE:
        raise RejectRegistryError(f"알 수 없는 게이트 {gate!r} (등록: {GATES})")
    return REASONS_BY_GATE[gate]


def assert_rejectable(gate: str, code: str) -> Reason:
    """이 게이트가 이 사유로 탈락시켜도 되는가. **원장에 행을 넣기 전에 부른다.**

    세 가지를 막는다.

      1. 미등록 사유 — 오타 하나로 어느 게이트에도 안 붙는 행이 생긴다
      2. 게이트 불일치 — 게이트2 의 사유로 게이트4 를 탈락시켜도 통과 수는 그대로다
      3. 미적용 사유 — `의미중복`(PER-184)은 자리만 있다. 쓰면 사유 분포가 거짓이 된다

    게이트3 은 사유가 0개라 무엇을 넣어도 여기서 멈춘다. 그게 PER-185 의 결정이다.
    """
    if gate not in REASONS_BY_GATE:
        raise RejectRegistryError(f"알 수 없는 게이트 {gate!r} (등록: {GATES})")
    entry = reason(code)
    if gate == GATE_POLARITY or not REASONS_BY_GATE[gate]:
        raise RejectRegistryError(
            f"{gate!r} 게이트로 {code!r} 탈락을 기록하려 했다.\n  → {GATE_POLARITY_NO_REJECT}"
        )
    if entry.gate != gate:
        raise RejectRegistryError(
            f"사유 {code!r} 는 {entry.gate!r} 게이트의 것인데 {gate!r} 로 기록하려 했다. "
            f"({gate!r} 의 사유: {list(REASONS_BY_GATE[gate])})\n"
            "  → 게이트와 사유가 1:1 이 아니면 rejected[] 를 게이트별로 되짚을 수 없다"
        )
    if entry.status != STATUS_ACTIVE:
        raise RejectRegistryError(
            f"사유 {code!r}({entry.label}) 는 {entry.status} 다 — {entry.note}\n"
            "  → 구현이 들어오기 전에 이 사유로 탈락시키면 rejected[] 의 사유 분포가 "
            "거짓이 된다. 없는 걸 있는 척하지 않는다"
        )
    return entry


def limitation(code: str) -> Limitation:
    """한계 1종. 미등록 한계도 조용히 넘기지 않는다 — 통과 행의 꼬리표도 어휘다."""
    try:
        return LIMITATIONS_BY_CODE[code]
    except KeyError:
        raise RejectRegistryError(
            f"등록되지 않은 한계 코드 {code!r}. 등록: {sorted(LIMITATIONS_BY_CODE)}"
        ) from None


def as_dict() -> dict:
    """리포트에 그대로 싣는 레지스트리 전문. 사유 어휘가 리포트에서 확인 가능해야 한다."""
    return {
        "issue": "PER-188",
        "gates": [
            {
                "gate": gate,
                "order": GATE_ORDER[gate],
                "issue": GATE_ISSUES[gate],
                "unit": (UNIT_CLAIM if gate == GATE_SUFFICIENCY else UNIT_REVIEW),
                "reasons": [BY_CODE[c].as_dict() for c in REASONS_BY_GATE[gate]],
                "note": GATE_POLARITY_NO_REJECT if gate == GATE_POLARITY else None,
            }
            for gate in GATES
        ],
        "limitations": [lim.as_dict() for lim in LIMITATIONS],
    }


__all__ = [
    "GATES",
    "GATE_DUPLICATE",
    "GATE_IDENTITY",
    "GATE_ISSUES",
    "GATE_ORDER",
    "GATE_POLARITY",
    "GATE_POLARITY_NO_REJECT",
    "GATE_SUFFICIENCY",
    "LIMITATIONS",
    "LIMITATIONS_BY_CODE",
    "LIMIT_LABELS",
    "LIMIT_MINORITY_WITHIN_NOISE",
    "LIMIT_ORDER_CHOSEN_DIRECTION",
    "LIMIT_RENEWAL_UNOBSERVED",
    "LIMIT_SINGLE_DISSENT",
    "LIMIT_TAGGER_DIRECTION_ERROR",
    "REASONS",
    "REASONS_BY_GATE",
    "REJECT_DUPLICATE_CONTENT",
    "REJECT_DUPLICATE_SEMANTIC",
    "REJECT_INSUFFICIENT",
    "REJECT_LABELS",
    "REJECT_MINORITY_SHARE",
    "REJECT_OPTION",
    "REJECT_OPTION_UNSTATED",
    "REJECT_PRODUCT",
    "REJECT_RECENCY",
    "REJECT_RENEWAL",
    "REJECT_SAME_AUTHOR",
    "REJECT_SEGMENT_TOO_SMALL",
    "STATUSES",
    "STATUS_ACTIVE",
    "STATUS_RESERVED",
    "UNITS",
    "UNIT_CLAIM",
    "UNIT_REVIEW",
    "Limitation",
    "Reason",
    "RejectRegistryError",
    "as_dict",
    "assert_rejectable",
    "label_of",
    "limitation",
    "reason",
    "reasons_for",
]
