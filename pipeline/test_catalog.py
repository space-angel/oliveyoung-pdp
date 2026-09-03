"""
카탈로그 계약 테스트 (PER-171).

완료 조건이 "미등록 상품 ID는 조용히 폴백하지 않고 에러를 낸다"이므로,
그 동작을 말이 아니라 테스트로 고정한다. 외부 의존 없음:

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
    CatalogError,
    ProductCatalog,
    UnknownGoodsNoError,
    UnknownProductError,
    load_catalog,
)

ROOT = Path(__file__).parents[1]


def write_catalog(products: list[dict], version: str = "v5-1") -> Path:
    path = Path(tempfile.mkdtemp()) / "catalog.json"
    path.write_text(json.dumps({"_meta": {"schemaVersion": version}, "products": products},
                               ensure_ascii=False))
    return path


def product(pid="p001", name="테스트 제품", goods=("A000000000001",), source="crawl_request"):
    return {
        "productId": pid,
        "displayName": name,
        "category": "에센스/세럼",
        "requestedGoodsNo": goods[0],
        "renewalPolicy": None,
        "notes": [],
        "goodsNos": [{"goodsNo": g, "source": source} for g in goods],
    }


class RealCatalog(unittest.TestCase):
    """커밋된 카탈로그가 실제 입력을 전부 덮는지."""

    @classmethod
    def setUpClass(cls):
        cls.catalog = load_catalog()

    def test_shape(self):
        self.assertEqual(len(self.catalog), 50)
        self.assertEqual(sum(len(p.goods_nos) for p in self.catalog.products), 167)

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

    def test_goods_no_in_two_products_raises(self):
        path = write_catalog([
            product("p001", "제품 A", ("A000000000001", "A000000000002")),
            product("p002", "제품 B", ("A000000000002",)),
        ])
        with self.assertRaises(CatalogError) as ctx:
            ProductCatalog.load(path)
        self.assertIn("두 제품에 걸쳐", str(ctx.exception))

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
