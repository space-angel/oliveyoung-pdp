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


class ParseTextTest(unittest.TestCase):
    """추론 모델의 `<reasoning>` 블록 처리 (PER-175).

    gpt-oss 계열은 사고 과정을 별도 필드가 아니라 `content` 안에 섞어 보낸다.
    실측: 20건 청크에서 출력 4,615토큰 중 reasoning 이 11,312자로 **89%** 를 차지했다.
    """

    def test_닫힌_reasoning_블록은_걷어낸다(self) -> None:
        from tag import parse_text

        self.assertEqual(
            parse_text('<reasoning>먼저 축을 훑는다</reasoning>\n{"results": []}'),
            {"results": []},
        )

    def test_reasoning_뒤에_코드펜스가_와도_읽는다(self) -> None:
        from tag import parse_text

        self.assertEqual(
            parse_text('<reasoning>x</reasoning>```json\n{"results": [1]}\n```'),
            {"results": [1]},
        )

    def test_열린_채_끝난_reasoning_은_파싱_실패로_남긴다(self) -> None:
        """max_tokens 에 걸려 잘린 응답이다. 조용히 빈 결과로 넘기면 그 청크의
        리뷰가 '태그 없음'으로 기록돼 재현율 손실이 영원히 안 보인다."""
        from tag import parse_text

        with self.assertRaises(json.JSONDecodeError):
            parse_text("<reasoning>축을 하나씩 보면 보습감은")


class ResumeGuardTest(unittest.TestCase):
    """같은 label 을 다른 모델로 다시 돌리면 멈춘다 (PER-175).

    재개는 `<label>_raw.jsonl` 을 label 로만 찾는다. 가드가 없으면 다른 모델이
    남의 태그를 수거해 그 모델의 성적표가 된다 — 벤치에서 실제로 났던 사고다
    (`minimax-m2.5` · `kimi-k2.5` 가 같은 label 로 접혔다).
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.runs = self.root / "runs"
        self.runs.mkdir(parents=True)
        for p in (
            mock.patch.object(tag_compat, "ROOT", self.root),
            mock.patch.object(tag_compat, "RUNS_DIR", self.runs),
            mock.patch.object(tag_compat, "load_reviews", lambda pilot: [review(1, "촉촉해요")]),
            mock.patch.object(tag_compat, "api_key", lambda env: "k"),
        ):
            p.start()
            self.addCleanup(p.stop)
        (self.runs / "reuse.json").write_text(json.dumps({"label": "reuse", "model": "모델A"}))

    def test_다른_모델로_같은_label_을_쓰면_멈춘다(self) -> None:
        with self.assertRaises(SystemExit) as cm:
            tag_compat.run("모델B", True, "reuse", "http://x", "E", 1, 10)
        msg = str(cm.exception)
        self.assertIn("이미 다른 조건으로 돌린 실행", msg)
        self.assertIn("모델A", msg)
        self.assertIn("모델B", msg)


class IncompleteChunkTest(unittest.TestCase):
    """받았지만 쓸 수 없는 청크를 골라낸다 (PER-175).

    재개는 "응답을 받았는지" 만 보기 때문에, 잘린 JSON 이나 리뷰를 빼먹은 결과는
    영영 다시 불리지 않는다. 그 리뷰들은 태그가 없는 채로 남고, "아무 aspect 도
    말하지 않은 리뷰" 와 구별되지 않게 된다.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raw = Path(self.tmp.name) / "raw.jsonl"
        self.reviews = [review(i, f"본문 {i}") for i in range(1, 5)]  # 청크 2개 × 2건

    def write(self, rows: list[dict]) -> None:
        self.raw.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))

    def chunk(self, idx: int, ids: list[int]) -> dict:
        payload = {"results": [{"reviewId": i, "aspects": []} for i in ids]}
        return {"chunk": idx, "text": json.dumps(payload, ensure_ascii=False)}

    def test_온전한_청크는_다시_부르지_않는다(self) -> None:
        self.write([self.chunk(0, [1, 2]), self.chunk(1, [3, 4])])
        self.assertEqual(tag_compat.incomplete_chunks(self.raw, self.reviews, 2), set())

    def test_잘린_응답은_다시_부를_대상이다(self) -> None:
        self.write([{"chunk": 0, "text": '{"results": [{"reviewId": 1'}, self.chunk(1, [3, 4])])
        self.assertEqual(tag_compat.incomplete_chunks(self.raw, self.reviews, 2), {0})

    def test_리뷰를_빼먹은_응답도_다시_부를_대상이다(self) -> None:
        """파싱은 되지만 입력의 모든 reviewId 가 결과에 있어야 한다는 계약을 어긴다."""
        self.write([self.chunk(0, [1]), self.chunk(1, [3, 4])])
        self.assertEqual(tag_compat.incomplete_chunks(self.raw, self.reviews, 2), {0})

    def test_추론_블록이_붙어도_온전하면_다시_부르지_않는다(self) -> None:
        row = self.chunk(0, [1, 2])
        row["text"] = "<reasoning>축을 훑는다</reasoning>" + row["text"]
        self.write([row, self.chunk(1, [3, 4])])
        self.assertEqual(tag_compat.incomplete_chunks(self.raw, self.reviews, 2), set())
