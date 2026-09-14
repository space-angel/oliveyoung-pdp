"""
게이트1(동일성, PER-182) · 게이트2(중복, PER-183) 계약 테스트.

## 게이트1 완료 조건

  1. 제품 동일성은 카탈로그가 정한다 — 행의 문자열이 아니라 `productId` (PER-171)
  2. 색상·호수 질문이면 **같은 색상**의 리뷰만 통과한다
  3. 리뉴얼 이전 리뷰는 컷되고, 확정하지 않은 제품은 통과하되 한계를 남긴다 (PER-172)
  4. 탈락은 드롭이 아니라 사유가 붙은 `rejected[]` 행이다 (PER-188)

## 게이트2 완료 조건

  5. 본문 완전일치는 `contentHash` 로 제거된다
  6. 동일 작성자의 복수 리뷰는 **1표**다 — 남길 1건의 선택이 결정적이어야 한다 (PER-170)
  7. 두 축은 서로 대체하지 않는다 — 한쪽만 걸면 다른 쪽 오염이 그대로 남는다
  8. 탈락은 `"중복"` / `"동일작성자"` 로 `rejected[]` 에 남는다
  9. `support.independentReviews` 는 **게이트를 통과한 수**다 — 통과하지 않은 묶음에서
     그 수를 세면 에러다 (N 이 조용히 부푸는 유일한 경로)

  python3 -m unittest discover -s pipeline -p 'test_*.py'
"""
import collections
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from contracts import content_hash  # noqa: E402
from gates import (  # noqa: E402
    GATE_DUPLICATE,
    GATE_IDENTITY,
    REJECT_DUPLICATE_CONTENT,
    REJECT_LABELS,
    REJECT_OPTION,
    REJECT_OPTION_UNSTATED,
    REJECT_PRODUCT,
    REJECT_SAME_AUTHOR,
    GateError,
    IdentityScope,
    identity_gate,
    independent_reviews,
    run_duplicate_gate,
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


# =============================================================================
# 게이트2 — 중복 (PER-183)
# =============================================================================


def dup_record(review_id: int, *, product_id: str = "p001", author: str = "민지",
               content: str = "촉촉하고 좋아요", date: str = "2026-01-15",
               score: float = 0.5) -> dict:
    """게이트2가 읽는 필드만 갖춘 입수 레코드. 해시는 실제 산출 함수를 쓴다."""
    return {
        "reviewId": review_id,
        "productId": product_id,
        "raw": {"reviewDate": date, "option": None, "content": content},
        "condition": {},
        "derived": {
            "authorKey": author,
            "contentHash": content_hash(content),
            "trustPrior": {"score": score},
        },
    }


def passed_ids(result) -> list[int]:
    return [r["reviewId"] for r in result.passed]


class DuplicateContent(unittest.TestCase):
    """완전 동일 본문 → `contentHash` 로 제거."""

    def test_같은_본문은_한_건만_남는다(self):
        result = run_duplicate_gate([
            dup_record(1, author="민지", content="수분감 최고", score=0.9),
            dup_record(2, author="수연", content="수분감 최고", score=0.4),
        ])
        self.assertEqual(passed_ids(result), [1])
        self.assertEqual(result.rejected[0].reason, REJECT_DUPLICATE_CONTENT)
        self.assertEqual(REJECT_LABELS[REJECT_DUPLICATE_CONTENT], "중복")

    def test_다른_제품의_같은_본문은_합치지_않는다(self):
        # 같은 사람이 다른 제품에 쓴 리뷰는 서로 독립 근거다. 제품을 넘어 합치면
        # 한 제품의 근거가 다른 제품 때문에 사라진다
        result = run_duplicate_gate([
            dup_record(1, product_id="p001", content="수분감 최고"),
            dup_record(2, product_id="p002", content="수분감 최고"),
        ])
        self.assertEqual(sorted(passed_ids(result)), [1, 2])
        self.assertEqual(result.rejected, [])

    def test_공백만_다른_본문은_같은_해시가_아니다(self):
        # 인용 대조(PER-175)와 달리 중복 판정에는 정규화를 넣지 않는다 — 증가분 0 이고,
        # 넣는 순간 "띄어쓰기만 지운 다른 글"까지 한 표로 합쳐진다
        result = run_duplicate_gate([
            dup_record(1, author="민지", content="수분감 최고"),
            dup_record(2, author="수연", content="수분감  최고"),
        ])
        self.assertEqual(sorted(passed_ids(result)), [1, 2])


class AuthorOneVote(unittest.TestCase):
    """동일 작성자의 복수 리뷰 → 1표 (PER-170)."""

    def test_같은_작성자_같은_제품은_한_표다(self):
        result = run_duplicate_gate([
            dup_record(1, author="민지", content="a", score=0.9),
            dup_record(2, author="민지", content="b", score=0.5),
            dup_record(3, author="민지", content="c", score=0.1),
        ])
        self.assertEqual(passed_ids(result), [1])
        self.assertEqual(result.rejected_by_reason(), {REJECT_SAME_AUTHOR: 2})
        self.assertEqual(REJECT_LABELS[REJECT_SAME_AUTHOR], "동일작성자")

    def test_같은_작성자라도_제품이_다르면_각각_한_표다(self):
        result = run_duplicate_gate([
            dup_record(1, product_id="p001", author="민지", content="a"),
            dup_record(2, product_id="p002", author="민지", content="b"),
        ])
        self.assertEqual(sorted(passed_ids(result)), [1, 2])

    def test_남길_한_건은_신뢰도_점수가_가른다(self):
        result = run_duplicate_gate([
            dup_record(1, content="a", score=0.2),
            dup_record(2, content="b", score=0.8),
        ])
        self.assertEqual(passed_ids(result), [2])

    def test_점수가_같으면_최신_리뷰가_남는다(self):
        # 25,000건에 서로 다른 점수가 757개뿐이라 동점이 흔하다 (PER-174)
        result = run_duplicate_gate([
            dup_record(1, content="a", date="2024-03-02", score=0.5),
            dup_record(2, content="b", date="2026-02-01", score=0.5),
        ])
        self.assertEqual(passed_ids(result), [2])

    def test_날짜까지_같으면_reviewId_가_작은_쪽이_남는다(self):
        result = run_duplicate_gate([
            dup_record(7, content="a"),
            dup_record(3, content="b"),
        ])
        self.assertEqual(passed_ids(result), [3])


class AxesDoNotSubstitute(unittest.TestCase):
    """두 축은 서로 대체하지 않는다 — 한쪽만 걸면 다른 쪽 오염이 남는다 (PER-170 §2)."""

    def test_같은_작성자의_다른_본문은_해시로_잡히지_않는다(self):
        # 재구매·옵션별·기간 경과 후 추가 작성. 해시가 커버하는 건 초과 표의 12.1% 뿐이다
        records = [
            dup_record(1, author="민지", content="처음 썼을 때 좋았어요", score=0.6),
            dup_record(2, author="민지", content="재구매했는데 여전히 좋아요", score=0.3),
        ]
        hashes = {r["derived"]["contentHash"] for r in records}
        self.assertEqual(len(hashes), 2, "본문이 다르므로 해시 축은 이 쌍을 못 잡는다")
        result = run_duplicate_gate(records)
        self.assertEqual(result.rejected_by_reason(), {REJECT_SAME_AUTHOR: 1})

    def test_다른_작성자의_같은_본문은_작성자_축으로_잡히지_않는다(self):
        records = [
            dup_record(1, author="민지", content="배송 빨라요", score=0.6),
            dup_record(2, author="수연", content="배송 빨라요", score=0.3),
        ]
        self.assertEqual(len({r["derived"]["authorKey"] for r in records}), 2)
        result = run_duplicate_gate(records)
        self.assertEqual(result.rejected_by_reason(), {REJECT_DUPLICATE_CONTENT: 1})


class DuplicateRejectedRecord(unittest.TestCase):
    """탈락은 드롭이 아니다 — 지우면 재현율을 영영 못 잰다 (PER-188)."""

    def test_입력이_통과와_탈락으로_모두_설명된다(self):
        records = [
            dup_record(1, author="민지", content="a", score=0.9),
            dup_record(2, author="민지", content="b", score=0.5),
            dup_record(3, author="수연", content="a", score=0.4),
            dup_record(4, author="지우", content="c", score=0.3),
        ]
        result = run_duplicate_gate(records)
        self.assertEqual(passed_ids(result), [1, 4])
        self.assertEqual(len(result.passed) + len(result.rejected), len(records))
        self.assertEqual(
            result.rejected_by_reason(),
            {REJECT_DUPLICATE_CONTENT: 1, REJECT_SAME_AUTHOR: 1},
        )

    def test_탈락_행이_어느_리뷰로_합쳐졌는지_남긴다(self):
        result = run_duplicate_gate([
            dup_record(1, author="민지", content="a", score=0.9),
            dup_record(2, author="민지", content="b", score=0.1),
        ])
        row = result.rejected[0].as_dict()
        self.assertEqual(row["reviewId"], 2)
        self.assertEqual(row["gate"], GATE_DUPLICATE)
        self.assertEqual(row["label"], "동일작성자")
        self.assertIn("1", row["detail"], "대표 리뷰를 짚어야 역추적이 된다")

    def test_결과가_어느_게이트의_판정인지_말한다(self):
        result = run_duplicate_gate([dup_record(1)])
        self.assertEqual(result.as_dict()["gate"], GATE_DUPLICATE)
        self.assertEqual(result.as_dict()["issue"], "PER-183")


class IndependentReviewCount(unittest.TestCase):
    """`support.independentReviews` = 게이트2를 통과한 수."""

    def test_통과분의_수가_독립_근거_수다(self):
        result = run_duplicate_gate([
            dup_record(1, author="민지", content="a"),
            dup_record(2, author="민지", content="b"),
            dup_record(3, author="수연", content="c"),
        ])
        self.assertEqual(independent_reviews(result.passed), 2)

    def test_게이트를_통과하지_않은_묶음에서_세면_에러다(self):
        records = [
            dup_record(1, author="민지", content="a"),
            dup_record(2, author="민지", content="b"),
        ]
        with self.assertRaises(GateError):
            independent_reviews(records)

    def test_같은_본문이_섞인_묶음도_에러다(self):
        records = [
            dup_record(1, author="민지", content="a"),
            dup_record(2, author="수연", content="a"),
        ]
        with self.assertRaises(GateError):
            independent_reviews(records)


class DuplicateGateContract(unittest.TestCase):
    """판정에 필요한 파생 필드가 없으면 조용히 넘어가지 않는다."""

    def test_작성자_키가_비면_에러다(self):
        record_ = dup_record(1)
        record_["derived"]["authorKey"] = ""
        with self.assertRaises(GateError):
            run_duplicate_gate([record_])

    def test_본문_해시가_없으면_에러다(self):
        record_ = dup_record(1)
        del record_["derived"]["contentHash"]
        with self.assertRaises(GateError):
            run_duplicate_gate([record_])

    def test_신뢰도_점수가_없으면_에러다(self):
        # 없으면 1표 선택이 날짜순으로 조용히 밀린다 — 같은 입력에 다른 대표가 남는다
        record_ = dup_record(1)
        del record_["derived"]["trustPrior"]
        with self.assertRaises(GateError):
            run_duplicate_gate([record_])


class DuplicateDeterminism(unittest.TestCase):
    def test_입력_순서를_섞어도_결과가_같다(self):
        records = [
            dup_record(i, author=f"작성자{i % 3}", content=f"본문{i % 4}",
                       date=f"2026-0{i % 8 + 1}-01", score=round(0.1 * (i % 5), 4))
            for i in range(1, 13)
        ]
        base = run_duplicate_gate(records).as_dict()
        for shift in range(1, len(records)):
            rotated = records[shift:] + records[:shift]
            self.assertEqual(run_duplicate_gate(rotated).as_dict(), base)
            self.assertEqual(
                passed_ids(run_duplicate_gate(rotated)), passed_ids(run_duplicate_gate(records)))


class GateOrder(unittest.TestCase):
    """게이트1 → 게이트2. 순서가 규격이다."""

    def test_컷된_리뷰는_대표로_남지_않는다(self):
        # 리센시 컷 밖 리뷰가 점수는 더 높은 경우. 게이트1을 건너뛰면 그 리뷰가 1표를
        # 가져가고, 화면에는 24개월 밖 근거가 남는다
        old_review = dup_record(1, author="민지", content="a", date="2019-03-01", score=0.9)
        fresh = dup_record(2, author="민지", content="b", date="2026-01-15", score=0.2)

        without_gate1 = run_duplicate_gate([old_review, fresh])
        self.assertEqual(passed_ids(without_gate1), [1])

        first = run_identity_gate([old_review, fresh], IdentityScope("p001"), CATALOG)
        self.assertEqual([r["reviewId"] for r in first.passed], [2])
        second = run_duplicate_gate(first.passed)
        self.assertEqual(passed_ids(second), [2])


if __name__ == "__main__":
    unittest.main()
