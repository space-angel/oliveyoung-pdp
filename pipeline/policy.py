"""
리뉴얼 취급 · 리센시 컷 · 충분성 임계값 정책 (PER-172 · PER-186 / PRD §3-3 · §4-4 · §9).

게이트1(동일성, PER-182)과 게이트4(충분성, PER-186)가 소비하는 **컷의 기준**을 여기서
정의한다. 카탈로그가 **어떤 제품인가**를 소유하고(PER-171), 이 모듈은 **그 리뷰를 지금
근거로 쓸 수 있는가**와 **그 근거가 주장을 세울 만큼인가**를 소유한다. 게이트 모듈
(`gates.py` · `sufficiency.py`)은 이 판정을 묶어 `rejected[]` 행으로 만들 뿐이다.

## 결정 1 — 리뉴얼은 별개 제품이다 (PRD 권장안 채택)

제형·용기가 바뀌면 리뷰의 주장이 무효가 되므로 세대를 섞지 않는다. 다만 **실행 범위는
관측 가능한 만큼으로 제한한다.** 25K 스냅샷에서 리뉴얼 시점을 확정할 구조화 필드가 없기
때문이다 (`eval/reports/renewal_recency_per172.json`).

  - `goodsNo` 교체는 신호가 아니다 — 멀티 goodsNo 제품 36개 중 교체형 1개, 병존형 35개
  - 세대 경계가 SKU 코드 **안쪽**에 있는 제품이 있다 (예: 에스쁘아 비벨벳 커버쿠션은
    goodsNo 1개로 2023.04~2026.08 전 구간). 그래서 리뉴얼 컷의 키는 goodsNo 가 아니라
    **(goodsNo, reviewDate)** 다

따라서 카탈로그의 `renewalPolicy` 는 3상태이고, 기본값은 "모른다"를 명시한다.

  `separate`    세대별로 productId 를 나눈다. `fromMonth`~`toMonth` 밖의 리뷰는 컷된다
  `single`      리뉴얼이 없음을 확인했다. 컷을 걸지 않는다
  `unobserved`  아직 확정하지 않았다. **컷을 걸지 않되 한계를 주장에 남긴다**

`unobserved` 를 조용히 `single` 로 취급하지 않는 것이 요점이다. 컷이 안 걸린 사실이
출력까지 따라가지 않으면, 세대가 섞인 근거와 확인된 근거를 구분할 수 없다.

## 결정 2 — 리센시 컷은 스냅샷 기준 24개월

`today` 기준 롤링 윈도우를 쓰지 않는다. 재현성 규칙(§5-2, "생성물에 시각을 기록하지
않는다")과 정면으로 충돌하기 때문이다 — 같은 입력을 내일 다시 돌리면 결과가 달라진다.
대신 **스냅샷 최신 월에 고정된 오프셋**으로 정의하고, 새 수집분이 들어오면
`assert_snapshot_current()` 가 에러를 내 정책을 다시 정하게 만든다.

24개월(2024-09~2026-08)의 비용은 리뷰 88.8% 잔존, 충분성 게이트를 통과하는
`productId×skinType` 셀 294→284 (-10, 3.4%) 다.

## 결정 3 — 충분성은 세 조건의 AND 다 (PER-186)

`U ≥ N_min AND U/D ≥ R_min AND S ≥ S_min`. **낮은 쪽이 아니라 높은 쪽을 만족해야
통과다.** 분모 D 는 주제를 언급한 고유 작성자이고 셀 크기 S 가 아니다 — 말하지 않은
사람을 어느 쪽 근거로도 세지 않는다는 PER-178 의 결정이 여기 걸린다.
근거는 `docs/DECISION_PER186_SUFFICIENCY.md`.

## 컷은 드롭이 아니다

어느 컷도 대상을 삭제하지 않고 `rejected[]` 에 사유를 남긴다(PRD §3-2). 리센시·리뉴얼은
리뷰를, 충분성은 주장을 남긴다. 통과한 것만 남기면 정밀도는 측정되지만 재현율은 영영
측정되지 않는다 — 과소근거로 떨어진 주장의 목록이 곧 커버리지 실험(PER-199)의 분모다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# --- 리센시 컷 ---

# 스냅샷 `data/input/reviews_50products.json` 의 최신 리뷰 월. 새 수집분이 들어오면
# 이 값이 낡고, assert_snapshot_current() 가 에러를 낸다.
SNAPSHOT_LATEST_MONTH = "2026-08"
RECENCY_WINDOW_MONTHS = 24
MONTH_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

# --- 리뉴얼 정책 어휘 ---

RENEWAL_SEPARATE = "separate"
RENEWAL_SINGLE = "single"
RENEWAL_UNOBSERVED = "unobserved"
RENEWAL_POLICIES = (RENEWAL_SEPARATE, RENEWAL_SINGLE, RENEWAL_UNOBSERVED)

# --- 충분성 컷 (PER-186) ---
#
# 세 임계값은 **모두** 만족해야 통과다. 낮은 쪽이 아니라 높은 쪽이다 — 단일 임계값
# (예: "15% 이상")으로 정의하면 소표본에서 무너진다 (PRD §4-4).
#
#   N_min  U ≥ 8       방향을 명시한 고유 작성자의 절대 하한
#   R_min  U/D ≥ 0.10  **언급한 사람** 중 그 방향의 몫. 분모는 셀 전체(S)가 아니다
#   S_min  S ≥ 8       셀 자체의 최소 관찰 수 — 주장 이전에 셀이 말할 자격이 있는가
#
# 분모를 D 로 두는 것이 PER-178 의 결정이다: 주제를 말하지 않은 사람은 긍정도 부정도
# 아니므로 어느 쪽 근거로도 세지 않는다 (`docs/DECISION_PER178_SILENCE_IS_NOT_EVIDENCE.md`).
# D 를 S 로 부풀리면 "40명 중 3명이 말했다"가 "40명 중 3명만 불만"으로 둔갑한다.
SUFFICIENCY_N_MIN = 8
SUFFICIENCY_R_MIN = 0.10
SUFFICIENCY_S_MIN = 8
# 소수 의견 하한. **컷이 아니라 한계 표시다** — 반대 1명을 다수 방향으로 뭉개지 않는다
# (PER-178 규격 §9 가 기각한 대안). 근거는 docs/DECISION_PER186_SUFFICIENCY.md §4.
SUFFICIENCY_MINORITY_MIN = 2

# `rejected[]` 사유 코드 / 주장에 남기는 한계 코드
REJECT_RECENCY = "recency_cut"
REJECT_RENEWAL = "renewal_cut"
LIMIT_RENEWAL_UNOBSERVED = "renewal_unobserved"

# 충분성 탈락 사유 (PER-186). 셋 다 PRD 가 말하는 "과소근거" 부류지만 코드는 나눈다 —
# 무엇이 결속했는지 모르면 임계값을 바꿨을 때 달라진 몫을 귀속시킬 수 없다 (게이트1의
# `옵션불일치`/`옵션미기재` 와 같은 이유).
REJECT_INSUFFICIENT = "insufficient_support"    # U < N_min
REJECT_MINORITY_SHARE = "minority_share"        # U/D < R_min
REJECT_SEGMENT_TOO_SMALL = "segment_too_small"  # S < S_min
LIMIT_SINGLE_DISSENT = "single_dissent"


class PolicyError(Exception):
    """정책 입력이 계약을 위반했다 (날짜 파싱 실패, 낡은 스냅샷 기준 등)."""


@dataclass(frozen=True)
class GateDecision:
    """게이트 1건의 판정.

    `passed` 만 보고 버리면 안 된다 — `reason` 은 `rejected[]` 에, `limitation` 은
    통과한 주장에 남는다. 통과했는데 한계가 있는 경우(`unobserved`)가 실재한다.
    """
    passed: bool
    reason: str | None = None
    limitation: str | None = None


def month_of(review_date: str) -> str:
    """`2026.07.19` / `2026-07-19` → `2026-07`. 파싱 불가는 조용히 넘기지 않고 에러다."""
    text = (review_date or "").strip().replace(".", "-")
    if len(text) < 7 or not MONTH_PATTERN.match(text[:7]):
        raise PolicyError(f"리뷰 날짜를 월로 읽을 수 없다: {review_date!r} (기대: 2026.07.19)")
    return text[:7]


def _shift_month(month: str, back: int) -> str:
    year, mon = int(month[:4]), int(month[5:7])
    total = year * 12 + (mon - 1) - back
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def recency_cutoff_month(
    latest_month: str = SNAPSHOT_LATEST_MONTH, window: int = RECENCY_WINDOW_MONTHS
) -> str:
    """윈도우의 **첫 달**. 윈도우는 최신 월을 포함하므로 24개월이면 latest-23 이다."""
    if not MONTH_PATTERN.match(latest_month):
        raise PolicyError(f"월 형식 위반: {latest_month!r} (기대: 2026-08)")
    if window < 1:
        raise PolicyError(f"리센시 윈도우는 1개월 이상이어야 한다: {window}")
    return _shift_month(latest_month, window - 1)


RECENCY_CUTOFF_MONTH = recency_cutoff_month()


def assert_snapshot_current(latest_review_date: str) -> None:
    """스냅샷이 정책 기준월보다 새로우면 에러.

    새 수집분을 넣고 리센시 컷을 그대로 두면 윈도우가 소리 없이 과거로 밀린다.
    조용히 밀리게 두지 않고 여기서 멈춰 `SNAPSHOT_LATEST_MONTH` 를 다시 정하게 한다.
    """
    latest = month_of(latest_review_date)
    if latest > SNAPSHOT_LATEST_MONTH:
        raise PolicyError(
            f"스냅샷 최신 월 {latest} 가 정책 기준 {SNAPSHOT_LATEST_MONTH} 보다 새롭다.\n"
            "  → 새 수집분이 들어왔다. pipeline/policy.py 의 SNAPSHOT_LATEST_MONTH 와\n"
            "     리센시 컷을 다시 정하고 eval/measure_renewal_recency.py 로 비용을 재측정하라.\n"
            "     (그냥 두면 24개월 윈도우가 조용히 과거로 밀린다)"
        )


def recency_gate(review_date: str, cutoff: str = RECENCY_CUTOFF_MONTH) -> GateDecision:
    """리뷰가 리센시 윈도우 안에 있는가. 밖이면 드롭이 아니라 `rejected[]` 행이다."""
    if month_of(review_date) >= cutoff:
        return GateDecision(passed=True)
    return GateDecision(passed=False, reason=REJECT_RECENCY)


# --- 리뉴얼 컷 ---


def renewal_gate(product, review_date: str) -> GateDecision:
    """리뷰가 이 제품 **세대**의 것인가.

    `product` 는 `pipeline.catalog.Product` 다 (순환 임포트를 피하려고 타입을 강제하지
    않는다 — `renewal_policy` / `renewal_from_month` / `renewal_to_month` 만 읽는다).

      separate    세대 구간 밖이면 컷한다
      single      리뉴얼 없음이 확인됐다 — 통과
      unobserved  통과시키되 `limitation` 을 남긴다. 여기서 조용히 통과시키면
                  세대가 섞인 근거를 확인된 근거와 구분할 수 없게 된다
    """
    policy = product.renewal_policy
    if policy == RENEWAL_SINGLE:
        return GateDecision(passed=True)
    if policy == RENEWAL_UNOBSERVED:
        return GateDecision(passed=True, limitation=LIMIT_RENEWAL_UNOBSERVED)
    if policy != RENEWAL_SEPARATE:
        raise PolicyError(
            f"{product.product_id}: 알 수 없는 renewalPolicy {policy!r} "
            f"(기대: {RENEWAL_POLICIES})"
        )

    month = month_of(review_date)
    if product.renewal_from_month is not None and month < product.renewal_from_month:
        return GateDecision(passed=False, reason=REJECT_RENEWAL)
    if product.renewal_to_month is not None and month > product.renewal_to_month:
        return GateDecision(passed=False, reason=REJECT_RENEWAL)
    return GateDecision(passed=True)


# --- 충분성 컷 (PER-186) ---


@dataclass(frozen=True)
class SufficiencyPolicy:
    """충분성 임계값 묶음. **설정값이고 `meta` 에 기록된다** (PER-186 완료 조건).

    임계값을 인자로 받는 이유는 민감도 때문이다 — 임계값을 바꿨을 때 통과 주장 수가
    어떻게 변하는지가 PER-199 커버리지 측정의 입력이다
    (`eval/reports/gate4_sufficiency_per186.json` 의 `sensitivity`).

    기본값을 코드 상수로 두는 것은 리센시 컷(`SNAPSHOT_LATEST_MONTH`)과 같은 취급이다:
    스냅샷마다 다시 정하는 값이 아니라 **정책**이므로, 바꾸면 커밋 로그에 남아야 한다.
    """
    n_min: int = SUFFICIENCY_N_MIN
    r_min: float = SUFFICIENCY_R_MIN
    s_min: int = SUFFICIENCY_S_MIN
    minority_min: int = SUFFICIENCY_MINORITY_MIN

    def __post_init__(self) -> None:
        if not isinstance(self.n_min, int) or isinstance(self.n_min, bool) or self.n_min < 1:
            raise PolicyError(f"N_min 은 1 이상의 정수여야 한다: {self.n_min!r}")
        if not isinstance(self.s_min, int) or isinstance(self.s_min, bool) or self.s_min < 1:
            raise PolicyError(f"S_min 은 1 이상의 정수여야 한다: {self.s_min!r}")
        if not isinstance(self.r_min, (int, float)) or isinstance(self.r_min, bool):
            raise PolicyError(f"R_min 은 수여야 한다: {self.r_min!r}")
        if not 0.0 < self.r_min <= 1.0:
            raise PolicyError(f"R_min 은 0 초과 1 이하여야 한다: {self.r_min!r} (0 이면 비율 조건이 꺼진다)")
        if self.s_min < self.n_min:
            # U ≤ D ≤ S 이므로 S_min < N_min 이면 세그먼트 조건이 영원히 결속하지 않는다.
            # "세 조건을 모두 만족해야 통과"가 조용히 두 조건으로 줄어드는 경로다.
            raise PolicyError(
                f"S_min({self.s_min}) 이 N_min({self.n_min}) 보다 작다. U ≤ D ≤ S 라 "
                "이 설정은 세그먼트 조건을 껐다는 뜻이다 — 끄려면 그렇게 적어라"
            )
        if not isinstance(self.minority_min, int) or isinstance(self.minority_min, bool) or self.minority_min < 1:
            raise PolicyError(f"minority_min 은 1 이상의 정수여야 한다: {self.minority_min!r}")

    def as_meta(self) -> dict:
        """`meta.sufficiency` 에 그대로 실린다. 수치가 어느 정책에서 나왔는지 남는다."""
        return {
            "issue": "PER-186",
            "nMin": self.n_min,
            "rMin": self.r_min,
            "sMin": self.s_min,
            "minorityMin": self.minority_min,
            "denominator": "spokeAuthors",
            "note": (
                "U ≥ N_min AND U/D ≥ R_min AND S ≥ S_min 을 모두 만족해야 통과. "
                "분모 D 는 주제를 언급한 고유 작성자다 (PER-178 — 침묵은 근거가 아니다)"
            ),
        }


DEFAULT_SUFFICIENCY = SufficiencyPolicy()


def sufficiency_gate(
    support_authors: int,
    spoke_authors: int,
    cell_authors: int,
    minority_authors: int | None = None,
    policy: SufficiencyPolicy = DEFAULT_SUFFICIENCY,
) -> GateDecision:
    """주장 1건의 충분성 판정. **세 조건을 모두** 만족해야 통과다 (PRD §4-4).

      support_authors  U — 주장의 방향을 명시한 고유 작성자 수
      spoke_authors    D — 그 주제를 말한 고유 작성자 수 (U0 중립 포함)
      cell_authors     S — 셀의 유효 고유 작성자 수 (게이트2 통과분)
      minority_authors 방향이 갈릴 때 적은 쪽의 수. 없으면 `None`

    판정 순서는 넓은 것부터다 — 셀이 말할 자격이 없으면(S) 주장의 근거 수(U)를 물을
    이유가 없고, 사유가 하나로 정해져야 `rejected[]` 가 재현율의 단서가 된다.

    U ≤ D ≤ S 는 불변식이다. 깨지면 어딘가에서 침묵을 근거로 세었다는 뜻이므로 조용히
    통과시키지 않고 에러다. 다만 이 함수는 **수만 받으므로** 셀 밖 작성자가 섞인 경우는
    여기서 잡히지 않는다 — 집합 대조는 `sufficiency.ClaimSupport.as_dict()` 가 한다.
    """
    for name, value in (("U", support_authors), ("D", spoke_authors), ("S", cell_authors)):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise PolicyError(f"{name} 는 0 이상의 정수여야 한다: {value!r}")
    if not support_authors <= spoke_authors <= cell_authors:
        raise PolicyError(
            f"U ≤ D ≤ S 위반: U={support_authors} D={spoke_authors} S={cell_authors}. "
            "말하지 않은 사람을 근거로 세었거나(PER-178) 셀 밖 작성자가 섞였다"
        )

    if cell_authors < policy.s_min:
        return GateDecision(passed=False, reason=REJECT_SEGMENT_TOO_SMALL)
    if support_authors < policy.n_min:
        return GateDecision(passed=False, reason=REJECT_INSUFFICIENT)
    if support_authors / spoke_authors < policy.r_min:
        return GateDecision(passed=False, reason=REJECT_MINORITY_SHARE)

    # 통과했다고 한계가 없는 것은 아니다 — 게이트1의 `unobserved` 와 같은 자리다.
    if minority_authors is not None and 0 < minority_authors < policy.minority_min:
        return GateDecision(passed=True, limitation=LIMIT_SINGLE_DISSENT)
    return GateDecision(passed=True)
