"""
게이트1(동일성) 계약 테스트 (PER-182).

완료 조건 네 개를 여기서 고정한다.

  1. 제품 동일성은 카탈로그가 정한다 — 행의 문자열이 아니라 `productId` (PER-171)
  2. 색상·호수 질문이면 **같은 색상**의 리뷰만 통과한다
  3. 리뉴얼 이전 리뷰는 컷되고, 확정하지 않은 제품은 통과하되 한계를 남긴다 (PER-172)
  4. 탈락은 드롭이 아니라 사유가 붙은 `rejected[]` 행이다 (PER-188)

  python3 -m unittest discover -s pipeline -p 'test_*.py'
"""
import collections
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from gates import (  # noqa: E402
    GATE_IDENTITY,
    REJECT_LABELS,
    REJECT_OPTION,
    REJECT_OPTION_UNSTATED,
    REJECT_PRODUCT,
    IdentityScope,
    identity_gate,
    run_identity_gate,
)
from option_norm import OptionIndex  # noqa: E402
from policy import (  # noqa: E402
    LIMIT_RENEWAL_UNOBSERVED,
    REJECT_RECENCY,
    REJECT_RENEWAL,
    RENEWAL_SEPARATE,
    RENEWAL_SINGLE,
    RENEWAL_UNOBSERVED,
)


@dataclass(frozen=True)
class FakeProduct:
    product_id: str
    renewal_policy: str = RENEWAL_UNOBSERVED
    renewal_from_month: str | None = None
    renewal_to_month: str | None = None


class FakeCatalog:
    def __init__(self, *products: FakeProduct):
        self._by_id = {p.product_id: p for p in products}

    def product(self, product_id: str) -> FakeProduct:
        return self._by_id[product_id]


def record(review_id: int, product_id: str = "p001", *, date: str = "2026-01-15",
           option: str | None = None) -> dict:
    """게이트가 읽는 필드만 갖춘 최소 레코드."""
    return {
        "reviewId": review_id,
        "productId": product_id,
        "raw": {"reviewDate": date, "option": option},
        "condition": {},
        "derived": {},
    }


def index_of(product_id: str, forms: dict[str, int]) -> OptionIndex:
    return OptionIndex.from_forms({product_id: collections.Counter(forms)})


CATALOG = FakeCatalog(FakeProduct("p001"), FakeProduct("p002"))


class ProductIdentity(unittest.TestCase):
    def test_다른_제품의_리뷰는_탈락한다(self):
        decision, _ = identity_gate(record(1, "p002"), IdentityScope("p001"), CATALOG)
        self.assertFalse(decision.passed)
        self.assertEqual(decision.reason, REJECT_PRODUCT)

    def test_같은_제품은_통과한다(self):
        decision, _ = identity_gate(record(1, "p001"), IdentityScope("p001"), CATALOG)
        self.assertTrue(decision.passed)


class OptionIdentity(unittest.TestCase):
    """§4-1 '색상·호수 질문이면 같은 옵션 리뷰만'."""

    FORMS = {"21N1(본품+리필)": 36, "17N1(본품+리필)": 16, "50ml": 3}

    def setUp(self):
        self.index = index_of("p001", self.FORMS)
        self.scope = IdentityScope("p001", option_key="21|N1")

    def test_같은_색상만_통과한다(self):
        passed, _ = identity_gate(
            record(1, option="[기획] 21N1"), self.scope, CATALOG, self.index)
        self.assertTrue(passed.passed)

    def test_다른_색상은_옵션불일치다(self):
        decision, detail = identity_gate(
            record(2, option="17N1(본품+리필)"), self.scope, CATALOG, self.index)
        self.assertFalse(decision.passed)
        self.assertEqual(decision.reason, REJECT_OPTION)
        self.assertEqual(REJECT_LABELS[decision.reason], "옵션불일치")
        self.assertIn("17|N1", detail)

    def test_미기재는_불일치와_다른_사유다(self):
        # 둘을 뭉치면 '색상을 안 적어서 못 쓴 근거'와 '다른 색이라 못 쓴 근거'를
        # 구분할 수 없고, 옵션 정규화 개선분이 귀속되지 않는다
        decision, _ = identity_gate(record(3, option=None), self.scope, CATALOG, self.index)
        self.assertFalse(decision.passed)
        self.assertEqual(decision.reason, REJECT_OPTION_UNSTATED)

    def test_용량_구성은_색상이_아니므로_탈락한다(self):
        decision, _ = identity_gate(record(4, option="50ml"), self.scope, CATALOG, self.index)
        self.assertFalse(decision.passed)
        self.assertEqual(decision.reason, REJECT_OPTION)

    def test_범위를_걸지_않으면_옵션을_보지_않는다(self):
        # 색상 질문이 아닌데 컷을 걸면 기재분(68.1%)만 남고 나머지가 이유 없이 사라진다
        for option in (None, "17N1(본품+리필)", "50ml"):
            decision, _ = identity_gate(
                record(5, option=option), IdentityScope("p001"), CATALOG, self.index)
            self.assertTrue(decision.passed, option)

    def test_범위를_걸었는데_어휘가_없으면_에러다(self):
        with self.assertRaises(ValueError):
            identity_gate(record(6, option="21N1"), self.scope, CATALOG, option_index=None)


class RenewalAndRecency(unittest.TestCase):
    def test_세대_밖_리뷰는_리뉴얼이전으로_탈락한다(self):
        catalog = FakeCatalog(FakeProduct(
            "p001", RENEWAL_SEPARATE, renewal_from_month="2025-01"))
        decision, _ = identity_gate(
            record(1, date="2024-11-02"), IdentityScope("p001"), catalog)
        self.assertFalse(decision.passed)
        self.assertEqual(decision.reason, REJECT_RENEWAL)
        self.assertEqual(REJECT_LABELS[decision.reason], "리뉴얼이전")

    def test_리센시_윈도우_밖은_탈락한다(self):
        decision, _ = identity_gate(
            record(1, date="2019-03-01"), IdentityScope("p001"), CATALOG)
        self.assertFalse(decision.passed)
        self.assertEqual(decision.reason, REJECT_RECENCY)

    def test_확정하지_않은_제품은_통과하되_한계를_남긴다(self):
        decision, _ = identity_gate(record(1), IdentityScope("p001"), CATALOG)
        self.assertTrue(decision.passed)
        self.assertEqual(decision.limitation, LIMIT_RENEWAL_UNOBSERVED)

    def test_리뉴얼_없음이_확인된_제품은_한계가_없다(self):
        catalog = FakeCatalog(FakeProduct("p001", RENEWAL_SINGLE))
        decision, _ = identity_gate(record(1), IdentityScope("p001"), catalog)
        self.assertTrue(decision.passed)
        self.assertIsNone(decision.limitation)


class RejectedRecord(unittest.TestCase):
    """탈락은 드롭이 아니다 — 지우면 재현율을 영영 못 잰다 (PER-188)."""

    def test_탈락_리뷰가_사유와_함께_남는다(self):
        index = index_of("p001", {"21N1": 5, "17N1": 5})
        records = [
            record(1, option="21N1"),
            record(2, option="17N1"),
            record(3, option=None),
            record(4, "p002", option="21N1"),
            record(5, option="21N1", date="2019-01-01"),
        ]
        result = run_identity_gate(
            records, IdentityScope("p001", "21|N1"), CATALOG, index)

        self.assertEqual([r["reviewId"] for r in result.passed], [1])
        self.assertEqual(len(result.rejected), 4, "입력 5건이 통과 1 + 탈락 4로 모두 설명돼야 한다")
        self.assertEqual(
            result.rejected_by_reason(),
            {REJECT_OPTION: 1, REJECT_OPTION_UNSTATED: 1,
             REJECT_PRODUCT: 1, REJECT_RECENCY: 1},
        )

    def test_rejected_행에_게이트와_한글_표기가_붙는다(self):
        index = index_of("p001", {"21N1": 5, "17N1": 5})
        result = run_identity_gate(
            [record(2, option="17N1")], IdentityScope("p001", "21|N1"), CATALOG, index)
        row = result.rejected[0].as_dict()
        self.assertEqual(row["gate"], GATE_IDENTITY)
        self.assertEqual(row["reason"], REJECT_OPTION)
        self.assertEqual(row["label"], "옵션불일치")

    def test_통과분의_한계가_결과에_모인다(self):
        result = run_identity_gate([record(1)], IdentityScope("p001"), CATALOG)
        self.assertEqual(result.limitations, {LIMIT_RENEWAL_UNOBSERVED})

    def test_아무것도_통과하지_못해도_설명이_남는다(self):
        result = run_identity_gate([record(1, "p002")], IdentityScope("p001"), CATALOG)
        self.assertEqual(result.passed, [])
        self.assertEqual(result.as_dict()["rejectedByReason"], {REJECT_PRODUCT: 1})


class Determinism(unittest.TestCase):
    def test_같은_입력이면_같은_결과다(self):
        index = index_of("p001", {"21N1": 5, "17N1": 5})
        records = [record(i, option="21N1" if i % 2 else "17N1") for i in range(1, 11)]
        scope = IdentityScope("p001", "21|N1")
        first = run_identity_gate(records, scope, CATALOG, index).as_dict()
        second = run_identity_gate(records, scope, CATALOG, index).as_dict()
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
