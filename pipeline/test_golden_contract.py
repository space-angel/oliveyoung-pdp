"""
주장 골든셋 라벨 계약 테스트 (PER-178).

"라벨이 규격을 위반하면 저장되지 않는다"는 완료 조건을 테스트로 고정한다. 위반 클래스마다
하나씩 — 어느 하나가 조용히 통과하면 골든셋이 오염되고, 그 위에서 잰 judge 일치율(PER-197)이
틀린다.

  python3 -m unittest discover -s pipeline -p 'test_*.py'
"""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from contracts import MISSING_SEGMENT  # noqa: E402
from golden_contract import (  # noqa: E402
    LABEL_FIELDS,
    TAXONOMY_PATH,
    GoldenContractError,
    TaxonomyError,
    load_failure_taxonomy,
    support_counts,
    validate_label,
    validate_labels,
)


def review(review_id: int, content: str, author: str, skin_type: str | None = "A02", troubles=("C05",), option: str | None = "30ml") -> dict:
    def single(code):
        return {"code": code, "stated": code is not None, "segment": code if code else MISSING_SEGMENT}

    return {
        "reviewId": review_id,
        "productId": "p001",
        "raw": {"content": content, "rating": 5, "reviewDate": "2026.01.01"},
        "condition": {
            "skinType": single(skin_type),
            "skinTrouble": {"codes": list(troubles), "stated": bool(troubles), "segments": list(troubles) or [MISSING_SEGMENT]},
            "option": single(option),
        },
        "derived": {"authorKey": author, "reviewYearMonth": "2026-01"},
    }


REVIEWS = [
    review(1, "속보습은 못 느꼈어요.\r\n금새 날라가는 느낌", "a", skin_type="A02"),
    review(2, "촉촉하고 순해요", "b", skin_type="A02"),
    review(3, "건성인데 촉촉함이 오래가요", "c", skin_type=None, troubles=()),  # 미기재
    review(4, "번들거려서 별로", "a", skin_type="A01"),  # 작성자 a 의 다른 리뷰 (제품 번들에서만 생길 수 있는 상황)
]


def bundle(scope_axis=None, segment=None, reviews=REVIEWS) -> dict:
    return {
        "bundleId": "B01",
        "productId": "p001",
        "scope": {"kind": "product" if scope_axis is None else scope_axis, "axis": scope_axis, "segment": segment},
        "reviews": reviews,
    }


def label(**over) -> dict:
    base = {
        "labelId": "B01-1",
        "bundleId": "B01",
        "productId": "p001",
        "aspect": "보습감",
        "question": "건성에도 촉촉한가요?",
        "answer": "촉촉하다는 쪽과 금방 날아간다는 쪽이 갈린다",
        "condition": {"skinType": "A02", "skinTrouble": None, "option": None},
        "direction": "mixed",
        "evidence": [
            {"reviewId": 2, "stance": "positive", "quote": "촉촉하고 순해요"},
            {"reviewId": 1, "stance": "negative", "quote": "속보습은 못 느꼈어요.\n금새"},
        ],
        "failureReasons": [],
        "evaluation": "complete",
        "notes": None,
        "minutesSpent": 5,
        "source": "human",
        "candidateId": None,
    }
    base.update(over)
    return base


class TestTaxonomy(unittest.TestCase):
    def test_v1_has_eight_keys_and_sorts_by_severity(self):
        tax = load_failure_taxonomy()
        self.assertEqual(tax.version, "failure-taxonomy-v1")
        self.assertEqual(len(tax.keys), 8)
        self.assertEqual(
            tax.sort(["duplicate_claim", "missing_condition", "unsupported_claim", "polarity_mismatch"]),
            ["unsupported_claim", "polarity_mismatch", "missing_condition", "duplicate_claim"],
        )

    def test_taxonomy_file_without_version_is_rejected(self):
        data = json.loads(TAXONOMY_PATH.read_text())
        del data["version"]
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(data, f)
        with self.assertRaises(TaxonomyError):
            load_failure_taxonomy(f.name)

    def test_duplicate_key_is_rejected(self):
        data = json.loads(TAXONOMY_PATH.read_text())
        data["types"].append(dict(data["types"][0]))
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(data, f)
        with self.assertRaises(TaxonomyError):
            load_failure_taxonomy(f.name)


class TestValidLabel(unittest.TestCase):
    def test_valid_mixed_label_normalizes(self):
        out = validate_label(label(), bundle())
        self.assertEqual(out["direction"], "mixed")
        self.assertEqual(out["condition"], {"skinType": "A02", "skinTrouble": None, "option": None})
        self.assertEqual([e["reviewId"] for e in out["evidence"]], [2, 1])

    def test_quote_matches_across_crlf(self):
        """원문은 CRLF, 인용은 LF — 보이지 않는 문자만 접으므로 통과한다 (PER-175)."""
        validate_label(label(), bundle())

    def test_missing_segment_claim_with_missing_evidence(self):
        lab = label(
            condition={"skinType": MISSING_SEGMENT, "skinTrouble": None, "option": None},
            direction="positive",
            evidence=[{"reviewId": 3, "stance": "positive", "quote": "촉촉함이 오래가요"}],
        )
        out = validate_label(lab, bundle())
        self.assertEqual(out["condition"]["skinType"], MISSING_SEGMENT)

    def test_support_counts_unique_authors_not_reviews(self):
        lab = label(
            condition={"skinType": None, "skinTrouble": None, "option": None},
            direction="mixed",
            evidence=[
                {"reviewId": 2, "stance": "positive", "quote": "촉촉하고"},
                {"reviewId": 1, "stance": "negative", "quote": "속보습은"},
                {"reviewId": 4, "stance": "negative", "quote": "번들거려서"},  # 작성자 a 두 번째
            ],
        )
        counts = support_counts(validate_label(lab, bundle()), bundle())
        self.assertEqual(counts["evidenceReviews"], 3)
        self.assertEqual(counts["negativeAuthors"], 1)  # a 의 리뷰 2건 = 1표
        self.assertEqual(counts["positiveAuthors"], 1)

    def test_direction_is_derived_from_evidence(self):
        pos_only = label(direction="positive", evidence=[{"reviewId": 2, "stance": "positive", "quote": "촉촉하고"}])
        self.assertEqual(validate_label(pos_only, bundle())["direction"], "positive")
        one_dissent = label(direction="mixed", evidence=[
            {"reviewId": 2, "stance": "positive", "quote": "촉촉하고"},
            {"reviewId": 1, "stance": "negative", "quote": "속보습은"},
        ])
        self.assertEqual(validate_label(one_dissent, bundle())["direction"], "mixed")  # 반대 1명도 mixed


class TestViolations(unittest.TestCase):
    def assert_fails(self, lab: dict, fragment: str, b: dict | None = None):
        with self.assertRaises(GoldenContractError) as ctx:
            validate_label(lab, b or bundle())
        self.assertIn(fragment, str(ctx.exception))

    def test_unknown_field(self):
        self.assert_fails(label(rating=5), "계약에 없는 필드")

    def test_missing_field(self):
        lab = label()
        del lab["answer"]
        self.assert_fails(lab, "필수 필드 누락")

    def test_empty_answer(self):
        self.assert_fails(label(answer="  "), "answer")

    def test_aspect_outside_frozen_taxonomy(self):
        self.assert_fails(label(aspect="피부결"), "14종 택소노미 밖")

    def test_quote_must_be_verbatim(self):
        lab = label(direction="positive", evidence=[{"reviewId": 2, "stance": "positive", "quote": "촉촉하고 순하다"}])
        self.assert_fails(lab, "원문 부분문자열이 아니다")

    def test_squeezed_whitespace_is_not_verbatim(self):
        """띄어쓰기를 지운 편집은 인용이 아니다 — squeeze 금지 (CLAUDE.md)."""
        lab = label(direction="positive", evidence=[{"reviewId": 2, "stance": "positive", "quote": "촉촉하고순해요"}])
        self.assert_fails(lab, "원문 부분문자열이 아니다")

    def test_evidence_outside_bundle(self):
        lab = label(evidence=[{"reviewId": 99, "stance": "positive", "quote": "x"}])
        self.assert_fails(lab, "이 번들에 없다")

    def test_empty_evidence(self):
        self.assert_fails(label(evidence=[]), "evidence 가 비었다")

    def test_duplicate_review_in_evidence(self):
        lab = label(evidence=[
            {"reviewId": 2, "stance": "positive", "quote": "촉촉"},
            {"reviewId": 2, "stance": "negative", "quote": "순해요"},
        ])
        self.assert_fails(lab, "두 번")

    def test_bad_stance(self):
        lab = label(evidence=[{"reviewId": 2, "stance": "support", "quote": "촉촉"}])
        self.assert_fails(lab, "stance")

    def test_direction_mismatch_is_rejected(self):
        """사람이 고른 direction 이 근거와 다르면 에러 — direction 은 계산값이다."""
        lab = label(direction="positive")  # 근거는 긍정+부정
        self.assert_fails(lab, "근거에서 계산한 방향은 'mixed'")
        lab = label(direction="mixed", evidence=[{"reviewId": 2, "stance": "positive", "quote": "촉촉하고"}])
        self.assert_fails(lab, "근거에서 계산한 방향은 'positive'")

    def test_direction_enum(self):
        self.assert_fails(label(direction="neutral"), "direction")

    def test_condition_label_instead_of_code(self):
        lab = label(condition={"skinType": "건성", "skinTrouble": None, "option": None})
        self.assert_fails(lab, "코드가 아닌 값")

    def test_condition_axis_mixed(self):
        lab = label(condition={"skinType": "C05", "skinTrouble": None, "option": None})
        self.assert_fails(lab, "축이 섞였다")

    def test_condition_missing_axis(self):
        self.assert_fails(label(condition={"skinType": "A02"}), "정확히 가져야")

    def test_skin_trouble_must_be_list(self):
        lab = label(condition={"skinType": None, "skinTrouble": "C05", "option": None})
        self.assert_fails(lab, "코드 배열")

    def test_missing_cannot_mix_with_code(self):
        lab = label(condition={"skinType": None, "skinTrouble": [MISSING_SEGMENT, "C05"], "option": None})
        self.assert_fails(lab, "섞을 수 없다")

    def test_conditional_claim_rejects_missing_evidence(self):
        """건성(A02) 주장에 미기재 리뷰를 근거로 — PER-177 §3 의 정확한 위반."""
        lab = label(
            direction="positive",
            evidence=[
                {"reviewId": 2, "stance": "positive", "quote": "촉촉하고"},
                {"reviewId": 3, "stance": "positive", "quote": "촉촉함이 오래가요"},
            ],
        )
        self.assert_fails(lab, "미기재 리뷰를 특정 조건의 지지로 세지 않는다")

    def test_conditional_claim_rejects_other_segment_evidence(self):
        lab = label(direction="mixed", evidence=[
            {"reviewId": 2, "stance": "positive", "quote": "촉촉하고"},
            {"reviewId": 4, "stance": "negative", "quote": "번들거려서"},  # A01
        ])
        self.assert_fails(lab, "주장의 조건은 ['A02']")

    def test_missing_segment_claim_rejects_stated_evidence(self):
        lab = label(
            condition={"skinType": MISSING_SEGMENT, "skinTrouble": None, "option": None},
            direction="positive",
            evidence=[{"reviewId": 2, "stance": "positive", "quote": "촉촉하고"}],  # A02 기재
        )
        self.assert_fails(lab, "주장의 조건은 ['미기재']")

    def test_cell_bundle_forces_its_segment(self):
        b = bundle(scope_axis="skinType", segment="A02", reviews=[REVIEWS[0], REVIEWS[1]])
        lab = label(condition={"skinType": None, "skinTrouble": None, "option": None})
        self.assert_fails(lab, "셀 번들", b)

    def test_cell_bundle_accepts_its_segment(self):
        b = bundle(scope_axis="skinType", segment="A02", reviews=[REVIEWS[0], REVIEWS[1]])
        validate_label(label(), b)

    def test_failure_reason_outside_taxonomy(self):
        self.assert_fails(label(failureReasons=["hallucination"]), "택소노미")

    def test_failure_reasons_must_be_sorted(self):
        lab = label(failureReasons=["duplicate_claim", "unsupported_claim"])
        self.assert_fails(lab, "정렬")

    def test_failure_reasons_sorted_passes(self):
        out = validate_label(label(failureReasons=["unsupported_claim", "duplicate_claim"]), bundle())
        self.assertEqual(out["failureReasons"][0], "unsupported_claim")

    def test_not_evaluable_requires_notes(self):
        self.assert_fails(label(evaluation="not_evaluable", notes=None), "notes")

    def test_minutes_required_positive(self):
        self.assert_fails(label(minutesSpent=0), "minutesSpent")
        self.assert_fails(label(minutesSpent=True), "minutesSpent")

    def test_bundle_mismatch(self):
        self.assert_fails(label(bundleId="B02"), "bundleId")
        self.assert_fails(label(productId="p002"), "productId")

    def test_label_fields_are_the_contract(self):
        """필드 목록이 바뀌면 문서도 바뀌어야 한다 — 실수로 늘어나지 않게 개수를 고정."""
        self.assertEqual(len(LABEL_FIELDS), 15)


class TestSource(unittest.TestCase):
    """출처 규칙 (B안). 후보에서 온 라벨과 사람이 만든 라벨을 나중에 갈라 볼 수 있어야 한다."""

    def assert_fails(self, lab, fragment):
        with self.assertRaises(GoldenContractError) as ctx:
            validate_label(lab, bundle())
        self.assertIn(fragment, str(ctx.exception))

    def test_source_required(self):
        self.assert_fails(label(source=None), "source")
        self.assert_fails(label(source="llm"), "source")

    def test_candidate_sources_need_candidate_id(self):
        self.assert_fails(label(source="candidate_accepted", candidateId=None), "candidateId")
        out = validate_label(label(source="candidate_accepted", candidateId="B01-c1"), bundle())
        self.assertEqual(out["candidateId"], "B01-c1")

    def test_rejected_candidate_needs_failure_reasons(self):
        self.assert_fails(label(source="candidate_rejected", candidateId="B01-c1", failureReasons=[]), "기각 사유")
        validate_label(label(source="candidate_rejected", candidateId="B01-c1", failureReasons=["overbroad_question"]), bundle())

    def test_human_label_has_no_candidate_id(self):
        self.assert_fails(label(source="human", candidateId="B01-c1"), "source=human")


class TestValidateLabels(unittest.TestCase):
    def test_duplicate_label_id(self):
        with self.assertRaises(GoldenContractError) as ctx:
            validate_labels([label(), copy.deepcopy(label())], {"B01": bundle()})
        self.assertIn("labelId 중복", str(ctx.exception))

    def test_unknown_bundle(self):
        with self.assertRaises(GoldenContractError) as ctx:
            validate_labels([label(bundleId="B99")], {"B01": bundle()})
        self.assertIn("표본에 없다", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
