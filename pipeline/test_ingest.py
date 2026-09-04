"""
v5 입수 계약 테스트 (PER-173).

  python3 -m unittest discover -s pipeline -p 'test_*.py'

25K 전건을 한 번 돌리므로 몇 초 걸린다. 이 테스트가 고정하는 것은
"미기재를 조건 없음으로 취급하지 않는다"와 "재실행 시 같은 출력"이다.
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from catalog import UnknownGoodsNoError, load_catalog  # noqa: E402
from contracts import (  # noqa: E402
    CONDITION_AXES,
    MISSING_SEGMENT,
    RAW_FIELDS,
    author_key,
    build_record,
    content_hash,
    sentiment_prior,
)
from ingest import INPUT_PATH, ingest  # noqa: E402

ROOT = Path(__file__).parents[1]


def row(**over) -> dict:
    base = {
        "reviewId": 1,
        "content": "  보습은 좋은데 향이 강해요  ",
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
        "productKey": "믿으면 안 되는 값",
        "category": "믿으면 안 되는 값",
        "profileImageUrl": "https://example.invalid/x.png",
        "reviewImages": ["https://example.invalid/y.png"],
        "reviewerRank": None,
        "isTopReviewer": False,
    }
    base.update(over)
    return base


class RecordShape(unittest.TestCase):
    def test_three_layers_only(self):
        r = build_record(row(), "p031").to_dict()
        self.assertEqual(set(r), {"reviewId", "productId", "raw", "condition", "derived"})
        self.assertEqual(set(r["raw"]), set(RAW_FIELDS))
        self.assertEqual(set(r["condition"]), set(CONDITION_AXES))

    def test_dropped_fields_are_absent(self):
        r = build_record(row(), "p031").to_dict()
        flat = json.dumps(r, ensure_ascii=False)
        for field in ("productKey", "category", "profileImageUrl", "reviewImages",
                      "reviewerRank", "isTopReviewer"):
            self.assertNotIn(f'"{field}"', flat)
        # 제품 동일성은 카탈로그가 준 값만 쓴다
        self.assertEqual(r["productId"], "p031")
        self.assertNotIn("믿으면 안 되는 값", flat)

    def test_raw_is_untouched(self):
        src = row()
        r = build_record(src, "p031").to_dict()
        self.assertEqual(r["raw"]["content"], src["content"])  # strip 하지 않는다
        self.assertEqual(r["raw"]["skinTrouble"], src["skinTrouble"])  # 정렬하지 않는다

    def test_derived_is_recomputable(self):
        src = row()
        d = build_record(src, "p031").to_dict()["derived"]
        self.assertEqual(d["authorKey"], author_key(src["userName"]))
        self.assertEqual(d["contentHash"], content_hash(src["content"]))
        self.assertEqual(d["sentimentPrior"], sentiment_prior(src["rating"]).value)
        self.assertEqual(d["contentLength"], len(src["content"].strip()))
        self.assertEqual(d["reviewYearMonth"], "2026-07")

    def test_required_field_missing_raises(self):
        for field in ("reviewId", "content", "rating", "reviewDate", "userName"):
            bad = row()
            del bad[field]
            with self.subTest(field=field), self.assertRaises(ValueError):
                build_record(bad, "p031")


class MissingIsItsOwnSegment(unittest.TestCase):
    """미기재를 '조건 없음'으로 취급하면 §7-1 '조건 누락' 실패가 된다."""

    def test_single_axis_missing(self):
        for value in (None, "", "   "):
            r = build_record(row(skinType=value, option=value), "p031").to_dict()
            for axis in ("skinType", "option"):
                with self.subTest(axis=axis, value=value):
                    c = r["condition"][axis]
                    self.assertFalse(c["stated"])
                    self.assertIsNone(c["code"])
                    self.assertEqual(c["segment"], MISSING_SEGMENT)

    def test_multi_axis_missing(self):
        for value in (None, [], ["", "  "]):
            c = build_record(row(skinTrouble=value), "p031").to_dict()["condition"]["skinTrouble"]
            with self.subTest(value=value):
                self.assertFalse(c["stated"])
                self.assertEqual(c["codes"], [])
                self.assertEqual(c["segments"], [MISSING_SEGMENT])

    def test_multi_axis_is_multi_label_not_a_combo_key(self):
        c = build_record(row(skinTrouble=["C05", "C01", "C05"]), "p031").to_dict()["condition"]["skinTrouble"]
        self.assertEqual(c["segments"], ["C01", "C05"])  # 조합 키 'C01+C05' 가 아니다

    def test_segment_is_never_null(self):
        r = build_record(row(skinType=None, option=None, skinTrouble=None), "p031").to_dict()
        self.assertTrue(all(r["condition"][a].get("segment") or r["condition"][a].get("segments")
                            for a in CONDITION_AXES))


class FullSnapshot(unittest.TestCase):
    """25K 전건 — 계약 위반이 한 건이라도 있으면 여기서 걸린다."""

    @classmethod
    def setUpClass(cls):
        cls.records, cls.meta, cls.profile = ingest(INPUT_PATH)

    def test_every_review_ingested(self):
        self.assertEqual(len(self.records), 25000)
        self.assertEqual(self.meta["records"], 25000)

    def test_review_ids_unique(self):
        self.assertEqual(len({r["reviewId"] for r in self.records}), 25000)

    def test_all_product_ids_resolve_to_catalog(self):
        catalog = load_catalog()
        ids = {r["productId"] for r in self.records}
        self.assertEqual(len(ids), 50)
        for pid in ids:
            catalog.product(pid)  # 없으면 raise

    def test_no_null_segments_in_snapshot(self):
        for r in self.records:
            self.assertIsNotNone(r["condition"]["skinType"]["segment"])
            self.assertTrue(r["condition"]["skinTrouble"]["segments"])
            self.assertIsNotNone(r["condition"]["option"]["segment"])

    def test_author_key_and_content_hash_present(self):
        self.assertTrue(all(r["derived"]["authorKey"] for r in self.records))
        self.assertEqual(len({len(r["derived"]["contentHash"]) for r in self.records}), 1)

    def test_content_hash_has_no_collisions_on_distinct_content(self):
        by_hash = {}
        for r in self.records:
            by_hash.setdefault(r["derived"]["contentHash"], set()).add(r["raw"]["content"].strip())
        collided = {h: v for h, v in by_hash.items() if len(v) > 1}
        self.assertEqual(collided, {}, "16 hex 절단으로 서로 다른 본문이 충돌했다")

    def test_meta_records_source_hashes_not_timestamps(self):
        self.assertIn("sha256", self.meta["source"])
        self.assertIn("sha256", self.meta["catalog"])
        self.assertNotIn("generatedAt", json.dumps(self.meta))

    def test_rerun_is_byte_identical(self):
        again, _, _ = ingest(INPUT_PATH)
        self.assertEqual(json.dumps(self.records, ensure_ascii=False),
                         json.dumps(again, ensure_ascii=False))

    def test_unknown_goods_no_stops_ingest(self):
        catalog = load_catalog()
        with self.assertRaises(UnknownGoodsNoError):
            catalog.resolve_goods_no("A000000999999")

    def test_profile_matches_prior_measurements(self):
        # PER-170 측정치(eval/reports/author_identity_per170.json)와 같은 수를 세는지
        d = self.profile["duplication"]
        self.assertEqual(d["uniqueAuthorProductPairs"], 19389)
        self.assertEqual(d["excessVotes"], 5611)
        self.assertEqual(self.profile["skinTypeCells"]["atLeast8Raw"], 310)
        self.assertEqual(self.profile["skinTypeCells"]["atLeast8AfterAuthorDedup"], 294)


if __name__ == "__main__":
    unittest.main(verbosity=2)
