"""
주장 골든셋 라벨 계약 (PER-178 / PRD §7-2, §11-4).

골든셋은 "가장 느리고 가장 믿을 만한" 평가 층이다. 규격이 흔들리면 이후 judge 일치율
(PER-197)·전수 평가(PER-198)·v4 대비 비교(PER-201)가 전부 흔들린다. 그래서 라벨 1건의
모양과 그 위반을 여기서 **코드로** 고정한다 — 라벨 도구(`eval/label_concern_golden.py`)와
게이트(`scripts/verify.sh`)가 같은 함수를 부른다.

## 라벨 1건 = claim 1개

    labelId          고유 ID (사람이 정한다, 예: B03-1)
    bundleId         어느 라벨링 번들에서 만들었나 (`sample_concern_golden.py`)
    productId        번들의 제품 (세대 단위, PER-171/172)
    aspect           14종 택소노미(`tag_contract.ASPECTS`) 중 하나, 또는 null
    question         구매자가 물을 법한 질문
    answer           리뷰가 주는 답 (질문만 있으면 사용자가 답을 직접 찾아야 한다, PRD §6)
    condition        {skinType, skinTrouble, option} — 축마다 null(무관) / "미기재" / 코드
    direction        positive | negative | mixed | neutral — **사람이 고르지 않고 근거에서 계산한다** (derive_direction)
    evidence[]       {reviewId, quote, stance}. quote 는 원문 부분문자열, stance 는 그 문장이 주제에 대해
                     positive | negative | neutral 인지 (답 문장과 무관한 절대값). neutral = 주제를 말하지만
                     방향이 없는 문장("지속력은 보통") — 태깅 계약(PER-175)의 polarity 3종과 같은 어휘
    failureReasons[] 이 claim 이 실패 사례라면 PER-177 8유형 키. 정상 claim 은 []
    evaluation       complete | not_evaluable
    notes            판단 메모. not_evaluable 이면 필수
    minutesSpent     건당 소요 시간 (PER-179 가 일정 추정 근거로 요구)
    source           human | candidate_accepted | candidate_edited | candidate_rejected (출처, B안)
    candidateId      모델 후보에서 왔으면 그 후보 ID, 사람이 만들었으면 null

## 왜 이런 규칙인가

- **조건 null 과 "미기재"는 다르다.** null 은 "이 주장은 그 축과 무관하다"(제품 전체), "미기재"는
  "프로필을 안 밝힌 리뷰들의 세그먼트"다 (`contracts.MISSING_SEGMENT`). 둘을 합치면 미기재
  리뷰가 모든 세그먼트의 근거로 새어 들어간다 — PER-177 §3 '조건 누락' 의 정확한 발생 경로.
- **조건부 주장의 근거는 그 세그먼트 리뷰만이다.** 건성(A02) 주장에 미기재 리뷰를 넣으면
  에러다. 미기재는 "모든 조건"이 아니다.
- **인용은 원문 부분문자열이어야 한다** (`tag_contract.is_verbatim`, 보이지 않는 문자만
  접는다). v4 88.8% 의 재발 경로를 라벨 단계에서부터 막는다.
- **근거 카운트는 리뷰 수가 아니라 고유 작성자 수다** (PER-170). `support_counts()` 가 센다.
- **침묵은 근거가 아니다.** 주제를 언급하지 않은 리뷰는 긍정도 부정도 아니다 (`silentAuthors` 로 따로 센다).
  **중립 언급은 침묵이 아니다** (v3) — "보통이에요"는 주제를 말한 것이라 D(언급 작성자)에 들어가고 U+/U− 에는 안 들어간다.
  중립을 근거에서 빼면 그 작성자가 침묵으로 잘못 세어진다.
  "대부분 트러블이 없다"처럼 언급 없음을 부정 증거로 일반화하면 `unsupported_claim` 이다 — 안 생겼다는 **명시 문장**만 긍정 근거다.
- **failureReasons 는 정렬된 상태로 저장한다** — 첫 원소가 대표 `failureReason` 이므로
  순서가 곧 판정이다. 어휘는 코드 상수가 아니라 `pipeline/failure_taxonomy.json` 이다.
- **방향을 별점에서 가져오지 않는다.** 별점은 라벨 필드에 없다. 화면에는 보이지만 근거는 원문이다.
- **입장(stance)은 답 문장에 상대적이지 않다.** v1 의 support/oppose 는 답을 어떻게 쓰느냐에 따라 뒤집혀 라벨러가
  헷갈렸다(2026-09-09 B01 실측). v2 는 문장 자체의 긍/부정이고, direction 은 고유 작성자 기준으로
  긍정만 → positive, 부정만 → negative, 둘 다 → mixed 로 **계산**한다. 반대 1명도 mixed 다 — 골든셋은 방향별
  수를 보존하고, 소수 반대를 무시할지는 PER-186 게이트 정책이 정한다.

위반은 전부 `GoldenContractError` 다 — 조용히 고치거나 건너뛰지 않는다. 라벨 도구는 위반 라벨을
파일에 쓰지 않고, 게이트는 위반이 하나라도 있으면 종료 코드 1 이다.
"""
from __future__ import annotations

import collections
import json
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from codebook import UnknownConditionCodeError, load_codebook  # noqa: E402
from contracts import CONDITION_AXES, MISSING_SEGMENT  # noqa: E402
from tag_contract import ASPECTS, is_verbatim  # noqa: E402

ROOT = Path(__file__).parents[1]
GOLDEN_SCHEMA_VERSION = "concern-golden-v3"  # v2: stance 절대값 + direction 계산 · v3: neutral 입장 추가
TAXONOMY_PATH = Path(__file__).parent / "failure_taxonomy.json"

DIRECTIONS = ("positive", "negative", "mixed", "neutral")
STANCES = ("positive", "negative", "neutral")
EVALUATIONS = ("complete", "not_evaluable")
CODED_AXES = ("skinType", "skinTrouble")

LABEL_FIELDS = (
    "labelId", "bundleId", "productId", "aspect", "question", "answer", "condition",
    "direction", "evidence", "failureReasons", "evaluation", "notes", "minutesSpent",
    "source", "candidateId",
)

# 라벨의 출처 (B안, PER-178). judge 일치율(PER-197)·v4 비교(PER-201)는 출처별로 갈라 본다.
#   human               사람이 번들을 읽고 직접 만들었다 — 후보에 없던 claim (재현율의 근거)
#   candidate_accepted  모델 후보를 그대로 채택
#   candidate_edited    모델 후보를 고쳐 채택
#   candidate_rejected  모델 후보를 기각 — failureReasons 가 있어야 한다 (음성 정답)
SOURCES = ("human", "candidate_accepted", "candidate_edited", "candidate_rejected")
CANDIDATE_SOURCES = ("candidate_accepted", "candidate_edited", "candidate_rejected")


class GoldenContractError(ValueError):
    """라벨이 골든셋 계약을 위반했다. 라벨을 고치지 말고 세운다."""


class TaxonomyError(ValueError):
    """`failure_taxonomy.json` 자체가 계약을 위반했다."""


@dataclass(frozen=True)
class FailureTaxonomy:
    version: str
    severity_order: tuple[str, ...]
    types: tuple[dict, ...]

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(t["key"] for t in self.types)

    def label(self, key: str) -> str:
        for t in self.types:
            if t["key"] == key:
                return t["label"]
        raise TaxonomyError(f"택소노미에 없는 키: {key!r}")

    def sort(self, keys: list[str]) -> list[str]:
        """대표 사유가 첫 원소가 되도록 severity → order 로 정렬한다 (PER-177 §4-3)."""
        rank = {
            t["key"]: (self.severity_order.index(t["severity"]), t["order"]) for t in self.types
        }
        return sorted(keys, key=lambda k: rank[k])


def load_failure_taxonomy(path: Path | str = TAXONOMY_PATH) -> FailureTaxonomy:
    try:
        data = json.loads(Path(path).read_text())
    except FileNotFoundError:
        raise TaxonomyError(f"택소노미 파일이 없다: {path}") from None
    version = data.get("version")
    if not isinstance(version, str) or not version.strip():
        raise TaxonomyError("택소노미에 version 이 없다 — 버전 없는 설정은 결과를 귀속시킬 수 없다")
    order = tuple(data.get("severityOrder") or ())
    if not order:
        raise TaxonomyError("severityOrder 가 없다")
    types = data.get("types") or []
    if not types:
        raise TaxonomyError("types 가 비었다")
    keys = [t.get("key") for t in types]
    if len(set(keys)) != len(keys):
        raise TaxonomyError(f"키 중복: {[k for k, n in collections.Counter(keys).items() if n > 1]}")
    for t in types:
        for field in ("key", "label", "severity", "order"):
            if field not in t:
                raise TaxonomyError(f"types 항목에 {field} 가 없다: {t}")
        if t["severity"] not in order:
            raise TaxonomyError(f"{t['key']}: severity {t['severity']!r} 가 severityOrder 밖이다")
    return FailureTaxonomy(version=version, severity_order=order, types=tuple(types))


# --- 검증 ---


def _fail(label: dict, message: str) -> None:
    raise GoldenContractError(f"[labelId={label.get('labelId')!r}] {message}")


def _require_text(label: dict, field: str) -> str:
    value = label.get(field)
    if not isinstance(value, str) or not value.strip():
        _fail(label, f"{field} 가 비었거나 문자열이 아니다")
    return value.strip()


def _review_segments(review: dict, axis: str) -> tuple[str, ...]:
    cond = review["condition"][axis]
    if axis == "skinTrouble":
        return tuple(cond["segments"])
    return (cond["segment"],)


def _validate_condition(label: dict, bundle: dict, reviews: dict[int, dict]) -> dict:
    """축마다 null / 미기재 / 코드. 셀 번들이면 그 셀과 같아야 한다."""
    cond = label.get("condition")
    if not isinstance(cond, dict) or set(cond) != set(CONDITION_AXES):
        _fail(label, f"condition 은 축 {list(CONDITION_AXES)} 를 정확히 가져야 한다 (받은 값 {cond!r})")

    codebook = load_codebook()
    normalized: dict = {}
    for axis in CONDITION_AXES:
        value = cond[axis]
        if value is None:
            normalized[axis] = None
            continue
        if axis == "skinTrouble":
            if not isinstance(value, list) or not value:
                _fail(label, f"condition.skinTrouble 은 null 또는 코드 배열이어야 한다 (받은 값 {value!r})")
            values = list(value)
        else:
            if not isinstance(value, str) or not value.strip():
                _fail(label, f"condition.{axis} 는 null / '{MISSING_SEGMENT}' / 코드여야 한다 (받은 값 {value!r})")
            values = [value.strip()]
        if MISSING_SEGMENT in values and len(values) > 1:
            _fail(label, f"condition.{axis}: '{MISSING_SEGMENT}' 는 코드와 섞을 수 없다")
        for v in values:
            if v == MISSING_SEGMENT:
                continue
            if axis in CODED_AXES:
                try:
                    codebook.assert_code(axis, v)
                except UnknownConditionCodeError as e:
                    _fail(label, f"condition.{axis}: {e}")
        normalized[axis] = values if axis == "skinTrouble" else values[0]

    scope = bundle.get("scope") or {}
    if scope.get("axis"):
        axis, segment = scope["axis"], scope["segment"]
        got = normalized.get(axis)
        got_list = got if isinstance(got, list) else [got]
        if segment not in got_list:
            _fail(label, (
                f"셀 번들 {bundle['bundleId']} 은 {axis}={segment!r} 셀인데 라벨의 condition.{axis} 는 "
                f"{got!r} 다. 셀 번들의 주장은 그 세그먼트를 조건으로 가져야 한다 — 제품 전체 "
                "주장은 제품 번들에서 만든다"
            ))

    # 조건부 주장의 근거는 그 세그먼트의 리뷰만 (PER-177 §3: 미기재는 '모든 조건'이 아니다)
    for axis in CONDITION_AXES:
        want = normalized[axis]
        if want is None:
            continue
        wants = want if isinstance(want, list) else [want]
        for ev in label.get("evidence") or []:
            review = reviews.get(ev.get("reviewId"))
            if review is None:
                continue  # reviewId 오류는 evidence 검증이 잡는다
            have = _review_segments(review, axis)
            missing = [w for w in wants if w not in have]
            if missing:
                _fail(label, (
                    f"reviewId={ev['reviewId']} 는 {axis} 가 {list(have)} 인데 주장의 조건은 {wants} 다. "
                    "조건부 주장의 근거는 그 세그먼트 리뷰만 쓴다 — 미기재 리뷰를 특정 조건의 "
                    "지지로 세지 않는다 (PER-177 §3)"
                ))
    return normalized


def _validate_evidence(label: dict, bundle_reviews: dict[int, dict]) -> list[dict]:
    evidence = label.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        _fail(label, "evidence 가 비었다 — 근거 없는 claim 은 골든셋에도 들어가지 않는다 (PRD §1.2)")
    seen: set[int] = set()
    out: list[dict] = []
    for i, ev in enumerate(evidence):
        if not isinstance(ev, dict):
            _fail(label, f"evidence[{i}] 는 객체여야 한다")
        rid = ev.get("reviewId")
        if isinstance(rid, bool) or not isinstance(rid, int):
            _fail(label, f"evidence[{i}].reviewId 는 정수여야 한다 (받은 값 {rid!r})")
        if rid not in bundle_reviews:
            _fail(label, (
                f"evidence[{i}].reviewId={rid} 는 이 번들에 없다. 라벨은 번들 안에서만 만든다 — "
                "번들 밖 리뷰를 보고 만들면 표본 규칙이 깨진다"
            ))
        if rid in seen:
            _fail(label, f"evidence 에 reviewId={rid} 가 두 번 있다")
        seen.add(rid)
        stance = ev.get("stance")
        if stance not in STANCES:
            _fail(label, f"evidence[{i}].stance 는 {list(STANCES)} 중 하나여야 한다 (받은 값 {stance!r})")
        quote = ev.get("quote")
        if not isinstance(quote, str) or not quote.strip():
            _fail(label, f"evidence[{i}].quote 가 비었다 — 인용 없는 근거는 귀속을 검증할 수 없다")
        content = bundle_reviews[rid]["raw"]["content"]
        if not is_verbatim(quote, content):
            _fail(label, (
                f"evidence[{i}] (reviewId={rid}) 의 인용이 원문 부분문자열이 아니다: {quote!r}. "
                "요약·재구성은 인용이 아니다 — 원문에서 그대로 복사하라"
            ))
        out.append({"reviewId": rid, "quote": quote, "stance": stance})
    return out


def validate_label(
    label: dict,
    bundle: dict,
    taxonomy: FailureTaxonomy | None = None,
) -> dict:
    """라벨 1건을 계약에 비춰 검증하고 정규화한다. 위반은 `GoldenContractError`.

    `bundle` 은 `sample_concern_golden.py` 가 만든 번들 레코드다 (리뷰 전문 포함).
    """
    taxonomy = taxonomy or load_failure_taxonomy()
    if not isinstance(label, dict):
        raise GoldenContractError(f"라벨은 객체여야 한다 (받은 값 {type(label).__name__})")
    unknown = sorted(set(label) - set(LABEL_FIELDS))
    if unknown:
        _fail(label, f"계약에 없는 필드 {unknown}. 필드를 늘리려면 golden_contract.LABEL_FIELDS 와 문서를 함께 고친다")
    absent = sorted(set(LABEL_FIELDS) - set(label))
    if absent:
        _fail(label, f"필수 필드 누락 {absent}")

    label_id = _require_text(label, "labelId")
    bundle_id = _require_text(label, "bundleId")
    if bundle_id != bundle["bundleId"]:
        _fail(label, f"bundleId {bundle_id!r} 가 번들 {bundle['bundleId']!r} 와 다르다")
    if label.get("productId") != bundle["productId"]:
        _fail(label, f"productId {label.get('productId')!r} 가 번들의 제품 {bundle['productId']!r} 와 다르다")

    aspect = label.get("aspect")
    if aspect is not None and aspect not in ASPECTS:
        _fail(label, f"aspect {aspect!r} 는 14종 택소노미 밖이다 (동결, PER-175). 없는 주제면 null 로 두고 notes 에 적는다")

    question = _require_text(label, "question")
    answer = _require_text(label, "answer")

    direction = label.get("direction")
    if direction not in DIRECTIONS:
        _fail(label, f"direction 은 {list(DIRECTIONS)} 중 하나여야 한다 (받은 값 {direction!r})")

    bundle_reviews = {r["reviewId"]: r for r in bundle["reviews"]}
    evidence = _validate_evidence(label, bundle_reviews)
    condition = _validate_condition(label, bundle, bundle_reviews)

    derived = derive_direction(evidence, bundle)
    if direction != derived:
        _fail(label, (
            f"direction={direction!r} 인데 근거에서 계산한 방향은 {derived!r} 다. direction 은 사람이 고르지 않는다 — "
            "근거의 고유 작성자 기준으로 긍정만 → positive, 부정만 → negative, 둘 다 → mixed, 중립만 → neutral"
        ))

    reasons = label.get("failureReasons")
    if not isinstance(reasons, list):
        _fail(label, f"failureReasons 는 배열이어야 한다 (정상 claim 은 []). 받은 값 {reasons!r}")
    bad = [k for k in reasons if k not in taxonomy.keys]
    if bad:
        _fail(label, f"failureReasons 에 택소노미({taxonomy.version}) 밖의 키 {bad}. 허용: {list(taxonomy.keys)}")
    if len(set(reasons)) != len(reasons):
        _fail(label, f"failureReasons 에 중복 키가 있다: {reasons}")
    if reasons != taxonomy.sort(reasons):
        _fail(label, f"failureReasons 는 대표 사유가 앞에 오도록 정렬돼야 한다: {taxonomy.sort(reasons)}")

    evaluation = label.get("evaluation")
    if evaluation not in EVALUATIONS:
        _fail(label, f"evaluation 은 {list(EVALUATIONS)} 중 하나여야 한다 (받은 값 {evaluation!r})")
    notes = label.get("notes")
    if notes is not None and not isinstance(notes, str):
        _fail(label, "notes 는 문자열 또는 null 이어야 한다")
    if evaluation == "not_evaluable" and not (notes or "").strip():
        _fail(label, "evaluation=not_evaluable 이면 notes 에 왜 판정할 수 없는지 적어야 한다")

    minutes = label.get("minutesSpent")
    if isinstance(minutes, bool) or not isinstance(minutes, (int, float)) or minutes <= 0:
        _fail(label, f"minutesSpent 는 양수여야 한다 (받은 값 {minutes!r}) — PER-179 가 건당 소요 시간을 요구한다")

    source = label.get("source")
    if source not in SOURCES:
        _fail(label, f"source 는 {list(SOURCES)} 중 하나여야 한다 (받은 값 {source!r}). 출처 없는 라벨은 judge 일치율을 출처별로 갈라 볼 수 없다")
    candidate_id = label.get("candidateId")
    if source in CANDIDATE_SOURCES:
        if not isinstance(candidate_id, str) or not candidate_id.strip():
            _fail(label, f"source={source} 인데 candidateId 가 없다 — 어느 후보였는지 남겨야 한다")
        if source == "candidate_rejected" and not reasons:
            _fail(label, "source=candidate_rejected 인데 failureReasons 가 비었다 — 기각 사유가 없는 기각은 음성 정답이 아니다")
    elif candidate_id is not None:
        _fail(label, f"source=human 인데 candidateId={candidate_id!r} 가 있다. 후보에서 왔으면 candidate_* 로 적는다")

    return {
        "labelId": label_id,
        "bundleId": bundle_id,
        "productId": bundle["productId"],
        "aspect": aspect,
        "question": question,
        "answer": answer,
        "condition": condition,
        "direction": direction,
        "evidence": evidence,
        "failureReasons": list(reasons),
        "evaluation": evaluation,
        "notes": (notes or "").strip() or None,
        "minutesSpent": minutes,
        "source": source,
        "candidateId": candidate_id.strip() if isinstance(candidate_id, str) else None,
    }


def _authors_by_stance(evidence: list[dict], bundle: dict) -> dict[str, set[str]]:
    authors = {r["reviewId"]: r["derived"]["authorKey"] for r in bundle["reviews"]}
    out: dict[str, set[str]] = {"positive": set(), "negative": set(), "neutral": set()}
    for ev in evidence:
        if ev.get("stance") in out and ev.get("reviewId") in authors:
            out[ev["stance"]].add(authors[ev["reviewId"]])
    return out


def derive_direction(evidence: list[dict], bundle: dict) -> str:
    """근거의 고유 작성자 기준 방향. 사람이 고르는 값이 아니다."""
    by = _authors_by_stance(evidence, bundle)
    if by["positive"] and by["negative"]:
        return "mixed"
    if by["positive"]:
        return "positive"
    if by["negative"]:
        return "negative"
    return "neutral"  # 중립 언급만 있다 — 부정으로 바꾸지 않는다 (PER-177 §2 경계 규칙)


def support_counts(label: dict, bundle: dict) -> dict:
    """근거 카운트 — 리뷰 수가 아니라 **고유 작성자 수** (PER-170)."""
    by = _authors_by_stance(label["evidence"], bundle)
    bundle_authors = {r["derived"]["authorKey"] for r in bundle["reviews"]}
    spoke = by["positive"] | by["negative"] | by["neutral"]
    return {
        "positiveAuthors": len(by["positive"]),   # U+
        "negativeAuthors": len(by["negative"]),   # U-
        "neutralAuthors": len(by["neutral"]),     # 주제를 말했지만 방향 없음 — D 에는 들어가고 U 에는 안 들어간다
        "spokeAuthors": len(spoke),               # D — 이 주제를 말한 작성자
        "silentAuthors": len(bundle_authors - spoke),  # S - D — 말하지 않은 작성자. 어느 쪽 근거도 아니다
        "evidenceReviews": len(label["evidence"]),
        "bundleAuthors": len(bundle_authors),     # S
    }


def validate_labels(labels: list[dict], bundles: dict[str, dict], taxonomy: FailureTaxonomy | None = None) -> list[dict]:
    """라벨 파일 전체. labelId 유일성까지 본다."""
    taxonomy = taxonomy or load_failure_taxonomy()
    seen: set[str] = set()
    out: list[dict] = []
    for i, label in enumerate(labels):
        if not isinstance(label, dict):
            raise GoldenContractError(f"labels[{i}] 는 객체여야 한다")
        bundle = bundles.get(label.get("bundleId"))
        if bundle is None:
            raise GoldenContractError(
                f"[labelId={label.get('labelId')!r}] bundleId {label.get('bundleId')!r} 가 표본에 없다"
            )
        normalized = validate_label(label, bundle, taxonomy)
        if normalized["labelId"] in seen:
            raise GoldenContractError(f"labelId 중복: {normalized['labelId']!r}")
        seen.add(normalized["labelId"])
        out.append(normalized)
    return out
