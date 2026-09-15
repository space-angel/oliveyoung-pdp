"""
컨텍스트 정렬 5단 배치(PER-187) 계약 테스트.

## 완료 조건

  1. 배치가 **결정론적**이다 — 같은 입력에 같은 순서, 입력 순서를 섞어도 바이트 동일
  2. 근거 인용은 신뢰도 점수(PER-174) 내림차순이고 **동률의 tie-break 가 고정**돼 있다
     (`trust.rank_key()` — 점수 ↓ → reviewDate 최신 → reviewId 최소)
  3. 5단 순서(`무엇 → 조건 → 근거 → 방향 → 충분성`)가 뒤바뀌면 에러다
  4. 3단 근거가 0건이면 배치를 만들지 않는다. 나머지 단은 비지 않고 **명시값**이다
  5. 인용은 원문 부분문자열이다 — 보이지 않는 문자만 접고 띄어쓰기 편집은 탈락이다
  6. 침묵은 5단에 실리되 **4단 비율의 분모가 아니다** (분모 = 긍정 + 부정, PER-178)
  7. 사용기간·계절은 조건축이 아니다 — 빠진 사유가 배치 자료에 남는다
  8. 게이트3 판정과 게이트4 근거가 다른 셀에서 왔으면 에러다

  python3 -m unittest discover -s pipeline -p 'test_*.py'
"""
import json
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from catalog import Product  # noqa: E402
from contracts import MISSING_SEGMENT  # noqa: E402
from context_layout import (  # noqa: E402
    EXCLUDED_AXES,
    LAYOUT_CONDITION_AXES,
    STAGES,
    ContextLayout,
    ContextLayoutError,
    assert_stage_order,
    layout_context,
    render_layout,
)
from gates import GateError  # noqa: E402
from polarity import AspectSupport, LIMIT_TAGGER_DIRECTION_ERROR  # noqa: E402
from policy import (  # noqa: E402
    LIMIT_SINGLE_DISSENT,
    REJECT_INSUFFICIENT,
    GateDecision,
)
from sufficiency import Claim, ClaimSupport, EvidenceCell  # noqa: E402

ASPECT = "지속성"
PRODUCT_ID = "p001"

PRODUCT = Product(
    product_id=PRODUCT_ID,
    display_name="테스트 세럼",
    category="스킨케어",
    requested_goods_no="A000000000001",
    goods_nos=("A000000000001",),
    lineage_id="L001",
    renewal_policy="unobserved",
)


def record(
    review_id: int,
    *,
    content: str = "지속력이 오래가요",
    score: float = 0.5,
    date: str = "2026.05.01",
    skin_type: str = "A02",
    troubles: tuple[str, ...] = ("C01",),
) -> dict:
    """게이트1·2 통과 레코드 1건. 작성자·본문 해시는 리뷰마다 유일하게 둔다."""
    return {
        "reviewId": review_id,
        "productId": PRODUCT_ID,
        "raw": {"content": content, "rating": 5, "reviewDate": date},
        "condition": {
            "skinType": {"code": skin_type, "stated": True, "segment": skin_type},
            "skinTrouble": {"codes": list(troubles), "stated": True, "segments": list(troubles)},
            "option": {"code": None, "stated": False, "segment": MISSING_SEGMENT},
        },
        "derived": {
            "authorKey": f"작성자{review_id}",
            "contentHash": f"h{review_id}",
            "contentLength": len(content),
            "sentimentPrior": "positive",
            "reviewYearMonth": date[:7].replace(".", "-"),
            "trustPrior": {"score": score, "signals": {}},
        },
    }


def tag(review_id: int, polarity: str, snippet: str, aspect: str = ASPECT) -> dict:
    return {"reviewId": review_id, "aspect": aspect, "polarity": polarity, "snippet": snippet}


def build(
    records: list[dict],
    tags: list[dict],
    *,
    condition: dict | None = None,
    decision: GateDecision | None = None,
    **kwargs,
) -> ContextLayout:
    """게이트3·4 판정을 레코드·태그에서 만들어 배치를 부른다."""
    condition = dict(condition or {})
    authors = frozenset(r["derived"]["authorKey"] for r in records)
    cell = EvidenceCell(product_id=PRODUCT_ID, condition=condition, authors=authors)
    by_author = {r["reviewId"]: r["derived"]["authorKey"] for r in records}
    stances: dict[str, set] = {"positive": set(), "negative": set(), "neutral": set()}
    for t in tags:
        if t["aspect"] == ASPECT and t["reviewId"] in by_author:
            stances[t["polarity"]].add(by_author[t["reviewId"]])
    support = ClaimSupport(
        aspect=ASPECT,
        positive=frozenset(stances["positive"]),
        negative=frozenset(stances["negative"]),
        neutral=frozenset(stances["neutral"]),
    )
    polarity = AspectSupport(
        aspect=ASPECT,
        positive_authors=len(support.positive),
        negative_authors=len(support.negative),
        neutral_authors=len(support.neutral),
        silent_authors=len(authors) - len(support.spoke),
        minority_p_value=1.0,
        limitations=frozenset({LIMIT_TAGGER_DIRECTION_ERROR}),
    )
    return layout_context(
        product=PRODUCT,
        claim=Claim("c1", cell, support),
        polarity=polarity,
        decision=decision or GateDecision(passed=True),
        records=records,
        tags=tags,
        **kwargs,
    )


def simple(n: int = 3, negatives: int = 1) -> tuple[list[dict], list[dict]]:
    """긍정 n−negatives · 부정 negatives 로 이뤄진 셀. 점수는 리뷰마다 다르게 둔다."""
    records, tags = [], []
    for i in range(n):
        records.append(record(100 + i, content=f"지속력 이야기 {i} 입니다", score=0.9 - i * 0.1))
        polarity = "negative" if i < negatives else "positive"
        tags.append(tag(100 + i, polarity, f"지속력 이야기 {i}"))
    return records, tags


class StageOrderTest(unittest.TestCase):
    """완료 조건 3 — 5단 순서가 규격이고 뒤바뀌면 에러다."""

    def test_stage_keys_are_in_spec_order(self):
        payload = build(*simple()).as_dict()
        self.assertEqual(payload["stageOrder"], list(STAGES))
        self.assertEqual(list(payload["stages"]), list(STAGES))
        self.assertEqual(
            [payload["stages"][k]["stage"] for k in STAGES], [1, 2, 3, 4, 5]
        )

    def test_reordered_stages_are_an_error(self):
        payload = build(*simple()).as_dict()
        flipped = dict(payload)
        flipped["stages"] = {k: payload["stages"][k] for k in reversed(STAGES)}
        with self.assertRaises(ContextLayoutError):
            assert_stage_order(flipped)
        with self.assertRaises(ContextLayoutError):
            render_layout(flipped)

    def test_missing_or_renamed_stage_is_an_error(self):
        payload = build(*simple()).as_dict()
        dropped = dict(payload)
        dropped["stages"] = {k: v for k, v in payload["stages"].items() if k != "direction"}
        with self.assertRaises(ContextLayoutError):
            assert_stage_order(dropped)

        renumbered = json.loads(json.dumps(payload))
        renumbered["stages"]["evidence"]["stage"] = 5
        with self.assertRaises(ContextLayoutError):
            assert_stage_order(renumbered)


class DeterminismTest(unittest.TestCase):
    """완료 조건 1 — 같은 입력이면 바이트가 같다."""

    def test_same_input_same_bytes(self):
        records, tags = simple(5, negatives=2)
        first = json.dumps(build(records, tags).as_dict(), ensure_ascii=False, sort_keys=False)
        second = json.dumps(build(records, tags).as_dict(), ensure_ascii=False, sort_keys=False)
        self.assertEqual(first, second)

    def test_input_order_does_not_change_layout(self):
        records, tags = simple(6, negatives=2)
        reference = json.dumps(build(records, tags).as_dict(), ensure_ascii=False)
        rng = random.Random(20260915)
        for _ in range(20):
            shuffled_records = records[:]
            shuffled_tags = tags[:]
            rng.shuffle(shuffled_records)
            rng.shuffle(shuffled_tags)
            payload = json.dumps(
                build(shuffled_records, shuffled_tags).as_dict(), ensure_ascii=False
            )
            self.assertEqual(reference, payload)

    def test_render_is_deterministic(self):
        records, tags = simple(4)
        payload = build(records, tags).as_dict()
        self.assertEqual(render_layout(payload), render_layout(payload))
        shuffled = build(records[::-1], tags[::-1]).as_dict()
        self.assertEqual(render_layout(payload), render_layout(shuffled))


class EvidenceOrderTest(unittest.TestCase):
    """완료 조건 2 — 신뢰도 내림차순이고 동률의 tie-break 가 고정돼 있다."""

    def test_sorted_by_trust_desc(self):
        records = [
            record(1, content="지속력 A 이야기", score=0.10),
            record(2, content="지속력 B 이야기", score=0.90),
            record(3, content="지속력 C 이야기", score=0.50),
        ]
        tags = [tag(i, "positive", f"지속력 {c}") for i, c in ((1, "A"), (2, "B"), (3, "C"))]
        quotes = build(records, tags).as_dict()["stages"]["evidence"]["quotes"]
        self.assertEqual([q["reviewId"] for q in quotes], [2, 3, 1])
        self.assertEqual([q["trustScore"] for q in quotes], [0.90, 0.50, 0.10])
        self.assertEqual([q["rank"] for q in quotes], [1, 2, 3])

    def test_tie_break_is_date_then_review_id(self):
        """점수가 같으면 최신 날짜, 그래도 같으면 reviewId 최소다 (trust.rank_key)."""
        records = [
            record(11, content="지속력 가 이야기", score=0.5, date="2025.01.02"),
            record(12, content="지속력 나 이야기", score=0.5, date="2026.03.04"),
            record(13, content="지속력 다 이야기", score=0.5, date="2026.03.04"),
        ]
        tags = [tag(i, "positive", s) for i, s in
                ((11, "지속력 가"), (12, "지속력 나"), (13, "지속력 다"))]
        quotes = build(records, tags).as_dict()["stages"]["evidence"]["quotes"]
        self.assertEqual([q["reviewId"] for q in quotes], [12, 13, 11])

        # 입력 순서를 뒤집어도 같은 순서여야 동률이 흔들리지 않는 것이다
        flipped = build(records[::-1], tags[::-1]).as_dict()["stages"]["evidence"]["quotes"]
        self.assertEqual([q["reviewId"] for q in flipped], [12, 13, 11])

    def test_missing_trust_prior_is_an_error(self):
        """점수가 없으면 0 으로 깔지 않는다 — 정렬이 입력 순서로 정해진다.

        게이트2 통과 확인(`gates._dedup_keys`)이 먼저 잡고 배치도 같은 검사를 다시
        한다. 어느 쪽이 먼저 잡든 **조용히 통과하지 않는다**는 것이 이 테스트다.
        """
        for mutate in (
            lambda r: r["derived"].pop("trustPrior"),
            lambda r: r["derived"].__setitem__("trustPrior", {}),
        ):
            records, tags = simple(2)
            mutate(records[0])
            with self.assertRaises(ValueError) as caught:
                build(records, tags)
            self.assertIn("trustPrior", str(caught.exception))

    def test_quote_limit_records_what_it_dropped(self):
        records, tags = simple(5)
        evidence = build(records, tags, max_quotes=2).as_dict()["stages"]["evidence"]
        self.assertEqual(evidence["quotesShown"], 2)
        self.assertEqual(evidence["quotesTotal"], 5)
        self.assertEqual(evidence["omittedByRank"], 3)
        self.assertEqual([q["rank"] for q in evidence["quotes"]], [1, 2])


class TruncationTest(unittest.TestCase):
    """상한이 방향을 통째로 지우지 않는다 (PER-185 §4-3 결정 3).

    전수 실측에서 `p019 × 트러블/자극` 은 긍정 309 · 부정 6 인데 신뢰도 상위 8건이
    전부 긍정이었다 (`eval/reports/context_layout_per187.json`). 그대로 자르면 4단은
    "부정 6명"인데 3단에 부정 인용이 없다.
    """

    def minority_cell(self) -> tuple[list[dict], list[dict]]:
        """상위 5건이 전부 긍정이고 부정은 순위 최하위인 셀."""
        records, tags = [], []
        for i in range(6):
            records.append(record(500 + i, content=f"지속력 이야기 {i} 입니다", score=0.9 - i * 0.1))
            tags.append(tag(500 + i, "negative" if i == 5 else "positive", f"지속력 이야기 {i}"))
        return records, tags

    def test_truncation_keeps_every_direction(self):
        records, tags = self.minority_cell()
        evidence = build(records, tags, max_quotes=3).as_dict()["stages"]["evidence"]
        self.assertEqual(sorted(evidence["directionsShown"]), ["negative", "positive"])
        self.assertEqual(evidence["reservedForDirection"], [505])
        self.assertEqual(evidence["quotesShown"], 3)
        self.assertEqual(evidence["omittedByRank"], 3)

    def test_reserved_quote_keeps_its_original_rank(self):
        """채워 넣은 인용도 몇 등짜리 근거인지 감추지 않는다."""
        records, tags = self.minority_cell()
        quotes = build(records, tags, max_quotes=3).as_dict()["stages"]["evidence"]["quotes"]
        self.assertEqual([q["rank"] for q in quotes], [1, 2, 6])
        self.assertEqual([q["polarity"] for q in quotes], ["positive", "positive", "negative"])

    def test_untruncated_layout_reserves_nothing(self):
        records, tags = self.minority_cell()
        evidence = build(records, tags).as_dict()["stages"]["evidence"]
        self.assertEqual(evidence["reservedForDirection"], [])
        self.assertEqual(evidence["omittedByRank"], 0)

    def test_limit_below_direction_count_is_an_error(self):
        records, tags = self.minority_cell()
        with self.assertRaises(ContextLayoutError):
            build(records, tags, max_quotes=1)
        with self.assertRaises(ContextLayoutError):
            build(records, tags, max_quotes=0)

    def test_truncation_is_deterministic(self):
        records, tags = self.minority_cell()
        reference = json.dumps(build(records, tags, max_quotes=3).as_dict(), ensure_ascii=False)
        rng = random.Random(7)
        for _ in range(10):
            shuffled_records, shuffled_tags = records[:], tags[:]
            rng.shuffle(shuffled_records)
            rng.shuffle(shuffled_tags)
            self.assertEqual(reference, json.dumps(
                build(shuffled_records, shuffled_tags, max_quotes=3).as_dict(),
                ensure_ascii=False))


class VerbatimQuoteTest(unittest.TestCase):
    """완료 조건 5 — 인용은 원문 부분문자열이다."""

    def test_invisible_characters_are_folded(self):
        records = [record(1, content="지속력이 좋아요.\r\n다시 살게요")]
        tags = [tag(1, "positive", "지속력이 좋아요.\n다시 살게요")]
        quotes = build(records, tags).as_dict()["stages"]["evidence"]["quotes"]
        self.assertIn("지속력이 좋아요", quotes[0]["quote"])

    def test_paraphrase_is_rejected(self):
        records = [record(1, content="지속력이 오래갑니다")]
        tags = [tag(1, "positive", "지속력이 매우 오래갑니다")]
        with self.assertRaises(ContextLayoutError):
            build(records, tags)

    def test_squeezed_whitespace_is_rejected(self):
        """공백 전체 squeeze 는 금지다 — 띄어쓰기를 지운 편집은 인용이 아니다."""
        records = [record(1, content="지속력 이 오래 갑니다")]
        tags = [tag(1, "positive", "지속력이오래갑니다")]
        with self.assertRaises(ContextLayoutError):
            build(records, tags)

    def test_empty_snippet_is_rejected(self):
        records = [record(1)]
        tags = [tag(1, "positive", "")]
        with self.assertRaises(ContextLayoutError):
            build(records, tags)


class EmptyStageTest(unittest.TestCase):
    """완료 조건 4 — 3단만 비면 에러이고, 나머지 단은 비지 않고 명시값이다."""

    def test_no_evidence_is_an_error(self):
        records = [record(1), record(2)]
        with self.assertRaises(ContextLayoutError):
            build(records, [])

    def test_other_aspect_tags_do_not_count_as_evidence(self):
        records = [record(1), record(2)]
        tags = [tag(1, "positive", "지속력 이야기", aspect="향")]
        with self.assertRaises(ContextLayoutError):
            build(records, tags)

    def test_unconditional_claim_states_that_axes_are_unscoped(self):
        payload = build(*simple()).as_dict()
        axes = payload["stages"]["condition"]["axes"]
        self.assertEqual([a["axis"] for a in axes], list(LAYOUT_CONDITION_AXES))
        self.assertFalse(payload["stages"]["condition"]["conditional"])
        for axis in axes:
            self.assertFalse(axis["scoped"])
            self.assertIsNone(axis["stated"])

    def test_missing_segment_is_not_the_same_as_unscoped(self):
        """무관(null)과 미기재(세그먼트)는 다르다 (PER-177 §3)."""
        records, tags = [], []
        for i in range(3):
            records.append(record(200 + i, content=f"지속력 이야기 {i} 입니다", skin_type=None))
            records[-1]["condition"]["skinType"] = {
                "code": None, "stated": False, "segment": MISSING_SEGMENT}
            tags.append(tag(200 + i, "positive", f"지속력 이야기 {i}"))
        payload = build(records, tags, condition={"skinType": MISSING_SEGMENT}).as_dict()
        skin = payload["stages"]["condition"]["axes"][0]
        self.assertTrue(skin["scoped"])
        self.assertEqual(skin["segments"], [MISSING_SEGMENT])
        self.assertFalse(skin["stated"])
        self.assertTrue(payload["stages"]["condition"]["conditional"])


class SilenceTest(unittest.TestCase):
    """완료 조건 6 — 침묵은 5단에 실리되 4단 비율의 분모가 아니다 (PER-178)."""

    def test_silent_authors_are_not_in_the_denominator(self):
        records, tags = [], []
        for i in range(10):
            records.append(record(300 + i, content=f"지속력 이야기 {i} 입니다", score=0.5))
        for i in range(3):  # 10명 중 3명만 이 주제를 말했다
            tags.append(tag(300 + i, "positive" if i else "negative", f"지속력 이야기 {i}"))
        payload = build(records, tags).as_dict()

        direction = payload["stages"]["direction"]
        self.assertEqual(direction["denominator"]["value"], 3)
        self.assertEqual(direction["negativeRatio"], round(1 / 3, 4))
        self.assertEqual(direction["positiveRatio"], round(2 / 3, 4))

        sufficiency = payload["stages"]["sufficiency"]
        self.assertEqual(sufficiency["cellAuthors"], 10)
        self.assertEqual(sufficiency["spokeAuthors"], 3)
        self.assertEqual(sufficiency["silentAuthors"], 7)
        self.assertNotIn("silentAuthors", direction)

    def test_neutral_mentions_are_in_d_but_not_in_the_ratio(self):
        records, tags = [], []
        for i in range(4):
            records.append(record(400 + i, content=f"지속력 이야기 {i} 입니다", score=0.5))
        tags.append(tag(400, "positive", "지속력 이야기 0"))
        tags.append(tag(401, "negative", "지속력 이야기 1"))
        tags.append(tag(402, "neutral", "지속력 이야기 2"))
        payload = build(records, tags).as_dict()
        self.assertEqual(payload["stages"]["direction"]["denominator"]["value"], 2)
        self.assertEqual(payload["stages"]["sufficiency"]["spokeAuthors"], 3)
        self.assertEqual(payload["stages"]["sufficiency"]["neutralAuthors"], 1)
        self.assertEqual(payload["stages"]["sufficiency"]["silentAuthors"], 1)

    def test_rejected_claim_keeps_its_numbers(self):
        """탈락도 배치된다 — 수치가 붙은 rejected[] 행이 되려면 5단이 필요하다."""
        records, tags = simple(3)
        payload = build(
            records, tags,
            decision=GateDecision(passed=False, reason=REJECT_INSUFFICIENT),
        ).as_dict()
        self.assertFalse(payload["stages"]["sufficiency"]["passed"])
        self.assertEqual(payload["stages"]["sufficiency"]["reason"], REJECT_INSUFFICIENT)

    def test_limitation_is_carried(self):
        records, tags = simple(4, negatives=1)
        payload = build(
            records, tags,
            decision=GateDecision(passed=True, limitation=LIMIT_SINGLE_DISSENT),
        ).as_dict()
        self.assertEqual(
            payload["stages"]["sufficiency"]["limitations"], [LIMIT_SINGLE_DISSENT])
        self.assertIn(
            LIMIT_TAGGER_DIRECTION_ERROR, payload["stages"]["direction"]["limitations"])


class ExcludedAxesTest(unittest.TestCase):
    """완료 조건 7 — 사용기간·계절은 조건축이 아니고, 빠진 사유가 자료에 남는다."""

    def test_usage_period_and_season_are_not_condition_axes(self):
        self.assertEqual(LAYOUT_CONDITION_AXES, ("skinType", "skinTrouble"))
        payload = build(*simple()).as_dict()
        axes = {a["axis"] for a in payload["stages"]["condition"]["axes"]}
        self.assertNotIn("usagePeriod", axes)
        self.assertNotIn("season", axes)

    def test_exclusion_reasons_are_in_the_payload(self):
        payload = build(*simple()).as_dict()
        excluded = {a["axis"]: a["reason"] for a in payload["stages"]["condition"]["excludedAxes"]}
        self.assertEqual(set(excluded), {a["axis"] for a in EXCLUDED_AXES})
        self.assertIn("필드가 없다", excluded["usagePeriod"])
        self.assertIn("스키마에 없다", excluded["season"])


class CellConsistencyTest(unittest.TestCase):
    """완료 조건 8 — 게이트3·4 가 같은 셀에서 나온 판정인지 대조한다."""

    def test_gate3_counts_from_another_cell_are_an_error(self):
        records, tags = simple(4, negatives=1)
        authors = frozenset(r["derived"]["authorKey"] for r in records)
        cell = EvidenceCell(PRODUCT_ID, {}, authors)
        by_author = {r["reviewId"]: r["derived"]["authorKey"] for r in records}
        pos = {by_author[t["reviewId"]] for t in tags if t["polarity"] == "positive"}
        neg = {by_author[t["reviewId"]] for t in tags if t["polarity"] == "negative"}
        support = ClaimSupport(ASPECT, frozenset(pos), frozenset(neg), frozenset())
        # 제품 전체(다른 셀)의 방향 판정을 가져다 붙인다
        wrong = AspectSupport(ASPECT, 120, 8, 0, 300, 1.0, frozenset())
        with self.assertRaises(ContextLayoutError):
            layout_context(
                product=PRODUCT,
                claim=Claim("c1", cell, support),
                polarity=wrong,
                decision=GateDecision(passed=True),
                records=records,
                tags=tags,
            )

    def test_record_outside_the_cell_is_an_error(self):
        records, tags = simple(3)
        records.append(record(999, content="지속력 이야기 9 입니다", skin_type="A05"))
        tags.append(tag(999, "positive", "지속력 이야기 9"))
        with self.assertRaises(ContextLayoutError):
            build(records, tags, condition={"skinType": "A02"})

    def test_duplicate_author_is_an_error(self):
        """같은 작성자가 두 번 있으면 인용이 두 번 나가고 비율이 그쪽으로 기운다."""
        records, tags = simple(2, negatives=0)
        records[1]["derived"]["authorKey"] = records[0]["derived"]["authorKey"]
        with self.assertRaises(GateError):
            build(records, tags)

    def test_tag_from_another_run_is_an_error(self):
        records, tags = simple(2)
        tags.append(tag(77777, "positive", "지속력 이야기 0"))
        with self.assertRaises(ContextLayoutError):
            build(records, tags)

    def test_product_mismatch_is_an_error(self):
        records, tags = simple(2)
        other = Product(
            product_id="p002",
            display_name="다른 제품",
            category="스킨케어",
            requested_goods_no="A000000000002",
            goods_nos=("A000000000002",),
            lineage_id="L002",
            renewal_policy="unobserved",
        )
        authors = frozenset(r["derived"]["authorKey"] for r in records)
        support = ClaimSupport(ASPECT, authors, frozenset(), frozenset())
        with self.assertRaises(ContextLayoutError):
            layout_context(
                product=other,
                claim=Claim("c1", EvidenceCell(PRODUCT_ID, {}, authors), support),
                polarity=AspectSupport(ASPECT, len(authors), 0, 0, 0, 1.0, frozenset()),
                decision=GateDecision(passed=True),
                records=records,
                tags=tags,
            )


class SubjectStageTest(unittest.TestCase):
    """1단 — 제품 동일성은 카탈로그가 소유하고 옵션은 1단에 놓는다."""

    def test_subject_carries_catalog_identity(self):
        payload = build(*simple()).as_dict()
        subject = payload["stages"]["subject"]
        self.assertEqual(subject["productId"], PRODUCT_ID)
        self.assertEqual(subject["displayName"], "테스트 세럼")
        self.assertEqual(subject["lineageId"], "L001")
        self.assertEqual(subject["aspect"], ASPECT)
        self.assertFalse(subject["option"]["scoped"])

    def test_option_scope_is_recorded(self):
        payload = build(*simple(), option_scope="21호").as_dict()
        self.assertTrue(payload["stages"]["subject"]["option"]["scoped"])
        self.assertEqual(payload["stages"]["subject"]["option"]["key"], "21호")

    def test_unknown_aspect_is_an_error(self):
        records, tags = simple(2)
        authors = frozenset(r["derived"]["authorKey"] for r in records)
        support = ClaimSupport("없는주제", authors, frozenset(), frozenset())
        with self.assertRaises(ContextLayoutError):
            layout_context(
                product=PRODUCT,
                claim=Claim("c1", EvidenceCell(PRODUCT_ID, {}, authors), support),
                polarity=AspectSupport("없는주제", len(authors), 0, 0, 0, 1.0, frozenset()),
                decision=GateDecision(passed=True),
                records=records,
                tags=tags,
            )


class RenderTest(unittest.TestCase):
    """문자열 렌더링은 배치와 분리돼 있고, 5단 표기를 순서대로 낸다."""

    def test_render_has_five_stages_in_order(self):
        text = render_layout(build(*simple()).as_dict())
        positions = [text.index(f"[{i}] ") for i in range(1, 6)]
        self.assertEqual(positions, sorted(positions))

    def test_render_quotes_are_verbatim_substrings(self):
        records, tags = simple(3)
        text = render_layout(build(records, tags).as_dict())
        for t in tags:
            self.assertIn(t["snippet"], text)


if __name__ == "__main__":
    unittest.main()
