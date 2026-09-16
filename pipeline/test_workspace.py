"""작업공간 계약 (PER-194).

이 파일이 지키는 것은 하나다 — **정본 경로가 기존 상수와 한 글자도 다르지 않다.**
여기가 어긋나면 25,000건으로 만든 리포트가 조용히 다른 파일을 보게 된다.

두 번째는 격리다. 런의 어떤 경로도 `data/input` · `data/output` · `eval/reports` ·
`eval/gold` 를 가리키면 안 된다.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import generate  # noqa: E402
import ingest  # noqa: E402
import ledger  # noqa: E402
import tag  # noqa: E402
from catalog import DEFAULT_CATALOG_PATH  # noqa: E402
from workspace import (  # noqa: E402
    CANONICAL,
    CANONICAL_PREFIXES,
    ROOT,
    Workspace,
    WorkspaceError,
)


class CanonicalMatchesExistingConstants(unittest.TestCase):
    """정본 작업공간이 각 모듈의 기존 상수와 같은 파일을 가리키는가.

    상수 쪽을 정답으로 둔다. 작업공간은 나중에 만든 것이라, 다르면 작업공간이 틀렸다.
    """

    def test_reviews_input(self):
        self.assertEqual(CANONICAL.reviews_input, ingest.INPUT_PATH)

    def test_catalog(self):
        self.assertEqual(CANONICAL.catalog, DEFAULT_CATALOG_PATH)

    def test_reviews(self):
        self.assertEqual(CANONICAL.reviews, ingest.OUTPUT_PATH)
        self.assertEqual(CANONICAL.reviews, ledger.REVIEWS_PATH)
        self.assertEqual(CANONICAL.reviews, tag.REVIEWS_PATH)

    def test_reviews_meta(self):
        self.assertEqual(CANONICAL.reviews_meta, ingest.META_PATH)

    def test_tags(self):
        self.assertEqual(CANONICAL.tags, ledger.TAGS_PATH)
        self.assertEqual(CANONICAL.tags, tag.CANONICAL_TAGS_PATH)
        self.assertEqual(CANONICAL.tags, ingest.TAGS_PATH)

    def test_tags_meta(self):
        self.assertEqual(CANONICAL.tags_meta, ledger.TAGS_META_PATH)
        self.assertEqual(CANONICAL.tags_meta, tag.CANONICAL_META_PATH)

    def test_ledger(self):
        self.assertEqual(CANONICAL.ledger, ledger.LEDGER_PATH)
        self.assertEqual(CANONICAL.ledger_summary, ledger.SUMMARY_PATH)

    def test_claims(self):
        self.assertEqual(CANONICAL.claims, generate.OUT_PATH)
        self.assertEqual(CANONICAL.claims_meta, generate.META_PATH)

    def test_canonical_has_no_run_meta(self):
        """정본에는 런 메타가 없다 — 단계마다 meta 가 따로 있다."""
        with self.assertRaises(WorkspaceError):
            CANONICAL.run_meta


class RunIsIsolated(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace.for_run("oy-A000000211119")

    def test_every_path_lives_under_the_run(self):
        base = ROOT / "data/runs/oy-A000000211119"
        for name in ("reviews_input", "catalog", "reviews", "reviews_meta", "tags",
                     "tags_meta", "ledger", "ledger_summary", "claims", "claims_meta",
                     "claims_csv", "run_meta"):
            with self.subTest(name=name):
                self.assertEqual(getattr(self.ws, name).parent, base)

    def test_no_path_touches_canonical(self):
        for name in ("reviews_input", "catalog", "reviews", "claims", "claims_meta"):
            rel = getattr(self.ws, name).relative_to(ROOT).as_posix()
            with self.subTest(name=name):
                self.assertFalse(rel.startswith(CANONICAL_PREFIXES), rel)

    def test_assert_isolated_passes(self):
        self.ws.assert_isolated()          # 예외가 없어야 한다

    def test_run_uses_its_own_catalog(self):
        """정본 카탈로그를 읽지 않는다 — 미등록 goodsNo 는 에러이고(PER-171),
        새 제품을 정본에 집어넣는 유혹을 원천 차단한다."""
        self.assertNotEqual(self.ws.catalog, DEFAULT_CATALOG_PATH)


class RunIdIsNarrow(unittest.TestCase):
    """런 ID 는 경로가 된다. 넓게 받으면 런 밖으로 쓴다."""

    def test_rejects_traversal(self):
        for bad in ("../escape", "a/b", "..", "./x", "/abs"):
            with self.subTest(bad=bad), self.assertRaises(WorkspaceError):
                Workspace.for_run(bad)

    def test_rejects_empty_and_too_long(self):
        for bad in ("", "-leading", "x" * 65):
            with self.subTest(bad=bad), self.assertRaises(WorkspaceError):
                Workspace.for_run(bad)

    def test_accepts_goods_no(self):
        for ok in ("A000000211119", "oy-A000000211119", "run_2026-09-16.1"):
            with self.subTest(ok=ok):
                self.assertEqual(Workspace.for_run(ok).run_id, ok)


class EnsureDirs(unittest.TestCase):
    def test_canonical_creates_nothing(self):
        """정본에는 아무것도 만들지 않는다 — 이미 있고, 만들 이유도 없다."""
        CANONICAL.ensure_dirs()            # 예외 없이 통과하면 된다

    def test_run_creates_its_base(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            ws = Workspace.for_run("t1", root=Path(tmp))
            self.assertFalse(ws.base.exists())
            ws.ensure_dirs()
            self.assertTrue(ws.base.is_dir())


if __name__ == "__main__":
    unittest.main()
