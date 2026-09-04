"""
v5 파이프라인 러너.

PRD §11 의존성 순서를 단계 레지스트리로 고정한다. 아직 안 만든 단계는 **조용히
건너뛰지 않고 에러**를 내고, 그 단계를 맡은 이슈 번호를 알려준다.

  .venv/bin/python pipeline/run_v5.py --steps ingest
  .venv/bin/python pipeline/run_v5.py            # 구현된 단계까지 순서대로
  .venv/bin/python pipeline/run_v5.py --list

v4 는 `legacy/v4/pipeline/run_pipeline.py` 로 동결돼 있다. 두 파이프라인은
중간 산출물 파일명이 겹치지 않는다 (v4: `step*_*.json` / v5: `v5_*`).
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


@dataclass(frozen=True)
class Step:
    name: str
    issue: str
    milestone: str
    summary: str
    entrypoint: str | None = None  # "모듈:함수" — None 이면 미구현

    @property
    def implemented(self) -> bool:
        return self.entrypoint is not None


STEPS: tuple[Step, ...] = (
    Step(
        "catalog",
        "PER-171",
        "#2 데이터 계약",
        "제품 동일성 카탈로그 생성 (goodsNo → productId)",
        "build_product_catalog:main",
    ),
    Step(
        "ingest",
        "PER-173",
        "#2 데이터 계약",
        "입수 태깅 — 원문/조건/파생 3층으로 스냅샷 적재",
        "ingest:main",
    ),
    Step("tag", "PER-175", "#2 데이터 계약", "25K 전수 aspect/polarity 태깅 (Batch API)"),
    Step("gates", "PER-182~188", "#4 근거 선별 게이트", "동일성·중복·방향성·충분성 4게이트 + rejected[] 기록"),
    Step("claims", "PER-189~195", "#5 주장 생성", "claim 생성 + 인용 원문 부분문자열 강제 + 스키마 검증"),
    Step("judge", "PER-196~201", "#6 자동 평가", "루브릭 judge (생성과 다른 모델) + 전수 평가"),
)
BY_NAME = {s.name: s for s in STEPS}


def run_step(step: Step) -> None:
    if not step.implemented:
        raise SystemExit(
            f"[{step.name}] 미구현 — {step.issue} ({step.milestone})\n"
            f"  {step.summary}\n"
            "  이 단계를 건너뛰고 뒤 단계를 돌리면 근거 없는 결과가 나온다."
        )
    module_name, func_name = step.entrypoint.split(":")
    module = __import__(module_name)
    print(f"\n{'=' * 60}\n[{step.name}] {step.summary}  ({step.issue})\n{'=' * 60}")
    sys.argv = [module_name]  # 하위 스크립트의 argparse 가 러너 인자를 먹지 않게
    getattr(module, func_name)()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", help="쉼표로 구분 (예: catalog,ingest). 생략하면 구현된 단계 전부")
    ap.add_argument("--list", action="store_true", help="단계 목록과 구현 상태")
    args = ap.parse_args()

    if args.list:
        for s in STEPS:
            mark = "구현" if s.implemented else "미구현"
            print(f"  {s.name:9s} [{mark:3s}] {s.issue:11s} {s.milestone:16s} {s.summary}")
        return

    if args.steps:
        names = [n.strip() for n in args.steps.split(",")]
        unknown = [n for n in names if n not in BY_NAME]
        if unknown:
            raise SystemExit(f"알 수 없는 단계 {unknown}. --list 로 확인하라")
        targets = [BY_NAME[n] for n in names]
    else:
        targets = [s for s in STEPS if s.implemented]

    for step in targets:
        run_step(step)

    done = [s.name for s in targets]
    pending = [s for s in STEPS if not s.implemented]
    print(f"\n완료: {', '.join(done)}")
    if pending:
        print("남은 단계: " + ", ".join(f"{s.name}({s.issue})" for s in pending))


if __name__ == "__main__":
    main()
