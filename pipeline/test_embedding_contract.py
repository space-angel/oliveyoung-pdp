"""
PER-184 임베딩 버전 기록 계약 테스트.

CLAUDE.md: **"'에러를 낸다'는 완료 조건은 테스트로 고정한다."** 이 파일이 고정하는 건
"버전이 빠지거나 어긋나면 조용히 폴백하지 않는다"는 네 조항이다.

  1조 벡터 레코드에 `embeddingModel` + `embeddingVersion`
  2조 어휘 버전과 임베딩 버전은 묶여서 움직인다 (하나만 바뀌면 에러)
  3조 태그·claim 산출물 `meta` 에 두 버전이 모두 남는다
  4조 모델 교체는 벡터 재생성 → 어휘 재확정 → 정답셋 재라벨링이 한 작업이다
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from embedding_contract import (
    CONFIG_PATH,
    EmbeddingContractError,
    EmbeddingSpec,
    aspect_digest,
    assert_paired_bump,
    assert_release_registered,
    assert_swap_complete,
    load_embedding_config,
    load_embedding_spec,
    plan_model_swap,
    spec_from_selected,
    stamp_meta,
    stamp_vector,
    validate_meta,
    validate_vector_record,
)
from tag_contract import ASPECTS


def write_config(payload: dict) -> Path:
    tmp = Path(tempfile.mkdtemp()) / "embedding_config.json"
    tmp.write_text(json.dumps(payload, ensure_ascii=False))
    return tmp


class TestConfigLoad(unittest.TestCase):
    """정본 설정이 지금 규칙과 맞는지, 그리고 위반이 에러인지."""

    def setUp(self) -> None:
        self.raw = json.loads(CONFIG_PATH.read_text())

    def test_real_config_loads(self) -> None:
        cfg = load_embedding_config()
        spec = load_embedding_spec()
        self.assertEqual(spec.model, cfg["selected"]["model"])
        self.assertTrue(spec.embeddingVersion)
        self.assertTrue(spec.vocabVersion)
        self.assertTrue(spec.goldenVersion)

    def test_real_config_pins_a_revision(self) -> None:
        """모델을 태그가 아니라 커밋으로 박는다 — 태그는 뒤에서 움직인다."""
        for key, cand in self.raw["candidates"].items():
            with self.subTest(candidate=key):
                self.assertRegex(cand["revision"], r"^[0-9a-f]{40}$")
        self.assertRegex(self.raw["selected"]["revision"], r"^[0-9a-f]{40}$")

    def test_selected_is_one_of_the_candidates(self) -> None:
        models = {c["model"]: c for c in self.raw["candidates"].values()}
        sel = self.raw["selected"]
        self.assertIn(sel["model"], models)
        cand = models[sel["model"]]
        self.assertEqual(cand["revision"], sel["revision"])
        self.assertEqual(cand["dimension"], sel["dimension"])

    def test_swap_requirements_are_the_three_steps(self) -> None:
        """4조 — 단계가 하나라도 빠진 설정은 계약을 조용히 약화시킨다."""
        self.assertEqual(
            tuple(self.raw["swapRequirements"]),
            ("vectorsRebuilt", "vocabRefixed", "goldenRelabeled"))

    def test_vocab_drift_is_an_error(self) -> None:
        """택소노미를 확장하면서 vocabVersion 을 안 올리면 로드에서 걸린다."""
        bad = json.loads(json.dumps(self.raw))
        bad["selected"]["aspectsSha256"] = aspect_digest(ASPECTS + ("신규축",))
        with self.assertRaises(EmbeddingContractError) as cm:
            load_embedding_config(write_config(bad))
        self.assertIn("어휘", str(cm.exception))

    def test_schema_version_mismatch_is_an_error(self) -> None:
        bad = json.loads(json.dumps(self.raw))
        bad["schemaVersion"] = "embedding-config-v0"
        with self.assertRaises(EmbeddingContractError):
            load_embedding_config(write_config(bad))

    def test_missing_selected_field_is_an_error(self) -> None:
        for field in ("embeddingVersion", "vocabVersion", "goldenVersion",
                      "model", "revision", "aspectsSha256"):
            with self.subTest(field=field):
                bad = json.loads(json.dumps(self.raw))
                bad["selected"].pop(field)
                with self.assertRaises(EmbeddingContractError):
                    load_embedding_config(write_config(bad))

    def test_unregistered_release_is_an_error(self) -> None:
        """2조 — 어휘만 바꾼 조합은 등록돼 있지 않으므로 배포되지 않는다."""
        bad = json.loads(json.dumps(self.raw))
        bad["selected"]["vocabVersion"] = "aspect-v2"
        with self.assertRaises(EmbeddingContractError) as cm:
            load_embedding_config(write_config(bad))
        self.assertIn("등록되지 않은", str(cm.exception))

    def test_one_embedding_version_cannot_mean_two_vocabs(self) -> None:
        bad = json.loads(json.dumps(self.raw))
        rel = dict(bad["releases"][0])
        rel["vocabVersion"] = "aspect-v9"
        bad["releases"].append(rel)
        with self.assertRaises(EmbeddingContractError) as cm:
            load_embedding_config(write_config(bad))
        self.assertIn("두 가지를 뜻할 수 없다", str(cm.exception))

    def test_empty_releases_is_an_error(self) -> None:
        bad = json.loads(json.dumps(self.raw))
        bad["releases"] = []
        with self.assertRaises(EmbeddingContractError):
            load_embedding_config(write_config(bad))

    def test_bad_dimension_is_an_error(self) -> None:
        for value in (0, -1, "1024"):
            with self.subTest(value=value):
                bad = json.loads(json.dumps(self.raw))
                bad["selected"]["dimension"] = value
                with self.assertRaises(EmbeddingContractError):
                    load_embedding_config(write_config(bad))


def make_spec(**over) -> EmbeddingSpec:
    base = dict(embeddingVersion="emb-v1", model="org/model-a", revision="a" * 40,
                dimension=4, maxSeqLength=512, vocabVersion="aspect-v1",
                goldenVersion="gold-v2")
    base.update(over)
    return EmbeddingSpec(**base)


class TestVectorRecord(unittest.TestCase):
    """1조 — 벡터만 남으면 무엇으로 만들었는지 영영 알 수 없다."""

    def setUp(self) -> None:
        self.spec = make_spec()

    def test_stamped_record_passes(self) -> None:
        rec = stamp_vector({"id": "r1", "vector": [0.1, 0.2, 0.3, 0.4]}, self.spec)
        self.assertEqual(rec["embeddingModel"], "org/model-a")
        self.assertEqual(rec["embeddingVersion"], "emb-v1")
        validate_vector_record(rec, self.spec)

    def test_missing_stamp_is_an_error(self) -> None:
        for rec in ({"id": "r1"},
                    {"id": "r1", "embeddingModel": "org/model-a"},
                    {"id": "r1", "embeddingVersion": "emb-v1"}):
            with self.subTest(record=rec):
                with self.assertRaises(EmbeddingContractError):
                    validate_vector_record(rec, self.spec)

    def test_blank_stamp_is_not_a_stamp(self) -> None:
        with self.assertRaises(EmbeddingContractError):
            validate_vector_record(
                {"embeddingModel": "", "embeddingVersion": "emb-v1"}, self.spec)

    def test_other_model_vector_is_an_error_not_a_fallback(self) -> None:
        rec = {"embeddingModel": "org/model-b", "embeddingVersion": "emb-v1"}
        with self.assertRaises(EmbeddingContractError):
            validate_vector_record(rec, self.spec)

    def test_old_version_vector_is_an_error(self) -> None:
        rec = {"embeddingModel": "org/model-a", "embeddingVersion": "emb-v0"}
        with self.assertRaises(EmbeddingContractError):
            validate_vector_record(rec, self.spec)

    def test_dimension_mismatch_is_an_error(self) -> None:
        rec = stamp_vector({"vector": [0.0, 0.1]}, self.spec)
        with self.assertRaises(EmbeddingContractError):
            validate_vector_record(rec, self.spec)

    def test_stamp_does_not_overwrite_another_model(self) -> None:
        with self.assertRaises(EmbeddingContractError):
            stamp_vector({"embeddingModel": "org/model-b"}, self.spec)

    def test_stamp_is_idempotent(self) -> None:
        once = stamp_vector({"id": "r1"}, self.spec)
        self.assertEqual(stamp_vector(once, self.spec), once)


class TestOutputMeta(unittest.TestCase):
    """3조 — 리포트 수치가 어느 벡터·어휘에서 나왔는지 되짚을 수 있어야 한다."""

    def setUp(self) -> None:
        self.spec = make_spec()

    def test_stamped_meta_passes(self) -> None:
        meta = stamp_meta({"issue": "PER-212", "tags": 10}, self.spec)
        validate_meta(meta, self.spec)
        self.assertEqual(meta["vocabVersion"], "aspect-v1")
        self.assertEqual(meta["issue"], "PER-212")

    def test_embedding_version_alone_is_an_error(self) -> None:
        """둘 중 하나만 남긴 meta 가 이 계약이 막으려는 바로 그 상태다."""
        meta = {"embeddingModel": "org/model-a", "embeddingVersion": "emb-v1"}
        with self.assertRaises(EmbeddingContractError) as cm:
            validate_meta(meta, self.spec)
        self.assertIn("vocabVersion", str(cm.exception))

    def test_vocab_version_alone_is_an_error(self) -> None:
        with self.assertRaises(EmbeddingContractError):
            validate_meta({"vocabVersion": "aspect-v1"}, self.spec)

    def test_stale_pair_is_an_error(self) -> None:
        meta = stamp_meta({}, make_spec(embeddingVersion="emb-v0",
                                        vocabVersion="aspect-v0"))
        with self.assertRaises(EmbeddingContractError):
            validate_meta(meta, self.spec)


class TestPairedBump(unittest.TestCase):
    """2조 — 둘 중 하나만 바뀌는 배포 금지."""

    def test_embedding_only_bump_is_an_error(self) -> None:
        with self.assertRaises(EmbeddingContractError) as cm:
            assert_paired_bump(make_spec(), make_spec(embeddingVersion="emb-v2"))
        self.assertIn("어휘", str(cm.exception))

    def test_vocab_only_bump_is_an_error(self) -> None:
        with self.assertRaises(EmbeddingContractError) as cm:
            assert_paired_bump(make_spec(), make_spec(vocabVersion="aspect-v2"))
        self.assertIn("임베딩", str(cm.exception))

    def test_both_bumped_is_fine(self) -> None:
        assert_paired_bump(make_spec(), make_spec(embeddingVersion="emb-v2",
                                                  vocabVersion="aspect-v2"))

    def test_neither_bumped_is_fine(self) -> None:
        assert_paired_bump(make_spec(), make_spec())


class TestModelSwap(unittest.TestCase):
    """4조 — 부분 교체는 옛 축으로 매긴 정답셋에 새 벡터를 채점시킨다."""

    def setUp(self) -> None:
        self.cfg = {
            "swapRequirements": ["vectorsRebuilt", "vocabRefixed", "goldenRelabeled"],
            "releases": [
                {"embeddingVersion": "emb-v1", "vocabVersion": "aspect-v1",
                 "goldenVersion": "gold-v2"},
                {"embeddingVersion": "emb-v2", "vocabVersion": "aspect-v2",
                 "goldenVersion": "gold-v3"},
            ],
        }
        self.old = make_spec()
        self.new = make_spec(model="org/model-b", revision="b" * 40,
                             embeddingVersion="emb-v2", vocabVersion="aspect-v2",
                             goldenVersion="gold-v3")

    def test_plan_lists_all_three_steps(self) -> None:
        self.assertEqual(plan_model_swap(self.old, self.new, self.cfg),
                         ("vectorsRebuilt", "vocabRefixed", "goldenRelabeled"))

    def test_swapping_to_the_same_model_is_an_error(self) -> None:
        with self.assertRaises(EmbeddingContractError):
            plan_model_swap(self.old, make_spec(embeddingVersion="emb-v2",
                                                vocabVersion="aspect-v2",
                                                goldenVersion="gold-v3"), self.cfg)

    def test_swap_to_unregistered_release_is_an_error(self) -> None:
        stray = make_spec(model="org/model-c", revision="c" * 40,
                          embeddingVersion="emb-v3", vocabVersion="aspect-v3",
                          goldenVersion="gold-v9")
        with self.assertRaises(EmbeddingContractError):
            plan_model_swap(self.old, stray, self.cfg)

    def test_partial_swap_is_an_error(self) -> None:
        for done in (
            {"vectorsRebuilt": True},
            {"vectorsRebuilt": True, "vocabRefixed": True},
            {"vectorsRebuilt": True, "vocabRefixed": True, "goldenRelabeled": False},
        ):
            with self.subTest(done=done):
                with self.assertRaises(EmbeddingContractError) as cm:
                    assert_swap_complete(done, self.cfg)
                self.assertIn("goldenRelabeled", str(cm.exception))

    def test_unknown_step_is_an_error(self) -> None:
        with self.assertRaises(EmbeddingContractError):
            assert_swap_complete({"vectorsRebuilt": True, "vocabRefixed": True,
                                  "goldenRelabeled": True, "shipped": True}, self.cfg)

    def test_complete_swap_passes(self) -> None:
        assert_swap_complete({"vectorsRebuilt": True, "vocabRefixed": True,
                              "goldenRelabeled": True}, self.cfg)

    def test_release_registration_uses_all_three_versions(self) -> None:
        mixed = make_spec(embeddingVersion="emb-v2", vocabVersion="aspect-v2",
                          goldenVersion="gold-v2")
        with self.assertRaises(EmbeddingContractError):
            assert_release_registered(mixed, self.cfg)


class TestAspectDigest(unittest.TestCase):
    def test_digest_moves_when_the_vocabulary_moves(self) -> None:
        self.assertNotEqual(aspect_digest(ASPECTS), aspect_digest(ASPECTS + ("신규축",)))
        self.assertNotEqual(aspect_digest(ASPECTS),
                            aspect_digest(tuple(reversed(ASPECTS))))

    def test_digest_is_stable(self) -> None:
        self.assertEqual(aspect_digest(ASPECTS), aspect_digest(ASPECTS))


class TestSpecFromSelected(unittest.TestCase):
    def test_prefixes_default_to_empty(self) -> None:
        spec = spec_from_selected({
            "embeddingVersion": "emb-v1", "model": "m", "revision": "r",
            "dimension": 8, "maxSeqLength": 512, "vocabVersion": "v",
            "goldenVersion": "g",
        })
        self.assertEqual(spec.queryPrefix, "")
        self.assertEqual(spec.passagePrefix, "")
        self.assertTrue(spec.normalize)


if __name__ == "__main__":
    unittest.main()
