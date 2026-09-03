# PM 완료

완성 파일:
- [x] 01_problem.md
- [x] 02_data_model.md
- [x] 03_pipeline_design.md
- [x] 04_success_criteria.md

DEV에게:
1. `skinType`/`skinConcerns`/`satisfactionTags` 필드는 500건 전체 공란 — 피부 타입은 반드시 리뷰 텍스트(`content`)에서 추출해야 한다.
2. Step 2(LLM 추출)는 reviewId 기준 캐시 필수 — 전체 500건 재실행 비용 방지.
3. 제품당 질문은 3~7개 범위 엄수 — 초과 시 likes 가중 합산 점수로 상위 N개만 유지.
