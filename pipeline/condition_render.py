"""
condition 정규화 — 모든 주장을 `조건 → 결과` 로 (PER-192 / PRD §4-2).

두 가지를 소유한다.

  표기   코드 조건(`{"skinType": ["A02"]}`) → 사람이 읽는 문장 (`건성 피부 → …`)
  검증   **조건이 안 붙은 주장이 정말 무조건인가** — 조건축별로 방향이 갈리는데
         무조건으로 서술하면 그게 택소노미의 `missing_condition`(조건 누락)이다

## 저장은 코드, 표기에서 라벨 — 이슈 설명문의 한 줄을 기각했다

설명문에 *"`condition` 객체가 코드가 아니라 라벨로 채워진다"* 가 있었다. **그대로
하지 않았다.** `CLAUDE.md` 가 정반대를 못박고 있다 — *"`skinTypeHint` 는 라벨이 아니라
코드(A01~A07)로 받는다. 조건축과 같은 어휘여야 집계에서 합쳐진다 — v4 는 라벨로 받아
힌트를 통째로 잃었고 그게 카테고리 분포 FAIL 의 원인이었다."*

라벨로 저장하면 세 가지가 동시에 깨진다.

  1. **집계가 갈린다.** `A02` 와 `건성` 이 서로 다른 세그먼트가 된다. 코드북 검증
     (`codebook.assert_code`)은 라벨을 도메인 밖으로 보고 에러를 내므로, 라벨을
     저장하기로 하면 그 검증을 꺼야 한다 — 조용한 새 세그먼트의 문이 열린다
  2. **되돌릴 수 없다.** 코드 → 라벨은 함수지만 라벨 → 코드는 아니다. `민감성` 은
     `skinType` 의 A04 이면서 `skinTrouble` 의 C08 이다 — 축을 잃으면 복원이 안 된다
  3. **코드북이 정본이 아니게 된다.** 라벨 문구를 고치는 순간 저장된 값이 낡는다

그래서 저장은 코드고(`contracts` · `claim_contract`), 라벨은 **이 모듈에서만** 붙인다.
`codebook.label()` 의 주석이 이미 그렇게 적혀 있다 — *"표기 단계 전용."* 어휘를 새로
만들지 않고 코드북에 묻는다. 근거는 `docs/DECISION_PER192_CONDITION_NORMALIZATION.md`.

## 미기재는 "모든 사람" 이 아니다 (PER-178 · PER-177 §3)

`null`(무관)과 `"미기재"`(세그먼트)는 다르다. 미기재를 "모든 사람" 으로 번역하면
프로필을 안 밝힌 리뷰 묶음이 전수로 둔갑한다 — 침묵을 근거로 세는 것과 같은 사고다.
그래서 미기재의 표기는 **"피부타입을 안 밝힌 리뷰"** 이고, 무조건 주장의 표기는
**"조건 없음(이 제품 리뷰 전체)"** 이다. 둘은 서로 다른 문자열이고 섞이지 않는다.

## 무조건 서술 검증 — 갈리는데 무조건이면 `missing_condition`

"이 제품은 지속력이 좋다" 는 조건이 안 붙은 주장이다. 전수 실측에서 `p014 × 지속성` 은
트러블(C05) 고민 작성자의 부정 몫이 7.5% 인데 모공(C09) 고민 작성자는 68.8% 다
(`eval/reports/condition_split_per192.json`). 무조건으로 쓰면 모공 쪽 11명이 화면에서
사라진다. **이 함수는 claim 을 고치지 않는다** — 판정과 수치를 내고, 그 결과가
`failureReasons` 후보로 나간다. 무엇을 실패로 확정할지는 judge(PER-196)의 몫이다.

판정은 세 가지를 요구한다.

  기재된 세그먼트   `미기재` 는 비교에서 뺀다. "프로필을 안 밝힌 사람들" 은 독자가
                    자기에게 적용할 수 있는 조건이 아니다 (셋은 따로 세어 낸다)
  양쪽 다 충분      게이트4(PER-186)의 `U ≥ N_min AND U/D ≥ R_min AND S ≥ S_min`.
                    임계값을 여기서 새로 정하지 않는다
  잡음을 넘는 차이  두 세그먼트의 부정 몫이 Fisher 정확검정에서 `alpha` 미만.
                    **다중비교 보정은 호출부(측정)의 몫이다** — 여기서는 검정 1건의
                    p 값을 그대로 낸다

방향과 비율의 정의는 게이트3(`polarity.AspectSupport`)이 소유한다. 이 모듈은 그
객체를 **받아서** 비교할 뿐 방향을 다시 정의하지 않는다 — 정의가 갈리면 judge 가
게이트가 아니라 정의 차이를 재게 된다.
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).parent))

from claim_contract import CONDITION_AXES, NON_GOAL_AXES  # noqa: E402
from codebook import load_codebook  # noqa: E402
from contracts import MISSING_SEGMENT  # noqa: E402
from golden_contract import load_failure_taxonomy  # noqa: E402
from polarity import AspectSupport, DIRECTION_NEGATIVE, DIRECTION_POSITIVE  # noqa: E402
from policy import DEFAULT_SUFFICIENCY, SufficiencyPolicy, sufficiency_gate  # noqa: E402

ISSUE = "PER-192"

# `조건 → 결과` 의 화살표. 자료에 상수로 둔다 — 문자열을 호출부마다 박으면 표기가 갈린다.
ARROW = "→"

# 축별 표기 틀. **어휘는 코드북이 소유한다** — 여기서는 라벨 뒤에 붙는 말만 정한다.
# `option` 은 코드북에 도메인이 없는 자유 문자열이라 값을 그대로 쓴다 (PER-182).
AXIS_SUFFIX: dict[str, str] = {
    "skinType": "{label} 피부",
    "skinTrouble": "{label} 고민",
    "option": "{label} 옵션",
}

# 미기재 표기. **"모든 사람" 으로 번역하지 않는다** (PER-178 · PER-177 §3).
MISSING_PHRASE: dict[str, str] = {
    "skinType": "피부타입을 안 밝힌 리뷰",
    "skinTrouble": "피부고민을 안 밝힌 리뷰",
    "option": "옵션을 안 밝힌 리뷰",
}

# 조건이 없는 주장. 미기재와 **다른 문자열**이어야 한다 — 같으면 둘이 섞인다.
UNCONDITIONAL_SUBJECT = "조건 없음 (이 제품 리뷰 전체)"

# 한 축 안에서 코드가 여럿일 때. `skinTrouble` 은 AND 다 (둘 다 고른 사람).
CODE_JOIN = "·"
# 축이 여럿일 때.
AXIS_JOIN = " · "

NOT_EVERYONE_NOTE = (
    "'미기재' 는 프로필을 안 밝힌 리뷰들이지 '모든 사람' 이 아니다. 무조건 주장의 "
    f"표기는 '{UNCONDITIONAL_SUBJECT}' 이고 둘은 다른 세그먼트다 (PER-177 §3 · PER-178)"
)

# 조건 누락의 실패 유형 키. **택소노미가 정본이라 존재를 확인하고 쓴다** (PER-177).
FAILURE_MISSING_CONDITION = "missing_condition"

# 두 세그먼트의 부정 몫이 다르다고 볼 유의수준. 잠정값이고 교정은 PER-200 이다.
# 다중비교 보정은 측정(`eval/measure_condition_split.py`)이 한다.
SPLIT_ALPHA = 0.05


class ConditionRenderError(ValueError):
    """조건 표기·검증 입력이 계약을 위반했다. 조용한 폴백 금지.

    `ValueError` 를 상속한다 — 호출부가 이미 `ValueError` 를 잡고 있어도 위반이
    표기 문자열로 둔갑하지 않게 한다 (`ClaimContractError` 와 같은 이유).
    """


# ---------------------------------------------------------------- 표기


def _codes_of(axis: str, value) -> tuple[str, ...]:
    """축 값 → 코드 튜플. 문자열 하나는 글자로 쪼개지 않는다 (claim_contract 와 같은 규칙)."""
    if isinstance(value, str):
        if axis in ("skinType", "skinTrouble"):
            raise ConditionRenderError(
                f"{axis} 는 배열이어야 한다 (받은 값: {value!r}). 문자열 하나를 주면 "
                f"글자 단위로 쪼개져 조용히 다른 세그먼트가 된다 — 미기재도 "
                f"['{MISSING_SEGMENT}'] 로 준다 (pipeline/claim_contract.py 가 정본)"
            )
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(value)
    raise ConditionRenderError(f"{axis} 의 조건 값이 문자열도 배열도 아니다: {value!r}")


def render_axis(axis: str, value) -> str:
    """조건축 1개의 표기. 라벨은 **코드북에 묻는다** — 어휘를 새로 만들지 않는다.

    도메인 밖 코드나 라벨(`건성`)을 주면 `codebook` 이 에러를 낸다. 표기 단계에서
    조용히 통과시키면 화면에는 그럴듯한 문장이 나오고 집계만 갈린다.
    """
    if axis not in CONDITION_AXES:
        raise ConditionRenderError(
            f"모르는 조건축: {axis!r}. 쓸 수 있는 축: {list(CONDITION_AXES)}"
        )
    if axis in NON_GOAL_AXES:
        raise ConditionRenderError(
            f"{axis} 는 조건축이 아니다 — 데이터에 필드가 없다 (PER-187 §3). 값은 항상 null 이다"
        )
    codes = _codes_of(axis, value)
    if not codes:
        raise ConditionRenderError(f"{axis} 의 조건 값이 비었다 — 무관이면 null 이다")
    if MISSING_SEGMENT in codes:
        if len(codes) > 1:
            raise ConditionRenderError(
                f"{axis} 가 '{MISSING_SEGMENT}' 와 코드를 함께 담고 있다: {list(codes)!r} — "
                "미기재는 '모든 조건' 이 아니라 별개 세그먼트다 (pipeline/claim_contract.py)"
            )
        return MISSING_PHRASE[axis]
    if axis == "option":
        if len(codes) > 1:
            raise ConditionRenderError(
                f"option 은 단일 축이다 — 값이 여럿이면 셀을 나눠 각각 표기하라: {list(codes)!r}"
            )
        label = codes[0]
        if not isinstance(label, str) or not label.strip():
            raise ConditionRenderError(f"option 값이 비었다: {codes[0]!r}")
    else:
        book = load_codebook()
        # 라벨은 표기 단계 전용이다 (codebook.label 주석). 코드가 아니면 여기서 에러다.
        label = CODE_JOIN.join(book.label(axis, code) for code in codes)
    return AXIS_SUFFIX[axis].format(label=label)


def render_subject(condition: Mapping[str, object] | None) -> str:
    """조건 전체의 표기 — `조건 → 결과` 의 왼쪽.

    조건이 하나도 없으면 `UNCONDITIONAL_SUBJECT` 다. **빈 문자열을 내지 않는다** —
    빈 문자열을 내면 호출부가 화살표만 남은 문장을 만들고, 그 문장은 조건이 없다는
    사실을 말하지 않는다.
    """
    parts = [
        render_axis(axis, condition[axis])
        for axis in CONDITION_AXES
        if condition and condition.get(axis) not in (None, (), [])
    ]
    return AXIS_JOIN.join(parts) if parts else UNCONDITIONAL_SUBJECT


def render(condition: Mapping[str, object] | None, result: str) -> str:
    """`조건 → 결과` 한 문장. 결정론적 순수 함수다 (같은 입력 = 같은 바이트)."""
    if not isinstance(result, str) or not result.strip():
        raise ConditionRenderError("결과 문장이 비었다 — 조건만 표기하려면 render_subject 를 써라")
    return f"{render_subject(condition)} {ARROW} {result.strip()}"


def render_claim(claim) -> str:
    """claim 1건(PER-189 스키마 또는 그 dict)을 `조건 → 결과` 로. `answer` 가 결과다."""
    if isinstance(claim, Mapping):
        condition, answer = claim.get("condition"), claim.get("answer")
    else:
        condition, answer = getattr(claim, "condition", None), getattr(claim, "answer", None)
    return render(condition, answer)


def describe(condition: Mapping[str, object] | None) -> dict:
    """표기용 자료. 화면(PER-202~204)이 문자열만 받으면 축을 다시 쪼갤 수 없다.

    **저장값(코드)을 함께 낸다** — 표기만 남기면 되돌릴 수 없다는 것이 라벨 저장을
    기각한 이유 중 하나다 (§0).
    """
    axes = []
    for axis in CONDITION_AXES:
        value = condition.get(axis) if condition else None
        if value in (None, (), []):
            continue
        codes = _codes_of(axis, value)
        axes.append({
            "axis": axis,
            "codes": list(codes),          # 저장값 — 집계가 읽는 것
            "phrase": render_axis(axis, value),  # 표기 — 사람이 읽는 것
            "stated": MISSING_SEGMENT not in codes,
        })
    return {
        "issue": ISSUE,
        "subject": render_subject(condition),
        "claimType": "conditional" if axes else "unconditional",
        "axes": axes,
        "note": NOT_EVERYONE_NOTE,
    }


# ------------------------------------------------- 무조건 서술 검증


def _log_choose(n: int, k: int) -> float:
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def fisher_two_sided(a: int, b: int, c: int, d: int) -> float:
    """2×2 Fisher 정확검정 (양측). 행은 세그먼트, 열은 (부정, 긍정)이다.

    `scipy` 를 들이지 않고 직접 센다 — `polarity.binom_sf` 와 같은 이유다. 항을
    로그로 계산해 큰 셀에서 이항계수가 넘치지 않게 한다. 양측 p 는 관측 표의 확률
    이하인 표의 확률 합이다(conventional two-sided).

    카이제곱 근사를 쓰지 않는 이유: 조건 셀은 작다. `A06 × 가성비` 는 9명이고,
    근사는 그 구간에서 p 를 낙관적으로 준다 — 갈렸다고 더 쉽게 말하게 된다.
    """
    for name, value in (("a", a), ("b", b), ("c", c), ("d", d)):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ConditionRenderError(f"{name} 는 0 이상의 정수여야 한다: {value!r}")
    n = a + b + c + d
    row1, row2, col1 = a + b, c + d, a + c
    if not row1 or not row2 or not col1 or col1 == n:
        # 한 행이나 한 열이 비면 비교할 것이 없다. 1.0 은 "다르다고 말할 수 없다" 다
        return 1.0
    log_denom = _log_choose(n, col1)

    def prob(x: int) -> float:
        return math.exp(_log_choose(row1, x) + _log_choose(row2, col1 - x) - log_denom)

    observed = prob(a)
    total = 0.0
    for x in range(max(0, col1 - row2), min(row1, col1) + 1):
        p = prob(x)
        if p <= observed * (1 + 1e-9):
            total += p
    return min(1.0, total)


def _support_authors(positive: int, negative: int) -> int:
    """U — 방향을 **명시한** 작성자. `mixed` 면 양쪽을 흡수한다.

    규칙은 `sufficiency.ClaimSupport.support` 와 같아야 한다. 여기서는 집합이 아니라
    수만 받으므로 같은 규칙을 수로 다시 쓴 것이고, 두 경로가 같은 값을 내는지는
    `test_condition_render.SupportMatchesSufficiency` 가 고정한다.
    """
    if positive and negative:
        return positive + negative
    return positive or negative or 0


@dataclass(frozen=True)
class SegmentDirection:
    """조건 세그먼트 1개의 방향 — 게이트3 판정을 **받아서** 담는다.

    `support` 는 `polarity.AspectSupport` 다. 방향·부정 몫의 정의를 이 모듈이 다시
    쓰지 않는다 (PER-185 가 소유한다).
    """
    axis: str
    segment: str
    support: AspectSupport

    def __post_init__(self) -> None:
        if self.axis not in CONDITION_AXES or self.axis in NON_GOAL_AXES:
            raise ConditionRenderError(f"조건축이 아니다: {self.axis!r}")
        if not isinstance(self.segment, str) or not self.segment.strip():
            raise ConditionRenderError(f"[{self.axis}] 세그먼트가 비었다")

    @property
    def stated(self) -> bool:
        return self.segment != MISSING_SEGMENT

    @property
    def cell_authors(self) -> int:
        """S — 셀의 고유 작성자. 말한 사람 + 침묵이다 (게이트2 통과분 전제)."""
        return self.support.mentioned_authors + self.support.silent_authors

    @property
    def negative_share(self) -> float | None:
        """부정 몫. 분모는 방향을 말한 사람뿐이다 (PER-178)."""
        return self.support.negative_ratio

    @property
    def majority(self) -> str | None:
        """다수 방향. 정확히 반반이면 `None` 이다 — 다수가 없다."""
        share = self.negative_share
        if share is None or share == 0.5:
            return None
        return DIRECTION_NEGATIVE if share > 0.5 else DIRECTION_POSITIVE

    def sufficient(self, policy: SufficiencyPolicy = DEFAULT_SUFFICIENCY) -> bool:
        """게이트4를 그대로 건다. 임계값을 여기서 새로 정하지 않는다 (PER-186)."""
        return sufficiency_gate(
            support_authors=_support_authors(
                self.support.positive_authors, self.support.negative_authors),
            spoke_authors=self.support.mentioned_authors,
            cell_authors=self.cell_authors,
            policy=policy,
        ).passed

    def as_dict(self) -> dict:
        return {
            "axis": self.axis,
            "segment": self.segment,
            "phrase": render_axis(self.axis, [self.segment]),
            "stated": self.stated,
            "direction": self.support.direction,
            "positiveAuthors": self.support.positive_authors,
            "negativeAuthors": self.support.negative_authors,
            "neutralAuthors": self.support.neutral_authors,
            "silentAuthors": self.support.silent_authors,
            "spokeAuthors": self.support.mentioned_authors,
            "cellAuthors": self.cell_authors,
            "negativeShare": self.negative_share,
            "majority": self.majority,
        }


@dataclass(frozen=True)
class SplitPair:
    """같은 축의 두 세그먼트에서 방향이 갈린 1쌍. **판정이 아니라 수다.**"""
    axis: str
    left: SegmentDirection
    right: SegmentDirection
    p_value: float

    @property
    def gap(self) -> float:
        return round(abs((self.left.negative_share or 0.0) - (self.right.negative_share or 0.0)), 4)

    @property
    def flips(self) -> bool:
        """다수 방향이 서로 뒤집히는가. 몫 차이보다 강한 조건이다."""
        return (
            self.left.majority is not None
            and self.right.majority is not None
            and self.left.majority != self.right.majority
        )

    def as_dict(self) -> dict:
        return {
            "axis": self.axis,
            "left": self.left.as_dict(),
            "right": self.right.as_dict(),
            "negativeShareGap": self.gap,
            "majorityFlips": self.flips,
            "pValue": round(self.p_value, 8),
            "sentence": (
                f"{render_axis(self.axis, [self.left.segment])} {ARROW} "
                f"부정 {self.left.support.negative_authors}/"
                f"{self.left.support.directional_authors}"
                f" · {render_axis(self.axis, [self.right.segment])} {ARROW} "
                f"부정 {self.right.support.negative_authors}/"
                f"{self.right.support.directional_authors}"
            ),
        }


@dataclass(frozen=True)
class UnconditionalVerdict:
    """무조건 서술이 성립하는가. **claim 을 고치지 않는다** — 후보를 낼 뿐이다."""
    aspect: str
    holds: bool
    pairs: tuple[SplitPair, ...]
    compared: int
    skipped_unstated: int
    skipped_insufficient: int
    alpha: float

    @property
    def failure_reasons(self) -> tuple[str, ...]:
        """`failureReasons` 후보. 확정은 judge(PER-196)의 몫이다."""
        if self.holds:
            return ()
        taxonomy = load_failure_taxonomy()
        if FAILURE_MISSING_CONDITION not in taxonomy.keys:
            raise ConditionRenderError(
                f"택소노미에 {FAILURE_MISSING_CONDITION!r} 이 없다 — "
                "pipeline/failure_taxonomy.json 이 정본이다 (PER-177)"
            )
        return (FAILURE_MISSING_CONDITION,)

    @property
    def contradicting_authors(self) -> int:
        """무조건으로 서술했다면 덮였을 작성자 수 — 갈린 쌍에서 **소수 쪽 세그먼트**의
        방향 근거를 세그먼트 단위로 중복 없이 센다.

        "몇 명이 지워지는가" 가 이 검증의 값어치다. 쌍마다 더하면 같은 세그먼트를 여러
        번 세므로 `(축, 세그먼트)` 로 묶어 한 번만 센다.
        """
        worst: dict[tuple[str, str], int] = {}
        for pair in self.pairs:
            for side, other in ((pair.left, pair.right), (pair.right, pair.left)):
                if side.majority is None or side.majority == other.majority:
                    continue
                if (side.support.directional_authors
                        >= other.support.directional_authors):
                    continue  # 큰 쪽이 아니라 소수 쪽만 센다
                count = (side.support.negative_authors
                         if side.majority == DIRECTION_NEGATIVE
                         else side.support.positive_authors)
                key = (side.axis, side.segment)
                worst[key] = max(worst.get(key, 0), count)
        return sum(worst.values())

    def as_dict(self) -> dict:
        return {
            "aspect": self.aspect,
            "holds": self.holds,
            "alpha": self.alpha,
            "comparedPairs": self.compared,
            "splitPairs": [p.as_dict() for p in self.pairs],
            "majorityFlips": sum(1 for p in self.pairs if p.flips),
            "contradictingAuthors": self.contradicting_authors,
            "failureReasons": list(self.failure_reasons),
            "skipped": {
                "unstatedSegments": self.skipped_unstated,
                "insufficientSegments": self.skipped_insufficient,
            },
            "note": (
                "무조건 서술이 성립하지 않으면 `missing_condition` **후보**다. claim 을 "
                "여기서 고치지 않는다 — 확정은 judge(PER-196)이 하고, 다중비교 보정은 "
                "측정(eval/measure_condition_split.py)이 한다"
            ),
        }


def check_unconditional(
    aspect: str,
    segments: Sequence[SegmentDirection] | Iterable[SegmentDirection],
    policy: SufficiencyPolicy = DEFAULT_SUFFICIENCY,
    alpha: float = SPLIT_ALPHA,
) -> UnconditionalVerdict:
    """조건이 안 붙은 주장이 정말 제품 전체에서 성립하는가.

    같은 축의 **기재된 · 충분한** 세그먼트끼리 부정 몫을 견준다. 하나라도 잡음을
    넘게 갈리면 `holds=False` 이고 `missing_condition` 후보가 나온다.

    비교에서 뺀 것은 지우지 않고 센다 (`skipped`) — 무엇을 안 봤는지 모르면 "갈리지
    않았다" 가 "볼 게 없었다" 와 구별되지 않는다. 게이트1·2 의 `rejected[]` 와 같은 규칙.
    """
    if not isinstance(alpha, float) or not 0.0 < alpha < 1.0:
        raise ConditionRenderError(f"alpha 는 (0, 1) 안의 실수여야 한다: {alpha!r}")
    items = list(segments)
    for item in items:
        if not isinstance(item, SegmentDirection):
            raise ConditionRenderError(f"SegmentDirection 이 아니다: {item!r}")
        if item.support.aspect != aspect:
            raise ConditionRenderError(
                f"[{aspect}] 다른 aspect 의 세그먼트가 섞였다: {item.support.aspect!r} "
                f"({item.axis}={item.segment}) — 축이 섞이면 갈림이 aspect 차이로 둔갑한다"
            )

    unstated = [s for s in items if not s.stated]
    stated = [s for s in items if s.stated]
    usable = [s for s in stated if s.sufficient(policy) and s.support.directional_authors]
    insufficient = len(stated) - len(usable)

    by_axis: dict[str, list[SegmentDirection]] = {}
    for item in usable:
        by_axis.setdefault(item.axis, []).append(item)

    pairs: list[SplitPair] = []
    compared = 0
    for axis in sorted(by_axis):
        group = sorted(by_axis[axis], key=lambda s: s.segment)
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                left, right = group[i], group[j]
                compared += 1
                p_value = fisher_two_sided(
                    left.support.negative_authors, left.support.positive_authors,
                    right.support.negative_authors, right.support.positive_authors,
                )
                if p_value < alpha:
                    pairs.append(SplitPair(axis, left, right, p_value))

    ordered = tuple(sorted(pairs, key=lambda p: (p.p_value, p.axis,
                                                 p.left.segment, p.right.segment)))
    return UnconditionalVerdict(
        aspect=aspect,
        holds=not ordered,
        pairs=ordered,
        compared=compared,
        skipped_unstated=len(unstated),
        skipped_insufficient=insufficient,
        alpha=alpha,
    )


__all__ = [
    "ARROW",
    "AXIS_SUFFIX",
    "FAILURE_MISSING_CONDITION",
    "MISSING_PHRASE",
    "MISSING_SEGMENT",
    "NOT_EVERYONE_NOTE",
    "SPLIT_ALPHA",
    "UNCONDITIONAL_SUBJECT",
    "ConditionRenderError",
    "SegmentDirection",
    "SplitPair",
    "UnconditionalVerdict",
    "check_unconditional",
    "describe",
    "fisher_two_sided",
    "render",
    "render_axis",
    "render_claim",
    "render_subject",
]
