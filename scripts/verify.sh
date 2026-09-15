#!/usr/bin/env bash
# 병합 게이트 (docs/GIT_WORKFLOW.md §3).
#
# 2단이다. 가르는 기준은 중요도가 아니라 **gitignore 인 data/intermediate 가 필요한가** 다.
#
#   verify.sh          코드만 있으면 도는 것. 클론 직후 바로 돌아간다
#   verify.sh --full   스냅샷 재현까지. **병합 게이트는 이쪽이다** (병합 전·후 양쪽에서)
#
# 나누는 이유: data/intermediate 는 커밋하지 않는다(CLAUDE.md). 전부 한 덩어리로 두면
# 새로 받은 사람은 두 번째 검사에서 멈추고 그게 "코드가 깨졌다"처럼 보인다. 반대로
# 데이터가 필요한 검사를 빼 버리면 리포트가 조용히 낡는다 — 그래서 빼지 않고 --full 로 민다.
set -euo pipefail
cd "$(dirname "$0")/.."

PY=".venv/bin/python3"
[ -x "$PY" ] || PY="python3"

FULL=0
for arg in "$@"; do
  case "$arg" in
    --full) FULL=1 ;;
    -h|--help)
      sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "모르는 인자: $arg (쓸 수 있는 것: --full)" >&2; exit 2 ;;
  esac
done

echo "== 계약 테스트 =="
"$PY" -m unittest discover -s pipeline -p 'test_*.py'

echo
echo "== 생성물 재현 확인 — 데이터 불필요 =="
"$PY" pipeline/build_product_catalog.py --check
# 주장 골든셋 라벨 계약 (PER-178). 라벨이 규격을 위반하면 병합하지 않는다
"$PY" eval/label_concern_golden.py validate
# 게이트1 동일성 — 옵션 어휘·정규화 규칙이 바뀌면 리포트가 조용히 낡는다 (PER-182)
"$PY" eval/measure_gate1_identity.py --check
# 모델 후보의 계약 표시가 현재 규칙과 같은지 — 규칙이 바뀌면 표시가 조용히 낡는다 (PER-178)
"$PY" eval/concern_candidates.py recheck --check
# 태거 후보 채점 — 계약 규칙·정답셋이 바뀌면 모델 비교표가 조용히 낡는다 (PER-175)
"$PY" eval/measure_quote_probe.py --check
"$PY" eval/measure_bedrock_bakeoff.py --check
# 리포트가 늘거나 meta 축이 바뀌면 "무엇이 아직 기록되지 않는가" 가 조용히 낡는다 (PER-193)
"$PY" eval/measure_meta_coverage.py --check

echo
echo "== 입력 계약 강제 (PER-176) =="
# 위반 클래스가 하나라도 조용히 통과하면 종료 코드 1 이다
"$PY" eval/measure_input_contract.py

if [ "$FULL" -eq 0 ]; then
  echo
  echo "verify.sh OK (기본)"
  echo "  ! 스냅샷 재현 확인은 건너뛰었다. 병합 전에는 --full 로 돌려라 (docs/GIT_WORKFLOW.md §3)"
  exit 0
fi

echo
echo "== 스냅샷 재현 확인 (--full) — data/intermediate 필요 =="

# 없는 파일을 검사 중간에 만나면 "코드가 깨졌다"처럼 보인다. 먼저 한 번에 확인하고
# 무엇을 돌려야 하는지 알려준다. 되살리는 비용이 파일마다 다르다는 것이 요점이다.
missing=0
need() {  # need <경로> <되살리는 법> <비용>
  if [ ! -s "$1" ]; then
    echo "  없음: $1"
    echo "        → $2   ($3)"
    missing=1
  fi
}
need "data/intermediate/v5_reviews.jsonl" \
     "$PY pipeline/ingest.py" "LLM 없음 · 약 1초"
need "data/intermediate/v5_tags.jsonl" \
     "$PY pipeline/run_v5.py --steps tag" "LLM 태깅 · 약 \$4"
# 게이트3 의 order_chosen 한계는 태깅 **원문 응답**에서만 다시 셀 수 있다. 없으면 리포트가
# 그 한계를 빼고 만들어져 --check 가 '한계가 빠진 리포트'로 실패한다 (PER-185 §5·§6).
need "data/intermediate/tag_runs/full_glm47_raw.jsonl" \
     "태깅 원문 응답 — 태그를 가진 워크트리에서 복사하거나 tag 단계를 다시 돌린다" \
     "게이트3 의 order_chosen 한계 측정용"
if [ "$missing" -ne 0 ]; then
  echo
  echo "FAIL: --full 에 필요한 스냅샷이 없다. 위 명령을 먼저 돌려라."
  exit 1
fi

"$PY" pipeline/ingest.py --check
# 평가 고정물 — 표본이 달라지면 손으로 만든 정답셋이 통째로 무의미해진다 (PER-175)
"$PY" pipeline/sample_tag_pilot.py --check
# 주장 골든셋 번들 (PER-178)
"$PY" pipeline/sample_concern_golden.py --check
"$PY" eval/measure_v4_golden_migration.py --check
# 중복 판정 규칙·신뢰도 가중치가 바뀌면 "리뷰 N건"의 근거가 조용히 낡는다 (PER-183)
"$PY" eval/measure_gate2_duplicate.py --check
# 방향 정의·태거 잡음 모수가 바뀌면 혼재 판정의 근거가 조용히 낡는다 (PER-185)
"$PY" eval/measure_gate3_polarity.py --check
# 충분성 임계값이 바뀌면 통과 주장 수의 민감도가 조용히 낡는다 (PER-186 → PER-199 입력)
"$PY" eval/measure_gate4_sufficiency.py --check
# 5단 배치 규칙·정렬 키가 바뀌면 배치 스냅샷이 조용히 낡는다 (PER-187)
"$PY" eval/measure_context_layout.py --check
# 탈락 사유 어휘·게이트 판정이 바뀌면 골든셋 역추적(재현율의 근거)이 조용히 낡는다 (PER-188)
"$PY" eval/measure_rejected_ledger.py --check
# 정답셋·키워드 사전이 바뀌면 임베딩 후보 비교의 기준선이 조용히 낡는다 (PER-184)
# 모델 수치는 가중치 수 GB 가 필요해 대조하지 않는다 — 모델 없는 부분만 본다
"$PY" eval/measure_embedding_probe.py --check
# claim 스키마나 골든셋이 바뀌면 "무엇을 담을 수 있는가" 가 조용히 낡는다 (PER-189)
"$PY" eval/measure_claim_schema.py --check
# 정규화 규칙·게이트·골든셋이 바뀌면 인용 원문성 수치가 조용히 낡는다 (PER-190)
"$PY" eval/measure_quote_fidelity.py --check
# 표기 규칙·충분성 임계값·태그가 바뀌면 조건 갈림의 근거가 조용히 낡는다 (PER-192)
# 순열 200회 × 4단계라 약 60초 걸린다 — 이 대열에서 가장 느리다
"$PY" eval/measure_condition_split.py --check

echo
echo "verify.sh OK (--full)"
