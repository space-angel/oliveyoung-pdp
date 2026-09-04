"""
v5 태깅 계약 — (리뷰 × 주제) 단위 aspect/polarity (PER-175 / PRD §3, §4-3).

이 층의 출력은 "리뷰 1건에 감성 1개"가 아니라 **(리뷰 × aspect) 쌍**이다.
한 리뷰가 "발색은 좋은데 지속력은 별로"라고 말할 수 있기 때문이다 (§4-3).

멈추는 조건 (조용한 폴백 금지 — 태그를 버리지 말고 에러를 낸다)
  - 택소노미에 없는 aspect            → TagContractError
  - polarity 가 3종 밖의 값            → TagContractError
  - snippet 이 원문 부분문자열이 아님   → TagContractError  (v4 88.8% 였던 지점)
  - 같은 리뷰에 같은 aspect 두 번       → TagContractError
  - skinTypeHint 가 코드북 밖의 값      → TagContractError
  - 입력에 없는 reviewId               → TagContractError

`skinTypeHint` 를 라벨("건성")이 아니라 **코드(A02)** 로 받는 이유:
condition.skinType 과 같은 어휘여야 집계에서 합쳐진다. v4 는 라벨 문자열로 받아
집계 단계에서 조건축과 만나지 못했고, 그게 카테고리 분포 FAIL 의 원인이었다.

## 눈에 안 보이는 문자 정규화 (`fold_invisible`)

원문 25,000건 실측: 개행이 `\r\n` 인 리뷰 11,582건(46.3%), 폭 없는 공백·비분리
공백 등 공백 변종이 섞인 리뷰 268건(1.1%). 태거가 화면에서 본 대로 정확히 복사해도
이 문자들 때문에 부분문자열 판정이 깨진다 — v4 인용 원문성 88.8% 에 이 몫이 섞여 있다.

그래서 **태거에게 주는 본문과 대조하는 본문 양쪽에 같은 접기를 적용한다.**

  개행 `\r\n`·`\r` → `\n`  ·  Zs 계열 공백(U+00A0, U+3000 …) → ASCII 공백
  폭 없는 공백 U+200B·U+FEFF 제거

접는 것은 **보이지 않는 문자뿐**이다. 글자·띄어쓰기 수는 그대로다 — "요약·재구성은
인용이 아니다"라는 규칙은 조금도 느슨해지지 않는다. U+200D(ZWJ)는 건드리지 않는다:
이모지 결합에 쓰이고 246회 출현분이 전부 그 용도다.

`raw.content` 자체는 고치지 않는다 (입력 계약상 원문층은 무가공). 접기는 태깅 입력과
대조에만 쓰는 파생 뷰다.
"""
from __future__ import annotations

import hashlib
import json
import unicodedata
from pathlib import Path

TAG_SCHEMA_VERSION = "tag-v1"
MAX_ASPECTS_PER_REVIEW = 5
SNIPPET_MAX_LEN = 40

ROOT = Path(__file__).parents[1]
PROMPT_PATH = Path(__file__).parent / "prompts/tag/v1.md"
CODEBOOK_PATH = ROOT / "data/input/skin_codebook.json"

# v4 가 쓴 14종을 그대로 승계한다. 바꾸면 v4 대비 비교(PER-201)의 축이 흔들린다.
# 택소노미 변경은 별도 이슈에서 근거를 대고 한다.
ASPECTS: tuple[str, ...] = (
    "보습감",
    "흡수력",
    "발림감/텍스처",
    "지속성",
    "향",
    "광택/윤기",
    "커버력",
    "발색",
    "유분/번들거림",
    "트러블/자극",
    "탄력/탱탱함",
    "분사력",
    "밀착력",
    "가성비",
)
POLARITIES: tuple[str, ...] = ("positive", "negative", "neutral")


class TagContractError(ValueError):
    """태깅 결과가 계약을 위반했다. 해당 태그를 버리는 게 아니라 실행을 세운다."""


def skin_type_codes() -> tuple[str, ...]:
    codebook = json.loads(CODEBOOK_PATH.read_text())
    return tuple(codebook["skinType"])


def prompt_version() -> dict:
    """프롬프트 파일의 신원. meta 에 남겨야 점수가 프롬프트에 귀속된다 (§5-2)."""
    body = PROMPT_PATH.read_bytes()
    return {
        "path": str(PROMPT_PATH.relative_to(ROOT)),
        "sha256": hashlib.sha256(body).hexdigest(),
        "schemaVersion": TAG_SCHEMA_VERSION,
    }


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


ZERO_WIDTH = ("\u200b", "\ufeff")


def fold_invisible(text: str) -> str:
    """보이지 않는 문자만 접는다. 글자와 띄어쓰기 수는 그대로 둔다."""
    text = nfc(text).replace("\r\n", "\n").replace("\r", "\n")
    for ch in ZERO_WIDTH:
        text = text.replace(ch, "")
    return "".join(" " if unicodedata.category(c) == "Zs" else c for c in text)


def tagging_text(content: str) -> str:
    """태거에게 주는 본문. 대조와 같은 함수를 써야 인용이 맞는다."""
    return fold_invisible(content)


def is_verbatim(snippet: str, content: str) -> bool:
    """인용은 원문 부분문자열이어야 한다 (CLAUDE.md 서술 규칙)."""
    return fold_invisible(snippet) in fold_invisible(content)


def is_verbatim_loose(snippet: str, content: str) -> bool:
    """공백·개행만 접어서 본 완화 판정. 통과 기준이 아니라 실패 원인 분류용이다."""
    squeeze = lambda s: "".join(fold_invisible(s).split())  # noqa: E731
    return squeeze(snippet) in squeeze(content)


def validate_tags(tags: list[dict], reviews: dict[int, dict]) -> list[dict]:
    """태그 목록을 계약에 비춰 검증하고 정규화한다. 위반이 하나라도 있으면 세운다.

    reviews: reviewId → v5 레코드 (`content` 를 원문 대조에 쓴다)
    """
    codes = set(skin_type_codes())
    seen: set[tuple[int, str]] = set()
    per_review: dict[int, int] = {}
    out: list[dict] = []

    for i, tag in enumerate(tags):
        where = f"tags[{i}] reviewId={tag.get('reviewId')}"
        review_id = tag.get("reviewId")
        if review_id not in reviews:
            raise TagContractError(f"{where}: 입력에 없는 reviewId")

        aspect = tag.get("aspect")
        if aspect not in ASPECTS:
            raise TagContractError(f"{where}: 택소노미 밖의 aspect {aspect!r}")

        polarity = tag.get("polarity")
        if polarity not in POLARITIES:
            raise TagContractError(f"{where}: polarity {polarity!r} 는 {POLARITIES} 중 하나여야 한다")

        key = (review_id, aspect)
        if key in seen:
            raise TagContractError(f"{where}: 같은 리뷰에 aspect {aspect!r} 가 두 번. 방향이 갈리면 aspect 를 나눠라")
        seen.add(key)

        per_review[review_id] = per_review.get(review_id, 0) + 1
        if per_review[review_id] > MAX_ASPECTS_PER_REVIEW:
            raise TagContractError(f"{where}: 리뷰당 aspect 상한 {MAX_ASPECTS_PER_REVIEW} 초과")

        snippet = tag.get("snippet") or ""
        content = reviews[review_id]["raw"]["content"]
        if not snippet:
            raise TagContractError(f"{where}: snippet 이 비어 있다 — 근거 없는 태그는 만들지 않는다")
        if not is_verbatim(snippet, content):
            reason = "띄어쓰기가 바뀜" if is_verbatim_loose(snippet, content) else "원문에 없음"
            raise TagContractError(f"{where}: snippet 이 원문 부분문자열이 아니다 ({reason}) {snippet!r}")

        hint = tag.get("skinTypeHint")
        if hint is not None and hint not in codes:
            raise TagContractError(f"{where}: skinTypeHint {hint!r} 는 코드북(A01~A07) 밖이다. 라벨이 아니라 코드로 낸다")

        out.append(
            {
                "reviewId": review_id,
                "productId": reviews[review_id]["productId"],
                "aspect": aspect,
                "polarity": polarity,
                "snippet": fold_invisible(snippet),
                "skinTypeHint": hint,
            }
        )
    return out
