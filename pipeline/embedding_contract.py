"""
임베딩 버전 기록 계약 (PER-184 / PRD §8-2 "버전을 서로 연결한다").

이 모듈은 **벡터를 만들지 않는다.** 이번 사이클의 범위는 모델 선정과 계약까지고,
실제 벡터 생성·게이트2 부착·어휘 발견은 사이클 2(PER-212)다. 그런데도 계약을 지금
쓰는 이유는 이슈 코멘트가 완료 조건으로 넣었기 때문이고, 그 완료 조건이 나온 이유는
아래 한 문장이다.

  **임베딩이 바뀌면 어휘가 바뀌고, 어휘가 바뀌면 정답셋이 무의미해진다.**

이건 추측이 아니라 이 저장소에서 이미 한 번 일어난 일이다. 태그 정답셋을 v1(365개)
에서 v2(432개)로 다시 만들었을 때 v1 로 매긴 점수는 **나란히 놓을 수 없게** 됐다
(`eval/gold/README.md`, `eval/reports/gold_v1_v2_diff.md`). 자가 달라지면 수치가
달라진다. 임베딩 교체는 그보다 더 위쪽에서 같은 일을 일으킨다 — 벡터가 달라지면
클러스터가 달라지고, 클러스터가 달라지면 축(어휘)이 달라지고, 축이 달라지면 그 축으로
매긴 정답셋이 통째로 낡는다.

## 계약 4조

  1. **모든 벡터 레코드에 `embeddingModel` + `embeddingVersion`.** 벡터만 남으면
     그게 무엇으로 만들어졌는지 영영 알 수 없다 — 코사인 값은 모델을 말해주지 않는다
  2. **어휘 버전과 임베딩 버전을 묶어서 기록한다.** 둘 중 하나만 바뀌는 배포는 에러다
     (`assert_paired_bump`). 같은 축 이름이 나와도 **다시 확인했다는 사실**이 버전으로
     남아야 한다
  3. **태그·claim 산출물의 `meta` 에 두 버전이 모두 남는다** (`stamp_meta`). 리포트
     수치가 어느 벡터에서 나왔는지를 되짚을 수 없으면 나빠졌을 때 되돌릴 수 없다
  4. **모델 교체는 하나의 작업이다** — 벡터 재생성 → 어휘 재확정 → 정답셋 재라벨링.
     셋 중 하나라도 빠지면 에러다 (`assert_swap_complete`). 부분 교체는 "옛 축으로
     매긴 정답셋에 새 벡터를 채점시키는" 상태를 만든다

## 조용한 폴백을 만들지 않는다

계약 위반은 전부 `EmbeddingContractError` 다. 버전이 없으면 `"unknown"` 으로 채우거나
설정의 현재 값으로 덮어쓰지 않는다 — 그러면 다른 모델로 만든 벡터가 현재 모델의
것으로 둔갑한다. 미등록 `goodsNo` 를 폴백하지 않는 것(PER-171)과 같은 이유다.

## 어휘 드리프트는 로드 시점에 걸린다

설정의 `aspectsSha256` 은 `tag_contract.ASPECTS` 14종에서 계산한다. 택소노미를
확장하면서(PER-212) `vocabVersion` 을 올리지 않으면 **설정을 읽는 순간 에러**다.
벡터가 아직 없는 지금도 이 조항은 동작한다.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from tag_contract import ASPECTS

CONFIG_PATH = Path(__file__).parent / "embedding_config.json"

CONFIG_SCHEMA_VERSION = "embedding-config-v1"

# 벡터 레코드가 반드시 지니는 두 필드 (계약 1조)
VECTOR_STAMP_FIELDS = ("embeddingModel", "embeddingVersion")
# 산출물 meta 가 반드시 지니는 세 필드 (계약 3조)
META_STAMP_FIELDS = ("embeddingModel", "embeddingVersion", "vocabVersion")

_SELECTED_REQUIRED = (
    "embeddingVersion", "model", "revision", "dimension", "maxSeqLength",
    "vocabVersion", "goldenVersion", "aspectsSha256",
)
_RELEASE_KEYS = ("embeddingVersion", "vocabVersion", "goldenVersion")


class EmbeddingContractError(ValueError):
    """임베딩 버전 기록이 계약을 위반했다. 값을 채워 넘기는 게 아니라 실행을 세운다."""


@dataclass(frozen=True)
class EmbeddingSpec:
    """지금 유효한 임베딩 정체. 세 버전이 한 덩어리로 움직인다."""

    embeddingVersion: str
    model: str
    revision: str
    dimension: int
    maxSeqLength: int
    vocabVersion: str
    goldenVersion: str
    queryPrefix: str = ""
    passagePrefix: str = ""
    normalize: bool = True

    @property
    def release(self) -> tuple[str, str, str]:
        return (self.embeddingVersion, self.vocabVersion, self.goldenVersion)


def aspect_digest(aspects: tuple[str, ...] = ASPECTS) -> str:
    """어휘의 지문. 축이 하나라도 늘거나 순서가 바뀌면 값이 달라진다."""
    return hashlib.sha256("\n".join(aspects).encode()).hexdigest()


# --- 설정 읽기 ---------------------------------------------------------------

def load_embedding_config(path: Path | None = None) -> dict:
    """설정 파일을 읽고 형태를 검증한다. 어휘가 드리프트했으면 여기서 에러다."""
    path = path or CONFIG_PATH
    if not path.exists():
        raise EmbeddingContractError(f"임베딩 설정이 없다: {path}")
    cfg = json.loads(path.read_text())

    if cfg.get("schemaVersion") != CONFIG_SCHEMA_VERSION:
        raise EmbeddingContractError(
            f"설정 스키마 버전이 다르다: {cfg.get('schemaVersion')!r} "
            f"(기대 {CONFIG_SCHEMA_VERSION!r})")

    sel = cfg.get("selected")
    if not isinstance(sel, dict):
        raise EmbeddingContractError("설정에 selected 가 없다")
    missing = [k for k in _SELECTED_REQUIRED if not sel.get(k)]
    if missing:
        raise EmbeddingContractError(f"selected 에 빠진 필드: {missing}")
    if not isinstance(sel["dimension"], int) or sel["dimension"] <= 0:
        raise EmbeddingContractError(f"dimension 이 양의 정수가 아니다: {sel['dimension']!r}")
    if not isinstance(sel["maxSeqLength"], int) or sel["maxSeqLength"] <= 0:
        raise EmbeddingContractError(
            f"maxSeqLength 가 양의 정수가 아니다: {sel['maxSeqLength']!r}")

    want = aspect_digest()
    if sel["aspectsSha256"] != want:
        raise EmbeddingContractError(
            "어휘(aspect 택소노미)가 설정에 기록된 것과 다르다.\n"
            f"  설정 {sel['aspectsSha256']}\n  현재 {want}\n"
            "  → 택소노미를 바꿨으면 vocabVersion 을 올리고 벡터·정답셋을 한 작업으로 "
            "갱신한다 (PER-184 계약 4조). 해시만 고쳐 넘기지 않는다")

    releases = cfg.get("releases")
    if not isinstance(releases, list) or not releases:
        raise EmbeddingContractError("설정에 releases 가 없다 — 배포 조합을 등록해야 한다")
    seen: dict[str, tuple[str, str]] = {}
    for i, rel in enumerate(releases):
        bad = [k for k in _RELEASE_KEYS if not rel.get(k)]
        if bad:
            raise EmbeddingContractError(f"releases[{i}] 에 빠진 필드: {bad}")
        ev = rel["embeddingVersion"]
        pair = (rel["vocabVersion"], rel["goldenVersion"])
        if ev in seen and seen[ev] != pair:
            raise EmbeddingContractError(
                f"embeddingVersion {ev!r} 이 서로 다른 어휘/정답셋에 묶여 있다: "
                f"{seen[ev]} vs {pair} — 한 버전이 두 가지를 뜻할 수 없다")
        seen[ev] = pair

    if not cfg.get("swapRequirements"):
        raise EmbeddingContractError("설정에 swapRequirements 가 없다")

    spec = spec_from_selected(sel)
    assert_release_registered(spec, cfg)
    return cfg


def spec_from_selected(sel: Mapping[str, Any]) -> EmbeddingSpec:
    return EmbeddingSpec(
        embeddingVersion=sel["embeddingVersion"],
        model=sel["model"],
        revision=sel["revision"],
        dimension=sel["dimension"],
        maxSeqLength=sel["maxSeqLength"],
        vocabVersion=sel["vocabVersion"],
        goldenVersion=sel["goldenVersion"],
        queryPrefix=sel.get("queryPrefix", ""),
        passagePrefix=sel.get("passagePrefix", ""),
        normalize=bool(sel.get("normalize", True)),
    )


def load_embedding_spec(path: Path | None = None) -> EmbeddingSpec:
    return spec_from_selected(load_embedding_config(path)["selected"])


def assert_release_registered(spec: EmbeddingSpec, cfg: Mapping[str, Any]) -> None:
    """계약 2조 — 임베딩·어휘·정답셋 세 버전의 조합이 등록돼 있어야 한다."""
    registered = {tuple(r[k] for k in _RELEASE_KEYS) for r in cfg["releases"]}
    if spec.release not in registered:
        raise EmbeddingContractError(
            f"등록되지 않은 배포 조합이다: {spec.release}\n"
            f"  등록된 조합 {sorted(registered)}\n"
            "  → 임베딩·어휘·정답셋은 묶여서 움직인다. 하나만 바꾼 조합은 배포하지 않는다")


# --- 벡터 레코드 (계약 1조) ---------------------------------------------------

def stamp_vector(record: Mapping[str, Any], spec: EmbeddingSpec) -> dict:
    """벡터 레코드에 모델·버전을 찍는다. 이미 다른 값이 찍혀 있으면 에러다."""
    out = dict(record)
    for field, value in zip(VECTOR_STAMP_FIELDS, (spec.model, spec.embeddingVersion)):
        have = out.get(field)
        if have is not None and have != value:
            raise EmbeddingContractError(
                f"벡터 레코드의 {field} 를 덮어쓰려 한다: {have!r} → {value!r}\n"
                "  → 다른 모델로 만든 벡터다. 덮어쓰지 말고 다시 생성한다")
        out[field] = value
    return out


def validate_vector_record(record: Mapping[str, Any], spec: EmbeddingSpec) -> None:
    """계약 1조 — 스탬프가 없거나 현재 정체와 다르면 에러다. 조용히 채우지 않는다."""
    missing = [f for f in VECTOR_STAMP_FIELDS if not record.get(f)]
    if missing:
        raise EmbeddingContractError(
            f"벡터 레코드에 {missing} 가 없다 — 무엇으로 만든 벡터인지 알 수 없다")
    if record["embeddingModel"] != spec.model:
        raise EmbeddingContractError(
            f"벡터의 embeddingModel 이 현재 정체와 다르다: "
            f"{record['embeddingModel']!r} vs {spec.model!r}")
    if record["embeddingVersion"] != spec.embeddingVersion:
        raise EmbeddingContractError(
            f"벡터의 embeddingVersion 이 현재 정체와 다르다: "
            f"{record['embeddingVersion']!r} vs {spec.embeddingVersion!r}\n"
            "  → 섞어 쓰지 않는다. 옛 벡터는 재생성 대상이다")
    vec = record.get("vector")
    if vec is not None and len(vec) != spec.dimension:
        raise EmbeddingContractError(
            f"벡터 차원이 {len(vec)} 다 — 설정은 {spec.dimension} 이다")


# --- 산출물 meta (계약 3조) ---------------------------------------------------

def stamp_meta(meta: Mapping[str, Any], spec: EmbeddingSpec) -> dict:
    """태그·claim 산출물의 meta 에 두 버전을 함께 남긴다."""
    out = dict(meta)
    out["embeddingModel"] = spec.model
    out["embeddingVersion"] = spec.embeddingVersion
    out["vocabVersion"] = spec.vocabVersion
    return out


def validate_meta(meta: Mapping[str, Any], spec: EmbeddingSpec) -> None:
    """계약 3조 — 둘 중 하나만 있는 meta 는 에러다."""
    missing = [f for f in META_STAMP_FIELDS if not meta.get(f)]
    if missing:
        raise EmbeddingContractError(
            f"산출물 meta 에 {missing} 가 없다 — 어느 벡터·어휘에서 나온 수치인지 "
            "되짚을 수 없다 (PER-184 계약 3조)")
    if (meta["embeddingVersion"], meta["vocabVersion"]) != (
            spec.embeddingVersion, spec.vocabVersion):
        raise EmbeddingContractError(
            f"meta 의 버전 쌍이 현재 정체와 다르다: "
            f"{(meta['embeddingVersion'], meta['vocabVersion'])} vs "
            f"{(spec.embeddingVersion, spec.vocabVersion)}")


# --- 모델 교체 (계약 2·4조) ---------------------------------------------------

def assert_paired_bump(old: EmbeddingSpec, new: EmbeddingSpec) -> None:
    """계약 2조 — 임베딩 버전과 어휘 버전 중 하나만 바뀌는 배포는 에러다."""
    emb_changed = old.embeddingVersion != new.embeddingVersion
    vocab_changed = old.vocabVersion != new.vocabVersion
    if emb_changed != vocab_changed:
        moved, stayed = (("임베딩", "어휘") if emb_changed else ("어휘", "임베딩"))
        raise EmbeddingContractError(
            f"{moved} 버전만 바뀌고 {stayed} 버전이 그대로다: "
            f"{old.embeddingVersion}/{old.vocabVersion} → "
            f"{new.embeddingVersion}/{new.vocabVersion}\n"
            "  → 벡터가 바뀌면 클러스터가 바뀌고 축이 바뀐다. 같은 축이 다시 나와도 "
            "'다시 확인했다'는 사실이 버전으로 남아야 한다 (PER-184 계약 2조)")


def plan_model_swap(old: EmbeddingSpec, new: EmbeddingSpec,
                    cfg: Mapping[str, Any]) -> tuple[str, ...]:
    """교체 한 건이 요구하는 단계 목록. 순서가 곧 의존 순서다."""
    if old.model == new.model and old.revision == new.revision:
        raise EmbeddingContractError(
            f"교체할 모델이 같다: {old.model}@{old.revision}")
    assert_paired_bump(old, new)
    assert_release_registered(new, cfg)
    return tuple(cfg["swapRequirements"])


def assert_swap_complete(done: Mapping[str, bool], cfg: Mapping[str, Any]) -> None:
    """계약 4조 — 벡터 재생성·어휘 재확정·정답셋 재라벨링은 하나의 작업이다."""
    required = tuple(cfg["swapRequirements"])
    unknown = [k for k in done if k not in required]
    if unknown:
        raise EmbeddingContractError(f"모르는 교체 단계: {unknown} (있는 단계 {required})")
    pending = [k for k in required if not done.get(k)]
    if pending:
        raise EmbeddingContractError(
            f"모델 교체가 부분으로 끝났다 — 남은 단계 {pending}\n"
            "  → 벡터 재생성 → 어휘 재확정 → 정답셋 재라벨링은 한 작업이다. "
            "부분 교체는 옛 축으로 매긴 정답셋에 새 벡터를 채점시키는 상태를 만든다")
