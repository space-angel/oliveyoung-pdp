"""인용 원문성 게이트 (PER-190).

"에러를 낸다"는 완료 조건은 테스트로 고정한다 (CLAUDE.md). 이 파일이 고정하는 것은
세 가지다.

1. **무엇이 통과하고 무엇이 안 되는가** — 보이지 않는 문자는 접고, 띄어쓰기 편집과
   재구성은 버린다. 공백 전체 squeeze 를 통과 기준으로 쓰지 않는다
2. **실패했을 때 무슨 일이 일어나는가** — 인용 폐기 → 다른 근거로 대체 → 그래도
   모자라면 주장 폐기. 세 단계가 전부 원장과 로그에 남는다
3. **게이트의 통과 기준이 `claim_contract` 와 같은가** — 갈리면 게이트를 통과한 claim 이
   계약 검증에서 터진다. 양방향으로 고정한다
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from claim_contract import (  # noqa: E402
    CLAIM_SCHEMA_VERSION,
    ClaimContractError,
    assert_quotes_verbatim,
    validate_claim,
)
from quote_gate import (  # noqa: E402
    OUTCOME_CLEAN,
    OUTCOME_DISCARDED,
    OUTCOME_REDUCED,
    OUTCOME_SUBSTITUTED,
    PASSING_STATUSES,
    QUOTE_GATE,
    REASON_MISATTRIBUTED,
    REASON_NOT_VERBATIM,
    REASON_NO_EVIDENCE_LEFT,
    STATUS_FABRICATED,
    STATUS_LINEBREAK_ONLY,
    STATUS_MISATTRIBUTED,
    STATUS_SPACING_EDITED,
    STATUS_UNKNOWN_REVIEW,
    STATUS_VERBATIM,
    QuoteGateError,
    QuoteGatePolicy,
    classify_quote,
    enforce_evidence,
    gate_claim_payload,
    registry_entries,
    summarize,
)

REVIEWS = {
    1: "촉촉해서 좋아요\r\n다음에도 살게요",
    2: "지속력은 별로였어요",
    3: "발색이 예쁘고 오래가요",
    4: "향이 강해서 부담스러웠어요",
}


def support(pos=9, neg=0, neu=0, silent=3):
    spoke = pos + neg + neu
    return {
        "positiveAuthors": pos, "negativeAuthors": neg, "neutralAuthors": neu,
        "spokeAuthors": spoke, "silentAuthors": silent,
        "supportAuthors": pos + neg,
    }


def payload(**over):
    base = {
        "schemaVersion": CLAIM_SCHEMA_VERSION,
        "claimId": "p002|product=None|보습감",
        "productId": "p002",
        "aspect": "보습감",
        "question": "바르고 나서 입술이 건조해지지 않나요?",
        "answer": "촉촉하다는 리뷰가 있다",
        "condition": {"skinType": None, "skinTrouble": None, "option": None, "usagePeriod": None},
        "direction": "positive",
        "evidence": [{"reviewId": 1, "quote": "촉촉해서 좋아요", "stance": "positive"}],
        "support": support(),
        "rejected": [],
        "failureReasons": [],
        "failureReason": None,
        "limitations": [],
        "meta": {},
    }
    base.update(over)
    return base


class NormalizationIsNamed(unittest.TestCase):
    """정규화 규칙 — 보이지 않는 문자만 접는다. 그 밖은 표기 차이라도 통과가 아니다."""

    def test_원문_부분문자열은_통과한다(self):
        self.assertEqual(classify_quote("촉촉해서 좋아요", 1, REVIEWS).status, STATUS_VERBATIM)

    def test_CRLF_는_접는다(self):
        # 원문 25,000건 중 46.3% 가 CRLF 다 (PER-175). 접지 않으면 정상 인용이 탈락한다
        self.assertEqual(
            classify_quote("좋아요\n다음에도", 1, REVIEWS).status, STATUS_VERBATIM)

    def test_비분리공백은_ASCII_공백으로_접는다(self):
        self.assertEqual(classify_quote("지속력은 별로였어요", 2, REVIEWS).status,
                         STATUS_VERBATIM)

    def test_폭없는공백은_제거한다(self):
        self.assertEqual(classify_quote("촉촉해서​ 좋아요", 1, REVIEWS).status,
                         STATUS_VERBATIM)

    def test_줄바꿈을_공백으로_옮겨_적으면_통과가_아니다(self):
        # 표기 차이로 보이지만 통과시키지 않는다 — fold_invisible 을 넓히는 것은 태깅
        # 입력·중복 판정이 같이 쓰는 함수를 바꾸는 일이라 이 이슈에서 하지 않는다
        verdict = classify_quote("좋아요 다음에도", 1, REVIEWS)
        self.assertEqual(verdict.status, STATUS_LINEBREAK_ONLY)
        self.assertFalse(verdict.ok)

    def test_띄어쓰기를_지운_편집은_통과가_아니다(self):
        # v4 는 공백을 전부 squeeze 해서 이걸 통과시켰다 (legacy/v4/eval/eval_v4.py)
        verdict = classify_quote("발색이예쁘고오래가요", 3, REVIEWS)
        self.assertEqual(verdict.status, STATUS_SPACING_EDITED)
        self.assertFalse(verdict.ok)

    def test_통과_등급은_하나뿐이다(self):
        self.assertEqual(PASSING_STATUSES, (STATUS_VERBATIM,))


class WrongSourceIsCaught(unittest.TestCase):
    """인용만 보면 통과하는 것 — reviewId 와 짝지어야 잡힌다."""

    def test_다른_리뷰의_문장이면_출처불일치다(self):
        verdict = classify_quote("발색이 예쁘고 오래가요", 1, REVIEWS)
        self.assertEqual(verdict.status, STATUS_MISATTRIBUTED)
        self.assertEqual(verdict.matched_review_id, 3)
        self.assertEqual(verdict.reason, REASON_MISATTRIBUTED)

    def test_코퍼스_어디에도_없으면_재구성이다(self):
        verdict = classify_quote("14시간 넘게 있는데 수정화장 안함", 1, REVIEWS)
        self.assertEqual(verdict.status, STATUS_FABRICATED)
        self.assertEqual(verdict.reason, REASON_NOT_VERBATIM)

    def test_코퍼스에_없는_리뷰를_가리키면_통과가_아니다(self):
        self.assertEqual(classify_quote("촉촉해서 좋아요", 999, REVIEWS).status,
                         STATUS_UNKNOWN_REVIEW)

    def test_빈_인용은_에러다(self):
        with self.assertRaises(QuoteGateError):
            classify_quote("   ", 1, REVIEWS)


class FailedQuoteIsDiscarded(unittest.TestCase):
    """실패한 인용은 고치지 않고 버린다. 버린 사실이 남는다."""

    def test_통과분만_남는다(self):
        result = enforce_evidence("c1", [
            {"reviewId": 1, "quote": "촉촉해서 좋아요", "stance": "positive"},
            {"reviewId": 3, "quote": "지어낸 문장입니다", "stance": "positive"},
        ], REVIEWS)
        self.assertEqual(result.outcome, OUTCOME_REDUCED)
        self.assertEqual([e["reviewId"] for e in result.kept], [1])
        self.assertEqual(len(result.dropped), 1)

    def test_전부_통과하면_clean_이다(self):
        result = enforce_evidence("c1", [
            {"reviewId": 1, "quote": "촉촉해서 좋아요", "stance": "positive"},
        ], REVIEWS)
        self.assertEqual(result.outcome, OUTCOME_CLEAN)
        self.assertEqual(result.ledger, ())

    def test_폐기가_원장과_로그에_남는다(self):
        lines = []
        result = enforce_evidence("c1", [
            {"reviewId": 1, "quote": "촉촉해서 좋아요", "stance": "positive"},
            {"reviewId": 3, "quote": "지어낸 문장입니다", "stance": "positive"},
        ], REVIEWS, log=lines.append)
        row = result.ledger[0]
        self.assertEqual(row["gate"], QUOTE_GATE)
        self.assertEqual(row["reason"], REASON_NOT_VERBATIM)
        self.assertEqual(row["reviewId"], 3)
        self.assertTrue(any("인용 폐기" in line for line in lines))
        self.assertEqual(tuple(lines), result.log)

    def test_인용을_고쳐_끼우지_않는다(self):
        # 가장 비슷한 원문으로 갈아끼우면 모델이 하려던 말과 다른 문장이 근거가 된다
        result = enforce_evidence("c1", [
            {"reviewId": 3, "quote": "발색이예쁘고오래가요", "stance": "positive"},
        ], REVIEWS, policy=QuoteGatePolicy(min_evidence=1))
        self.assertEqual(result.outcome, OUTCOME_DISCARDED)
        self.assertEqual(result.kept, ())


class SubstitutionThenDiscard(unittest.TestCase):
    """대체 → 그래도 모자라면 주장 폐기. 순서가 완료 조건이다."""

    def test_다른_근거로_대체한다(self):
        result = enforce_evidence(
            "c1",
            [{"reviewId": 1, "quote": "지어낸 문장입니다", "stance": "positive"}],
            REVIEWS,
            alternates=[{"reviewId": 3, "quote": "발색이 예쁘고", "stance": "positive"}],
        )
        self.assertEqual(result.outcome, OUTCOME_SUBSTITUTED)
        self.assertEqual([e["reviewId"] for e in result.kept], [3])
        self.assertEqual(len(result.substituted), 1)

    def test_대체_후보도_같은_게이트를_통과해야_한다(self):
        result = enforce_evidence(
            "c1",
            [{"reviewId": 1, "quote": "지어낸 문장입니다", "stance": "positive"}],
            REVIEWS,
            alternates=[{"reviewId": 3, "quote": "이것도 지어냈습니다", "stance": "positive"}],
        )
        self.assertEqual(result.outcome, OUTCOME_DISCARDED)
        self.assertEqual(len(result.dropped), 2)

    def test_이미_쓴_리뷰로는_대체하지_않는다(self):
        # 한 리뷰는 한 번이다 (PER-170 · claim_contract 의 중복 근거 금지)
        result = enforce_evidence(
            "c1",
            [{"reviewId": 1, "quote": "촉촉해서 좋아요", "stance": "positive"},
             {"reviewId": 3, "quote": "지어낸 문장입니다", "stance": "positive"}],
            REVIEWS,
            alternates=[{"reviewId": 1, "quote": "다음에도 살게요", "stance": "positive"},
                        {"reviewId": 4, "quote": "향이 강해서", "stance": "negative"}],
        )
        self.assertEqual([e["reviewId"] for e in result.kept], [1, 4])

    def test_근거가_소진되면_주장을_버린다(self):
        lines = []
        result = enforce_evidence(
            "c1",
            [{"reviewId": 1, "quote": "지어낸 문장입니다", "stance": "positive"}],
            REVIEWS, log=lines.append,
        )
        self.assertTrue(result.discarded)
        self.assertEqual(result.kept, ())
        self.assertEqual(result.ledger[-1]["reason"], REASON_NO_EVIDENCE_LEFT)
        self.assertEqual(result.ledger[-1]["unit"], "claim")
        self.assertTrue(any("주장 폐기" in line for line in lines))

    def test_min_evidence_를_못_채우면_남아도_버린다(self):
        result = enforce_evidence(
            "c1",
            [{"reviewId": 1, "quote": "촉촉해서 좋아요", "stance": "positive"},
             {"reviewId": 3, "quote": "지어낸 문장입니다", "stance": "positive"}],
            REVIEWS, policy=QuoteGatePolicy(min_evidence=2),
        )
        self.assertTrue(result.discarded)

    def test_대체_한도를_지킨다(self):
        result = enforce_evidence(
            "c1",
            [{"reviewId": 1, "quote": "지어낸 문장입니다", "stance": "positive"},
             {"reviewId": 2, "quote": "이것도 지어냈습니다", "stance": "positive"}],
            REVIEWS,
            alternates=[{"reviewId": 3, "quote": "발색이 예쁘고", "stance": "positive"},
                        {"reviewId": 4, "quote": "향이 강해서", "stance": "negative"}],
            policy=QuoteGatePolicy(min_evidence=1, max_substitutions=1),
        )
        self.assertEqual(len(result.substituted), 1)

    def test_근거가_비면_게이트_이전에_에러다(self):
        with self.assertRaises(QuoteGateError):
            enforce_evidence("c1", [], REVIEWS)


class PolicyCannotBeSilentlyDisabled(unittest.TestCase):
    """조건을 조용히 끄는 설정은 에러다 (`SufficiencyPolicy` 와 같은 규칙)."""

    def test_min_evidence_0_은_에러다(self):
        with self.assertRaises(QuoteGateError):
            QuoteGatePolicy(min_evidence=0)

    def test_음수_대체한도는_에러다(self):
        with self.assertRaises(QuoteGateError):
            QuoteGatePolicy(max_substitutions=-1)


class GateMatchesClaimContract(unittest.TestCase):
    """게이트의 통과 기준이 `claim_contract` 와 같은가. **양방향으로 본다.**

    갈리면 둘 중 하나가 일어난다 — 게이트를 통과한 claim 이 계약 검증에서 터지거나,
    계약이 잡을 것을 게이트가 먼저 흘려보낸다. 어느 쪽이든 "생성 시점 강제"가 거짓이 된다.
    """

    CASES = [
        (1, "촉촉해서 좋아요"),          # 그대로
        (1, "좋아요\n다음에도"),          # CRLF
        (2, "지속력은 별로였어요"),       # NBSP
        (1, "좋아요 다음에도"),           # 줄바꿈 표기차
        (3, "발색이예쁘고오래가요"),      # 띄어쓰기 편집
        (1, "발색이 예쁘고 오래가요"),    # 출처불일치
        (1, "14시간 넘게 있는데"),        # 재구성
    ]

    def test_두_판정이_같다(self):
        for review_id, quote in self.CASES:
            with self.subTest(quote=quote):
                gate_ok = classify_quote(quote, review_id, REVIEWS).ok
                claim = validate_claim(payload(
                    evidence=[{"reviewId": review_id, "quote": quote, "stance": "positive"}]))
                try:
                    assert_quotes_verbatim(claim, REVIEWS)
                    contract_ok = True
                except ClaimContractError:
                    contract_ok = False
                self.assertEqual(gate_ok, contract_ok)

    def test_게이트를_통과한_payload_는_계약도_통과한다(self):
        gated, result = gate_claim_payload(payload(evidence=[
            {"reviewId": 1, "quote": "촉촉해서 좋아요", "stance": "positive"},
            {"reviewId": 3, "quote": "지어낸 문장입니다", "stance": "positive"},
        ]), REVIEWS)
        self.assertEqual(result.outcome, OUTCOME_REDUCED)
        claim = validate_claim(gated, reviews=REVIEWS)
        self.assertEqual(len(claim.evidence), 1)

    def test_폐기되면_payload_가_None_이다(self):
        gated, result = gate_claim_payload(payload(evidence=[
            {"reviewId": 1, "quote": "지어낸 문장입니다", "stance": "positive"},
        ]), REVIEWS)
        self.assertIsNone(gated)
        self.assertTrue(result.discarded)


class SummaryAndRegistry(unittest.TestCase):
    def test_요약이_통과율을_센다(self):
        results = [
            enforce_evidence("c1", [{"reviewId": 1, "quote": "촉촉해서 좋아요",
                                     "stance": "positive"}], REVIEWS),
            enforce_evidence("c2", [{"reviewId": 1, "quote": "촉촉해서 좋아요",
                                     "stance": "positive"},
                                    {"reviewId": 3, "quote": "지어냈습니다",
                                     "stance": "positive"}], REVIEWS),
        ]
        summary = summarize(results)
        self.assertEqual(summary["quotesChecked"], 3)
        self.assertEqual(summary["quotesVerbatim"], 2)
        self.assertEqual(summary["verbatimPct"], 66.7)

    def test_레지스트리_미등록이_사실로_적혀_있다(self):
        entries = registry_entries()
        self.assertFalse(entries["registered"])
        self.assertEqual({r["code"] for r in entries["reasons"]},
                         {REASON_NOT_VERBATIM, REASON_MISATTRIBUTED, REASON_NO_EVIDENCE_LEFT})

    def test_미등록_사유로는_claim_rejected_행을_만들_수_없다(self):
        # 이게 맞는 동작이다 — 없는 어휘를 이 이슈에서 조용히 만들지 않는다 (PER-188)
        from reject_registry import RejectRegistryError, assert_rejectable
        with self.assertRaises(RejectRegistryError):
            assert_rejectable(QUOTE_GATE, REASON_NOT_VERBATIM)


if __name__ == "__main__":
    unittest.main()
