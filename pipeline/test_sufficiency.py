"""
게이트4(충분성, PER-186) 계약 테스트.

## 완료 조건

  1. 세 조건을 **모두** 만족해야 통과한다 — 낮은 쪽이 아니라 높은 쪽
  2. 분모는 D(언급한 작성자)다. 침묵(S−D)을 분모에도 근거에도 넣지 않는다 (PER-178)
  3. `N_min` / `R_min` / `S_min` 이 설정값이고 `meta` 에 기록된다
  4. 임계값을 바꾸면 통과 주장 수가 변한다 (PER-199 민감도의 입력)
  5. 리뷰가 적은 셀은 주장을 내지 않는다 (§5-3 기본 폴백)
  6. 탈락은 드롭이 아니라 `"과소근거"` 가 붙은 `rejected[]` 행이고 수치가 함께 남는다
  7. 소수 의견을 다수 방향으로 뭉개지 않는다 — 컷이 아니라 `single_dissent` 한계다
  8. 불변식 U ≤ D ≤ S 위반과 계약 위반은 조용히 넘기지 않고 에러다

  python3 -m unittest discover -s pipeline -p 'test_*.py'
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from contracts import MISSING_SEGMENT  # noqa: E402
from gates import GateError  # noqa: E402
from golden_contract import derive_direction  # noqa: E402
from policy import (  # noqa: E402
    LIMIT_SINGLE_DISSENT,
    REJECT_INSUFFICIENT,
    REJECT_MINORITY_SHARE,
    REJECT_SEGMENT_TOO_SMALL,
    PolicyError,
    SufficiencyPolicy,
    sufficiency_gate,
)
from sufficiency import (  # noqa: E402
    SUFFICIENCY_REJECT_LABELS,
    Claim,
    ClaimSupport,
    EvidenceCell,
    SufficiencyError,
    cell_sufficient,
    run_sufficiency_gate,
    silent_cells,
)

ASPECT = "트러블/자극"


def cell(size: int = 40, product_id: str = "p001", condition: dict | None = None) -> EvidenceCell:
    return EvidenceCell(
        product_id=product_id,
        condition=dict(condition or {}),
        authors=frozenset(f"작성자{i}" for i in range(size)),
    )


def support(pos: int = 0, neg: int = 0, neu: int = 0, aspect: str = ASPECT) -> ClaimSupport:
    """겹치지 않는 작성자로 U+/U−/U0 를 만든다. `cell()` 의 작성자 이름과 같은 공간."""
    names = [f"작성자{i}" for i in range(pos + neg + neu)]
    return ClaimSupport(
        aspect=aspect,
        positive=frozenset(names[:pos]),
        negative=frozenset(names[pos:pos + neg]),
        neutral=frozenset(names[pos + neg:]),
    )


def claim(claim_id: str = "c1", *, pos: int = 0, neg: int = 0, neu: int = 0,
          size: int = 40, condition: dict | None = None) -> Claim:
    return Claim(claim_id=claim_id, cell=cell(size, condition=condition),
                 support=support(pos, neg, neu))


def record(review_id: int, author: str, *, product_id: str = "p001",
           skin_type: str | None = "A02", trouble: tuple[str, ...] = ()) -> dict:
    """게이트4가 읽는 필드만 갖춘 최소 레코드 (게이트2 통과분 형태)."""
    return {
        "reviewId": review_id,
        "productId": product_id,
        "raw": {},
        "condition": {
            "skinType": {
                "code": skin_type,
                "stated": skin_type is not None,
                "segment": skin_type or MISSING_SEGMENT,
            },
            "skinTrouble": {
                "codes": list(trouble),
                "stated": bool(trouble),
                "segments": list(trouble) or [MISSING_SEGMENT],
            },
        },
        "derived": {
            "authorKey": author,
            "contentHash": f"h{review_id}",
            "trustPrior": {"score": 0.5},
        },
    }


class ThreeConditionsAnd(unittest.TestCase):
    """§4-4 '낮은 쪽이 아니라 높은 쪽을 만족해야 통과다'."""

    def test_셋을_모두_만족하면_통과한다(self):
        result = run_sufficiency_gate([claim(pos=8, neu=1, size=40)])
        self.assertEqual(len(result.passed), 1)
        self.assertEqual(result.rejected, [])

    def test_비율만_만족하면_탈락한다(self):
        # 언급자 2명이 둘 다 같은 방향 → U/D = 100%. 단일 임계값이었으면 통과했다
        result = run_sufficiency_gate([claim(pos=2, size=40)])
        self.assertEqual(result.rejected_by_reason(), {REJECT_INSUFFICIENT: 1})

    def test_절대하한만_만족하면_탈락한다(self):
        # U=8 로 N_min 은 넘지만 언급자 100명 중 8명 → 8% < 10%
        result = run_sufficiency_gate([claim(pos=8, neu=92, size=200)])
        self.assertEqual(result.rejected_by_reason(), {REJECT_MINORITY_SHARE: 1})

    def test_셀이_작으면_나머지를_묻지_않는다(self):
        # S 가 먼저다 — 셀이 말할 자격이 없으면 주장의 근거 수를 물을 이유가 없다
        result = run_sufficiency_gate([claim(pos=5, size=5)])
        self.assertEqual(result.rejected_by_reason(), {REJECT_SEGMENT_TOO_SMALL: 1})
        self.assertEqual(
            SUFFICIENCY_REJECT_LABELS[REJECT_SEGMENT_TOO_SMALL], "세그먼트과소")


class SilenceIsNotEvidence(unittest.TestCase):
    """PER-178 — 말하지 않은 사람은 긍정도 부정도 아니다."""

    def test_분모는_D_다_S_가_아니다(self):
        # 100명 셀에서 8명이 말했고 전원 같은 방향. U/D=1.0 통과.
        # 분모를 S 로 뒀다면 8/100=0.08 로 탈락했을 자리다
        result = run_sufficiency_gate([claim(pos=8, size=100)])
        self.assertEqual(len(result.passed), 1)

    def test_침묵은_따로_센다(self):
        counts = support(pos=3, neu=1).as_dict(cell(40))
        self.assertEqual(counts["spokeAuthors"], 4)
        self.assertEqual(counts["silentAuthors"], 36)
        self.assertEqual(counts["cellAuthors"], 40)

    def test_중립은_침묵이_아니라_D_에_들어간다(self):
        # 중립을 근거에서 빼면 그 작성자가 침묵으로 잘못 세어진다 (결정 문서 §2)
        counts = support(pos=2, neu=3).as_dict(cell(40))
        self.assertEqual(counts["spokeAuthors"], 5)
        self.assertEqual(counts["silentAuthors"], 35)

    def test_중립만_있으면_방향이_없고_통과하지_못한다(self):
        s = support(neu=20)
        self.assertEqual(s.direction, "neutral")
        self.assertEqual(len(s.support), 0)
        result = run_sufficiency_gate([Claim("c1", cell(40), s)])
        self.assertEqual(result.rejected_by_reason(), {REJECT_INSUFFICIENT: 1})

    def test_침묵이_많아도_근거가_충분하면_통과하되_수가_남는다(self):
        result = run_sufficiency_gate([claim(pos=9, size=400)])
        self.assertEqual(len(result.passed), 1)
        counts = result.passed[0].support.as_dict(result.passed[0].cell)
        self.assertEqual(counts["silentAuthors"], 391)


class PolicyIsConfigured(unittest.TestCase):
    """완료 조건: 임계값이 설정값이고 meta 에 기록된다."""

    def test_meta_에_세_임계값이_기록된다(self):
        meta = run_sufficiency_gate([]).as_dict()["meta"]
        self.assertEqual(meta["sufficiency"]["nMin"], 8)
        self.assertEqual(meta["sufficiency"]["rMin"], 0.10)
        self.assertEqual(meta["sufficiency"]["sMin"], 8)
        self.assertEqual(meta["sufficiency"]["denominator"], "spokeAuthors")

    def test_임계값을_낮추면_통과_주장이_늘어난다(self):
        claims = [claim(f"c{i}", pos=i, size=40) for i in range(1, 11)]
        strict = run_sufficiency_gate(claims)
        loose = run_sufficiency_gate(claims, SufficiencyPolicy(n_min=3, s_min=3))
        self.assertEqual(len(strict.passed), 3)   # U=8,9,10
        self.assertEqual(len(loose.passed), 8)    # U=3..10
        self.assertEqual(loose.as_dict()["meta"]["sufficiency"]["nMin"], 3)

    def test_S_min_이_N_min_보다_작으면_에러다(self):
        # U ≤ D ≤ S 라 이 설정은 세그먼트 조건을 조용히 끄는 것과 같다
        with self.assertRaises(PolicyError):
            SufficiencyPolicy(n_min=8, s_min=5)

    def test_R_min_0_은_비율_조건을_끈다(self):
        with self.assertRaises(PolicyError):
            SufficiencyPolicy(r_min=0.0)
        with self.assertRaises(PolicyError):
            SufficiencyPolicy(r_min=1.5)

    def test_N_min_0_은_절대하한을_끈다(self):
        with self.assertRaises(PolicyError):
            SufficiencyPolicy(n_min=0)


class RejectedRows(unittest.TestCase):
    """PER-188 — 탈락은 드롭이 아니다."""

    def test_과소근거로_기록된다(self):
        result = run_sufficiency_gate([claim("c1", pos=3, size=40)])
        row = result.as_dict()["rejected"][0]
        self.assertEqual(row["claimId"], "c1")
        self.assertEqual(row["gate"], "sufficiency")
        self.assertEqual(row["reason"], REJECT_INSUFFICIENT)
        self.assertEqual(row["label"], "과소근거")

    def test_탈락_행에_수치가_남는다(self):
        # "과소근거" 세 글자로는 임계값을 고칠 수 없다. 되살아날 주장을 리포트를
        # 다시 돌리지 않고 읽을 수 있어야 민감도가 PER-199 의 입력이 된다
        row = run_sufficiency_gate([claim("c1", pos=3, neu=1, size=40)]).as_dict()["rejected"][0]
        self.assertEqual(row["support"]["supportAuthors"], 3)
        self.assertEqual(row["support"]["spokeAuthors"], 4)
        self.assertEqual(row["support"]["silentAuthors"], 36)
        self.assertIn("N_min=8", row["detail"])

    def test_claimId_가_중복이면_에러다(self):
        with self.assertRaises(SufficiencyError):
            run_sufficiency_gate([claim("c1", pos=8), claim("c1", pos=9)])


class FallbackSilence(unittest.TestCase):
    """§5-3 — 리뷰가 적은 제품은 주장을 적게 내거나 아무것도 안 낸다."""

    def test_셀이_작으면_주장_이전에_침묵한다(self):
        self.assertFalse(cell_sufficient(cell(7)))
        self.assertTrue(cell_sufficient(cell(8)))

    def test_침묵할_셀의_목록을_낸다(self):
        cells = [cell(7, "p051"), cell(40, "p001"), cell(0, "p052")]
        self.assertEqual([c.product_id for c in silent_cells(cells)], ["p051", "p052"])


class ConditionCells(unittest.TestCase):
    """조건부 주장의 근거는 그 세그먼트 리뷰만이다 (PER-177 §3)."""

    RECORDS = [
        record(1, "가", skin_type="A02"),
        record(2, "나", skin_type="A02"),
        record(3, "다", skin_type="A01"),
        record(4, "라", skin_type=None),
        record(5, "마", skin_type="A02", trouble=("C01", "C05")),
    ]

    def test_조건_셀은_그_세그먼트만_센다(self):
        c = EvidenceCell.of(self.RECORDS, "p001", {"skinType": "A02"})
        self.assertEqual(c.size, 3)
        self.assertTrue(c.conditional)

    def test_미기재는_별도_세그먼트다(self):
        # '미기재'를 '모든 조건'으로 취급하면 조건부 주장의 근거가 오염된다
        c = EvidenceCell.of(self.RECORDS, "p001", {"skinType": MISSING_SEGMENT})
        self.assertEqual(c.size, 1)
        self.assertEqual(sorted(c.authors), ["라"])

    def test_조건_None_은_무관이라_전원을_센다(self):
        c = EvidenceCell.of(self.RECORDS, "p001", {"skinType": None})
        self.assertEqual(c.size, 5)
        self.assertFalse(c.conditional)

    def test_다중_축은_코드를_모두_가진_리뷰만_센다(self):
        self.assertEqual(EvidenceCell.of(self.RECORDS, "p001", {"skinTrouble": ["C01"]}).size, 1)
        self.assertEqual(
            EvidenceCell.of(self.RECORDS, "p001", {"skinTrouble": ["C01", "C09"]}).size, 0)

    def test_게이트2_통과분이_아니면_에러다(self):
        # 같은 작성자가 두 번 남아 있으면 S 가 1명 부풀고, 그 1명이 S_min 경계를 넘긴다
        dup = self.RECORDS + [record(6, "가")]
        with self.assertRaises(GateError):
            EvidenceCell.of(dup, "p001", {"skinType": "A02"})

    def test_셀_밖_태그는_세지_않는다(self):
        cellA02 = EvidenceCell.of(self.RECORDS, "p001", {"skinType": "A02"})
        tags = [
            {"reviewId": 1, "aspect": ASPECT, "polarity": "negative"},
            {"reviewId": 3, "aspect": ASPECT, "polarity": "negative"},  # A01 — 셀 밖
            {"reviewId": 4, "aspect": ASPECT, "polarity": "negative"},  # 미기재 — 셀 밖
        ]
        s = ClaimSupport.of(tags, self.RECORDS, ASPECT, cellA02)
        self.assertEqual(sorted(s.negative), ["가"])

    def test_다른_제품의_태그는_세지_않는다(self):
        # 작성자 이름이 우연히 같아도 다른 제품의 근거는 이 셀의 근거가 아니다
        c = EvidenceCell.of(self.RECORDS, "p001", {})
        other = self.RECORDS + [record(9, "가", product_id="p002")]
        s = ClaimSupport.of([{"reviewId": 9, "aspect": ASPECT, "polarity": "negative"}],
                            other, ASPECT, c)
        self.assertEqual(len(s.spoke), 0)

    def test_다른_aspect_태그는_세지_않는다(self):
        c = EvidenceCell.of(self.RECORDS, "p001", {})
        tags = [
            {"reviewId": 1, "aspect": ASPECT, "polarity": "negative"},
            {"reviewId": 2, "aspect": "보습감", "polarity": "positive"},
        ]
        s = ClaimSupport.of(tags, self.RECORDS, ASPECT, c)
        self.assertEqual(len(s.spoke), 1)

    def test_polarity_가_3종_밖이면_에러다(self):
        c = EvidenceCell.of(self.RECORDS, "p001", {})
        with self.assertRaises(SufficiencyError):
            ClaimSupport.of([{"reviewId": 1, "aspect": ASPECT, "polarity": "mixed"}],
                            self.RECORDS, ASPECT, c)


class MinorityOpinion(unittest.TestCase):
    """§4 결정 — 소수 의견을 다수 방향으로 뭉개지 않는다."""

    def test_반대_1명도_mixed_다(self):
        s = support(pos=7, neg=1)
        self.assertEqual(s.direction, "mixed")
        self.assertEqual(s.minority, 1)

    def test_반대_1명은_컷이_아니라_한계다(self):
        result = run_sufficiency_gate([claim("c1", pos=7, neg=1, size=40)])
        self.assertEqual(len(result.passed), 1)
        self.assertEqual(result.as_dict()["limitations"], {LIMIT_SINGLE_DISSENT: ["c1"]})

    def test_양쪽이_2명_이상이면_한계가_없다(self):
        result = run_sufficiency_gate([claim("c1", pos=6, neg=2, size=40)])
        self.assertEqual(result.as_dict()["limitations"], {})

    def test_mixed_의_U_는_양쪽_합이다(self):
        # "의견이 갈린다"를 지지하는 것은 양쪽에서 방향을 말한 사람들이다
        s = support(pos=5, neg=3, neu=2)
        self.assertEqual(len(s.support), 8)
        self.assertEqual(len(s.spoke), 10)

    def test_소수_측_수가_표기에_남는다(self):
        counts = support(pos=7, neg=1).as_dict(cell(40))
        self.assertEqual(counts["positiveAuthors"], 7)
        self.assertEqual(counts["negativeAuthors"], 1)
        self.assertEqual(counts["minorityAuthors"], 1)


class Invariants(unittest.TestCase):
    def test_U_가_D_보다_크면_에러다(self):
        with self.assertRaises(PolicyError):
            sufficiency_gate(support_authors=9, spoke_authors=8, cell_authors=40)

    def test_D_가_S_보다_크면_에러다(self):
        with self.assertRaises(PolicyError):
            sufficiency_gate(support_authors=3, spoke_authors=40, cell_authors=8)

    def test_셀_밖_작성자가_섞이면_에러다(self):
        # 개수만 비교하면 U ≤ D ≤ S 를 만족해 통과한다. 그때 silentAuthors 가
        # S − D 로 줄어든 채 주장에 실리고, 그 수가 생성·judge 의 일반화 판단 근거다
        outside = ClaimSupport(ASPECT, positive=frozenset(f"외부{i}" for i in range(9)))
        with self.assertRaises(SufficiencyError):
            outside.as_dict(cell(40))
        with self.assertRaises(SufficiencyError):
            run_sufficiency_gate([Claim("c1", cell(40), outside)])

    def test_단일_축에_코드_배열을_주면_에러다(self):
        # 배열은 AND 라 단일 축에서는 만족할 리뷰가 없다. 에러가 아니면 셀이 조용히
        # 0명이 되고 그 결과가 '세그먼트과소' 탈락으로 둔갑한다
        with self.assertRaises(SufficiencyError):
            EvidenceCell.of(ConditionCells.RECORDS, "p001", {"skinType": ["A01", "A02"]})
        # 다중 축은 여러 코드를 모두 가진 리뷰를 뜻하므로 배열이 정상이다
        self.assertEqual(
            EvidenceCell.of(ConditionCells.RECORDS, "p001", {"skinTrouble": ["C01", "C05"]}).size, 1)

    def test_한_작성자가_두_방향에_있으면_에러다(self):
        with self.assertRaises(SufficiencyError):
            ClaimSupport(aspect=ASPECT, positive=frozenset({"가"}), negative=frozenset({"가"}))

    def test_방향_규칙은_골든셋과_같다(self):
        """다르면 judge 일치율이 게이트가 아니라 방향 정의의 차이를 잰다."""
        bundle = {"reviews": [
            {"reviewId": i, "derived": {"authorKey": a}}
            for i, a in enumerate(["가", "나", "다"])
        ]}
        cases = {
            "positive": [{"reviewId": 0, "stance": "positive"}],
            "negative": [{"reviewId": 0, "stance": "negative"}],
            "neutral": [{"reviewId": 0, "stance": "neutral"}],
            "mixed": [{"reviewId": 0, "stance": "positive"}, {"reviewId": 1, "stance": "negative"}],
        }
        for expected, evidence in cases.items():
            by = {"positive": set(), "negative": set(), "neutral": set()}
            for ev in evidence:
                by[ev["stance"]].add(bundle["reviews"][ev["reviewId"]]["derived"]["authorKey"])
            mine = ClaimSupport(ASPECT, frozenset(by["positive"]), frozenset(by["negative"]),
                                frozenset(by["neutral"]))
            self.assertEqual(mine.direction, expected)
            self.assertEqual(derive_direction(evidence, bundle), expected)


if __name__ == "__main__":
    unittest.main()
