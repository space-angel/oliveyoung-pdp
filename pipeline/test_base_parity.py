"""측정 기반이 파이프라인과 같은가 (PER-183·186·188 인계).

`--check` 는 "리포트가 재생성되는가"만 본다. 기반이 파이프라인과 갈린 채로도
리포트는 재현된다 — 2026-09-15 에 실제로 그랬다. `score_all` 에 `aspect_counts` 를
안 넘겨 `trustPrior` 의 `onTopic` 이 비었고, 게이트2 의 작성자 1표 대표가 달라져
후보 수가 7,660 대 7,689 로 벌어졌는데도 세 리포트가 모두 `--check` 를 통과했다.

여기서 고정하는 것은 `ingest.assert_matches_ingest` 가 **그 상황에서 에러를 내는가**다.
"에러를 낸다" 는 완료 조건은 테스트로 고정한다 (CLAUDE.md).
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from ingest import BaseParityError, assert_matches_ingest  # noqa: E402


def record(review_id: int, score: float, on_topic=None) -> dict:
    """입수 레코드의 모양만 흉내 낸 최소 고정물. 파생층에 trustPrior 가 있다."""
    return {
        "reviewId": review_id,
        "productId": "p001",
        "raw": {"reviewDate": "2026-07-19", "rating": 5},
        "derived": {"trustPrior": {"score": score, "signals": {"onTopic": on_topic}}},
    }


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records))


class BaseParity(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "v5_reviews.jsonl"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_같은_경로로_만들면_통과한다(self):
        records = [record(1, 0.72, 1.0), record(2, 0.51, 0.0)]
        write_jsonl(self.out, records)
        assert_matches_ingest([record(1, 0.72, 1.0), record(2, 0.51, 0.0)], self.out)

    def test_trustPrior_가_다르면_에러다(self):
        """onTopic 이 빠지면 점수가 달라진다 — 2026-09-15 에 실제로 일어난 일."""
        write_jsonl(self.out, [record(1, 0.7199, 1.0)])
        with self.assertRaises(BaseParityError) as cm:
            assert_matches_ingest([record(1, 0.5799, None)], self.out, who="게이트4 측정")
        msg = str(cm.exception)
        self.assertIn("reviewId 1", msg)
        self.assertIn("trustPrior", msg)
        self.assertIn("0.5799", msg)   # 측정 쪽 값
        self.assertIn("0.7199", msg)   # 입수 쪽 값
        self.assertIn("게이트4 측정", msg)

    def test_불일치_경로를_짚어_준다(self):
        """'다르다' 만으로는 못 고친다. 어느 필드인지 나와야 한다."""
        write_jsonl(self.out, [record(1, 0.72, 1.0)])
        with self.assertRaises(BaseParityError) as cm:
            assert_matches_ingest([record(1, 0.72, 0.0)], self.out)
        self.assertIn(".derived.trustPrior.signals.onTopic", str(cm.exception))

    def test_레코드_수가_다르면_에러다(self):
        write_jsonl(self.out, [record(1, 0.72), record(2, 0.51)])
        with self.assertRaises(BaseParityError) as cm:
            assert_matches_ingest([record(1, 0.72)], self.out)
        self.assertIn("레코드 수", str(cm.exception))

    def test_산출물이_없으면_돌리라고_알려준다(self):
        with self.assertRaises(BaseParityError) as cm:
            assert_matches_ingest([record(1, 0.72)], self.out)
        self.assertIn("pipeline/ingest.py", str(cm.exception))

    def test_원문층이_달라도_잡는다(self):
        """파생층만 보는 게 아니다 — 카탈로그·계약이 바뀌면 원문층도 갈린다."""
        base = record(1, 0.72)
        write_jsonl(self.out, [base])
        mine = json.loads(json.dumps(base))
        mine["productId"] = "p002"
        with self.assertRaises(BaseParityError) as cm:
            assert_matches_ingest([mine], self.out)
        self.assertIn("productId", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
