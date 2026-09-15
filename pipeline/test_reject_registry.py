"""
탈락 사유 레지스트리 계약 테스트 (PER-188).

완료 조건은 "버린 것을 왜 버렸는지 남긴다"이고, 그 조건은 아래 다섯으로 고정된다.

  1. **사유 없이 탈락시키면 에러다** — 빈 문자열도 `None` 도 사유가 아니다
  2. **미등록 사유로 탈락시키면 에러다** — 오타 하나면 그 행은 어느 게이트에도 귀속되지 않는다
  3. **게이트별 사유 집합이 레지스트리와 1:1 이다** — 실제 게이트를 돌려 나온 사유가
     레지스트리에 있고, 레지스트리의 `active` 사유가 전부 실제로 도달 가능하다.
     표만 맞춰 두고 코드가 다른 문자열을 쓰면 이 테스트가 잡는다
  4. **게이트3 으로는 아무것도 탈락시킬 수 없다** — 사유 0개는 누락이 아니라 결정이다
     (PER-185 §1). PER-188 이슈 설명문의 `게이트3 → 방향불일치` 를 기각한 자리
  5. **`의미중복` 은 미적용이라 쓰면 에러다** — 없는 걸 있는 척하지 않는다 (PER-184)

  python3 -m unittest discover -s pipeline -p 'test_*.py'
"""
import collections
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import reject_registry as registry  # noqa: E402
from contracts import MISSING_SEGMENT  # noqa: E402
from gates import (  # noqa: E402
    REJECT_LABELS,
    IdentityScope,
    RejectedRow,
    run_duplicate_gate,
    run_identity_gate,
)
from option_norm import OptionIndex  # noqa: E402
from policy import RENEWAL_SEPARATE, RENEWAL_UNOBSERVED  # noqa: E402
from reject_registry import (  # noqa: E402
    GATE_DUPLICATE,
    GATE_IDENTITY,
    GATE_POLARITY,
    GATE_SUFFICIENCY,
    GATES,
    REJECT_DUPLICATE_SEMANTIC,
    REJECT_PRODUCT,
    STATUS_ACTIVE,
    RejectRegistryError,
    assert_rejectable,
)
from sufficiency import (  # noqa: E402
    SUFFICIENCY_REJECT_LABELS,
    Claim,
    ClaimSupport,
    EvidenceCell,
    RejectedClaim,
    run_sufficiency_gate,
)


# --- 게이트1·2 를 실제로 돌리기 위한 최소 픽스처 (test_gates 와 같은 모양) ---


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


def review(review_id: int, product_id: str = "p001", *, date: str = "2026-01-15",
           option: str | None = None, author: str = "a", content: str = "c") -> dict:
    return {
        "reviewId": review_id,
        "productId": product_id,
        "raw": {"reviewDate": date, "option": option},
        "condition": {
            "skinType": {"code": "A02", "stated": True, "segment": "A02"},
            "skinTrouble": {"codes": [], "stated": False, "segments": [MISSING_SEGMENT]},
        },
        "derived": {
            "authorKey": author,
            "contentHash": content,
            "trustPrior": {"score": 0.5},
        },
    }


CATALOG = FakeCatalog(
    FakeProduct("p001", RENEWAL_SEPARATE, "2025-01", "2026-12"),
    FakeProduct("p002"),
)


class ReasonIsRequired(unittest.TestCase):
    """완료 조건 1 — 사유 없이 탈락시키지 않는다."""

    def test_사유가_없으면_탈락_행을_만들_수_없다(self):
        for missing in (None, "", "   "):
            with self.subTest(reason=missing):
                with self.assertRaises(RejectRegistryError):
                    RejectedRow(1, GATE_IDENTITY, missing)

    def test_주장_탈락도_사유가_없으면_에러다(self):
        with self.assertRaises(RejectRegistryError):
            RejectedClaim("c1", "", support={})


class UnregisteredReasonIsAnError(unittest.TestCase):
    """완료 조건 2 — 미등록 사유는 조용히 통과하지 않는다."""

    def test_등록되지_않은_사유는_에러다(self):
        with self.assertRaises(RejectRegistryError):
            RejectedRow(1, GATE_IDENTITY, "왠지_아니어서")

    def test_에러가_등록된_사유_목록을_알려준다(self):
        with self.assertRaises(RejectRegistryError) as caught:
            registry.reason("typo_cut")
        self.assertIn(REJECT_PRODUCT, str(caught.exception))

    def test_한글_표기도_사유_코드가_아니다(self):
        # 코드와 표기를 같은 문자열로 두면 표기를 고칠 때 판정이 흔들린다
        with self.assertRaises(RejectRegistryError):
            RejectedRow(1, GATE_IDENTITY, "제품불일치")

    def test_다른_게이트의_사유로_탈락시키면_에러다(self):
        with self.assertRaises(RejectRegistryError):
            RejectedRow(1, GATE_DUPLICATE, REJECT_PRODUCT)
        with self.assertRaises(RejectRegistryError):
            RejectedClaim("c1", REJECT_PRODUCT, support={})

    def test_알_수_없는_게이트도_에러다(self):
        with self.assertRaises(RejectRegistryError):
            assert_rejectable("gate9", REJECT_PRODUCT)


class GateReasonSetsAreOneToOne(unittest.TestCase):
    """완료 조건 3 — 게이트별 사유 집합이 레지스트리와 1:1.

    표만 맞추지 않는다. **실제 게이트를 돌려** 나온 사유를 모아 양방향으로 대조한다.
    """

    def _observed(self) -> dict[str, set[str]]:
        seen: dict[str, set[str]] = collections.defaultdict(set)

        index = OptionIndex.from_forms(
            {"p001": collections.Counter({"21N1": 5, "17N1": 5})})
        scope = IdentityScope("p001", option_key="21|N1")
        gate1 = run_identity_gate(
            [
                review(1, "p002"),                           # 제품불일치
                review(2, date="2024-12-01"),                # 리뉴얼이전 (세대 2025-01~)
                review(3, option="17N1"),                    # 옵션불일치
                review(4, option=None),                      # 옵션미기재
                review(5, option="21N1"),                    # 통과
            ],
            scope, CATALOG, index,
        )
        for row in gate1.rejected:
            seen[row.gate].add(row.reason)

        # 기간초과는 세대 컷과 같은 제품에서 함께 낼 수 없다 — 판정 순서가 세대 → 시점
        # 이라 세대 구간 밖 날짜는 먼저 `리뉴얼이전` 으로 귀속된다. 그 순서 자체가
        # PER-182 의 규격이므로 테스트를 순서에 맞추고, 리뉴얼 미확정 제품에서 잰다
        stale = run_identity_gate(
            [review(20, "p002", date="2020-03-03")], IdentityScope("p002"), CATALOG)
        for row in stale.rejected:
            seen[row.gate].add(row.reason)

        gate2 = run_duplicate_gate([
            review(10, author="A", content="x"),
            review(11, author="B", content="x"),   # 중복 (본문 완전일치)
            review(12, author="A", content="y"),   # 동일작성자
        ])
        for row in gate2.rejected:
            seen[row.gate].add(row.reason)

        # 게이트4 — 세 사유가 각각 결속하는 주장을 만든다
        big = frozenset(f"u{i}" for i in range(20))
        small = frozenset(f"v{i}" for i in range(3))
        claims = [
            # S < S_min
            Claim("c-small", EvidenceCell("p001", {}, small),
                  ClaimSupport("보습감", positive=frozenset({"v0"}))),
            # U < N_min (셀은 크고 방향을 말한 사람이 적다)
            Claim("c-few", EvidenceCell("p001", {}, big),
                  ClaimSupport("보습감", positive=frozenset({"u0", "u1"}))),
            # U/D < R_min (중립이 D 를 키운다)
            Claim("c-share", EvidenceCell("p001", {}, big),
                  ClaimSupport("보습감",
                               positive=frozenset({f"u{i}" for i in range(8)}),
                               neutral=frozenset({f"u{i}" for i in range(8, 20)}))),
        ]
        from policy import SufficiencyPolicy  # noqa: PLC0415 — 임계값을 여기서만 좁힌다
        result = run_sufficiency_gate(claims, SufficiencyPolicy(n_min=8, r_min=0.5, s_min=8))
        for row in result.rejected:
            seen[GATE_SUFFICIENCY].add(row.reason)
        return dict(seen)

    def test_게이트가_낸_사유가_전부_그_게이트에_등록돼_있다(self):
        for gate, reasons in self._observed().items():
            for code in reasons:
                with self.subTest(gate=gate, reason=code):
                    self.assertEqual(registry.reason(code).gate, gate)

    def test_등록된_active_사유가_전부_실제로_도달한다(self):
        observed = self._observed()
        for gate in GATES:
            registered = {
                code for code in registry.reasons_for(gate)
                if registry.reason(code).status == STATUS_ACTIVE
            }
            with self.subTest(gate=gate):
                self.assertEqual(registered, observed.get(gate, set()))

    def test_표기는_레지스트리_한_벌에서_나온다(self):
        # gates.REJECT_LABELS · sufficiency.SUFFICIENCY_REJECT_LABELS 가 두 벌이면
        # 한쪽만 고쳤을 때 같은 사유가 화면에서 두 이름으로 보인다
        self.assertIs(REJECT_LABELS, registry.REJECT_LABELS)
        for code, label in SUFFICIENCY_REJECT_LABELS.items():
            self.assertEqual(label, registry.label_of(code))

    def test_사유_코드와_한글_표기가_서로_다르다(self):
        for entry in registry.REASONS:
            self.assertNotEqual(entry.code, entry.label)

    def test_모든_사유가_판정_단위를_밝힌다(self):
        # 리뷰 탈락과 주장 탈락을 한 수로 더하지 못하게 하는 필드다
        for entry in registry.REASONS:
            self.assertIn(entry.unit, registry.UNITS)
            self.assertEqual(
                entry.unit,
                registry.UNIT_CLAIM if entry.gate == GATE_SUFFICIENCY
                else registry.UNIT_REVIEW,
            )


class PolarityGateHasNoRejectReason(unittest.TestCase):
    """완료 조건 4 — 게이트3 은 근거를 버리지 않는다 (PER-185 §1).

    PER-188 이슈 설명문의 `게이트3 → 방향불일치` 를 **기각한 자리**다. 부정 근거는
    틀린 근거가 아니다. 그 결정을 레지스트리가 강제한다.
    """

    def test_게이트3_은_사유가_0개다(self):
        self.assertEqual(registry.reasons_for(GATE_POLARITY), ())

    def test_게이트3_으로_탈락시키려_하면_에러다(self):
        for code in (REJECT_PRODUCT, "direction_mismatch", "방향불일치"):
            with self.subTest(reason=code):
                with self.assertRaises(RejectRegistryError):
                    assert_rejectable(GATE_POLARITY, code)

    def test_에러가_왜_없는지를_말한다(self):
        with self.assertRaises(RejectRegistryError) as caught:
            assert_rejectable(GATE_POLARITY, REJECT_PRODUCT)
        message = str(caught.exception)
        self.assertIn("PER-185", message)
        self.assertIn("부정 근거는 틀린 근거가 아니다", message)

    def test_방향불일치라는_사유는_아예_등록돼_있지_않다(self):
        self.assertNotIn("direction_mismatch", registry.BY_CODE)

    def test_게이트3_은_한계만_소유한다(self):
        # 탈락은 없지만 산출이 없는 건 아니다 — 한계 3종이 이 게이트의 것이다
        owned = {lim.code for lim in registry.LIMITATIONS if lim.gate == GATE_POLARITY}
        self.assertEqual(owned, {
            registry.LIMIT_TAGGER_DIRECTION_ERROR,
            registry.LIMIT_ORDER_CHOSEN_DIRECTION,
            registry.LIMIT_MINORITY_WITHIN_NOISE,
        })


class SemanticDuplicateIsReserved(unittest.TestCase):
    """완료 조건 5 — `의미중복` 은 자리만 있다 (PER-184 미적용)."""

    def test_코드는_등록돼_있다(self):
        entry = registry.reason(REJECT_DUPLICATE_SEMANTIC)
        self.assertEqual(entry.gate, GATE_DUPLICATE)
        self.assertEqual(entry.label, "의미중복")

    def test_미적용이라_탈락시키면_에러다(self):
        with self.assertRaises(RejectRegistryError):
            RejectedRow(1, GATE_DUPLICATE, REJECT_DUPLICATE_SEMANTIC)

    def test_에러가_미적용임을_말한다(self):
        with self.assertRaises(RejectRegistryError) as caught:
            assert_rejectable(GATE_DUPLICATE, REJECT_DUPLICATE_SEMANTIC)
        self.assertIn("미적용", str(caught.exception))

    def test_상태가_reserved_다(self):
        self.assertEqual(
            registry.reason(REJECT_DUPLICATE_SEMANTIC).status, registry.STATUS_RESERVED)


class LimitationsAreVocabularyToo(unittest.TestCase):
    """한계 코드도 어휘다 — 통과 행의 꼬리표가 오타로 새 종류가 되지 않는다."""

    def test_등록되지_않은_한계는_에러다(self):
        with self.assertRaises(RejectRegistryError):
            registry.limitation("대충_한계")

    def test_모든_한계가_어느_게이트의_것인지_밝힌다(self):
        for lim in registry.LIMITATIONS:
            self.assertIn(lim.gate, GATES)

    def test_한계는_탈락_사유가_아니다(self):
        for lim in registry.LIMITATIONS:
            with self.subTest(limitation=lim.code):
                self.assertNotIn(lim.code, registry.BY_CODE)


class RegistryIsSerialisable(unittest.TestCase):
    """리포트에 실리는 전문이 사유 어휘와 어긋나지 않는다."""

    def test_전문에_모든_게이트와_사유가_들어간다(self):
        payload = registry.as_dict()
        self.assertEqual([g["gate"] for g in payload["gates"]], list(GATES))
        codes = {r["code"] for g in payload["gates"] for r in g["reasons"]}
        self.assertEqual(codes, set(registry.BY_CODE))

    def test_게이트3_행이_왜_비었는지_설명을_담는다(self):
        payload = registry.as_dict()
        row = next(g for g in payload["gates"] if g["gate"] == GATE_POLARITY)
        self.assertEqual(row["reasons"], [])
        self.assertIn("PER-185", row["note"])


if __name__ == "__main__":
    unittest.main()
