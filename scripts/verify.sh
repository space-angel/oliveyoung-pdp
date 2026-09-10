#!/usr/bin/env bash
# 병합 게이트 (docs/GIT_WORKFLOW.md §3).
# 계약 테스트 + 생성물 재현 확인. 병합 전과 병합 후 양쪽에서 돌린다.
set -euo pipefail
cd "$(dirname "$0")/.."

PY=".venv/bin/python3"
[ -x "$PY" ] || PY="python3"

echo "== 계약 테스트 =="
"$PY" -m unittest discover -s pipeline -p 'test_*.py'

echo
echo "== 생성물 재현 확인 =="
"$PY" pipeline/build_product_catalog.py --check
"$PY" pipeline/ingest.py --check
# 평가 고정물 — 표본이 달라지면 손으로 만든 정답셋이 통째로 무의미해진다 (PER-175)
"$PY" pipeline/sample_tag_pilot.py --check
# 주장 골든셋 번들 + 라벨 계약 (PER-178). 라벨이 규격을 위반하면 병합하지 않는다
"$PY" pipeline/sample_concern_golden.py --check
"$PY" eval/label_concern_golden.py validate
"$PY" eval/measure_v4_golden_migration.py --check
# 모델 후보의 계약 표시가 현재 규칙과 같은지 — 규칙이 바뀌면 표시가 조용히 낡는다 (PER-178)
"$PY" eval/concern_candidates.py recheck --check

echo
echo "== 입력 계약 강제 (PER-176) =="
# 위반 클래스가 하나라도 조용히 통과하면 종료 코드 1 이다
"$PY" eval/measure_input_contract.py

echo
echo "verify.sh OK"
