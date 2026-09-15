"""
통합 `rejected[]` 원장 · 골든셋 역추적 계약 테스트 (PER-188).

완료 조건은 두 개다.

  A. **버린 것을 왜 버렸는지 남긴다** — 게이트 1~4 전부가 사유가 붙은 행을 원장에 넣고,
     리뷰 하나가 어느 게이트에서 멈췄는지 한 줄로 되짚을 수 있다
  B. **골든셋이 잡은 주장 중 파이프라인이 못 낸 것을 `rejected[]` 로 역추적할 수 있다** —
     이게 재현율 계산 방식이다. 미스는 **전부** 게이트·사유를 갖거나, 되짚을 수 없다는
     사실이 명시적으로 남는다 (`untraceable`)

되짚을 수 없는 미스를 조용히 분모에서 빼면 재현율이 올라간다. 그래서 `recall_summary`
가 `untraceable` 을 분모에서 빼되 **수를 남기는지**도 여기서 고정한다.

  python3 -m unittest discover -s pipeline -p 'test_*.py'
"""
import collections
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from contracts import MISSING_SEGMENT  # noqa: E402
from gates import IdentityScope  # noqa: E402
from ledger import (  # noqa: E402
    MISS_EMPTY_CELL,
    MISS_NO_ASPECT,
    MISS_NO_MENTION,
    MISS_UNKNOWN_OPTION,
    OUTCOME_NO_CANDIDATE,
    OUTCOME_PRODUCED,
    OUTCOME_REJECTED,
    OUTCOME_UNTRACEABLE,
    ClaimTarget,
    LedgerError,
    LedgerRow,
    RejectedLedger,
    build_claims,
    claim_id,
    recall_summary,
    run_gates,
    run_review_gates,
    trace_all,
    trace_target,
)
from option_norm import OptionIndex  # noqa: E402
from policy import RENEWAL_SEPARATE, RENEWAL_UNOBSERVED, SufficiencyPolicy  # noqa: E402
from reject_registry import (  # noqa: E402
    GATE_DUPLICATE,
    GATE_IDENTITY,
    GATE_POLARITY,
    GATE_SUFFICIENCY,
    REJECT_DUPLICATE_SEMANTIC,
    REJECT_INSUFFICIENT,
    REJECT_PRODUCT,
    REJECT_RECENCY,
    REJECT_SAME_AUTHOR,
    UNIT_CLAIM,
    UNIT_REVIEW,
    RejectRegistryError,
)

ASPECT = "보습감"
OTHER_ASPECT = "지속성"


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


CATALOG = FakeCatalog(
    FakeProduct("p001"),
    FakeProduct("p002", RENEWAL_SEPARATE, "2025-01", "2026-12"),
)


def review(review_id: int, *, product_id: str = "p001", author: str | None = None,
           content: str | None = None, date: str = "2026-01-15",
           skin_type: str | None = "A02", option: str | None = None) -> dict:
    author = author or f"u{review_id}"
    return {
        "reviewId": review_id,
        "productId": product_id,
        "raw": {"reviewDate": date, "option": option},
        "condition": {
            "skinType": {
                "code": skin_type,
                "stated": skin_type is not None,
                "segment": skin_type or MISSING_SEGMENT,
            },
            "skinTrouble": {"codes": [], "stated": False, "segments": [MISSING_SEGMENT]},
            "option": {
                "code": option,
                "stated": option is not None,
                "segment": option or MISSING_SEGMENT,
            },
        },
        "derived": {
            "authorKey": author,
            "contentHash": content or f"h{review_id}",
            "trustPrior": {"score": 0.5},
        },
    }


def tag(review_id: int, polarity: str, aspect: str = ASPECT) -> dict:
    return {"reviewId": review_id, "aspect": aspect, "polarity": polarity}


def corpus(n: int = 12, *, polarity: str = "positive", start: int = 1) -> list[dict]:
    return [review(i) for i in range(start, start + n)]


def tags_of(records: list[dict], polarity: str = "positive", aspect: str = ASPECT) -> list[dict]:
    return [tag(r["reviewId"], polarity, aspect) for r in records]


def by_review(tags: list[dict]) -> dict[int, list[tuple[str, str]]]:
    out: dict[int, list[tuple[str, str]]] = collections.defaultdict(list)
    for t in tags:
        out[t["reviewId"]].append((t["aspect"], t["polarity"]))
    return dict(out)


# =============================================================================
# A. 원장
# =============================================================================


class LedgerRowsCarryAReason(unittest.TestCase):
    """행 하나 = 버린 것 하나 = 사유 하나."""

    def test_미등록_사유는_행이_되지_않는다(self):
        with self.assertRaises(RejectRegistryError):
            LedgerRow(GATE_IDENTITY, "그냥", UNIT_REVIEW, "1")

    def test_게이트3_은_행을_만들_수_없다(self):
        with self.assertRaises(RejectRegistryError):
            LedgerRow(GATE_POLARITY, REJECT_PRODUCT, UNIT_REVIEW, "1")

    def test_미적용_사유도_행이_되지_않는다(self):
        with self.assertRaises(RejectRegistryError):
            LedgerRow(GATE_DUPLICATE, REJECT_DUPLICATE_SEMANTIC, UNIT_REVIEW, "1")

    def test_단위가_어긋나면_에러다(self):
        # 리뷰 탈락과 주장 탈락을 한 수로 더하는 경로를 막는다
        with self.assertRaises(LedgerError):
            LedgerRow(GATE_SUFFICIENCY, REJECT_INSUFFICIENT, UNIT_REVIEW, "c1")
        with self.assertRaises(LedgerError):
            LedgerRow(GATE_IDENTITY, REJECT_RECENCY, UNIT_CLAIM, "1")

    def test_무엇이_탈락했는지가_비면_에러다(self):
        with self.assertRaises(LedgerError):
            LedgerRow(GATE_IDENTITY, REJECT_RECENCY, UNIT_REVIEW, "   ")

    def test_행에_한글_표기가_붙는다(self):
        row = LedgerRow(GATE_IDENTITY, REJECT_RECENCY, UNIT_REVIEW, "7")
        self.assertEqual(row.label, "기간초과")
        self.assertEqual(row.as_dict()["gateOrder"], 1)


class OneSubjectStopsOnce(unittest.TestCase):
    """리뷰는 한 번만 멈춘다 — 두 행이면 '어디서 멈췄나'의 답이 둘이 된다."""

    def test_같은_리뷰를_두_번_기록하면_에러다(self):
        ledger = RejectedLedger()
        ledger.add(LedgerRow(GATE_IDENTITY, REJECT_RECENCY, UNIT_REVIEW, "1"))
        with self.assertRaises(LedgerError):
            ledger.add(LedgerRow(GATE_DUPLICATE, REJECT_SAME_AUTHOR, UNIT_REVIEW, "1"))

    def test_에러가_먼저_멈춘_게이트를_알려준다(self):
        ledger = RejectedLedger()
        ledger.add(LedgerRow(GATE_IDENTITY, REJECT_RECENCY, UNIT_REVIEW, "1"))
        with self.assertRaises(LedgerError) as caught:
            ledger.add(LedgerRow(GATE_DUPLICATE, REJECT_SAME_AUTHOR, UNIT_REVIEW, "1"))
        self.assertIn(GATE_IDENTITY, str(caught.exception))

    def test_단위가_다르면_같은_이름이어도_다른_대상이다(self):
        ledger = RejectedLedger()
        ledger.add(LedgerRow(GATE_IDENTITY, REJECT_RECENCY, UNIT_REVIEW, "1"))
        ledger.add(LedgerRow(GATE_SUFFICIENCY, REJECT_INSUFFICIENT, UNIT_CLAIM, "1"))
        self.assertEqual(len(ledger.rows), 2)


class LedgerAnswersWhereItStopped(unittest.TestCase):
    """완료 조건 A — 한 줄로 되짚는다."""

    def setUp(self):
        self.ledger = RejectedLedger()
        records = [
            review(1, date="2020-01-01"),           # 기간초과
            review(2, author="A"),
            review(3, author="A"),                  # 동일작성자
            review(4, product_id="p002"),           # 제품불일치
        ]
        self.kept, _, self.counts = run_review_gates(
            records, IdentityScope("p001"), CATALOG, self.ledger)

    def test_탈락_리뷰가_게이트와_사유를_말한다(self):
        self.assertEqual(self.ledger.stopped_at(1).reason, REJECT_RECENCY)
        self.assertEqual(self.ledger.stopped_at(1).gate, GATE_IDENTITY)
        self.assertEqual(self.ledger.stopped_at(3).reason, REJECT_SAME_AUTHOR)
        self.assertEqual(self.ledger.stopped_at(3).gate, GATE_DUPLICATE)
        self.assertEqual(self.ledger.stopped_at(4).reason, REJECT_PRODUCT)

    def test_통과한_리뷰는_원장에_없다(self):
        self.assertIsNone(self.ledger.stopped_at(2))
        self.assertEqual([r["reviewId"] for r in self.kept], [2])

    def test_입력이_통과와_탈락으로_모두_설명된다(self):
        self.assertEqual(len(self.kept) + len(self.ledger.rows), 4)

    def test_단계별_수를_원장에서_역산하지_않는다(self):
        self.assertEqual(self.counts, {"reviews": 4, "afterGate1": 2, "afterGate2": 1})

    def test_게이트별_사유_집계가_난다(self):
        self.assertEqual(
            self.ledger.by_gate_reason(),
            {
                GATE_IDENTITY: {REJECT_PRODUCT: 1, REJECT_RECENCY: 1},
                GATE_DUPLICATE: {REJECT_SAME_AUTHOR: 1},
            },
        )

    def test_리뷰_행과_주장_행을_서로_더하지_않는다(self):
        payload = self.ledger.as_dict()
        self.assertEqual(payload["reviewRows"], 3)
        self.assertEqual(payload["claimRows"], 0)


class FourGatesInOrder(unittest.TestCase):
    """완료 조건 A — 게이트 1~4 **전부**가 원장에 남는다."""

    def setUp(self):
        records = corpus(12) + [
            review(90, date="2019-01-01"),            # 게이트1 기간초과
            review(91, author="u1"),                  # 게이트2 동일작성자
        ]
        tags = tags_of(corpus(12)[:2])                # 12명 중 2명만 말했다 → U=2 < N_min
        self.run = run_gates(records, by_review(tags), CATALOG)

    def test_네_게이트_중_탈락을_내는_셋이_원장에_있다(self):
        gates = set(self.run.ledger.by_gate())
        self.assertEqual(gates, {GATE_IDENTITY, GATE_DUPLICATE, GATE_SUFFICIENCY})

    def test_게이트3_은_원장에_행이_없다(self):
        # 부정 근거는 틀린 근거가 아니다 (PER-185 §1)
        self.assertNotIn(GATE_POLARITY, self.run.ledger.by_gate())

    def test_주장_탈락이_수치를_달고_남는다(self):
        row = self.run.ledger.stopped_at(
            claim_id("p001", "product", None, ASPECT), UNIT_CLAIM)
        self.assertEqual(row.reason, REJECT_INSUFFICIENT)
        self.assertEqual(row.metrics["supportAuthors"], 2)
        self.assertEqual(row.metrics["cellAuthors"], 12)
        self.assertIn("N_min", row.detail)

    def test_게이트1_에서_컷된_리뷰는_셀에_들어가지_않는다(self):
        self.assertNotIn(90, {r["reviewId"] for r in self.run.kept["p001"]})
        self.assertNotIn(91, {r["reviewId"] for r in self.run.kept["p001"]})

    def test_한계가_단위와_함께_센다(self):
        # renewal_unobserved 는 리뷰에 붙고 single_dissent 는 주장에 붙는다
        limits = self.run.limitations
        self.assertEqual(limits["renewal_unobserved"]["unit"], UNIT_REVIEW)
        self.assertEqual(limits["renewal_unobserved"]["count"], 12)


class CandidateClaimsNeedAMention(unittest.TestCase):
    """후보는 D ≥ 1 인 (셀 × aspect) 다 — 아무도 말하지 않은 주제는 주장이 아니다."""

    def test_태그가_없으면_후보가_없다(self):
        records = corpus(10)
        self.assertEqual(build_claims(records, "p001", {}), [])

    def test_말한_aspect_만_후보가_된다(self):
        records = corpus(10)
        claims = build_claims(records, "p001", by_review(tags_of(records)))
        self.assertEqual({c.support.aspect for c in claims}, {ASPECT})

    def test_미기재도_세그먼트다(self):
        records = [review(i, skin_type=None) for i in range(1, 11)]
        claims = build_claims(records, "p001", by_review(tags_of(records)))
        segments = {c.cell.condition.get("skinType") for c in claims}
        self.assertIn(MISSING_SEGMENT, segments)


# =============================================================================
# B. 역추적
# =============================================================================


class TracebackFindsTheGate(unittest.TestCase):
    """완료 조건 B — 골든셋 claim 을 원장으로 되짚는다."""

    def setUp(self):
        self.records = corpus(12)
        self.tags = tags_of(self.records)

    def _trace(self, target: ClaimTarget, records=None, tags=None):
        return trace_target(
            target, records if records is not None else self.records,
            tags if tags is not None else self.tags, CATALOG)

    def test_통과하면_파이프라인이_낸_주장이다(self):
        trace = self._trace(ClaimTarget("g1", "p001", ASPECT,
                                        evidence_review_ids=(1, 2, 3)))
        self.assertEqual(trace.outcome, OUTCOME_PRODUCED)
        self.assertEqual(trace.claim_id, claim_id("p001", "product", None, ASPECT))
        self.assertEqual(trace.metrics["supportAuthors"], 12)

    def test_근거가_모자라면_게이트4_사유가_붙는다(self):
        records = corpus(12)
        tags = tags_of(records[:3])          # 12명 중 3명만 말했다
        trace = self._trace(
            ClaimTarget("g2", "p001", ASPECT, evidence_review_ids=(1, 2, 3)),
            records, tags)
        self.assertEqual(trace.outcome, OUTCOME_REJECTED)
        self.assertEqual(trace.gate, GATE_SUFFICIENCY)
        self.assertEqual(trace.reason, REJECT_INSUFFICIENT)
        self.assertEqual(trace.label, "과소근거")
        self.assertIn("U=3", trace.detail)

    def test_셀이_작으면_세그먼트과소로_떨어진다(self):
        records = corpus(4)
        trace = self._trace(
            ClaimTarget("g3", "p001", ASPECT, evidence_review_ids=(1,)),
            records, tags_of(records))
        self.assertEqual(trace.outcome, OUTCOME_REJECTED)
        self.assertEqual(trace.label, "세그먼트과소")

    def test_아무도_말하지_않았으면_후보조차_아니다(self):
        trace = self._trace(
            ClaimTarget("g4", "p001", OTHER_ASPECT, evidence_review_ids=(1,)))
        self.assertEqual(trace.outcome, OUTCOME_NO_CANDIDATE)
        self.assertEqual(trace.reason, MISS_NO_MENTION)

    def test_게이트1_이_셀을_비우면_그_사실이_남는다(self):
        stale = [review(i, date="2019-05-05") for i in range(1, 13)]
        trace = self._trace(
            ClaimTarget("g5", "p001", ASPECT, evidence_review_ids=(1, 2)),
            stale, tags_of(stale))
        self.assertEqual(trace.outcome, OUTCOME_NO_CANDIDATE)
        self.assertEqual(trace.reason, MISS_EMPTY_CELL)
        self.assertEqual(trace.gate, GATE_IDENTITY)
        # 근거 리뷰가 어느 게이트에서 빠졌는지까지 남는다 — v4 에서 손으로 하던 일
        self.assertTrue(all(e.reason == REJECT_RECENCY for e in trace.evidence))

    def test_택소노미_밖_aspect_는_되짚을_수_없다고_적는다(self):
        for aspect in (None, "사용기간"):  # null 과 동결 14종 밖
            with self.subTest(aspect=aspect):
                trace = self._trace(
                    ClaimTarget("g6", "p001", aspect, evidence_review_ids=(1,)))
                self.assertEqual(trace.outcome, OUTCOME_UNTRACEABLE)
                self.assertEqual(trace.reason, MISS_NO_ASPECT)

    def test_조건부_주장은_그_세그먼트만_본다(self):
        records = [review(i, skin_type="A02") for i in range(1, 11)] + \
                  [review(i, skin_type="A04") for i in range(11, 21)]
        tags = tags_of(records)
        trace = self._trace(
            ClaimTarget("g7", "p001", ASPECT, condition={"skinType": "A04"},
                        evidence_review_ids=(11, 12)),
            records, tags)
        self.assertEqual(trace.outcome, OUTCOME_PRODUCED)
        self.assertEqual(trace.metrics["cellAuthors"], 10)
        self.assertEqual(trace.claim_id, claim_id("p001", "skinType", "A04", ASPECT))

    def test_방향이_다르면_통과해도_드러난다(self):
        records = corpus(12)
        trace = self._trace(
            ClaimTarget("g8", "p001", ASPECT, evidence_review_ids=(1,),
                        direction="negative"),
            records, tags_of(records, "positive"))
        self.assertEqual(trace.outcome, OUTCOME_PRODUCED)
        self.assertEqual(trace.pipeline_direction, "positive")
        self.assertIs(trace.direction_match, False)

    def test_근거_리뷰의_운명이_항상_남는다(self):
        records = corpus(12) + [review(99, author="u1")]   # 동일작성자로 빠진다
        trace = self._trace(
            ClaimTarget("g9", "p001", ASPECT, evidence_review_ids=(1, 99)),
            records, tags_of(corpus(12)))
        fates = {e.review_id: e for e in trace.evidence}
        self.assertTrue(fates[1].passed)
        self.assertFalse(fates[99].passed)
        self.assertEqual(fates[99].reason, REJECT_SAME_AUTHOR)
        self.assertEqual(trace.evidence_lost, 1)


class OptionScopeIsGate1(unittest.TestCase):
    """옵션 동일성은 게이트4 의 셀이 아니라 게이트1 이 소유한다 (PER-182)."""

    def setUp(self):
        self.records = (
            [review(i, option="21N1") for i in range(1, 11)]
            + [review(i, option="17N1") for i in range(11, 21)]
        )
        self.tags = tags_of(self.records)
        self.index = OptionIndex.from_forms(
            {"p001": collections.Counter({"21N1": 10, "17N1": 10})})

    def test_옵션_범위를_걸면_그_색상만_셀이_된다(self):
        trace = trace_target(
            ClaimTarget("o1", "p001", ASPECT, option_scope="21N1",
                        evidence_review_ids=(1,)),
            self.records, self.tags, CATALOG, option_index=self.index)
        self.assertEqual(trace.outcome, OUTCOME_PRODUCED)
        self.assertEqual(trace.metrics["cellAuthors"], 10)

    def test_읽을_수_없는_옵션은_추측하지_않고_남긴다(self):
        trace = trace_target(
            ClaimTarget("o2", "p001", ASPECT, option_scope="없는색",
                        evidence_review_ids=(1,)),
            self.records, self.tags, CATALOG, option_index=self.index)
        self.assertEqual(trace.outcome, OUTCOME_UNTRACEABLE)
        self.assertEqual(trace.reason, MISS_UNKNOWN_OPTION)

    def test_어휘가_없으면_에러다(self):
        with self.assertRaises(LedgerError):
            trace_target(
                ClaimTarget("o3", "p001", ASPECT, option_scope="21N1"),
                self.records, self.tags, CATALOG)


class RecallIsCountedHonestly(unittest.TestCase):
    """완료 조건 B — 되짚을 수 없는 미스를 조용히 분모에서 빼지 않는다."""

    def setUp(self):
        records = corpus(12)
        tags = tags_of(records)
        self.traces = trace_all(
            [
                ClaimTarget("t1", "p001", ASPECT, evidence_review_ids=(1,)),       # produced
                ClaimTarget("t2", "p001", OTHER_ASPECT, evidence_review_ids=(1,)),  # no_candidate
                ClaimTarget("t3", "p001", None, evidence_review_ids=(1,)),          # untraceable
            ],
            records, tags, CATALOG)
        self.summary = recall_summary(self.traces)

    def test_모든_미스가_사유를_갖는다(self):
        self.assertTrue(self.summary["missedAllTraced"])

    def test_되짚을_수_없는_건은_분모에서_빠지되_수가_남는다(self):
        self.assertEqual(self.summary["targets"], 3)
        self.assertEqual(self.summary["traceable"], 2)
        self.assertEqual(self.summary["byOutcome"][OUTCOME_UNTRACEABLE], 1)

    def test_재현율은_되짚을_수_있는_것의_몫이다(self):
        self.assertEqual(self.summary["produced"], 1)
        self.assertEqual(self.summary["missed"], 1)
        self.assertEqual(self.summary["recall"], 0.5)

    def test_미스가_게이트_사유별로_모인다(self):
        self.assertEqual(
            self.summary["missByGateReason"], {f"{GATE_POLARITY}/{MISS_NO_MENTION}": 1})


class ThresholdsMoveTheLedger(unittest.TestCase):
    """임계값을 바꾸면 되살아나는 주장이 원장에서 읽힌다 (PER-199 입력)."""

    def test_N_min_을_내리면_과소근거가_통과로_바뀐다(self):
        records = corpus(12)
        tags = tags_of(records[:3])
        target = ClaimTarget("s1", "p001", ASPECT, evidence_review_ids=(1,))
        strict = trace_target(target, records, tags, CATALOG)
        loose = trace_target(target, records, tags, CATALOG,
                             policy=SufficiencyPolicy(n_min=3, r_min=0.1, s_min=8))
        self.assertEqual(strict.reason, REJECT_INSUFFICIENT)
        self.assertEqual(loose.outcome, OUTCOME_PRODUCED)
        # 탈락 행에 수치가 붙어 있어 리포트를 다시 돌리지 않고도 읽힌다
        self.assertEqual(strict.metrics["supportAuthors"], 3)


if __name__ == "__main__":
    unittest.main()
