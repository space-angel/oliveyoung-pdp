"""재현 meta 의 정본 (PER-193 / PRD §8-2 "버전을 서로 연결한다").

## 이 이슈가 meta 까지인 이유

이슈 제목은 「순수함수 · 시드 · 재현 meta」였지만 2026-09-14 코멘트가 범위를 잘랐다:

  > **09-16 범위 축소: `meta` 기록만 남긴다.**
  > 남기는 것: 출력 `meta` 에 프롬프트 버전 · 모델 ID · 임계값 · 시드 · 입력 스냅샷 해시.
  > PER-201(v4 대비 개선 귀속)의 전제라 뺄 수 없다.
  > **빼는 것: 순수함수 리팩터링.** 기존 단계가 이미 `--check` 로 재실행 동일성을
  > 확인하고 있으니 구조를 다시 손볼 이유가 09-16 전에는 없다.

그래서 이 모듈은 **구조를 바꾸지 않는다.** 기존 단계를 손대지 않고, 산출물이 스스로
"어느 설정에서 나온 수치인가"를 말하게 만드는 계약 하나만 건다.

## 왜 meta 가 PER-201 의 전제인가

PER-201 은 v4 대비 무엇이 좋아졌는지를 **귀속**해야 한다. 수치가 좋아졌을 때 그게
임계값을 낮춰서인지, 프롬프트를 고쳐서인지, 모델을 바꿔서인지, 입력 스냅샷이 달라져서인지
구분할 수 없으면 "좋아졌다"는 말은 근거가 아니다. 이 저장소는 이미 같은 일을 한 번 겪었다 —
태그 정답셋 v1(365) → v2(432) 교체 후 v1 로 매긴 점수를 나란히 놓을 수 없게 됐다
(`eval/gold/README.md`). 자가 달라지면 수치가 달라진다.

그래서 필드는 **수치를 움직이는 축** 그대로다:

| 필드 | 이게 바뀌면 |
|---|---|
| `promptVersion` (+파일 sha256) | 같은 모델도 다른 태그를 뱉는다. 버전 문자열만으로는 부족해서 파일 해시를 함께 박는다 |
| `modelId` | 태거 후보 7종이 aspect F1 에서 갈렸다 (`eval/reports/v5_tag_bedrock_bakeoff.json`) |
| `policy` | N_min·R_min·S_min 하나만 움직여도 통과 주장 수가 달라진다 (PER-186 `sensitivity`) |
| `seed` | 표본·샘플링·LLM 온도가 걸린 단계는 시드 없이 같은 수가 두 번 나오지 않는다 |
| `inputs` | 스냅샷이 늘면 커버리지가 저절로 올라간다. 개선이 아닌데 개선처럼 보인다 |
| `failureTaxonomyVersion` | 실패 어휘가 바뀌면 judge 점수의 분모가 바뀐다 (`failure_taxonomy.json` `_meta.metaField`) |
| `embeddingModel`/`embeddingVersion`/`vocabVersion` | 벡터가 바뀌면 축이 바뀌고 축이 바뀌면 정답셋이 낡는다 (PER-184) |

## 모르는 값을 0 이나 빈 문자열로 깔지 않는다

해당 없는 값을 `0` · `""` · `"unknown"` 으로 채우면 **모른다는 사실이 사라진다.**
0 은 "측정했더니 0" 과 구별되지 않고, 빈 문자열은 "아직 안 정했다" 와 "해당 없다" 를
같은 칸에 넣는다. PER-174 가 `onTopic` 을 0점으로 깔지 않고 `unavailable` 에 남긴 것,
PER-172 가 `renewalPolicy` 에 `null` 대신 `unobserved` 를 쓰는 것과 같은 규칙이다.

그래서 이 계약에서 값을 비우는 유일한 방법은 `"unavailable"` 이고, **사유를 함께 적어야
한다.** `unavailable` 에 사유가 없으면 에러고, 사유만 있고 값이 채워져 있어도 에러다
(둘은 서로를 강제한다).

## 입력 스냅샷 — sha256 과 커밋 해시 둘 다

이슈가 "커밋 해시로 대체 가능"이라고 적었고 실제로 `data/input/` 은 커밋돼 있다. 둘 다
지원하되 **무엇을 썼는지 meta 자체에 남긴다**(`digestKind`) — 나중에 되짚을 때 40자와
64자를 눈으로 구별하게 두지 않는다. 커밋 해시는 **작업 트리가 그 경로에 대해 깨끗할 때만**
찍는다. 더러운 트리에 커밋 해시를 박으면 그 해시는 실제로 읽은 바이트를 가리키지 않는다.

## 쓰는 법 (PER-191 생성기가 이렇게 부른다)

    from run_meta import stamp, file_input, prompt_ref, UNAVAILABLE

    meta = stamp(
        stage="generate",
        model_id="zai.glm-4.7",
        prompt=prompt_ref(ROOT / "pipeline/prompts/tag/v1.md", "tag-v1"),
        seed=20260916,
        policy=DEFAULT_SUFFICIENCY.as_meta(),
        inputs=[file_input("reviews", ROOT / "data/input/reviews_50products.json")],
    )
    report["meta"] = meta          # validate() 는 stamp() 안에서 이미 돈다
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

RUN_META_SCHEMA_VERSION = "run-meta-v1"

#: 값을 비우는 유일한 방법. 사유를 `unavailable` 에 함께 적어야 한다.
UNAVAILABLE = "unavailable"

#: meta 가 반드시 지니는 키. 하나라도 없으면 에러, 여기 없는 키가 있어도 에러다.
REQUIRED_FIELDS = (
    "schemaVersion",
    "stage",
    "promptVersion",
    "modelId",
    "seed",
    "policy",
    "inputs",
    "failureTaxonomyVersion",
    "embeddingModel",
    "embeddingVersion",
    "vocabVersion",
    "unavailable",
)

#: `"unavailable"` 을 쓸 수 있는 필드. 나머지는 실제 값이 있어야 한다 —
#: 입력이 무엇인지 모르는 실행은 재현 대상이 아니다.
NULLABLE_FIELDS = (
    "promptVersion", "modelId", "seed", "failureTaxonomyVersion",
    "embeddingModel", "embeddingVersion", "vocabVersion",
)

#: 실측(`eval/measure_meta_coverage.py`)이 기존 리포트에서 찾는 축. `unavailable` 과
#: `schemaVersion` 은 계약의 사무 필드라 커버리지 축에서 뺀다.
COVERAGE_FIELDS = (
    "promptVersion", "modelId", "policy", "seed", "inputs",
    "failureTaxonomyVersion", "embeddingModel", "embeddingVersion", "vocabVersion",
)

DIGEST_KINDS = ("sha256", "gitCommit")
_DIGEST_LEN = {"sha256": 64, "gitCommit": 40}
_HEX = re.compile(r"^[0-9a-f]+$")

#: "모른다"를 값처럼 적은 흔적. 조용히 통과시키지 않는다.
_PLACEHOLDERS = frozenset({
    "", "unknown", "n/a", "na", "none", "null", "tbd", "todo", "-", "?", "0",
})

_PROMPT_KEYS = ("path", "version", "sha256")
_INPUT_KEYS = ("name", "path", "digestKind", "digest")


class RunMetaError(ValueError):
    """재현 meta 가 계약을 위반했다. 값을 채워 넘기는 게 아니라 실행을 세운다."""


# --- 해시 -------------------------------------------------------------------

def sha256_file(path: Path) -> str:
    if not path.is_file():
        raise RunMetaError(f"해시할 파일이 없다: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _repo_root(path: Path) -> Path:
    for parent in [path if path.is_dir() else path.parent, *path.parents]:
        if (parent / ".git").exists():
            return parent
    raise RunMetaError(f"git 저장소를 찾을 수 없다: {path}")


def git_commit_of(path: Path) -> str:
    """그 경로를 마지막으로 바꾼 커밋. **작업 트리가 깨끗할 때만** 찍는다.

    더러운 트리에서 커밋 해시를 박으면 그 해시는 실제로 읽은 바이트를 가리키지 않는다 —
    재현 meta 가 거짓말을 하는 가장 쉬운 경로다. 그래서 에러다.
    """
    root = _repo_root(path)
    rel = path.resolve().relative_to(root.resolve()).as_posix()

    def git(*args: str) -> str:
        done = subprocess.run(["git", "-C", str(root), *args],
                              capture_output=True, text=True)
        if done.returncode != 0:
            raise RunMetaError(f"git {' '.join(args)} 실패: {done.stderr.strip()}")
        return done.stdout

    if git("ls-files", "--", rel).strip() == "":
        raise RunMetaError(f"커밋되지 않은 경로에 커밋 해시를 쓸 수 없다: {rel}")
    if git("status", "--porcelain", "--", rel).strip():
        raise RunMetaError(
            f"작업 트리가 {rel} 에 대해 깨끗하지 않다 — 커밋 해시가 실제로 읽은 바이트를\n"
            "  가리키지 않는다. 커밋하고 다시 찍거나 digest_kind='sha256' 을 써라")
    commit = git("log", "-1", "--format=%H", "--", rel).strip()
    if not commit:
        raise RunMetaError(f"{rel} 을 건드린 커밋이 없다")
    return commit


# --- 조각 만들기 --------------------------------------------------------------

def prompt_ref(path: Path, version: str) -> dict:
    """프롬프트 1개의 정체. 버전 문자열과 **파일 해시를 함께** 남긴다.

    버전만 남기면 "v1 을 고쳤는데 v1 이라 부르는" 실행이 조용히 통과한다.
    """
    if not isinstance(version, str) or version.strip().lower() in _PLACEHOLDERS:
        raise RunMetaError(f"프롬프트 버전이 비었거나 자리표시자다: {version!r}")
    return {"path": _rel(path), "version": version, "sha256": sha256_file(path)}


def file_input(name: str, path: Path, digest_kind: str = "sha256") -> dict:
    """입력 스냅샷 1개. `digest_kind` 로 sha256 과 커밋 해시를 고른다."""
    if not isinstance(name, str) or name.strip().lower() in _PLACEHOLDERS:
        raise RunMetaError(f"입력 이름이 비었거나 자리표시자다: {name!r}")
    if digest_kind not in DIGEST_KINDS:
        raise RunMetaError(f"모르는 digestKind: {digest_kind!r} (있는 것 {DIGEST_KINDS})")
    digest = sha256_file(path) if digest_kind == "sha256" else git_commit_of(path)
    return {"name": name, "path": _rel(path), "digestKind": digest_kind, "digest": digest}


def _rel(path: Path) -> str:
    """저장소 상대 경로. 워크트리 이름이 meta 에 새지 않게 한다."""
    p = Path(path)
    try:
        return p.resolve().relative_to(_repo_root(p).resolve()).as_posix()
    except (RunMetaError, ValueError):
        return p.as_posix()


def embedding_fields(spec: Any | None = None) -> dict:
    """PER-184 설정에서 세 버전을 가져온다. 중복 구현하지 않고 그 계약을 그대로 쓴다."""
    if spec is None:
        from embedding_contract import load_embedding_spec  # 지연 import — 순환 방지
        spec = load_embedding_spec()
    return {
        "embeddingModel": spec.model,
        "embeddingVersion": spec.embeddingVersion,
        "vocabVersion": spec.vocabVersion,
    }


def failure_taxonomy_version(path: Path | None = None) -> str:
    """`failure_taxonomy.json` 의 버전. 어휘 파일이 정본이고 코드 상수가 아니다."""
    path = path or Path(__file__).parent / "failure_taxonomy.json"
    data = json.loads(path.read_text())
    version = data.get("version") or data.get("_meta", {}).get("version")
    if not version:
        from golden_contract import FAILURE_TAXONOMY_VERSION  # 지연 import
        version = FAILURE_TAXONOMY_VERSION
    if not isinstance(version, str) or version.strip().lower() in _PLACEHOLDERS:
        raise RunMetaError(f"실패 택소노미 버전을 읽을 수 없다: {version!r}")
    return version


# --- 찍기 --------------------------------------------------------------------

def stamp(
    *,
    stage: str,
    policy: Mapping[str, Any],
    inputs: Sequence[Mapping[str, Any]],
    model_id: str = UNAVAILABLE,
    prompt: Mapping[str, Any] | str = UNAVAILABLE,
    seed: int | str = UNAVAILABLE,
    failure_taxonomy: str = UNAVAILABLE,
    embedding: Mapping[str, str] | None = None,
    unavailable: Mapping[str, str] | None = None,
) -> dict:
    """재현 meta 를 만든다. 만들자마자 `validate` 를 돌려 반쪽짜리를 내보내지 않는다.

    `embedding=None` 은 "PER-184 설정에서 읽는다"는 뜻이다. 세 필드를 통째로
    `unavailable` 로 두려면 `embedding={"embeddingModel": UNAVAILABLE, ...}` 로 **명시**하고
    사유를 `unavailable` 에 적는다 — 잊어서 빠지는 것과 일부러 빼는 것을 구별한다.
    """
    emb = dict(embedding) if embedding is not None else embedding_fields()
    meta: dict[str, Any] = {
        "schemaVersion": RUN_META_SCHEMA_VERSION,
        "stage": stage,
        "promptVersion": dict(prompt) if isinstance(prompt, Mapping) else prompt,
        "modelId": model_id,
        "seed": seed,
        "policy": dict(policy) if isinstance(policy, Mapping) else policy,
        "inputs": [dict(i) for i in inputs],
        "failureTaxonomyVersion": failure_taxonomy,
        "embeddingModel": emb.get("embeddingModel", UNAVAILABLE),
        "embeddingVersion": emb.get("embeddingVersion", UNAVAILABLE),
        "vocabVersion": emb.get("vocabVersion", UNAVAILABLE),
        "unavailable": dict(unavailable or {}),
    }
    validate(meta)
    return meta


# --- 검증 --------------------------------------------------------------------

def validate(meta: Mapping[str, Any]) -> None:
    """계약 위반이면 `RunMetaError`. 채워 넘기는 경로를 만들지 않는다."""
    if not isinstance(meta, Mapping):
        raise RunMetaError(f"meta 가 매핑이 아니다: {type(meta).__name__}")

    missing = [f for f in REQUIRED_FIELDS if f not in meta]
    if missing:
        raise RunMetaError(
            f"재현 meta 에 빠진 필드: {missing}\n"
            "  → 빈 값으로 채우지 말고 값을 넣거나 'unavailable' + 사유를 남겨라 (PER-193)")
    unknown = sorted(set(meta) - set(REQUIRED_FIELDS))
    if unknown:
        raise RunMetaError(
            f"모르는 meta 필드: {unknown}\n"
            "  → 계약은 닫혀 있다. 축을 늘리려면 REQUIRED_FIELDS 와 결정 문서를 함께 고친다")

    if meta["schemaVersion"] != RUN_META_SCHEMA_VERSION:
        raise RunMetaError(
            f"schemaVersion 이 다르다: {meta['schemaVersion']!r} "
            f"(기대 {RUN_META_SCHEMA_VERSION!r})")

    _require_text("stage", meta["stage"])

    # --- unavailable 과 값은 서로를 강제한다 ---
    un = meta["unavailable"]
    if not isinstance(un, dict):
        raise RunMetaError(f"unavailable 은 객체여야 한다: {type(un).__name__}")
    bad = sorted(set(un) - set(NULLABLE_FIELDS))
    if bad:
        raise RunMetaError(
            f"unavailable 이 비울 수 없는 필드를 가리킨다: {bad}\n"
            f"  → 비울 수 있는 것 {list(NULLABLE_FIELDS)}. 입력·임계값·단계는 없으면 "
            "재현 대상이 아니다")
    for field, reason in un.items():
        if not isinstance(reason, str) or reason.strip().lower() in _PLACEHOLDERS:
            raise RunMetaError(
                f"unavailable[{field!r}] 에 사유가 없다: {reason!r}\n"
                "  → '모른다'는 사실이 남아야 한다. 사유 없는 공란은 0 으로 까는 것과 같다")
    for field in NULLABLE_FIELDS:
        is_unavailable = meta[field] == UNAVAILABLE
        declared = field in un
        if is_unavailable and not declared:
            raise RunMetaError(
                f"{field} 가 'unavailable' 인데 사유가 없다 — unavailable[{field!r}] 를 적어라")
        if declared and not is_unavailable:
            raise RunMetaError(
                f"unavailable 이 {field} 를 가리키는데 값이 채워져 있다: {meta[field]!r}\n"
                "  → 둘 중 하나가 낡았다. 값을 쓸 거면 사유를 지운다")

    # --- 값 ---
    if meta["modelId"] != UNAVAILABLE:
        _require_text("modelId", meta["modelId"])
    if meta["failureTaxonomyVersion"] != UNAVAILABLE:
        _require_text("failureTaxonomyVersion", meta["failureTaxonomyVersion"])
    for field in ("embeddingModel", "embeddingVersion", "vocabVersion"):
        if meta[field] != UNAVAILABLE:
            _require_text(field, meta[field])

    seed = meta["seed"]
    if seed != UNAVAILABLE:
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise RunMetaError(
                f"seed 는 정수이거나 'unavailable' 이어야 한다: {seed!r}\n"
                "  → 시드를 안 쓰는 단계면 0 으로 깔지 말고 사유와 함께 unavailable 로 적어라")

    prompt = meta["promptVersion"]
    if prompt != UNAVAILABLE:
        if not isinstance(prompt, Mapping):
            raise RunMetaError(
                f"promptVersion 은 {{path, version, sha256}} 객체이거나 'unavailable' 이다: "
                f"{prompt!r}")
        _closed_object("promptVersion", prompt, _PROMPT_KEYS)
        for k in _PROMPT_KEYS:
            _require_text(f"promptVersion.{k}", prompt[k])
        _require_digest("promptVersion.sha256", prompt["sha256"], "sha256")

    policy = meta["policy"]
    if not isinstance(policy, Mapping) or not policy:
        raise RunMetaError(
            f"policy 가 비었다: {policy!r}\n"
            "  → 임계값이 없는 실행은 없다. 기본값을 썼으면 그 기본값을 적어라 "
            "(SufficiencyPolicy.as_meta() 가 그대로 들어간다)")

    inputs = meta["inputs"]
    if not isinstance(inputs, Sequence) or isinstance(inputs, (str, bytes)) or not inputs:
        raise RunMetaError(
            f"inputs 가 비었다: {inputs!r}\n"
            "  → 입력 스냅샷이 없으면 수치가 어느 데이터에서 나왔는지 되짚을 수 없다")
    seen: set[str] = set()
    for i, item in enumerate(inputs):
        if not isinstance(item, Mapping):
            raise RunMetaError(f"inputs[{i}] 가 객체가 아니다: {item!r}")
        _closed_object(f"inputs[{i}]", item, _INPUT_KEYS)
        for k in ("name", "path"):
            _require_text(f"inputs[{i}].{k}", item[k])
        kind = item["digestKind"]
        if kind not in DIGEST_KINDS:
            raise RunMetaError(
                f"inputs[{i}].digestKind 가 {kind!r} 다 (있는 것 {DIGEST_KINDS}) — "
                "무엇으로 찍었는지가 meta 에 남아야 40자와 64자를 눈으로 구별하지 않는다")
        _require_digest(f"inputs[{i}].digest", item["digest"], kind)
        if item["name"] in seen:
            raise RunMetaError(f"inputs 에 같은 이름이 두 번 있다: {item['name']!r}")
        seen.add(item["name"])


def _require_text(label: str, value: Any) -> None:
    if not isinstance(value, str):
        raise RunMetaError(f"{label} 는 문자열이어야 한다: {value!r}")
    if value.strip().lower() in _PLACEHOLDERS:
        raise RunMetaError(
            f"{label} 가 비었거나 자리표시자다: {value!r}\n"
            "  → 모르는 값을 '' · 0 · 'unknown' 으로 깔지 않는다. "
            "해당 없으면 'unavailable' + 사유다 (PER-193)")


def _require_digest(label: str, value: Any, kind: str) -> None:
    want = _DIGEST_LEN[kind]
    if not isinstance(value, str) or not value:
        raise RunMetaError(f"{label} 해시가 비었다: {value!r}")
    v = value.lower()
    if len(v) != want or not _HEX.match(v):
        raise RunMetaError(
            f"{label} 가 {kind} 해시 모양이 아니다: {value!r} (16진 {want}자여야 한다)")
    if set(v) == {"0"}:
        raise RunMetaError(f"{label} 가 0 으로 채워져 있다 — 해시를 못 구한 흔적이다")


def _closed_object(label: str, obj: Mapping[str, Any], keys: Iterable[str]) -> None:
    keys = tuple(keys)
    miss = [k for k in keys if k not in obj]
    if miss:
        raise RunMetaError(f"{label} 에 빠진 키: {miss}")
    extra = sorted(set(obj) - set(keys))
    if extra:
        raise RunMetaError(f"{label} 에 모르는 키: {extra} (있는 키 {list(keys)})")


# --- 대조 --------------------------------------------------------------------

def diff(old: Mapping[str, Any], new: Mapping[str, Any]) -> dict[str, tuple[Any, Any]]:
    """두 실행의 meta 차이. PER-201 이 "무엇을 바꿔서 좋아졌는가"를 여기서 읽는다.

    수치가 달라졌는데 이 결과가 비어 있으면 원인은 meta 밖에 있다 — 그 자체가 신호다.
    """
    validate(old)
    validate(new)
    return {f: (old[f], new[f]) for f in REQUIRED_FIELDS if old[f] != new[f]}


def coverage(meta: Mapping[str, Any]) -> dict[str, str]:
    """필드별 충족 상태. `present` · `unavailable` · `missing` 셋뿐이다."""
    out: dict[str, str] = {}
    for f in COVERAGE_FIELDS:
        if f not in meta:
            out[f] = "missing"
        elif meta[f] == UNAVAILABLE:
            out[f] = "unavailable"
        else:
            out[f] = "present"
    return out
