"""
옵션 정규화 — 색상·호수 동일성 (PER-182 / PRD §4-1).

게이트1이 "이 리뷰가 정말 **이 옵션**에 대한 것인가"를 판정하려면 옵션 문자열이
먼저 같은 어휘로 정리돼야 한다. 스냅샷의 `option` 은 기재율 68.1%(17,016건)에
고유값 797개인데, 그 797개가 797개의 색상을 뜻하지 않는다 — 같은 색상이
판촉 포장만 달리해 여러 문자열로 흩어져 있다.

  '허쉬 레드(뉴트럴 베스트)' · '[기획] 허쉬 레드+파우치' · '[기획] 허쉬 레드+미니립스틱'
  → 셋 다 3CE 캐시미어 허그 립스틱 **허쉬 레드** 한 색이다

정리하지 않으면 색상 질문의 근거가 색상마다 3~9조각으로 쪼개져 충분성 게이트
(PER-186, N_min=8)를 넘지 못한다. 실측으로 797개 문자열 → **450개 색상 키**다.

## 무엇이 위험한가 — 방향이 다른 두 오류

  과소병합  같은 색상이 안 합쳐진다 → 근거가 줄어 침묵한다. **측정 가능한 손실**
  과대병합  다른 색상이 합쳐진다   → 21호 근거가 13호 질문에 붙는다. **잘못된 귀속**
            (§7-1 치명 2유형)

둘의 값이 다르므로 규칙은 전부 **보수적인 쪽**으로 세웠다. 못 알아본 괄호는 지우지
않고 식별자로 남긴다 — 지우는 쪽이 과대병합이기 때문이다.

## 규칙 (전부 결정론적 · LLM 없음)

  1. 괄호/대괄호 묶음을 떼어낸다
  2. 묶음이 **판촉**이면 버린다 — `+` 로 시작(사은품)하거나, 판촉 토큰
     (`기획`·`단품`·`증정`…)을 담고 있거나, 용량·수량뿐인 경우
  3. 살아남은 묶음도 **코퍼스가 판촉이라고 말하면** 버린다 (아래 두 규칙)
  4. 남은 본문은 첫 `+` 조각만 쓴다 — `+` 뒤는 사은품이다
     ('글레이즈드 도넛 + 브러쉬 2ea' → '글레이즈드 도넛')
  5. 판촉 토큰·용량·구두점을 지우고 공백을 접는다
     ('03 베어그레이프' 와 '03 베어 그레이프' 는 같은 색이다)
  6. 남은 게 없으면 색상이 아니라 **용량/구성**이다 (`pack`)
  7. 앞머리 숫자를 호수로 떼고 `"{호수}|{이름}"` 을 키로 삼는다

**공백 접기는 여기서만 한다.** 인용 대조(`tag_contract.fold_invisible`)는 공백
squeeze 를 금지하는데, 그건 편집된 인용을 걸러야 하기 때문이다. 옵션은 인용이
아니라 식별자라서 반대 방향이 맞다 — '베어그레이프'/'베어 그레이프' 를 가르면
같은 색이 쪼개진다.

## 코퍼스가 정하는 두 규칙 (`OptionIndex`)

판촉 어휘를 아무리 적어도 콜라보 이름(`[갸루키티]`·`[미피]`·`[망곰]`)은 끝이 없다.
목록을 늘려 잡는 대신 **제품 안에서 그 묶음이 식별자처럼 행동하는지**를 본다.

  래퍼-A  한 묶음이 서로 다른 이름 2개 이상에 붙는다 → 색상과 직교하므로 판촉이다
          ('[미피]' 는 40 베이지그레이프·02 누카다미아·03 베어그레이프에 붙는다)
  래퍼-B  그 묶음을 떼면 **그 제품에 이미 있는 색상 이름**이 된다 → 같은 색의 포장이다
          ('[갸루키티]30호 클래시누드블러링' → '30호 클래시누드블러링' 이 이미 있다)

반대로 못 알아본 묶음은 **식별자로 남는다** — 닥터지 수딩크림의 `70ml(튜브)+30ml(튜브)`
와 `70ml(jar)+30ml(튜브)` 는 용기가 달라 다른 키가 된다. 규칙이 의도한 동작이다:
모르면 가르는 쪽이 안전하다.

용량·수량뿐인 묶음(`(8구)`·`(12g)`)은 판촉으로 보고 버린다. 그래서
`팔레트 케이스(8구)`·`(4구)`·`(3구)` 는 한 키로 합쳐진다. **색상 병합이 아니라
구성 병합이므로 잘못된 귀속을 만들지 않는다** — 색상 질문이 구성을 범위로 걸 수
없기 때문이다 (`resolve_scope` 가 에러를 낸다).

## 호수는 이름보다 강하다

호수를 버리고 이름만 쓰면 헤라 블랙 쿠션의 `21N1`·`17N1`·`13N1` 이 전부 `N1` 로
합쳐진다(254건 오병합). 그래서 키에 호수를 남긴다. 다만 같은 이름이 **정확히 한**
호수로만 나타나면 호수 없는 표기를 그 호수로 합친다 ('로즈코티지' → '39|로즈코티지').
호수가 둘 이상이면 합치지 않는다 — 그게 서로 다른 색이다.
"""
from __future__ import annotations

import collections
import json
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

MARKERS_PATH = Path(__file__).parent / "option_markers.json"

# 옵션 종류. `pack` 은 색상이 아니라 용량/구성이다 ('50ml (+35ml)', '본품+리필').
KIND_SHADE = "shade"
KIND_PACK = "pack"
KIND_UNSTATED = "unstated"

_GROUP = re.compile(r"[\[(]([^\])]*)[\])]?")
# 앞머리 호수: '03 베어그레이프' · '30호 클래시누드' · '01코렉트 베이지'.
# 뒤에 숫자가 아닌 글자가 와야 한다 — '40+40ml' 의 '40' 을 호수로 읽지 않기 위해서다.
_LEADING_NUMBER = re.compile(r"^(\d{1,3})\s*호?\s*(\D.*)$")
_NON_WORD = re.compile(r"[^0-9A-Za-z가-힣]+")


class OptionError(ValueError):
    """옵션 정규화 계약 위반. 조용한 폴백 금지."""


class UnknownOptionScopeError(OptionError):
    """질문이 건 옵션 범위가 그 제품에서 관측되지 않았다.

    임의로 통과시키면 존재하지 않는 색상에 근거가 붙고, 임의로 전부 탈락시키면
    질문이 조용히 침묵한다. 어느 쪽도 자동으로 고르지 않는다.
    """


@dataclass(frozen=True)
class OptionMarkers:
    promo_tokens: tuple[str, ...]
    unit_tokens: tuple[str, ...]
    version: str

    @classmethod
    def load(cls, path: Path = MARKERS_PATH) -> "OptionMarkers":
        if not path.exists():
            raise OptionError(f"옵션 어휘 파일이 없다: {path}")
        raw = json.loads(path.read_text())
        promo = tuple(raw.get("promoTokens") or ())
        units = tuple(raw.get("unitTokens") or ())
        if not promo or not units:
            raise OptionError(f"{path}: promoTokens·unitTokens 가 비었다")
        return cls(promo_tokens=promo, unit_tokens=units, version=raw.get("version", "?"))

    @property
    def volume_pattern(self) -> re.Pattern:
        units = "|".join(re.escape(u) for u in self.unit_tokens)
        # '60ml' · '2매' · '5매입' 같은 용량/수량, 그리고 '70*2' 같은 곱셈 표기
        return re.compile(rf"\d+\s*(?:{units})\b|\d+\s*[*x×]\s*\d+")


_MARKERS: OptionMarkers | None = None


def markers() -> OptionMarkers:
    global _MARKERS
    if _MARKERS is None:
        _MARKERS = OptionMarkers.load()
    return _MARKERS


def _has_promo_token(text: str) -> bool:
    upper = text.upper()
    return any(token.upper() in upper for token in markers().promo_tokens)


def _strip_promo_tokens(text: str) -> str:
    for token in markers().promo_tokens:
        text = re.sub(re.escape(token), " ", text, flags=re.IGNORECASE)
    return text


def _is_promo_group(content: str) -> bool:
    """괄호 묶음이 판촉인가. **모르면 False** — 지우는 쪽이 과대병합이다."""
    if content.strip().startswith("+"):
        return True  # '(+35ml)' — 사은품 구성
    if _has_promo_token(content):
        return True
    # 용량·수량만 남는 묶음 ('(12g)', '(2매)')
    return not _NON_WORD.sub(" ", markers().volume_pattern.sub(" ", content)).strip()


def _split_groups(text: str) -> tuple[list[str], str]:
    """괄호 묶음들과, 묶음을 뺀 나머지 본문."""
    groups: list[str] = []
    rest: list[str] = []
    cursor = 0
    for match in _GROUP.finditer(text):
        rest.append(text[cursor:match.start()])
        groups.append(match.group(1))
        cursor = match.end()
    rest.append(text[cursor:])
    return groups, "".join(rest)


def _core(text: str) -> tuple[str | None, str | None]:
    """본문 → (호수, 이름). 색상이 남지 않으면 (None, None) 이고 그건 `pack` 이다."""
    segment = text.split("+")[0]  # '+' 뒤는 사은품이다
    segment = _strip_promo_tokens(segment)
    cleaned = _NON_WORD.sub(" ", markers().volume_pattern.sub(" ", segment)).strip()
    if not cleaned:
        return None, None
    match = _LEADING_NUMBER.match(cleaned)
    if match and match.group(2).strip():
        number = match.group(1).lstrip("0") or "0"
        return number, re.sub(r"\s+", "", match.group(2))
    return None, re.sub(r"\s+", "", cleaned) or None


@dataclass(frozen=True)
class OptionForm:
    """옵션 문자열 1건의 정규화 결과 (코퍼스 규칙 적용 **전**)."""
    kept_groups: tuple[str, ...]
    rest: str
    number: str | None
    name: str | None

    @property
    def kind(self) -> str:
        return KIND_SHADE if self.name else KIND_PACK


def parse_form(raw: str | None, drop_groups: frozenset[str] = frozenset()) -> OptionForm | None:
    """옵션 문자열 → `OptionForm`. 미기재면 `None`.

    `drop_groups` 는 코퍼스가 판촉이라고 판정한 묶음이다 (`OptionIndex` 가 넣는다).
    """
    text = unicodedata.normalize("NFC", raw or "").strip()
    if not text:
        return None
    groups, rest = _split_groups(text)
    kept = tuple(
        g for g in groups if not _is_promo_group(g) and g.strip() not in drop_groups
    )
    number, name = _core(" ".join([*kept, rest]))
    return OptionForm(kept_groups=kept, rest=rest, number=number, name=name)


class OptionIndex:
    """제품별 옵션 어휘. 스냅샷에서 결정론적으로 만든다 (`ScoringContext` 와 같은 패턴).

    한 문자열만 봐서는 `[갸루키티]` 가 색상인지 판촉인지 알 수 없다. 그 제품의
    옵션 전체를 봐야 정해지므로 코퍼스 단위로 짓는다.
    """

    def __init__(self, wrappers: dict[str, frozenset[str]], numbers: dict[str, dict[str, str]],
                 keys: dict[str, dict[str, int]]):
        self._wrappers = wrappers
        self._numbers = numbers  # productId → {이름: 유일한 호수}
        self._keys = keys        # productId → {색상 키: 리뷰 수}

    # --- 생성 ---

    @classmethod
    def from_forms(cls, forms_by_product: dict[str, collections.Counter]) -> "OptionIndex":
        """`{productId: Counter({옵션 문자열: 리뷰 수})}` 에서 짓는다."""
        wrappers: dict[str, frozenset[str]] = {}
        for pid, forms in forms_by_product.items():
            wrappers[pid] = cls._wrappers_of(list(forms))

        numbers: dict[str, dict[str, str]] = {}
        keys: dict[str, dict[str, int]] = {}
        for pid, forms in forms_by_product.items():
            drop = wrappers[pid]
            seen: dict[str, set[str]] = collections.defaultdict(set)
            for raw in forms:
                form = parse_form(raw, drop)
                if form and form.name and form.number:
                    seen[form.name].add(form.number)
            # 이름이 호수 하나로만 나타날 때만 호수 없는 표기를 합친다
            numbers[pid] = {n: next(iter(v)) for n, v in seen.items() if len(v) == 1}

            counted: dict[str, int] = collections.Counter()
            for raw, n in forms.items():
                key = cls._key(parse_form(raw, drop), numbers[pid])
                if key:
                    counted[key] += n
            keys[pid] = dict(counted)
        return cls(wrappers, numbers, keys)

    @classmethod
    def from_records(cls, records: list[dict]) -> "OptionIndex":
        """v5 입수 레코드(`pipeline/ingest.py` 출력)에서 짓는다."""
        forms: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
        for record in records:
            raw = (record["raw"].get("option") or "").strip()
            if raw:
                forms[record["productId"]][unicodedata.normalize("NFC", raw)] += 1
        return cls.from_forms(dict(forms))

    @staticmethod
    def _wrappers_of(forms: list[str]) -> frozenset[str]:
        """판촉 묶음을 코퍼스에서 찾는다 (래퍼-A, 래퍼-B)."""
        parsed = {raw: parse_form(raw) for raw in forms}

        # 래퍼-A: 한 묶음이 서로 다른 이름 2개 이상에 붙으면 색상과 직교한다
        attached: dict[str, set[str | None]] = collections.defaultdict(set)
        for form in parsed.values():
            if form is None:
                continue
            _, bare = _core(form.rest)
            for group in form.kept_groups:
                attached[group.strip()].add(bare)
        wrappers = {g for g, names in attached.items() if len(names) >= 2}

        # 래퍼-B: 묶음을 떼면 그 제품에 이미 있는 색상 이름이 된다
        bare_names = {
            _core(f.rest)[1]
            for f in parsed.values()
            if f is not None and not [g for g in f.kept_groups if g.strip() not in wrappers]
        }
        bare_names.discard(None)
        for form in parsed.values():
            if form is None:
                continue
            extra = [g for g in form.kept_groups if g.strip() not in wrappers]
            if extra and _core(form.rest)[1] in bare_names:
                wrappers.update(g.strip() for g in extra)
        return frozenset(wrappers)

    @staticmethod
    def _key(form: OptionForm | None, numbers: dict[str, str]) -> str | None:
        if form is None or not form.name:
            return None
        number = form.number or numbers.get(form.name)
        return f"{number}|{form.name}" if number else form.name

    # --- 조회 ---

    def shade_key(self, product_id: str, option_raw: str | None) -> tuple[str | None, str]:
        """옵션 문자열 → (색상 키, 종류). 색상이 아니면 키는 `None` 이다."""
        form = parse_form(option_raw, self._wrappers.get(product_id, frozenset()))
        if form is None:
            return None, KIND_UNSTATED
        key = self._key(form, self._numbers.get(product_id, {}))
        return (key, KIND_SHADE) if key else (None, KIND_PACK)

    def resolve_scope(self, product_id: str, scope: str) -> str:
        """질문이 건 옵션 범위(자연어 표기) → 그 제품의 색상 키.

        리뷰와 **같은 정규화**를 거치게 해서 비교가 대칭이 되게 한다. 그 제품에서
        관측되지 않은 색상이면 에러다 — 추측으로 통과·탈락시키지 않는다.
        """
        key, kind = self.shade_key(product_id, scope)
        known = self._keys.get(product_id, {})
        if kind != KIND_SHADE or key not in known:
            raise UnknownOptionScopeError(
                f"{product_id}: 옵션 범위 {scope!r} 를 이 제품의 색상으로 읽을 수 없다"
                f" (정규화 결과 {key!r} / 종류 {kind}).\n"
                f"  → 관측된 색상 {sorted(known)[:8]}{' …' if len(known) > 8 else ''}\n"
                "     질문의 옵션 표기를 고치거나, 이 제품에 색상축이 없다면"
                " 옵션 범위를 걸지 않는다"
            )
        return key

    def shade_keys(self, product_id: str) -> dict[str, int]:
        """그 제품에서 관측된 색상 키와 리뷰 수."""
        return dict(self._keys.get(product_id, {}))

    def wrappers(self, product_id: str) -> frozenset[str]:
        return self._wrappers.get(product_id, frozenset())

    def has_shade_axis(self, product_id: str) -> bool:
        """색상축이 있는 제품인가 — 색상 키가 2개 이상이어야 고를 여지가 있다."""
        return len(self._keys.get(product_id, {})) >= 2
