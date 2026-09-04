"""
스킨 코드북 레이어 (PER-169 / PER-176).

조건축의 **어휘**를 소유한다. 카탈로그가 "어떤 제품인가"를 소유하듯(PER-171),
이 모듈은 "이 조건 코드가 실재하는 코드인가"를 소유한다.

정본은 `data/input/skin_codebook.json` 이고 PDP DOM 실측이다(추정 아님).
**도메인을 코드 상수로 들지 않는다** — 카탈로그와 같은 이유다. 코드가 늘면
크롤러의 검증 스크립트로 코드북을 다시 뽑고, 파일 하나만 바뀐다.

## 왜 검증이 필요한가 (v4 실패의 재발 경로)

v4는 피부 힌트를 **라벨**로 받아 조건축과 어휘가 갈렸고, 그게 카테고리 분포 FAIL의
원인이었다. 코드가 아닌 값(`건성`)이나 도메인 밖 코드(`A99`), 축이 섞인 코드(`skinType`에
`B03`)가 들어와도 지금 구조에서는 **그 값이 그대로 새 세그먼트가 된다** — 셀이 하나 늘고,
집계는 조용히 틀린다. 그래서 도메인 밖 코드는 폴백하지 않고 에러다.

빈 값은 위반이 아니다. 미기재는 별도 세그먼트이지 잘못된 코드가 아니다(PER-173).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

DEFAULT_CODEBOOK_PATH = Path(__file__).parents[1] / "data/input/skin_codebook.json"

# 축 이름 → 코드 접두사. 축이 섞인 코드(skinType 에 B03)를 잡는 데 쓴다.
AXIS_PREFIX = {"skinType": "A", "skinTone": "B", "skinTrouble": "C"}
CODE_PATTERN = re.compile(r"^[ABC]\d{2}$")


class CodebookError(Exception):
    """코드북 파일 자체가 계약을 위반했다 (로드 시점 검증 실패)."""


class UnknownConditionCodeError(Exception):
    """입력에 코드북이 모르는 조건 코드가 있다. 조용한 폴백 금지."""


class Codebook:
    """축 → 코드 도메인. 라벨은 표기 단계에서만 쓰고 입수는 코드만 본다."""

    def __init__(self, axes: dict[str, dict[str, str]]):
        self._axes = axes

    def __len__(self) -> int:
        return sum(len(v) for v in self._axes.values())

    @property
    def axes(self) -> tuple[str, ...]:
        return tuple(self._axes)

    def domain(self, axis: str) -> frozenset[str]:
        if axis not in self._axes:
            raise CodebookError(f"코드북에 없는 축: {axis!r} (있는 축: {self.axes})")
        return frozenset(self._axes[axis])

    def label(self, axis: str, code: str) -> str:
        """표기 단계 전용. 입수·집계는 라벨을 쓰지 않는다 (어휘가 갈린다)."""
        try:
            return self._axes[axis][code]
        except KeyError:
            raise UnknownConditionCodeError(f"{axis}={code!r} 는 코드북에 없다") from None

    def assert_code(self, axis: str, code: str) -> None:
        """조건 코드 1개 검증. 빈 값은 여기 오지 않는다 (미기재는 별도 세그먼트)."""
        domain = self.domain(axis)
        if code in domain:
            return
        prefix = AXIS_PREFIX[axis]
        if not CODE_PATTERN.match(code):
            hint = (
                f"코드가 아닌 값이다. 라벨이 아니라 코드({prefix}01…)로 받아야 조건축과 "
                "어휘가 합쳐진다 (v4 카테고리 분포 FAIL 의 원인)"
            )
        elif not code.startswith(prefix):
            other = [a for a, p in AXIS_PREFIX.items() if code.startswith(p)]
            hint = f"축이 섞였다 — {code} 는 {other[0] if other else '다른 축'} 의 코드다"
        else:
            hint = (
                "코드북 도메인 밖이다. 실제로 새 코드가 생겼다면 "
                "crawler/verify_skin_codebook.py 로 코드북을 다시 뽑아라 "
                "(추정으로 채우지 않는다)"
            )
        raise UnknownConditionCodeError(
            f"{axis}={code!r}: {hint}. 알려진 코드 {sorted(domain)}"
        )


def _load(path: Path) -> Codebook:
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        raise CodebookError(f"코드북 파일이 없다: {path}") from None

    axes: dict[str, dict[str, str]] = {}
    for axis, prefix in AXIS_PREFIX.items():
        entries = data.get(axis)
        if not isinstance(entries, dict) or not entries:
            raise CodebookError(f"코드북에 {axis} 매핑이 없거나 비어 있다 ({path})")
        for code, label in entries.items():
            if not CODE_PATTERN.match(code) or not code.startswith(prefix):
                raise CodebookError(f"{axis} 의 코드 형식 위반: {code!r} (기대: {prefix}01 꼴)")
            if not isinstance(label, str) or not label.strip():
                raise CodebookError(f"{axis}.{code} 의 라벨이 비었다")
        axes[axis] = dict(entries)
    return Codebook(axes)


_CACHE: dict[Path, Codebook] = {}


def load_codebook(path: Path = DEFAULT_CODEBOOK_PATH) -> Codebook:
    """25K 전건을 검증하므로 파일을 한 번만 읽는다 (내용은 불변으로 취급)."""
    if path not in _CACHE:
        _CACHE[path] = _load(path)
    return _CACHE[path]
