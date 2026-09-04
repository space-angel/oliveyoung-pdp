"""
카탈로그 계약 테스트 (PER-171 / PER-172).

완료 조건이 "미등록 상품 ID는 조용히 폴백하지 않고 에러를 낸다"이므로,
그 동작을 말이 아니라 테스트로 고정한다. PER-172 가 여기에 얹은 완료 조건은
"리뉴얼 세대는 날짜로 가르고, 가를 수 없으면 에러다"이다. 외부 의존 없음:

  python3 -m unittest discover -s pipeline -p 'test_*.py'
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from catalog import (  # noqa: E402
    DEFAULT_CATALOG_PATH,
    AmbiguousGenerationError,
    CatalogError,
    ProductCatalog,
    SCHEMA_VERSION,
    UnknownGoodsNoError,
    UnknownProductError,
    load_catalog,
)
from policy import RENEWAL_SEPARATE, RENEWAL_UNOBSERVED  # noqa: E402

ROOT = Path(__file__).parents[1]


def write_catalog(products: list[dict], version: str = SCHEMA_VERSION) -> Path:
    path = Path(tempfile.mkdtemp()) / "catalog.json"
    path.write_text(json.dumps({"_meta": {"schemaVersion": version}, "products": products},
                               ensure_ascii=False))
    return path


def product(
    pid="p001",
    name="테스트 제품",
    goods=("A000000000001",),
    source="crawl_request",
    lineage=None,
    renewal=None,
):
    return {
        "productId": pid,
        "displayName": name,
        "category": "에센스/세럼",
        "requestedGoodsNo": goods[0],
        "lineageId": lineage or f"L{pid[1:]}",
        "renewalPolicy": renewal or unobserved(),
        "notes": [],
        "goodsNos": [{"goodsNo": g, "source": source} for g in goods],
    }


def unobserved() -> dict:
    return {"policy": RENEWAL_UNOBSERVED, "fromMonth": None, "toMonth": None, "evidence": None}


def generation(from_month=None, to_month=None, evidence="리뉴얼 공지 확인") -> dict:
    return {
        "policy": RENEWAL_SEPARATE,
        "fromMonth": from_month,
        "toMonth": to_month,
        "evidence": evidence,
    }


class RealCatalog(unittest.TestCase):
    """커밋된 카탈로그가 실제 입력을 전부 덮는지."""

    @classmethod
    def setUpClass(cls):
        cls.catalog = load_catalog()

    def test_shape(self):
        self.assertEqual(len(self.catalog), 50)
        self.assertEqual(sum(len(p.goods_nos) for p in self.catalog.products), 167)

    def test_every_product_declares_a_renewal_policy(self):
        # PER-172: null 은 허용하지 않는다. 현 스냅샷은 리뉴얼 시점을 확정할 근거가 없어
        # 전 제품이 'unobserved' 이고, 계보는 제품당 하나다.
        for p in self.catalog.products:
            with self.subTest(product=p.product_id):
                self.assertEqual(p.renewal_policy, RENEWAL_UNOBSERVED)
                self.assertIsNone(p.renewal_from_month)
                self.assertIsNone(p.renewal_to_month)
                self.assertEqual(self.catalog.lineage_of(p.product_id), (p,))
        self.assertEqual(len(self.catalog.lineages), 50)

    def test_resolves_every_goods_no_in_snapshots(self):
        for name in ("reviews_50products", "reviews_200_normalized", "v4_reviews_500"):
            rows = json.loads((ROOT / f"data/input/{name}.json").read_text())
            for r in rows:
                self.assertRegex(self.catalog.resolve_goods_no(r["goodsNo"]), r"^p\d{3}$")

    def test_display_name_lookup_for_legacy_outputs(self):
        # v4 산출물·골든셋은 한글 제품명을 키로 쓴다. 그 조회가 살아 있어야 비교가 된다.
        p = self.catalog.by_display_name("달바 퍼스트 스프레이 세럼")
        self.assertEqual(p.product_id, self.catalog.resolve_goods_no(p.requested_goods_no))
        with self.assertRaises(UnknownProductError):
            self.catalog.by_display_name("존재하지 않는 제품")


class NoSilentFallback(unittest.TestCase):
    """미등록 상품 ID는 반드시 에러다 — 이게 이 레이어의 존재 이유다."""

    @classmethod
    def setUpClass(cls):
        cls.catalog = load_catalog()

    def test_unknown_goods_no_raises(self):
        for bad in ("A000000999999", "", "A000000166641x", "p001", None):
            with self.subTest(bad=bad), self.assertRaises(UnknownGoodsNoError):
                self.catalog.resolve_goods_no(bad)

    def test_unknown_product_id_raises(self):
        with self.assertRaises(UnknownProductError):
            self.catalog.product("p999")

    def test_error_message_names_the_fix(self):
        with self.assertRaises(UnknownGoodsNoError) as ctx:
            self.catalog.resolve_goods_no("A000000999999")
        self.assertIn("build_product_catalog.py", str(ctx.exception))


class LoadValidation(unittest.TestCase):
    """깨진 카탈로그는 로드 시점에 걸러야 한다. 런타임까지 끌고 가면 집계가 틀어진다."""

    def test_goods_no_across_two_lineages_raises(self):
        path = write_catalog([
            product("p001", "제품 A", ("A000000000001", "A000000000002")),
            product("p002", "제품 B", ("A000000000002",)),
        ])
        with self.assertRaises(CatalogError) as ctx:
            ProductCatalog.load(path)
        self.assertIn("서로 다른 계보", str(ctx.exception))

    def test_duplicate_display_name_raises(self):
        path = write_catalog([
            product("p001", "같은 이름", ("A000000000001",)),
            product("p002", "같은 이름", ("A000000000002",)),
        ])
        with self.assertRaises(CatalogError):
            ProductCatalog.load(path)

    def test_schema_version_mismatch_raises(self):
        with self.assertRaises(CatalogError):
            ProductCatalog.load(write_catalog([product()], version="v4"))

    def test_bad_product_id_raises(self):
        with self.assertRaises(CatalogError):
            ProductCatalog.load(write_catalog([product(pid="P1")]))

    def test_bad_goods_no_format_raises(self):
        with self.assertRaises(CatalogError):
            ProductCatalog.load(write_catalog([product(goods=("B000000000001",))]))

    def test_unknown_source_raises(self):
        with self.assertRaises(CatalogError):
            ProductCatalog.load(write_catalog([product(source="guessed")]))

    def test_requested_goods_no_must_be_listed(self):
        entry = product()
        entry["requestedGoodsNo"] = "A000000000009"
        with self.assertRaises(CatalogError):
            ProductCatalog.load(write_catalog([entry]))

    def test_missing_file_raises_with_hint(self):
        with self.assertRaises(CatalogError) as ctx:
            ProductCatalog.load(Path(tempfile.mkdtemp()) / "nope.json")
        self.assertIn("build_product_catalog.py", str(ctx.exception))

    def test_default_path_is_the_committed_catalog(self):
        self.assertEqual(DEFAULT_CATALOG_PATH, ROOT / "data/input/product_catalog.json")


class RenewalPolicyContract(unittest.TestCase):
    """PER-172 — 리뉴얼 취급은 명시해야 하고, 반쯤 적은 결정은 거절한다."""

    def test_null_renewal_policy_raises(self):
        entry = product()
        entry["renewalPolicy"] = None
        with self.assertRaises(CatalogError) as ctx:
            ProductCatalog.load(write_catalog([entry]))
        self.assertIn("unobserved", str(ctx.exception))

    def test_unknown_policy_raises(self):
        entry = product(renewal={"policy": "maybe", "fromMonth": None, "toMonth": None})
        with self.assertRaises(CatalogError):
            ProductCatalog.load(write_catalog([entry]))

    def test_separate_without_evidence_raises(self):
        entry = product(renewal=generation("2025-07", None, evidence=""))
        with self.assertRaises(CatalogError) as ctx:
            ProductCatalog.load(write_catalog([entry]))
        self.assertIn("evidence", str(ctx.exception))

    def test_separate_without_any_boundary_raises(self):
        # 양쪽이 다 열려 있으면 자를 지점을 정하지 않은 것이다.
        with self.assertRaises(CatalogError):
            ProductCatalog.load(write_catalog([product(renewal=generation(None, None))]))

    def test_separate_with_reversed_range_raises(self):
        with self.assertRaises(CatalogError):
            ProductCatalog.load(write_catalog([product(renewal=generation("2026-01", "2025-01"))]))

    def test_bad_month_format_raises(self):
        with self.assertRaises(CatalogError):
            ProductCatalog.load(write_catalog([product(renewal=generation("2025.07"))]))

    def test_non_separate_policy_cannot_carry_a_range(self):
        entry = product(renewal={"policy": "single", "fromMonth": "2025-07",
                                 "toMonth": None, "evidence": None})
        with self.assertRaises(CatalogError):
            ProductCatalog.load(write_catalog([entry]))

    def test_bad_lineage_id_raises(self):
        with self.assertRaises(CatalogError):
            ProductCatalog.load(write_catalog([product(lineage="lineage-1")]))


class GenerationResolution(unittest.TestCase):
    """세대가 나뉜 계보는 (goodsNo, reviewDate) 로만 갈린다."""

    @staticmethod
    def two_generations():
        # 같은 goodsNo 가 구·신 세대에 함께 등장하는 실제 형태 (리뉴얼 전 재고 리뷰).
        return write_catalog([
            product("p001", "구세대", ("A000000000001",), lineage="L001",
                    renewal=generation(None, "2025-06")),
            product("p002", "신세대", ("A000000000001", "A000000000002"), lineage="L001",
                    renewal=generation("2025-07", None)),
        ])

    def test_resolves_by_review_date(self):
        c = ProductCatalog.load(self.two_generations())
        self.assertEqual(c.resolve_goods_no("A000000000001", "2025.03.11"), "p001")
        self.assertEqual(c.resolve_goods_no("A000000000001", "2026.08.10"), "p002")

    def test_missing_date_raises_instead_of_guessing(self):
        c = ProductCatalog.load(self.two_generations())
        with self.assertRaises(AmbiguousGenerationError) as ctx:
            c.resolve_goods_no("A000000000001")
        self.assertIn("review_date", str(ctx.exception))

    def test_date_outside_every_generation_raises(self):
        c = ProductCatalog.load(write_catalog([
            product("p001", "구세대", ("A000000000001",), lineage="L001",
                    renewal=generation("2024-01", "2025-06")),
            product("p002", "신세대", ("A000000000001",), lineage="L001",
                    renewal=generation("2025-07", None)),
        ]))
        with self.assertRaises(AmbiguousGenerationError):
            c.resolve_goods_no("A000000000001", "2023.05.02")

    def test_single_generation_ignores_the_date(self):
        c = load_catalog()
        goods = c.products[0].requested_goods_no
        self.assertEqual(c.resolve_goods_no(goods), c.resolve_goods_no(goods, "2019.01.01"))

    def test_lineage_is_ordered_oldest_first(self):
        c = ProductCatalog.load(self.two_generations())
        self.assertEqual([p.product_id for p in c.lineage("L001")], ["p001", "p002"])
        self.assertEqual(c.lineage_of("p002"), c.lineage("L001"))


class LineageValidation(unittest.TestCase):
    """계보가 날짜로 갈리지 않는 형태면 로드 시점에 멈춘다."""

    def test_multi_product_lineage_must_be_separate(self):
        path = write_catalog([
            product("p001", "A", ("A000000000001",), lineage="L001",
                    renewal=generation(None, "2025-06")),
            product("p002", "B", ("A000000000002",), lineage="L001"),
        ])
        with self.assertRaises(CatalogError) as ctx:
            ProductCatalog.load(path)
        self.assertIn("separate", str(ctx.exception))

    def test_two_current_generations_raise(self):
        path = write_catalog([
            product("p001", "A", ("A000000000001",), lineage="L001",
                    renewal=generation("2024-01", None)),
            product("p002", "B", ("A000000000002",), lineage="L001",
                    renewal=generation("2025-07", None)),
        ])
        with self.assertRaises(CatalogError) as ctx:
            ProductCatalog.load(path)
        self.assertIn("현행 세대", str(ctx.exception))

    def test_overlapping_generations_raise(self):
        path = write_catalog([
            product("p001", "A", ("A000000000001",), lineage="L001",
                    renewal=generation("2024-01", "2025-08")),
            product("p002", "B", ("A000000000002",), lineage="L001",
                    renewal=generation("2025-07", None)),
        ])
        with self.assertRaises(CatalogError) as ctx:
            ProductCatalog.load(path)
        self.assertIn("겹친다", str(ctx.exception))

    def test_goods_no_shared_inside_one_lineage_is_allowed(self):
        c = ProductCatalog.load(write_catalog([
            product("p001", "구세대", ("A000000000001",), lineage="L001",
                    renewal=generation(None, "2025-06")),
            product("p002", "신세대", ("A000000000001",), lineage="L001",
                    renewal=generation("2025-07", None)),
        ]))
        self.assertEqual(len(c), 2)
        self.assertEqual(len(c.lineages), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
