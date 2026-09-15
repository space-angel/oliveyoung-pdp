"""조건 표기 · 무조건 서술 검증 (PER-192).

"에러를 낸다"는 완료 조건은 테스트로 고정한다 (CLAUDE.md). 이 파일이 고정하는 것은
넷이다.

  1. 표기는 **코드북에만** 묻는다 — 라벨(`건성`)·도메인 밖 코드를 주면 에러
  2. 미기재는 "모든 사람" 이 아니다 — 무조건 표기와 **다른 문자열**이어야 한다
  3. 조건이 붙은 주장과 안 붙은 주장이 구분된다 (claim_contract 의 `claimType` 과 일치)
  4. 갈리는데 무조건으로 서술하면 `missing_condition` **후보**가 나온다
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from claim_contract import Claim, ClaimContractError, ClaimEvidence  # noqa: E402
from codebook import UnknownConditionCodeError, load_codebook  # noqa: E402
from contracts import MISSING_SEGMENT  # noqa: E402
from golden_contract import load_failure_taxonomy  # noqa: E402
from polarity import AspectSupport  # noqa: E402
from policy import SufficiencyPolicy, DEFAULT_SUFFICIENCY  # noqa: E402
from sufficiency import ClaimSupport, EvidenceCell  # noqa: E402
from condition_render import (  # noqa: E402
    ARROW,
    FAILURE_MISSING_CONDITION,
    SPLIT_ALPHA,
    UNCONDITIONAL_SUBJECT,
    ConditionRenderError,
    SegmentDirection,
    _support_authors,
    check_unconditional,
    describe,
    fisher_two_sided,
    render,
    render_axis,
    render_claim,
    render_subject,
)


def seg(axis="skinTrouble", segment="C05", pos=0, neg=0, neu=0, silent=0, aspect="지속성"):
    support = AspectSupport(aspect, pos, neg, neu, silent, 1.0)
    return SegmentDirection(axis, segment, support)


class Rendering(unittest.TestCase):
    """코드 → 라벨은 코드북이 소유한다. 어휘를 새로 만들지 않는다."""

    def test_label_comes_from_codebook(self):
        book = load_codebook()
        self.assertIn(book.label("skinType", "A02"), render_axis("skinType", ["A02"]))
        self.assertIn(book.label("skinTrouble", "C09"), render_axis("skinTrouble", ["C09"]))

    def test_sentence_is_condition_then_result(self):
        self.assertEqual(
            render({"skinType": ["A02"]}, "촉촉하다는 쪽이다"),
            f"건성 피부 {ARROW} 촉촉하다는 쪽이다",
        )

    def test_deterministic(self):
        condition = {"skinTrouble": ["C09", "C05"], "skinType": ["A01"]}
        self.assertEqual(render(condition, "x"), render(dict(condition), "x"))

    def test_multi_axis_order_follows_contract(self):
        # 축 순서는 `claim_contract.CONDITION_AXES` 다 — 입력 dict 순서가 아니다
        a = render({"option": "40ml", "skinType": ["A01"]}, "x")
        b = render({"skinType": ["A01"], "option": "40ml"}, "x")
        self.assertEqual(a, b)
        self.assertLess(a.index("지성"), a.index("40ml"))

    def test_label_input_is_an_error(self):
        # v4 는 라벨로 받아 힌트를 통째로 잃었다. 표기 단계에서도 라벨은 입력이 아니다
        with self.assertRaises(UnknownConditionCodeError):
            render_axis("skinType", ["건성"])

    def test_axis_mixup_is_an_error(self):
        with self.assertRaises(UnknownConditionCodeError):
            render_axis("skinType", ["C05"])

    def test_unknown_axis_is_an_error(self):
        with self.assertRaises(ConditionRenderError):
            render_axis("season", ["여름"])

    def test_non_goal_axis_is_an_error(self):
        with self.assertRaises(ConditionRenderError):
            render_axis("usagePeriod", ["1개월"])

    def test_string_instead_of_list_is_an_error(self):
        # 문자열 하나를 주면 글자로 쪼개져 조용히 다른 세그먼트가 된다
        with self.assertRaises(ConditionRenderError):
            render_axis("skinType", "A02")

    def test_empty_result_is_an_error(self):
        with self.assertRaises(ConditionRenderError):
            render({"skinType": ["A02"]}, "   ")


class MissingIsNotEveryone(unittest.TestCase):
    """미기재(`"미기재"`)와 무관(`null`)은 다르다 (PER-177 §3 · PER-178)."""

    def test_missing_segment_has_its_own_phrase(self):
        phrase = render_axis("skinType", [MISSING_SEGMENT])
        self.assertNotEqual(phrase, UNCONDITIONAL_SUBJECT)
        self.assertNotIn("모든", phrase)
        self.assertIn("안 밝힌", phrase)

    def test_unconditional_is_not_the_missing_phrase(self):
        self.assertEqual(render_subject(None), UNCONDITIONAL_SUBJECT)
        self.assertEqual(render_subject({}), UNCONDITIONAL_SUBJECT)
        self.assertNotEqual(
            render_subject({"skinType": [MISSING_SEGMENT]}), UNCONDITIONAL_SUBJECT)

    def test_missing_mixed_with_codes_is_an_error(self):
        with self.assertRaises(ConditionRenderError):
            render_axis("skinTrouble", [MISSING_SEGMENT, "C05"])

    def test_claim_contract_rejects_the_same_input(self):
        # 같은 규칙을 두 모듈이 따로 정하지 않는다는 확인 — 정본은 claim_contract 다
        with self.assertRaises(ClaimContractError):
            Claim(
                claim_id="c1", product_id="p001", aspect="지속성",
                question="q", answer="a",
                condition={"skinTrouble": [MISSING_SEGMENT, "C05"]},
                direction="positive",
                evidence=(ClaimEvidence(1, "좋아요", "positive"),),
                support={"positiveAuthors": 1, "negativeAuthors": 0, "neutralAuthors": 0,
                         "spokeAuthors": 1, "silentAuthors": 0, "supportAuthors": 1},
            )


class ConditionalIsDistinguishable(unittest.TestCase):
    """조건이 붙은 주장과 안 붙은 주장이 구분 가능해야 한다 (이슈 완료 조건)."""

    def _claim(self, condition):
        return Claim(
            claim_id="c1", product_id="p001", aspect="지속성", question="q", answer="답",
            condition=condition, direction="positive",
            evidence=(ClaimEvidence(1, "좋아요", "positive"),),
            support={"positiveAuthors": 1, "negativeAuthors": 0, "neutralAuthors": 0,
                     "spokeAuthors": 1, "silentAuthors": 0, "supportAuthors": 1},
        )

    def test_claim_type_matches_claim_contract(self):
        for condition in ({}, {"skinType": ["A02"]}, {"skinTrouble": [MISSING_SEGMENT]},
                          {"option": "40ml"}, {"usagePeriod": None}):
            with self.subTest(condition=condition):
                self.assertEqual(
                    describe(condition)["claimType"], self._claim(condition).claim_type)

    def test_render_claim_uses_the_answer(self):
        claim = self._claim({"skinType": ["A02"]})
        self.assertEqual(render_claim(claim), f"건성 피부 {ARROW} 답")
        self.assertEqual(render_claim(claim.as_dict()), f"건성 피부 {ARROW} 답")

    def test_describe_keeps_the_stored_codes(self):
        # 표기만 남기면 되돌릴 수 없다 — 라벨 저장을 기각한 이유 중 하나다
        axes = describe({"skinTrouble": ["C05", "C09"]})["axes"]
        self.assertEqual(axes[0]["codes"], ["C05", "C09"])


class SupportMatchesSufficiency(unittest.TestCase):
    """U 정의가 게이트4(`ClaimSupport.support`)와 같은지 대조한다."""

    def test_same_as_claim_support(self):
        for pos, neg in ((0, 0), (3, 0), (0, 3), (5, 2), (1, 1)):
            with self.subTest(pos=pos, neg=neg):
                support = ClaimSupport(
                    aspect="지속성",
                    positive=frozenset(f"p{i}" for i in range(pos)),
                    negative=frozenset(f"n{i}" for i in range(neg)),
                )
                self.assertEqual(_support_authors(pos, neg), len(support.support))


class FisherExact(unittest.TestCase):
    """검정 자체의 성질. scipy 없이 직접 세므로 고정한다."""

    def test_identical_rates_are_not_significant(self):
        self.assertAlmostEqual(fisher_two_sided(5, 5, 5, 5), 1.0, places=9)

    def test_empty_margin_is_one(self):
        self.assertEqual(fisher_two_sided(0, 0, 3, 4), 1.0)
        self.assertEqual(fisher_two_sided(0, 5, 0, 5), 1.0)

    def test_symmetry(self):
        self.assertAlmostEqual(fisher_two_sided(3, 37, 11, 5),
                               fisher_two_sided(11, 5, 3, 37), places=12)

    def test_known_split_is_significant(self):
        # 전수 실측 p014 × 지속성 — C05(부정 3/40) vs C09(부정 11/16)
        self.assertLess(fisher_two_sided(3, 37, 11, 5), 1e-4)

    def test_negative_counts_are_an_error(self):
        with self.assertRaises(ConditionRenderError):
            fisher_two_sided(-1, 2, 3, 4)


class UnconditionalCheck(unittest.TestCase):
    """갈리는데 무조건으로 서술하면 `missing_condition` 후보다."""

    def test_split_is_detected(self):
        verdict = check_unconditional("지속성", [
            seg(segment="C05", pos=37, neg=3, neu=3, silent=20),
            seg(segment="C09", pos=5, neg=11, silent=10),
        ])
        self.assertFalse(verdict.holds)
        self.assertEqual(len(verdict.pairs), 1)
        self.assertTrue(verdict.pairs[0].flips)
        self.assertEqual(verdict.failure_reasons, (FAILURE_MISSING_CONDITION,))

    def test_failure_key_is_in_the_taxonomy(self):
        # 실패 유형은 코드 상수가 아니라 failure_taxonomy.json 이 정본이다 (PER-177)
        self.assertIn(FAILURE_MISSING_CONDITION, load_failure_taxonomy().keys)

    def test_same_direction_holds(self):
        verdict = check_unconditional("지속성", [
            seg(segment="C05", pos=37, neg=3, silent=20),
            seg(segment="C09", pos=30, neg=2, silent=10),
        ])
        self.assertTrue(verdict.holds)
        self.assertEqual(verdict.failure_reasons, ())
        self.assertEqual(verdict.compared, 1)

    def test_unstated_segment_is_skipped_but_counted(self):
        verdict = check_unconditional("지속성", [
            seg(segment="C05", pos=37, neg=3, silent=20),
            seg(segment=MISSING_SEGMENT, pos=2, neg=30, silent=5),
        ])
        self.assertTrue(verdict.holds)
        self.assertEqual(verdict.compared, 0)
        self.assertEqual(verdict.skipped_unstated, 1)

    def test_insufficient_segment_is_skipped_but_counted(self):
        verdict = check_unconditional("지속성", [
            seg(segment="C05", pos=37, neg=3, silent=20),
            seg(segment="C09", pos=0, neg=2, silent=1),  # S=3 < S_min
        ])
        self.assertTrue(verdict.holds)
        self.assertEqual(verdict.skipped_insufficient, 1)

    def test_axes_are_not_crossed(self):
        # skinType 세그먼트와 skinTrouble 세그먼트를 견주면 갈림이 축 차이로 둔갑한다
        verdict = check_unconditional("지속성", [
            seg(axis="skinType", segment="A01", pos=37, neg=3, silent=20),
            seg(axis="skinTrouble", segment="C09", pos=5, neg=11, silent=10),
        ])
        self.assertEqual(verdict.compared, 0)
        self.assertTrue(verdict.holds)

    def test_other_aspect_is_an_error(self):
        with self.assertRaises(ConditionRenderError):
            check_unconditional("지속성", [seg(aspect="보습감", pos=9, silent=1)])

    def test_contradicting_authors_counts_the_minority_segment(self):
        verdict = check_unconditional("지속성", [
            seg(segment="C05", pos=37, neg=3, neu=3, silent=20),
            seg(segment="C09", pos=5, neg=11, silent=10),
        ])
        # 무조건 '좋다' 로 서술하면 모공 고민 쪽 부정 11명이 덮인다
        self.assertEqual(verdict.contradicting_authors, 11)

    def test_policy_is_not_redefined(self):
        # 임계값을 올리면 세그먼트가 충분성에서 먼저 떨어진다 (게이트4 가 소유)
        strict = SufficiencyPolicy(n_min=30, r_min=DEFAULT_SUFFICIENCY.r_min, s_min=30)
        verdict = check_unconditional("지속성", [
            seg(segment="C05", pos=37, neg=3, neu=3, silent=20),
            seg(segment="C09", pos=5, neg=11, silent=10),
        ], policy=strict)
        self.assertTrue(verdict.holds)
        self.assertEqual(verdict.skipped_insufficient, 1)

    def test_alpha_out_of_range_is_an_error(self):
        with self.assertRaises(ConditionRenderError):
            check_unconditional("지속성", [], alpha=0.0)

    def test_default_alpha(self):
        self.assertEqual(
            check_unconditional("지속성", []).alpha, SPLIT_ALPHA)


if __name__ == "__main__":
    unittest.main()
