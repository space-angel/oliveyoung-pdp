# DEV 완료

실행 결과:
- concerns_v4.json: 30개 concern 생성 (제품당 5~7개)
- 처리 리뷰 수: 499건 (5개 제품)
- LLM 호출: Step1 25회, Step3 5회, Step4 5회

EVAL에게:
Step 1 aspect를 enum 14종으로 고정(피드백 반영)했으며 500건 실행 시 enum 외 경고 0건. Step 3 카테고리 분포는 실사용 약 80%로 편중되어 있고 적합성·비교 카테고리는 소수 — 과소 대표 여부를 중점 확인 필요. MIN_SUPPORT=5 기준으로 클러스터 필터링하므로 소수 의견(5건 미만)은 전부 탈락.

실행 방법:
```
cd /Users/banjax.index/development/product/OLY/concern-pipeline-v4 && source .venv/bin/activate && python3 2_DEV/run_pipeline.py --steps 1,2,3,4
```
