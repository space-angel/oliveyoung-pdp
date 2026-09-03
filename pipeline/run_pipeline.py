"""
End-to-end pipeline runner.
Usage: python run_pipeline.py [--steps 0,1,2,3,4] [--skip-cache]
"""
import argparse
import subprocess
import sys
from pathlib import Path

PIPELINE_DIR = Path(__file__).parent / "pipeline"
PYTHON = Path(__file__).parents[1] / ".venv/bin/python3"

STEPS = {
    0: "step0_preprocess.py",
    1: "step1_extract_claims.py",
    2: "step2_aggregate.py",
    3: "step3_axis_classify.py",
    4: "step4_generate.py",
}


def run_step(step_num: int):
    script = PIPELINE_DIR / STEPS[step_num]
    print(f"\n{'='*50}")
    print(f"STEP {step_num}: {script.name}")
    print("=" * 50)
    result = subprocess.run([str(PYTHON), str(script)], cwd=str(PIPELINE_DIR))
    if result.returncode != 0:
        print(f"\nERROR: Step {step_num} 실패 (exit {result.returncode})", file=sys.stderr)
        sys.exit(result.returncode)


def main():
    parser = argparse.ArgumentParser(description="Concern Pipeline v4")
    parser.add_argument("--steps", default="0,1,2,3,4", help="실행할 단계 (예: 0,1,2)")
    parser.add_argument("--skip-cache", action="store_true", help="Step 1 캐시 무시 (미구현: 수동으로 cache 파일 삭제)")
    args = parser.parse_args()

    steps = [int(s.strip()) for s in args.steps.split(",")]

    for step in sorted(steps):
        if step not in STEPS:
            print(f"ERROR: 알 수 없는 단계 {step}", file=sys.stderr)
            sys.exit(1)
        run_step(step)

    print("\n" + "=" * 50)
    print("파이프라인 완료!")
    output = Path(__file__).parent.parent / "data/output/concerns_v4.json"
    if output.exists():
        import json
        data = json.loads(output.read_text())
        print(f"  products: {data['totalProducts']}개")
        print(f"  concerns: {data['totalConcerns']}개")
        print(f"  출력 파일: {output}")
    print("=" * 50)


if __name__ == "__main__":
    main()
