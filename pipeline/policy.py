"""
리뉴얼 취급 · 리센시 컷 정책 (PER-172 / PRD §3-3 · §9).

게이트1(동일성, PER-182)이 소비하는 두 개의 컷을 여기서 정의한다. 카탈로그가
**어떤 제품인가**를 소유하고(PER-171), 이 모듈은 **그 리뷰를 지금 근거로 쓸 수 있는가**를
소유한다.

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

## 컷은 드롭이 아니다

두 컷 모두 리뷰를 삭제하지 않고 `rejected[]` 에 사유를 남긴다(PRD §3-2). 통과한 것만
남기면 정밀도는 측정되지만 재현율은 영영 측정되지 않는다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# --- 리센시 컷 ---

# 스냅샷 `data/input/reviews_50products.json` 의 최신 리뷰 월. 새 수집분이 들어오면
# 이 값이 낡고, assert_snapshot_current() 가 에러를 낸다.
SNAPSHOT_LATEST_MONTH = "2026-08"
RECENCY_WINDOW_MONTHS = 24
# 충분성 게이트(PER-186)의 절대하한. 컷 비용을 같은 잣대로 재기 위해 여기 둔다.
SUFFICIENCY_N_MIN = 8

MONTH_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

# --- 리뉴얼 정책 어휘 ---

RENEWAL_SEPARATE = "separate"
RENEWAL_SINGLE = "single"
RENEWAL_UNOBSERVED = "unobserved"
RENEWAL_POLICIES = (RENEWAL_SEPARATE, RENEWAL_SINGLE, RENEWAL_UNOBSERVED)

# `rejected[]` 사유 코드 / 주장에 남기는 한계 코드
REJECT_RECENCY = "recency_cut"
REJECT_RENEWAL = "renewal_cut"
LIMIT_RENEWAL_UNOBSERVED = "renewal_unobserved"


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
