"""태깅 계약 테스트 (PER-175). "에러를 낸다"는 완료 조건을 여기서 고정한다."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from tag_contract import (
    ASPECTS,
    fold_invisible,
    tagging_text,
    MAX_ASPECTS_PER_REVIEW,
    TagContractError,
    is_verbatim,
    is_verbatim_loose,
    prompt_version,
    skin_type_codes,
    validate_tags,
)

CONTENT = "발색은 정말 예쁜데 지속력은 아쉬워요.\r\n건성인데 안 당겨요"
REVIEWS = {
    1: {"reviewId": 1, "productId": "p001", "raw": {"content": CONTENT, "rating": 4}},
}


def tag(**over) -> dict:
    base = {"reviewId": 1, "aspect": "발색", "polarity": "positive", "snippet": "발색은 정말 예쁜데", "skinTypeHint": None}
    base.update(over)
    return base


class TestVerbatim(unittest.TestCase):
    def test_substring_passes(self):
        self.assertTrue(is_verbatim("지속력은 아쉬워요", CONTENT))

    def test_paraphrase_fails(self):
        self.assertFalse(is_verbatim("지속력이 아쉽다", CONTENT))

    def test_whitespace_difference_fails_strict_but_loose_catches_it(self):
        s = "발색은정말예쁜데"
        self.assertFalse(is_verbatim(s, CONTENT))
        self.assertTrue(is_verbatim_loose(s, CONTENT))

    def test_snippet_spanning_newline_fails_when_newline_replaced_by_space(self):
        # 개행을 공백으로 바꾼 건 접기 대상이 아니다 — 태거가 문장을 이어붙인 것이다
        self.assertFalse(is_verbatim("아쉬워요. 건성인데", CONTENT))


class TestFoldInvisible(unittest.TestCase):
    """실측(25K 중 268건 공백 변종 · 11,582건 CRLF)에 근거한 정규화."""

    def test_nbsp_snippet_matches(self):
        self.assertTrue(is_verbatim("가장 촉촉하기 때문에", "쿠션중\u00a0가장\u00a0촉촉하기\u00a0때문에"))

    def test_crlf_snippet_matches_lf(self):
        self.assertTrue(is_verbatim("아쉬워요.\n건성인데", CONTENT))

    def test_zero_width_space_is_dropped(self):
        self.assertTrue(is_verbatim("촉촉해요", "정말\u200b촉촉\u200b해요"))

    def test_emoji_zwj_is_preserved(self):
        self.assertIn("\u200d", fold_invisible("👩\u200d👦"))

    def test_folding_does_not_change_letter_or_space_count(self):
        src = "가장\u00a0촉촉\u3000하기"
        self.assertEqual(len(fold_invisible(src)), len(src))

    def test_paraphrase_still_fails_after_folding(self):
        self.assertFalse(is_verbatim("지속력이 아쉽다", CONTENT))

    def test_respacing_still_fails_strict(self):
        self.assertFalse(is_verbatim("발색은정말예쁜데", CONTENT))

    def test_tagging_text_is_idempotent(self):
        once = tagging_text(CONTENT)
        self.assertEqual(once, tagging_text(once))


class TestValidate(unittest.TestCase):
    def test_valid_tag_normalizes_and_carries_product_id(self):
        out = validate_tags([tag()], REVIEWS)
        self.assertEqual(out[0]["productId"], "p001")
        self.assertEqual(out[0]["aspect"], "발색")

    def test_two_aspects_opposite_polarity_in_one_review_is_allowed(self):
        out = validate_tags(
            [tag(), tag(aspect="지속성", polarity="negative", snippet="지속력은 아쉬워요")], REVIEWS
        )
        self.assertEqual({t["polarity"] for t in out}, {"positive", "negative"})

    def test_unknown_aspect_raises(self):
        with self.assertRaises(TagContractError):
            validate_tags([tag(aspect="사용감")], REVIEWS)

    def test_unknown_polarity_raises(self):
        with self.assertRaises(TagContractError):
            validate_tags([tag(polarity="mixed")], REVIEWS)

    def test_non_verbatim_snippet_raises(self):
        with self.assertRaises(TagContractError):
            validate_tags([tag(snippet="발색이 예쁘다고 함")], REVIEWS)

    def test_empty_snippet_raises(self):
        with self.assertRaises(TagContractError):
            validate_tags([tag(snippet="")], REVIEWS)

    def test_duplicate_aspect_in_same_review_raises(self):
        with self.assertRaises(TagContractError):
            validate_tags([tag(), tag(polarity="negative", snippet="발색은 정말 예쁜데")], REVIEWS)

    def test_unknown_review_id_raises(self):
        with self.assertRaises(TagContractError):
            validate_tags([tag(reviewId=999)], REVIEWS)

    def test_skin_type_hint_label_instead_of_code_raises(self):
        with self.assertRaises(TagContractError):
            validate_tags([tag(skinTypeHint="건성")], REVIEWS)

    def test_skin_type_hint_code_passes(self):
        self.assertEqual(validate_tags([tag(skinTypeHint="A02")], REVIEWS)[0]["skinTypeHint"], "A02")

    def test_too_many_aspects_raises(self):
        many = [
            tag(aspect=a, snippet="발색은 정말 예쁜데")
            for a in ASPECTS[: MAX_ASPECTS_PER_REVIEW + 1]
        ]
        with self.assertRaises(TagContractError):
            validate_tags(many, REVIEWS)


class TestProvenance(unittest.TestCase):
    def test_prompt_version_records_hash(self):
        v = prompt_version()
        self.assertEqual(len(v["sha256"]), 64)
        self.assertTrue(v["path"].endswith("prompts/tag/v1.md"))

    def test_skin_type_codes_come_from_codebook(self):
        self.assertEqual(skin_type_codes(), ("A01", "A02", "A03", "A04", "A05", "A06", "A07"))


if __name__ == "__main__":
    unittest.main()
