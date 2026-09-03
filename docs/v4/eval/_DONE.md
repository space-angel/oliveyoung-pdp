# EVAL 완료

평가 결과 요약:
- 전체 판정: **PASS** (v4 종합 합격 산식 = Gate 통과 + Specificity≥1.3 + Relevance≥3.5, 세 조건 모두 충족)
- 지표별:
  - Gate(구조): ✅ PASS (6/6)
  - Specificity: ✅ PASS (1.67/2.0, 목표 1.3)
  - Relevance: ✅ PASS (3.56/5.0, 목표 3.5 — 근소)
  - 감성 혼재: ✅ PASS (5/5 제품)
  - 카테고리 분포(≥3/4종): ❌ FAIL (1/5 제품만 충족)
  - 리스크 질문 존재(≥4/5): ❌ FAIL (3/5 제품)
  - 인용 정확도: ⚠️ 88.8% (127/143, 참고 지표)
  - Golden Set 일치율: ⚠️ 86.7% (13/15, 참고 지표)
  - Polarity 일관성: ⚠️ 93.3% (28/30, 참고 지표)
- 주요 발견: DEV가 예고한 `skin_type_hint` 유실로 인한 적합성 축 과소대표가 실측 확인됨(클리오는 7개 질문 전부 실사용, 적합성 0개). Golden Set 미스(G11)도 정확히 같은 지점을 가리킴. 인용 정확도 검증에서 클리오 _03("14시간 넘게 있는데...")처럼 원문에 없는 구체적 수치를 지어낸 파라프레이즈 사례를 발견 — 종합 합격 산식에는 안 걸리지만 실서비스 반영 전 반드시 검토 필요.

상세 리포트: [reports/eval_report_v4.md](reports/eval_report_v4.md)
평가 스크립트: [eval_v4.py](eval_v4.py) (`python3 3_EVAL/eval_v4.py --all --output reports/eval_report_v4.json`)
Golden Set: [../data/eval/golden_set.json](../data/eval/golden_set.json)
