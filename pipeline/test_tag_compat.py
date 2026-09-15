"""OpenAI 호환 태깅 경로 계약 (PER-175).

여기서 고정하는 것은 **수거가 멈추지 않는다**는 것이다. 위반 태그 하나에 collect 가
예외로 죽으면 25,000건을 다시 불러야 한다. 위반은 버리되 매니페스트에 남는다 —
`pipeline/tag.py` (Batch API 경로) 와 같은 규칙이어야 두 경로의 채점을 비교할 수 있다.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

import tag_compat  # noqa: E402


def review(review_id: int, content: str) -> dict:
    return {
        "reviewId": review_id,
        "productId": "P001",
        "raw": {"content": content, "rating": 5},
    }


class CollectTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "data/intermediate/tag_runs").mkdir(parents=True)
        self.runs = self.root / "data/intermediate/tag_runs"

        self.reviews = [
            review(1, "발색은 예쁜데 지속력이 아쉬워요"),
            review(2, "촉촉함이 오래갑니다"),
        ]
        patches = [
            mock.patch.object(tag_compat, "ROOT", self.root),
            mock.patch.object(tag_compat, "RUNS_DIR", self.runs),
            mock.patch.object(tag_compat, "load_reviews", lambda pilot: self.reviews),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def write_run(self, rows: list[dict]) -> str:
        label = "t"
        raw = self.runs / f"{label}_raw.jsonl"
        raw.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
        (self.runs / f"{label}.json").write_text(
            json.dumps(
                {"label": label, "pilot": True, "model": "m", "raw": str(raw.relative_to(self.root))}
            )
        )
        return label

    def manifest(self, label: str) -> dict:
        return json.loads((self.runs / f"{label}.json").read_text())

    def chunk(self, idx: int, results: list[dict], **extra) -> dict:
        return {"chunk": idx, "text": json.dumps({"results": results}, ensure_ascii=False), **extra}

    def test_계약_위반_태그는_수거를_멈추지_않고_버려진다(self) -> None:
        label = self.write_run([
            self.chunk(0, [
                {"reviewId": 1, "aspects": [
                    {"aspect": "발색", "polarity": "positive", "snippet": "발색은 예쁜데"},
                    # 원문에 없는 인용 — 계약 위반. 이것만 빠지고 나머지는 살아야 한다
                    {"aspect": "지속성", "polarity": "negative", "snippet": "하루종일 유지됩니다"},
                ]},
                {"reviewId": 2, "aspects": [
                    {"aspect": "보습감", "polarity": "positive", "snippet": "촉촉함이 오래갑니다"},
                ]},
            ]),
        ])

        tag_compat.collect(label)

        out = self.manifest(label)["output"]
        self.assertEqual(out["tagsRaw"], 3)
        self.assertEqual(out["tags"], 2)
        self.assertEqual(out["contractViolations"], 1)
        self.assertIn("원문에 없음", out["violations"][0]["violation"])

        kept = [json.loads(l) for l in (self.root / out["path"]).read_text().splitlines()]
        self.assertEqual({(t["reviewId"], t["aspect"]) for t in kept}, {(1, "발색"), (2, "보습감")})

    def test_위반_태그도_raw_산출물에는_남는다(self) -> None:
        label = self.write_run([
            self.chunk(0, [{"reviewId": 1, "aspects": [
                {"aspect": "지속성", "polarity": "negative", "snippet": "원문에없는말"},
            ]}]),
        ])

        tag_compat.collect(label)

        out = self.manifest(label)["output"]
        raw = [json.loads(l) for l in (self.root / out["rawPath"]).read_text().splitlines()]
        self.assertEqual(len(raw), 1)
        self.assertEqual(json.loads((self.root / out["path"]).read_text() or "null"), None)

    def test_토큰_사용량과_finish_reason_을_실측으로_모은다(self) -> None:
        label = self.write_run([
            self.chunk(0, [{"reviewId": 1, "aspects": []}],
                       usage={"prompt_tokens": 100, "completion_tokens": 20,
                              "prompt_tokens_details": {"cached_tokens": 40}},
                       finishReason="stop"),
            self.chunk(1, [{"reviewId": 2, "aspects": []}],
                       usage={"prompt_tokens": 110, "completion_tokens": 25},
                       finishReason="stop"),
        ])

        tag_compat.collect(label)

        usage = self.manifest(label)["output"]["usage"]
        self.assertEqual(usage, {"input": 210, "output": 45, "cacheRead": 40, "chunks": 2})
        self.assertEqual(self.manifest(label)["output"]["finishReasons"], {"stop": 2})

    def test_잘린_응답은_finish_reason_과_함께_실패_청크로_남는다(self) -> None:
        """추론 모델이 max_tokens 에 걸리면 JSON 이 깨진다. '모델이 못 한다'와
        '상한이 낮다'를 구별할 수 있어야 청크 크기를 줄일지 판단할 수 있다."""
        label = self.write_run([
            {"chunk": 0, "text": '{"results": [{"reviewId": 1, "aspe',
             "finishReason": "length", "usage": {"prompt_tokens": 9, "completion_tokens": 8000}},
        ])

        tag_compat.collect(label)

        out = self.manifest(label)["output"]
        self.assertEqual(len(out["failedChunks"]), 1)
        self.assertEqual(out["failedChunks"][0]["finishReason"], "length")
        self.assertEqual(out["finishReasons"], {"length": 1})

    def test_결과에_없는_리뷰는_재실행_대상으로_센다(self) -> None:
        label = self.write_run([self.chunk(0, [{"reviewId": 1, "aspects": []}])])

        tag_compat.collect(label)

        out = self.manifest(label)["output"]
        self.assertEqual(out["missingCount"], 1)
        self.assertEqual(out["missingReviewIds"], [2])


if __name__ == "__main__":
    unittest.main()
