"""
입력 계약 강제 테스트 (PER-176).

  python3 -m unittest pipeline.test_contract

이 파일이 고정하는 완료 조건은 하나다 — **계약 위반 입력이 들어오면 에러가 난다.**
"조용한 폴백 금지"는 문서에 적어두면 지켜지지 않고, 테스트로 박아야 지켜진다.

계약 본문은 docs/INPUT_CONTRACT.md. 여기 케이스와 그 문서 §의 대응은 각 클래스 독스트링에 있다.
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from catalog import load_catalog  # noqa: E402
from codebook import Codebook, CodebookError, load_codebook  # noqa: E402
from contracts import (  # noqa: E402
    KNOWN_FIELDS,
    RATING_RANGE,
    REQUIRED_FIELDS,
    ContractError,
    assert_row_schema,
    build_record,
)
from ingest import INPUT_PATH, ingest  # noqa: E402

ROOT = Path(__file__).parents[1]

# 계약을 지키는 행 1건. 각 테스트는 여기서 한 군데만 망가뜨린다.
GOOD = {
    "reviewId": 1,
    "content": "보습은 좋은데 향이 강해요",
    "rating": 4,
    "reviewDate": "2026.07.19",
    "userName": "돌핀러",
    "goodsNo": "A000000211119",
    "requestedGoodsNo": "A000000211119",
    "productName": "테스트 상품 40ml",
    "option": "40+40ml",
    "skinType": "A04",
    "skinTone": "B03",
    "skinTrouble": ["C05", "C01"],
    "reviewType": "NORMAL",
    "isRepurchase": False,
    "isMonthUseReview": False,
    "isMonthOverReview": False,
    "hasPhoto": False,
    "usefulPoint": 0,
    "recommendCount": 0,
    "productKey": "믿으면 안 되는 값",
    "category": "믿으면 안 되는 값",
    "profileImageUrl": "https://example.invalid/x.png",
    "reviewImages": [],
    "reviewerRank": None,
    "isTopReviewer": False,
}


def row(**over) -> dict:
    r = dict(GOOD)
    r.update(over)
    return r


class BaselineIsValid(unittest.TestCase):
    """망가뜨리지 않은 행은 통과해야 한다 — 안 그러면 아래 테스트가 전부 거짓 양성이다."""

    def test_good_row_builds(self):
        r = build_record(row(), "p031").to_dict()
        self.assertEqual(r["derived"]["reviewYearMonth"], "2026-07")

    def test_good_row_matches_snapshot_schema(self):
        assert_row_schema(row())


class RequiredFields(unittest.TestCase):
    """§3-1 유효성. 결측·공백·타입·범위는 전부 에러다."""

    def test_absent_raises(self):
        for field in REQUIRED_FIELDS:
            bad = row()
            del bad[field]
            with self.subTest(field=field), self.assertRaises(ContractError):
                build_record(bad, "p031")

    def test_null_raises(self):
        for field in REQUIRED_FIELDS:
            with self.subTest(field=field), self.assertRaises(ContractError):
                build_record(row(**{field: None}), "p031")

    def test_blank_string_raises(self):
        """공백만 있는 값은 '있는 값'이 아니다."""
        for field in ("content", "reviewDate", "userName"):
            for blank in ("", "   ", "\n"):
                with self.subTest(field=field, blank=repr(blank)):
                    with self.assertRaises(ContractError):
                        build_record(row(**{field: blank}), "p031")

    def test_blank_user_name_would_merge_strangers(self):
        """빈 작성자 키는 서로 다른 사람을 한 사람으로 합친다 (PER-170 중복 게이트)."""
        with self.assertRaises(ContractError) as cm:
            build_record(row(userName="  "), "p031")
        self.assertIn("userName", str(cm.exception))


class IdentifierTypes(unittest.TestCase):
    """§3-2 식별자. `reviewId` 는 양의 정수다 — 문자열·bool·0·음수 전부 에러."""

    def test_bad_review_id_raises(self):
        for value in ("1", 1.0, True, 0, -3, None, [1]):
            with self.subTest(value=repr(value)), self.assertRaises(ContractError):
                build_record(row(reviewId=value), "p031")

    def test_rating_out_of_range_raises(self):
        low, high = RATING_RANGE
        for value in (low - 1, high + 1, 0, 99, -1):
            with self.subTest(value=value), self.assertRaises(ContractError):
                build_record(row(rating=value), "p031")

    def test_rating_wrong_type_raises(self):
        for value in ("4", 4.0, True, None):
            with self.subTest(value=repr(value)), self.assertRaises(ContractError):
                build_record(row(rating=value), "p031")

    def test_every_valid_rating_passes(self):
        for value in range(RATING_RANGE[0], RATING_RANGE[1] + 1):
            with self.subTest(value=value):
                build_record(row(rating=value), "p031")


class ReviewDateIsRecencyInput(unittest.TestCase):
    """§3-3 날짜. 파싱 실패가 조용한 `None` 이 되면 리센시 컷(PER-172)이 빗나간다."""

    def test_unparseable_date_raises(self):
        for value in ("2026", "어제", "2026-13-01", "26.07.19", "2026.13.01"):
            with self.subTest(value=value), self.assertRaises(ContractError):
                build_record(row(reviewDate=value), "p031")

    def test_year_month_is_never_null(self):
        for value in ("2026.07.19", "2024-09-01", "2018.12.31"):
            with self.subTest(value=value):
                d = build_record(row(reviewDate=value), "p031").to_dict()["derived"]
                self.assertIsNotNone(d["reviewYearMonth"])
                self.assertRegex(d["reviewYearMonth"], r"^\d{4}-\d{2}$")


class ConditionCodesAreCodebookCodes(unittest.TestCase):
    """§4 조건축. 도메인 밖 코드가 조용히 새 세그먼트가 되면 집계가 틀린다."""

    def test_out_of_domain_code_raises(self):
        with self.assertRaises(ContractError):
            build_record(row(skinType="A99"), "p031")
        with self.assertRaises(ContractError):
            build_record(row(skinTrouble=["C05", "C99"]), "p031")

    def test_label_instead_of_code_raises(self):
        """v4 는 힌트를 라벨로 받아 조건축과 어휘가 갈렸다 — 그 재발 경로를 막는다."""
        with self.assertRaises(ContractError) as cm:
            build_record(row(skinType="건성"), "p031")
        self.assertIn("코드", str(cm.exception))

    def test_axis_mixup_raises(self):
        """`skinType` 자리에 `skinTone` 코드가 오면 세그먼트가 조용히 하나 는다."""
        with self.assertRaises(ContractError) as cm:
            build_record(row(skinType="B03"), "p031")
        self.assertIn("축", str(cm.exception))

    def test_multi_axis_string_raises(self):
        """문자열을 그대로 받으면 'C05' 가 세그먼트 ['0','5','C'] 로 쪼개진다."""
        with self.assertRaises(ContractError):
            build_record(row(skinTrouble="C05"), "p031")

    def test_single_axis_non_string_raises(self):
        for value in (["A04"], 4, {"code": "A04"}):
            with self.subTest(value=repr(value)), self.assertRaises(ContractError):
                build_record(row(skinType=value), "p031")

    def test_option_has_no_domain(self):
        """`option` 은 자유 문자열이다(25K 에서 798종) — 코드북으로 막지 않는다."""
        r = build_record(row(option="21호 라이트베이지 / 1+1"), "p031").to_dict()
        self.assertEqual(r["condition"]["option"]["segment"], "21호 라이트베이지 / 1+1")

    def test_missing_is_not_a_violation(self):
        """미기재는 잘못된 코드가 아니라 별도 세그먼트다 (PER-173)."""
        r = build_record(row(skinType="", skinTrouble=[], option=None), "p031").to_dict()
        self.assertFalse(r["condition"]["skinType"]["stated"])
        self.assertFalse(r["condition"]["skinTrouble"]["stated"])
        self.assertFalse(r["condition"]["option"]["stated"])


class SnapshotSchema(unittest.TestCase):
    """§3-4 스키마. 필드가 늘거나 준 것은 스냅샷 전체의 사건이라 경계에서 잡는다."""

    def test_unknown_field_raises(self):
        with self.assertRaises(ContractError) as cm:
            assert_row_schema(row(usagePeriod="3개월"))
        self.assertIn("usagePeriod", str(cm.exception))

    def test_absent_field_raises(self):
        bad = row()
        del bad["reviewType"]
        with self.assertRaises(ContractError) as cm:
            assert_row_schema(bad)
        self.assertIn("reviewType", str(cm.exception))

    def test_known_fields_cover_the_real_snapshot(self):
        """계약이 아는 필드 집합 == 스냅샷의 필드 집합. 어느 쪽으로도 어긋나면 안 된다."""
        rows = json.loads(INPUT_PATH.read_text())
        observed = {f for r in rows for f in r}
        self.assertEqual(observed, set(KNOWN_FIELDS))


class Codebook_(unittest.TestCase):
    """§4 코드북은 파일이 정본이다. 도메인을 코드 상수로 들지 않는다."""

    def test_domain_matches_dom_verified_file(self):
        cb = load_codebook()
        self.assertEqual(len(cb.domain("skinType")), 7)
        self.assertEqual(len(cb.domain("skinTone")), 6)
        self.assertEqual(len(cb.domain("skinTrouble")), 13)
        self.assertEqual(len(cb), 26)

    def test_unknown_axis_raises(self):
        with self.assertRaises(CodebookError):
            load_codebook().domain("usagePeriod")

    def test_label_is_available_but_not_used_by_ingest(self):
        """라벨은 표기 단계 전용이다. 입수 산출물에 라벨이 들어가면 안 된다."""
        self.assertEqual(load_codebook().label("skinType", "A02"), "건성")
        r = build_record(row(), "p031").to_dict()
        self.assertNotIn("건성", json.dumps(r, ensure_ascii=False))

    def test_malformed_codebook_raises_on_load(self):
        """코드북이 깨졌으면 25K 를 다 돌고 나서가 아니라 로드 시점에 안다."""
        cases = {
            "축이 통째로 없음": {},
            "축이 비었음": {"skinType": {}, "skinTone": {"B01": "쿨톤"}},
            "코드 형식 위반": {"skinType": {"A1": "지성"}},
            "축과 접두사 불일치": {"skinType": {"B01": "쿨톤"}},
            "라벨이 빈 값": {"skinType": {"A01": "  "}},
        }
        for name, axes in cases.items():
            with self.subTest(case=name), self.assertRaises(CodebookError):
                _load_codebook_from(axes)

    def test_missing_codebook_file_raises(self):
        with self.assertRaises(CodebookError):
            load_codebook(ROOT / "data/input/_no_such_codebook.json")


def _load_codebook_from(axes: dict) -> Codebook:
    """임시 파일에 써서 로드 시점 검증을 태운다 (도메인을 코드 상수로 들지 않으므로)."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "codebook.json"
        path.write_text(json.dumps(axes, ensure_ascii=False))
        return load_codebook(path)


class RealSnapshotSatisfiesTheContract(unittest.TestCase):
    """25K 전건이 계약을 만족한다 — 계약이 현실보다 엄격해서 못 쓰는 게 아님을 고정한다."""

    @classmethod
    def setUpClass(cls):
        cls.records, cls.meta, _ = ingest(INPUT_PATH)

    def test_all_25k_pass(self):
        self.assertEqual(len(self.records), 25000)

    def test_no_null_review_year_month(self):
        self.assertTrue(all(r["derived"]["reviewYearMonth"] for r in self.records))

    def test_all_stated_codes_are_in_the_codebook(self):
        cb = load_codebook()
        for r in self.records:
            st = r["condition"]["skinType"]
            if st["stated"]:
                self.assertIn(st["code"], cb.domain("skinType"))
            for code in r["condition"]["skinTrouble"]["codes"]:
                self.assertIn(code, cb.domain("skinTrouble"))

    def test_meta_records_the_enforced_contract(self):
        c = self.meta["contract"]
        self.assertEqual(c["doc"], "docs/INPUT_CONTRACT.md")
        self.assertEqual(c["codebook"]["codes"], 26)
        self.assertTrue(c["enforced"])

    def test_duplicate_review_id_stops_ingest(self):
        rows = json.loads(INPUT_PATH.read_text())
        dupe = ROOT / "data/intermediate/_test_dupe_reviews.json"
        dupe.parent.mkdir(parents=True, exist_ok=True)
        dupe.write_text(json.dumps(rows[:3] + [rows[0]], ensure_ascii=False))
        try:
            with self.assertRaises(ContractError) as cm:
                ingest(dupe)
            self.assertIn("reviewId 중복", str(cm.exception))
        finally:
            dupe.unlink()

    def test_unknown_goods_no_stops_ingest(self):
        """계약 위반은 어느 레이어에서 나든 멈춘다 (PER-171 카탈로그)."""
        from catalog import UnknownGoodsNoError

        with self.assertRaises(UnknownGoodsNoError):
            load_catalog().resolve_goods_no("A000000999999")


if __name__ == "__main__":
    unittest.main(verbosity=2)
