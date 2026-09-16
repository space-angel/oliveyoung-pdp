"""링크 진입점 계약 (PER-194).

네트워크도 LLM 도 쓰지 않는다. 검사하는 것은 네 가지다.

  1. 링크에서 goodsNo 를 **추측하지 않고** 뽑는가
  2. 런 카탈로그가 수집분의 변형 SKU 를 전부 등록하는가 (빠지면 입수가 멈춘다)
  3. 비용이 드는 단계를 --yes 없이 돌리지 않는가
  4. 런 요약이 **못 잰 것**을 적는가
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import run_url  # noqa: E402
from catalog import ProductCatalog  # noqa: E402
from workspace import WorkspaceError  # noqa: E402


def a_review(**over) -> dict:
    row = {
        "reviewId": 1, "content": "촉촉해요", "rating": 5, "reviewDate": "2026.09.01",
        "userName": "누구", "goodsNo": "A000000211119",
        "requestedGoodsNo": "A000000211119", "productKey": "아무 세럼",
        "productName": "아무 세럼 40ml", "category": "에센스/세럼",
    }
    row.update(over)
    return row


class GoodsNoFromUrl(unittest.TestCase):
    def test_reads_pdp_link(self):
        self.assertEqual(
            run_url.goods_no_from(
                "https://www.oliveyoung.co.kr/store/goods/getGoodsDetail.do"
                "?goodsNo=A000000211119"),
            "A000000211119")

    def test_reads_link_with_extra_query(self):
        self.assertEqual(
            run_url.goods_no_from(
                "https://www.oliveyoung.co.kr/store/goods/getGoodsDetail.do"
                "?goodsNo=A000000211119&dispCatNo=1000&trackingCd=x"),
            "A000000211119")

    def test_refuses_to_guess(self):
        """형식이 안 맞으면 멈춘다. 잘못 뽑으면 엉뚱한 제품을 수집하고,
        리뷰는 정상적으로 쌓이기 때문에 끝까지 드러나지 않는다."""
        for bad in ("", "https://www.oliveyoung.co.kr/store/main", "A00000021111",
                    "goodsNo=B000000211119"):
            with self.subTest(bad=bad), self.assertRaises(run_url.RunUrlError):
                run_url.goods_no_from(bad)

    def test_refuses_when_ambiguous(self):
        with self.assertRaises(run_url.RunUrlError):
            run_url.goods_no_from("?a=A000000211119&b=A000000231894")


class RunCatalogFromCrawl(unittest.TestCase):
    """크롤러는 요청한 goodsNo 로 부르지만 돌아오는 리뷰의 goodsNo 는 변형 SKU 라
    다른 경우가 흔하다. 빠뜨리면 입수가 미등록 goodsNo 에러로 멈춘다 (PER-171)."""

    def test_registers_every_goods_no_seen(self):
        reviews = [
            a_review(reviewId=1, goodsNo="A000000211119"),
            a_review(reviewId=2, goodsNo="A000000211120"),   # 변형 SKU
            a_review(reviewId=3, goodsNo="A000000211121"),
        ]
        cat = run_url.build_run_catalog(reviews, "A000000211119")
        registered = {g["goodsNo"] for g in cat["products"][0]["goodsNos"]}
        self.assertEqual(
            registered, {"A000000211119", "A000000211120", "A000000211121"})

    def test_marks_which_one_was_requested(self):
        cat = run_url.build_run_catalog(
            [a_review(), a_review(reviewId=2, goodsNo="A000000211120")], "A000000211119")
        by = {g["goodsNo"]: g["source"] for g in cat["products"][0]["goodsNos"]}
        self.assertEqual(by["A000000211119"], "crawl_request")
        self.assertEqual(by["A000000211120"], "observed_variant")

    def test_renewal_is_unobserved(self):
        """한 번 수집한 것으로 세대 경계를 알 수 없다. 모르는 것을 single 로 적으면
        세대가 섞인 근거가 한 제품으로 집계된다 (PER-172)."""
        cat = run_url.build_run_catalog([a_review()], "A000000211119")
        self.assertEqual(cat["products"][0]["renewalPolicy"]["policy"], "unobserved")

    def test_catalog_loads_with_the_real_loader(self):
        """카탈로그 계약을 직접 흉내내지 않고 진짜 로더로 읽는다 — 형식이 갈리면 여기서 잡힌다."""
        cat = run_url.build_run_catalog(
            [a_review(), a_review(reviewId=2, goodsNo="A000000211120")], "A000000211119")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.json"
            path.write_text(json.dumps(cat, ensure_ascii=False))
            loaded = ProductCatalog.load(path)
            self.assertEqual(loaded.resolve_goods_no("A000000211120"), "p001")

    def test_fails_when_requested_product_is_absent(self):
        """요청 제품의 리뷰가 하나도 안 오면 '요청'과 '수집'이 다른 채로 파이프라인이 돈다."""
        with self.assertRaises(run_url.RunUrlError):
            run_url.build_run_catalog([a_review(goodsNo="A000000999999")], "A000000211119")

    def test_fails_on_empty_crawl(self):
        with self.assertRaises(run_url.RunUrlError):
            run_url.build_run_catalog([], "A000000211119")


class CrawlContract(unittest.TestCase):
    def test_unknown_field_stops_before_ingest(self):
        """입수에서도 잡히지만, 거기까지 가면 크롤 비용을 이미 낸 뒤다."""
        with self.assertRaises(run_url.RunUrlError) as cm:
            run_url.assert_crawl_contract([a_review(newFieldFromCrawler="x")])
        self.assertIn("newFieldFromCrawler", str(cm.exception))

    def test_known_fields_pass(self):
        run_url.assert_crawl_contract([a_review()])


class PlanIsSafeByDefault(unittest.TestCase):
    class Args:
        url = ("https://www.oliveyoung.co.kr/store/goods/getGoodsDetail.do"
               "?goodsNo=A000000211119")
        goods = None
        run_id = None
        steps = None
        target = 500

    def test_run_id_defaults_to_goods_no(self):
        plan = run_url.make_plan(self.Args())
        self.assertEqual(plan.run_id, "oy-A000000211119")

    def test_every_path_is_inside_the_run(self):
        plan = run_url.make_plan(self.Args())
        plan.ws.assert_isolated()          # 정본을 가리키면 예외

    def test_costly_steps_are_named(self):
        plan = run_url.make_plan(self.Args())
        self.assertEqual(set(plan.costly_steps()), {"crawl", "tag", "claims"})

    def test_free_steps_only(self):
        args = self.Args()
        args.steps = "catalog,ingest,gates,report"
        self.assertEqual(run_url.make_plan(args).costly_steps(), [])

    def test_unknown_step_is_rejected(self):
        args = self.Args()
        args.steps = "crawl,polish"
        with self.assertRaises(run_url.RunUrlError):
            run_url.make_plan(args)

    def test_bad_run_id_is_rejected(self):
        args = self.Args()
        args.run_id = "../escape"
        with self.assertRaises(WorkspaceError):
            run_url.make_plan(args)

    def test_describe_says_what_is_not_measured(self):
        text = run_url.make_plan(self.Args()).describe()
        self.assertIn("재현율을 재지 않는다", text)
        self.assertIn("정답지가 없다", text)


class ReportSaysWhatItCouldNotMeasure(unittest.TestCase):
    """못 잰 것을 안 적으면 '안 나왔다'와 '나빴다'를 구분할 수 없다.
    v4 리포트가 떨어진 검사 둘을 합격 산식에서 빼놓고 합격을 띄운 것과 같은 실패다."""

    def _report(self):
        from policy import SnapshotPolicy
        from workspace import Workspace

        with tempfile.TemporaryDirectory() as tmp:
            ws = Workspace.for_run("t1", root=Path(tmp))
            ws.ensure_dirs()
            plan = run_url.Plan(None, "A000000211119", "t1", ws, ("report",), 500)
            return run_url.step_report(plan, SnapshotPolicy.derive(["2026.09.01"]))

    def test_golden_is_false_with_a_reason(self):
        ev = self._report()["evaluation"]
        self.assertFalse(ev["golden"])
        self.assertIsNone(ev["recall"])
        self.assertIn("골든셋", ev["reason"])

    def test_splits_measurable_from_not(self):
        ev = self._report()["evaluation"]
        self.assertIn("재현율", ev["notMeasurable"])
        self.assertIn("인용 원문 일치율", ev["measurable"])

    def test_records_the_derived_window(self):
        """어느 창으로 잘랐는지 감추지 않는다."""
        snap = self._report()["snapshot"]
        self.assertTrue(snap["derived"])
        self.assertEqual(snap["latestMonth"], "2026-09")


if __name__ == "__main__":
    unittest.main()
