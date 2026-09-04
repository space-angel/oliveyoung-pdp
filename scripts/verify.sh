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

echo
echo "verify.sh OK"
