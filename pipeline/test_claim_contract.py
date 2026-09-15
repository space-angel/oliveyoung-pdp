"""claim 출력 계약 (PER-189).

"에러를 낸다"는 완료 조건은 테스트로 고정한다 (CLAUDE.md). 이 파일이 고정하는 것은
**무엇이 claim 을 못 만들게 하는가**다 — 특히 `evidence[]` 가 비면 객체 자체가
만들어지지 않는다는 것(PRD §1.2)과, 모델이 `support`·`direction` 을 쓰면 에러라는 것
(PER-178·PER-185).
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from reject_registry import RejectRegistryError  # noqa: E402
from claim_contract import (  # noqa: E402
    CLAIM_SCHEMA_VERSION,
    Claim,
    ClaimContractError,
    ClaimEvidence,
    ConfidencePolicy,
    assert_model_draft,
    attempt_claim,
    validate_claim,
)


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


class EvidenceIsStructural(unittest.TestCase):
    """근거 없이는 객체가 존재할 수 없다 — 검증에서 걸러내는 게 아니다."""

    def test_evidence_가_비면_객체가_안_만들어진다(self):
        with self.assertRaises(ClaimContractError) as cm:
            Claim(claim_id="c1", product_id="p002", aspect="보습감", question="q", answer="a",
                  condition={}, direction="positive", evidence=(), support=support())
        self.assertIn("evidence 가 비었다", str(cm.exception))

    def test_직렬화된_claim_도_마찬가지다(self):
        with self.assertRaises(ClaimContractError):
            validate_claim(payload(evidence=[]))

    def test_같은_리뷰를_두_번_세지_않는다(self):
        with self.assertRaises(ClaimContractError) as cm:
            validate_claim(payload(evidence=[
                {"reviewId": 1, "quote": "촉촉해서 좋아요", "stance": "positive"},
                {"reviewId": 1, "quote": "촉촉해서 좋아요", "stance": "positive"},
            ]))
        self.assertIn("두 번", str(cm.exception))


class ModelDoesNotWriteNumbers(unittest.TestCase):
    """support 수치와 방향은 코드가 채운다 (PER-178 · PER-185)."""

    def test_초안에_support_가_있으면_에러다(self):
        with self.assertRaises(ClaimContractError) as cm:
            assert_model_draft({
                "aspect": "보습감", "question": "q", "answer": "a",
                "evidence": [{"reviewId": 1, "quote": "촉촉", "stance": "positive"}],
                "support": support(),
            })
        self.assertIn("support", str(cm.exception))

    def test_초안에_direction_이_있으면_에러다(self):
        with self.assertRaises(ClaimContractError):
            assert_model_draft({
                "aspect": "보습감", "question": "q", "answer": "a", "direction": "positive",
                "evidence": [{"reviewId": 1, "quote": "촉촉", "stance": "positive"}],
            })

    def test_초안의_evidence_가_비면_에러다(self):
        with self.assertRaises(ClaimContractError):
            assert_model_draft({"aspect": "보습감", "question": "q", "answer": "a", "evidence": []})

    def test_제_몫만_담으면_통과한다(self):
        assert_model_draft({
            "aspect": "보습감", "question": "q", "answer": "a",
            "evidence": [{"reviewId": 1, "quote": "촉촉", "stance": "positive"}],
        })


class SupportIsCounted(unittest.TestCase):
    def test_D_가_긍부중_합과_다르면_에러다(self):
        bad = support(pos=9)
        bad["spokeAuthors"] = 20
        with self.assertRaises(ClaimContractError) as cm:
            validate_claim(payload(support=bad))
        self.assertIn("spokeAuthors", str(cm.exception))

    def test_U_가_D_보다_크면_에러다(self):
        bad = support(pos=3)
        bad["supportAuthors"] = 99
        with self.assertRaises(ClaimContractError) as cm:
            validate_claim(payload(support=bad))
        self.assertIn("부분집합", str(cm.exception))

    def test_silentAuthors_가_없으면_에러다(self):
        bad = support()
        del bad["silentAuthors"]
        with self.assertRaises(ClaimContractError) as cm:
            validate_claim(payload(support=bad))
        self.assertIn("silentAuthors", str(cm.exception))


class ConditionAxes(unittest.TestCase):
    def test_usagePeriod_에_값을_넣으면_에러다(self):
        cond = {"skinType": None, "skinTrouble": None, "option": None, "usagePeriod": "1개월+"}
        with self.assertRaises(ClaimContractError) as cm:
            validate_claim(payload(condition=cond))
        self.assertIn("조건축이 아니다", str(cm.exception))

    def test_코드북_밖_코드는_에러다(self):
        cond = {"skinType": ["A99"], "skinTrouble": None, "option": None, "usagePeriod": None}
        with self.assertRaises(ClaimContractError):
            validate_claim(payload(condition=cond))

    def test_라벨을_코드_자리에_쓰면_에러다(self):
        cond = {"skinType": ["건성"], "skinTrouble": None, "option": None, "usagePeriod": None}
        with self.assertRaises(ClaimContractError):
            validate_claim(payload(condition=cond))

    def test_축을_섞으면_에러다(self):
        cond = {"skinType": ["C02"], "skinTrouble": None, "option": None, "usagePeriod": None}
        with self.assertRaises(ClaimContractError):
            validate_claim(payload(condition=cond))

    def test_문자열_하나를_주면_에러다(self):
        """글자 단위로 쪼개져 조용히 다른 세그먼트가 되는 것을 막는다."""
        cond = {"skinType": "A02", "skinTrouble": None, "option": None, "usagePeriod": None}
        with self.assertRaises(ClaimContractError) as cm:
            validate_claim(payload(condition=cond))
        self.assertIn("배열", str(cm.exception))

    def test_claimType_은_조건에서_파생된다(self):
        self.assertEqual(validate_claim(payload()).claim_type, "unconditional")
        cond = {"skinType": ["A02"], "skinTrouble": None, "option": None, "usagePeriod": None}
        self.assertEqual(validate_claim(payload(condition=cond)).claim_type, "conditional")

    def test_claimType_을_손으로_다르게_쓰면_에러다(self):
        with self.assertRaises(ClaimContractError) as cm:
            validate_claim(payload(claimType="conditional"))
        self.assertIn("파생값", str(cm.exception))


class FailureReasonIsTheFilter(unittest.TestCase):
    """`failureReason` 이 그대로 노출 필터다 — null 인 것만 나간다."""

    def test_실패가_없으면_노출된다(self):
        self.assertTrue(validate_claim(payload()).exposable)

    def test_실패가_있으면_노출되지_않는다(self):
        claim = validate_claim(payload(
            failureReasons=["unsupported_claim"], failureReason="unsupported_claim"))
        self.assertFalse(claim.exposable)

    def test_택소노미_밖_유형은_에러다(self):
        with self.assertRaises(ClaimContractError) as cm:
            validate_claim(payload(failureReasons=["왠지_아니어서"], failureReason="왠지_아니어서"))
        self.assertIn("택소노미", str(cm.exception))

    def test_심각도_순이_아니면_에러다(self):
        with self.assertRaises(ClaimContractError) as cm:
            validate_claim(payload(
                failureReasons=["duplicate_claim", "unsupported_claim"],
                failureReason="duplicate_claim"))
        self.assertIn("심각도", str(cm.exception))

    def test_대표_사유가_첫_원소가_아니면_에러다(self):
        with self.assertRaises(ClaimContractError) as cm:
            validate_claim(payload(
                failureReasons=["unsupported_claim", "duplicate_claim"],
                failureReason="duplicate_claim"))
        self.assertIn("첫 원소", str(cm.exception))


class QuotesAreVerbatim(unittest.TestCase):
    """인용은 **그 리뷰의** 원문 부분문자열이어야 한다."""

    REVIEWS = {1: "정말 촉촉해서 좋아요\r\n하루종일 당김이 없어요", 2: "향이 강해요"}

    def test_원문_부분문자열이면_통과한다(self):
        validate_claim(payload(), reviews=self.REVIEWS)

    def test_CRLF_는_접어서_통과시킨다(self):
        """원문에 CRLF 46.3% 가 섞여 있어 안 접으면 정상 인용이 탈락한다 (PER-175)."""
        validate_claim(payload(evidence=[
            {"reviewId": 1, "quote": "좋아요\n하루종일", "stance": "positive"}]),
            reviews=self.REVIEWS)

    def test_원문에_없으면_에러다(self):
        with self.assertRaises(ClaimContractError) as cm:
            validate_claim(payload(evidence=[
                {"reviewId": 1, "quote": "14시간 지속됩니다", "stance": "positive"}]),
                reviews=self.REVIEWS)
        self.assertIn("부분문자열이 아니다", str(cm.exception))

    def test_다른_리뷰의_문장이면_에러다(self):
        """misattribution — 인용만 보면 통과하므로 reviewId 와 짝지어 봐야 잡힌다."""
        with self.assertRaises(ClaimContractError):
            validate_claim(payload(evidence=[
                {"reviewId": 2, "quote": "정말 촉촉해서 좋아요", "stance": "positive"}]),
                reviews=self.REVIEWS)

    def test_띄어쓰기를_지운_편집은_탈락한다(self):
        """공백 전체 squeeze 를 하지 않는 이유 (PER-175)."""
        with self.assertRaises(ClaimContractError):
            validate_claim(payload(evidence=[
                {"reviewId": 1, "quote": "정말촉촉해서좋아요", "stance": "positive"}]),
                reviews=self.REVIEWS)

    def test_코퍼스에_없는_리뷰면_에러다(self):
        with self.assertRaises(ClaimContractError) as cm:
            validate_claim(payload(evidence=[
                {"reviewId": 999, "quote": "촉촉", "stance": "positive"}]),
                reviews=self.REVIEWS)
        self.assertIn("코퍼스에 없다", str(cm.exception))


class StanceIsAbsolute(unittest.TestCase):
    def test_모르는_stance_는_에러다(self):
        with self.assertRaises(ClaimContractError) as cm:
            ClaimEvidence(1, "촉촉", "찬성")
        self.assertIn("절대 긍/부정", str(cm.exception))


class ConfidenceIsTone(unittest.TestCase):
    """컷이 아니라 말투다. 왜 완곡해졌는지가 같이 나온다."""

    def test_근거가_충분하고_한방향이면_단정한다(self):
        conf = validate_claim(payload(support=support(pos=12, silent=3))).confidence
        self.assertEqual(conf["band"], "assertive")
        self.assertEqual(conf["reasons"], [])

    def test_방향이_갈리면_완곡해진다(self):
        conf = validate_claim(payload(
            direction="mixed", support=support(pos=10, neg=4, silent=2))).confidence
        self.assertEqual(conf["band"], "hedged")
        self.assertIn("direction_mixed", conf["reasons"])

    def test_침묵이_과반이면_완곡해진다(self):
        conf = validate_claim(payload(support=support(pos=12, silent=40))).confidence
        self.assertIn("silence_dominates", conf["reasons"])

    def test_말한_사람이_적으면_완곡해진다(self):
        conf = validate_claim(payload(support=support(pos=3, silent=1))).confidence
        self.assertIn("few_authors", conf["reasons"])

    def test_게이트_한계가_있으면_완곡해진다(self):
        conf = validate_claim(payload(
            support=support(pos=12, silent=3), limitations=["single_dissent"])).confidence
        self.assertIn("gate_limitation", conf["reasons"])

    def test_정책은_갈아끼울_수_있다(self):
        loose = ConfidencePolicy(silent_ratio_hedge=0.99, min_assertive_authors=1)
        conf = validate_claim(payload(support=support(pos=3, silent=1)), policy=loose).confidence
        self.assertEqual(conf["band"], "assertive")

    def test_조건을_조용히_끄는_정책은_에러다(self):
        with self.assertRaises(ClaimContractError):
            ConfidencePolicy(silent_ratio_hedge=0.0)
        with self.assertRaises(ClaimContractError):
            ConfidencePolicy(min_assertive_authors=0)


class SchemaBoundary(unittest.TestCase):
    def test_모르는_필드는_에러다(self):
        with self.assertRaises(ClaimContractError) as cm:
            validate_claim(payload(surprise="!"))
        self.assertIn("모르는 필드", str(cm.exception))

    def test_스키마_버전이_다르면_에러다(self):
        with self.assertRaises(ClaimContractError) as cm:
            validate_claim(payload(schemaVersion="claim-v0"))
        self.assertIn("schemaVersion", str(cm.exception))

    def test_rejected_의_사유는_그_게이트의_것이어야_한다(self):
        with self.assertRaises(RejectRegistryError) as cm:
            validate_claim(payload(rejected=[{"gate": "duplicate", "reason": "product_mismatch"}]))
        self.assertIn("identity", str(cm.exception))

    def test_게이트3_으로는_탈락시킬_수_없다(self):
        """부정 근거는 틀린 근거가 아니다 (PER-185 §1 · PER-188)."""
        with self.assertRaises(RejectRegistryError) as cm:
            validate_claim(payload(rejected=[{"gate": "polarity", "reason": "insufficient_support"}]))
        self.assertIn("polarity", str(cm.exception))

    def test_미적용_사유로는_탈락시킬_수_없다(self):
        """의미중복은 PER-184 가 채울 자리다 — 지금 쓰면 사유 분포가 거짓이 된다."""
        with self.assertRaises(RejectRegistryError) as cm:
            validate_claim(payload(rejected=[{"gate": "duplicate", "reason": "duplicate_semantic"}]))
        self.assertIn("reserved", str(cm.exception))


class RetryThenDiscard(unittest.TestCase):
    """검증 실패는 폐기 + 로그, 재시도 2회까지 (PRD §5-3)."""

    def test_두_번까지_다시_부르고_버린다(self):
        calls, logs = [], []
        def produce(attempt):
            calls.append(attempt)
            return payload(evidence=[])          # 항상 실패
        result = attempt_claim(produce, log=logs.append)
        self.assertIsNone(result)
        self.assertEqual(calls, [0, 1, 2])        # 최초 1회 + 재시도 2회
        self.assertEqual(len(logs), 4)            # 폐기 3줄 + 마지막 1줄
        self.assertIn("생성하지 않는다", logs[-1])

    def test_중간에_성공하면_더_부르지_않는다(self):
        calls = []
        def produce(attempt):
            calls.append(attempt)
            return payload(evidence=[]) if attempt == 0 else payload()
        result = attempt_claim(produce, log=lambda _m: None)
        self.assertIsNotNone(result)
        self.assertEqual(calls, [0, 1])

    def test_버린_사실이_로그에_남는다(self):
        logs = []
        attempt_claim(lambda _a: payload(evidence=[]), log=logs.append)
        self.assertTrue(any("폐기" in line for line in logs))


if __name__ == "__main__":
    unittest.main()
