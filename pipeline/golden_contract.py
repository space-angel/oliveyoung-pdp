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
    direction        positive | negative | mixed   (별점이 아니라 근거의 방향)
    evidence[]       {reviewId, quote, stance}. quote 는 원문 부분문자열, stance 는 support|oppose
    failureReasons[] 이 claim 이 실패 사례라면 PER-177 8유형 키. 정상 claim 은 []
    evaluation       complete | not_evaluable
    notes            판단 메모. not_evaluable 이면 필수
    minutesSpent     건당 소요 시간 (PER-179 가 일정 추정 근거로 요구)

## 왜 이런 규칙인가

- **조건 null 과 "미기재"는 다르다.** null 은 "이 주장은 그 축과 무관하다"(제품 전체), "미기재"는
  "프로필을 안 밝힌 리뷰들의 세그먼트"다 (`contracts.MISSING_SEGMENT`). 둘을 합치면 미기재
  리뷰가 모든 세그먼트의 근거로 새어 들어간다 — PER-177 §3 '조건 누락' 의 정확한 발생 경로.
- **조건부 주장의 근거는 그 세그먼트 리뷰만이다.** 건성(A02) 주장에 미기재 리뷰를 넣으면
  에러다. 미기재는 "모든 조건"이 아니다.
- **인용은 원문 부분문자열이어야 한다** (`tag_contract.is_verbatim`, 보이지 않는 문자만
  접는다). v4 88.8% 의 재발 경로를 라벨 단계에서부터 막는다.
- **근거 카운트는 리뷰 수가 아니라 고유 작성자 수다** (PER-170). `support_counts()` 가 센다.
- **failureReasons 는 정렬된 상태로 저장한다** — 첫 원소가 대표 `failureReason` 이므로
  순서가 곧 판정이다. 어휘는 코드 상수가 아니라 `pipeline/failure_taxonomy.json` 이다.
- **방향을 별점에서 가져오지 않는다.** 별점은 라벨 필드에 없다. 화면에는 보이지만 근거는 원문이다.

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
GOLDEN_SCHEMA_VERSION = "concern-golden-v1"
TAXONOMY_PATH = Path(__file__).parent / "failure_taxonomy.json"

DIRECTIONS = ("positive", "negative", "mixed")
STANCES = ("support", "oppose")
EVALUATIONS = ("complete", "not_evaluable")
CODED_AXES = ("skinType", "skinTrouble")

LABEL_FIELDS = (
    "labelId", "bundleId", "productId", "aspect", "question", "answer", "condition",
    "direction", "evidence", "failureReasons", "evaluation", "notes", "minutesSpent",
)


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

    stances = collections.Counter(ev["stance"] for ev in evidence)
    if stances["support"] == 0:
        _fail(label, "support 근거가 하나도 없다 — 답을 지지하는 리뷰가 없으면 claim 이 아니다")
    if direction == "mixed" and stances["oppose"] == 0:
        _fail(label, "direction=mixed 인데 oppose 근거가 없다. 한쪽만 있으면 positive/negative 다")

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
    }


def support_counts(label: dict, bundle: dict) -> dict:
    """근거 카운트 — 리뷰 수가 아니라 **고유 작성자 수** (PER-170)."""
    authors = {r["reviewId"]: r["derived"]["authorKey"] for r in bundle["reviews"]}
    by_stance: dict[str, set[str]] = {"support": set(), "oppose": set()}
    for ev in label["evidence"]:
        by_stance[ev["stance"]].add(authors[ev["reviewId"]])
    return {
        "supportAuthors": len(by_stance["support"]),
        "opposeAuthors": len(by_stance["oppose"]),
        "evidenceReviews": len(label["evidence"]),
        "bundleAuthors": len(set(authors.values())),
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
