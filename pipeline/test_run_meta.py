"""
PER-193 재현 meta 계약 테스트.

CLAUDE.md: **"'에러를 낸다'는 완료 조건은 테스트로 고정한다."** 이 파일이 고정하는 건
"모르는 값을 조용히 깔지 않는다"는 네 조항이다.

  1조 필수 필드가 빠지면 에러. 모르는 필드가 있어도 에러 (계약은 닫혀 있다)
  2조 값을 비우는 유일한 방법은 'unavailable' + 사유다. 사유 없는 공란도,
      사유가 있는데 값이 채워진 것도 에러다
  3조 자리표시자(''·0·'unknown')와 빈 해시·0 해시는 에러다
  4조 입력 스냅샷은 sha256 과 커밋 해시 둘 다 되지만 **무엇으로 찍었는지**가 남는다
"""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from run_meta import (
    COVERAGE_FIELDS,
    NULLABLE_FIELDS,
    REQUIRED_FIELDS,
    RUN_META_SCHEMA_VERSION,
    UNAVAILABLE,
    RunMetaError,
    coverage,
    diff,
    embedding_fields,
    failure_taxonomy_version,
    file_input,
    git_commit_of,
    prompt_ref,
    sha256_file,
    stamp,
    validate,
)

ROOT = Path(__file__).parents[1]
REVIEWS = ROOT / "data/input/reviews_50products.json"
PROMPT = ROOT / "pipeline/prompts/tag/v1.md"


def good_meta(**over) -> dict:
    meta = {
        "schemaVersion": RUN_META_SCHEMA_VERSION,
        "stage": "tag",
        "promptVersion": {"path": "pipeline/prompts/tag/v1.md", "version": "tag-v1",
                          "sha256": "a" * 64},
        "modelId": "zai.glm-4.7",
        "seed": 20260916,
        "policy": {"nMin": 8, "rMin": 0.1, "sMin": 8},
        "inputs": [{"name": "reviews", "path": "data/input/reviews_50products.json",
                    "digestKind": "sha256", "digest": "b" * 64}],
        "failureTaxonomyVersion": "failure-taxonomy-v1",
        "embeddingModel": "nlpai-lab/KURE-v1",
        "embeddingVersion": "emb-v1",
        "vocabVersion": "aspect-v1",
        "unavailable": {},
    }
    meta.update(over)
    return meta


class TestShape(unittest.TestCase):
    """1조 — 빠진 필드도, 모르는 필드도 에러다."""

    def test_good_meta_passes(self):
        validate(good_meta())

    def test_every_required_field_is_required(self):
        for field in REQUIRED_FIELDS:
            meta = good_meta()
            del meta[field]
            with self.assertRaises(RunMetaError, msg=f"{field} 가 없어도 통과했다"):
                validate(meta)

    def test_unknown_field_is_error(self):
        with self.assertRaises(RunMetaError):
            validate(good_meta(temperature=0.0))

    def test_schema_version_must_match(self):
        with self.assertRaises(RunMetaError):
            validate(good_meta(schemaVersion="run-meta-v0"))

    def test_not_a_mapping(self):
        with self.assertRaises(RunMetaError):
            validate([("stage", "tag")])


class TestUnavailable(unittest.TestCase):
    """2조 — 값과 사유는 서로를 강제한다."""

    def test_unavailable_without_reason_is_error(self):
        for field in NULLABLE_FIELDS:
            with self.assertRaises(RunMetaError, msg=f"{field} 가 사유 없이 비었는데 통과했다"):
                validate(good_meta(**{field: UNAVAILABLE}))

    def test_unavailable_with_reason_passes(self):
        validate(good_meta(modelId=UNAVAILABLE,
                           unavailable={"modelId": "LLM 호출 없는 단계다"}))

    def test_reason_without_unavailable_value_is_error(self):
        with self.assertRaises(RunMetaError):
            validate(good_meta(unavailable={"modelId": "LLM 호출 없는 단계다"}))

    def test_empty_reason_is_error(self):
        for reason in ("", "   ", "unknown", "-", "TBD"):
            with self.assertRaises(RunMetaError, msg=f"사유 {reason!r} 가 통과했다"):
                validate(good_meta(seed=UNAVAILABLE, unavailable={"seed": reason}))

    def test_non_nullable_field_cannot_be_declared_unavailable(self):
        # 입력·임계값·단계가 없는 실행은 재현 대상이 아니다
        for field in ("inputs", "policy", "stage"):
            with self.assertRaises(RunMetaError, msg=f"{field} 를 비울 수 있었다"):
                validate(good_meta(unavailable={field: "몰라서"}))

    def test_unavailable_must_be_object(self):
        with self.assertRaises(RunMetaError):
            validate(good_meta(unavailable=["modelId"]))


class TestPlaceholders(unittest.TestCase):
    """3조 — 0 · 빈 문자열 · 'unknown' 으로 깔지 않는다."""

    def test_empty_strings_rejected(self):
        for field in ("stage", "modelId", "failureTaxonomyVersion",
                      "embeddingModel", "embeddingVersion", "vocabVersion"):
            for bad in ("", "   ", "unknown", "n/a", "none", "0"):
                with self.assertRaises(RunMetaError, msg=f"{field}={bad!r} 가 통과했다"):
                    validate(good_meta(**{field: bad}))

    def test_seed_zero_is_a_real_seed(self):
        validate(good_meta(seed=0))

    def test_seed_must_be_int_or_unavailable(self):
        for bad in ("42", None, 1.5, True):
            with self.assertRaises(RunMetaError, msg=f"seed={bad!r} 가 통과했다"):
                validate(good_meta(seed=bad))

    def test_empty_policy_rejected(self):
        for bad in ({}, None, "nMin=8"):
            with self.assertRaises(RunMetaError, msg=f"policy={bad!r} 가 통과했다"):
                validate(good_meta(policy=bad))

    def test_empty_inputs_rejected(self):
        for bad in ([], None, "data/input/reviews_50products.json"):
            with self.assertRaises(RunMetaError, msg=f"inputs={bad!r} 가 통과했다"):
                validate(good_meta(inputs=bad))


class TestDigests(unittest.TestCase):
    """3·4조 — 빈 해시·0 해시·길이가 안 맞는 해시는 에러다."""

    def _with_input(self, **over):
        item = {"name": "reviews", "path": "data/input/reviews_50products.json",
                "digestKind": "sha256", "digest": "b" * 64}
        item.update(over)
        return good_meta(inputs=[item])

    def test_empty_digest(self):
        with self.assertRaises(RunMetaError):
            validate(self._with_input(digest=""))

    def test_zero_filled_digest(self):
        with self.assertRaises(RunMetaError):
            validate(self._with_input(digest="0" * 64))

    def test_wrong_length_digest(self):
        with self.assertRaises(RunMetaError):
            validate(self._with_input(digest="b" * 40))          # sha256 자리에 커밋 해시
        with self.assertRaises(RunMetaError):
            validate(self._with_input(digestKind="gitCommit", digest="b" * 64))

    def test_non_hex_digest(self):
        with self.assertRaises(RunMetaError):
            validate(self._with_input(digest="z" * 64))

    def test_git_commit_digest_allowed(self):
        validate(self._with_input(digestKind="gitCommit", digest="c" * 40))

    def test_unknown_digest_kind(self):
        with self.assertRaises(RunMetaError):
            validate(self._with_input(digestKind="md5", digest="b" * 32))

    def test_input_object_is_closed(self):
        with self.assertRaises(RunMetaError):
            validate(self._with_input(note="적당히"))
        item = {"name": "reviews", "path": "p", "digestKind": "sha256"}
        with self.assertRaises(RunMetaError):
            validate(good_meta(inputs=[item]))

    def test_duplicate_input_names(self):
        one = {"name": "reviews", "path": "a", "digestKind": "sha256", "digest": "b" * 64}
        two = dict(one, path="b")
        with self.assertRaises(RunMetaError):
            validate(good_meta(inputs=[one, two]))

    def test_prompt_object_is_closed_and_hashed(self):
        with self.assertRaises(RunMetaError):
            validate(good_meta(promptVersion={"version": "tag-v1", "sha256": "a" * 64}))
        with self.assertRaises(RunMetaError):
            validate(good_meta(promptVersion={"path": "p", "version": "tag-v1",
                                              "sha256": ""}))
        with self.assertRaises(RunMetaError):
            validate(good_meta(promptVersion="pipeline/prompts/tag/v1.md"))


# 정본 25K 스냅샷은 archive 브랜치에 있다 (main 은 실행 경로만 둔다).
# 지우지 않고 건너뛴다 — 스냅샷을 받아오면 이 검사들이 다시 돌아야 한다:
#   git checkout archive -- data/input/reviews_50products.json
SNAPSHOT = ROOT / "data/input/reviews_50products.json"
NEEDS_SNAPSHOT = unittest.skipUnless(
    SNAPSHOT.exists(),
    "정본 스냅샷이 없다 (archive 브랜치) — git checkout archive -- data/input/reviews_50products.json")


def _tracked(path) -> bool:
    """git 이 추적하는 경로인가. `gitCommit` 다이제스트는 추적되는 파일에만 쓸 수 있다."""
    import subprocess
    try:
        r = subprocess.run(["git", "ls-files", "--error-unmatch", str(path)],
                           cwd=ROOT, capture_output=True)
        return r.returncode == 0
    except OSError:
        return False


# 파일이 **있는** 것과 **커밋돼 있는** 것은 다르다. main 은 실행 경로만 두므로
# 스냅샷이 디스크에 있어도 추적되지 않을 수 있고, 그러면 커밋 해시를 쓸 수 없다.
NEEDS_TRACKED_SNAPSHOT = unittest.skipUnless(
    _tracked(SNAPSHOT), "정본 스냅샷이 이 브랜치에서 추적되지 않는다 (archive 브랜치에 있다)")


@NEEDS_SNAPSHOT
class TestBuilders(unittest.TestCase):
    """조각 만들기 — 저장소의 실제 파일에서 찍는다."""

    def test_file_input_sha256(self):
        item = file_input("reviews", REVIEWS)
        self.assertEqual(item["digestKind"], "sha256")
        self.assertEqual(item["digest"], sha256_file(REVIEWS))
        self.assertEqual(item["path"], "data/input/reviews_50products.json")

    @NEEDS_TRACKED_SNAPSHOT
    def test_file_input_git_commit(self):
        item = file_input("reviews", REVIEWS, digest_kind="gitCommit")
        self.assertEqual(item["digestKind"], "gitCommit")
        self.assertEqual(len(item["digest"]), 40)
        self.assertEqual(item["digest"], git_commit_of(REVIEWS))

    def test_git_commit_refuses_dirty_worktree(self):
        """더러운 트리의 커밋 해시는 실제로 읽은 바이트를 가리키지 않는다."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            run = lambda *a: subprocess.run(["git", "-C", str(repo), *a],
                                            capture_output=True, text=True, check=True)
            run("init", "-q")
            run("config", "user.email", "t@t")
            run("config", "user.name", "t")
            f = repo / "input.json"
            f.write_text("{}\n")
            run("add", "input.json")
            run("commit", "-qm", "x")
            self.assertEqual(len(git_commit_of(f)), 40)
            f.write_text('{"changed": 1}\n')
            with self.assertRaises(RunMetaError):
                git_commit_of(f)
            untracked = repo / "other.json"
            untracked.write_text("{}\n")
            with self.assertRaises(RunMetaError):
                git_commit_of(untracked)

    def test_file_input_missing_file(self):
        with self.assertRaises(RunMetaError):
            file_input("nope", ROOT / "data/input/does_not_exist.json")

    def test_file_input_rejects_unknown_kind(self):
        with self.assertRaises(RunMetaError):
            file_input("reviews", REVIEWS, digest_kind="md5")

    def test_prompt_ref(self):
        ref = prompt_ref(PROMPT, "tag-v1")
        self.assertEqual(ref["sha256"], sha256_file(PROMPT))
        with self.assertRaises(RunMetaError):
            prompt_ref(PROMPT, "")

    def test_embedding_fields_come_from_per184_config(self):
        emb = embedding_fields()
        self.assertEqual(set(emb), {"embeddingModel", "embeddingVersion", "vocabVersion"})
        self.assertTrue(all(emb.values()))

    def test_failure_taxonomy_version(self):
        self.assertTrue(failure_taxonomy_version().startswith("failure-taxonomy-"))


@NEEDS_SNAPSHOT
class TestStamp(unittest.TestCase):
    """stamp 는 반쪽짜리를 내보내지 않는다 — 만들자마자 validate 를 돈다."""

    def _stamp(self, **over):
        kw = dict(
            stage="measure",
            policy={"nMin": 8},
            inputs=[file_input("reviews", REVIEWS)],
            model_id=UNAVAILABLE,
            prompt=prompt_ref(PROMPT, "tag-v1"),
            seed=7,
            failure_taxonomy=failure_taxonomy_version(),
            unavailable={"modelId": "이 단계는 LLM 을 부르지 않는다"},
        )
        kw.update(over)
        return stamp(**kw)

    def test_stamp_validates(self):
        meta = self._stamp()
        validate(meta)
        self.assertEqual(set(meta), set(REQUIRED_FIELDS))
        self.assertEqual(meta["embeddingModel"], embedding_fields()["embeddingModel"])

    def test_stamp_defaults_are_not_silent(self):
        """기본값이 unavailable 이라 사유를 안 적으면 그 자리에서 멈춘다."""
        with self.assertRaises(RunMetaError):
            stamp(stage="x", policy={"a": 1}, inputs=[file_input("reviews", REVIEWS)])

    def test_stamp_explicit_embedding_unavailable(self):
        meta = self._stamp(
            embedding={"embeddingModel": UNAVAILABLE, "embeddingVersion": UNAVAILABLE,
                       "vocabVersion": UNAVAILABLE},
            unavailable={"modelId": "이 단계는 LLM 을 부르지 않는다",
                         "embeddingModel": "벡터는 사이클 2(PER-212)다",
                         "embeddingVersion": "벡터는 사이클 2(PER-212)다",
                         "vocabVersion": "벡터는 사이클 2(PER-212)다"})
        self.assertEqual(meta["vocabVersion"], UNAVAILABLE)


class TestDiffAndCoverage(unittest.TestCase):
    """PER-201 이 "무엇을 바꿔서 좋아졌는가"를 여기서 읽는다."""

    def test_diff_reports_changed_axes_only(self):
        old = good_meta()
        new = good_meta(modelId="anthropic.claude-haiku-4-5",
                        policy={"nMin": 5, "rMin": 0.1, "sMin": 8})
        self.assertEqual(set(diff(old, new)), {"modelId", "policy"})
        self.assertEqual(diff(old, old), {})

    def test_diff_validates_both_sides(self):
        with self.assertRaises(RunMetaError):
            diff(good_meta(), good_meta(seed="42"))

    def test_coverage_three_states(self):
        meta = good_meta(modelId=UNAVAILABLE, unavailable={"modelId": "없다"})
        del meta["seed"]
        cov = coverage(meta)
        self.assertEqual(set(cov), set(COVERAGE_FIELDS))
        self.assertEqual(cov["modelId"], "unavailable")
        self.assertEqual(cov["seed"], "missing")
        self.assertEqual(cov["inputs"], "present")


if __name__ == "__main__":
    unittest.main()
