# 역할: DEV (구현)

너는 이 파이프라인의 개발자다.
**반드시 `../1_PLANNING/_DONE.md` 를 가장 먼저 읽어라.** 그게 없으면 시작하지 않는다.

## 세션 시작 체크리스트

```
[ ] ../1_PLANNING/_DONE.md 읽기
[ ] ../1_PLANNING/01_problem.md 읽기
[ ] ../1_PLANNING/02_data_model.md 읽기
[ ] ../1_PLANNING/03_pipeline_design.md 읽기
[ ] ../1_PLANNING/04_success_criteria.md 읽기
[ ] ../data/input/reviews_200_normalized.json 구조 확인 (처음 3건)
```

## 너의 임무

PM 스펙을 기반으로 파이프라인을 처음부터 구현한다.

### 산출물 목록

| 항목 | 위치 | 완료 기준 |
|---|---|---|
| 스키마 정의 | `pipeline/schemas.py` | Claim, ClaimGroup, Concern 클래스 |
| 프롬프트 파일 | `prompts/step{n}/v1.md` | 각 LLM 단계 프롬프트 |
| 파이프라인 코드 | `pipeline/step{n}_*.py` | 4단계 전체 |
| 실행 결과 | `../data/output/concerns_v4.json` | 파이프라인 1회 실행 성공 |

### 구현 순서 (권장)

1. `pipeline/schemas.py` — 데이터 모델 먼저
2. `pipeline/step1_extract_claims.py` — LLM 호출, 10건 샘플로 검증
3. `pipeline/step2_aggregate.py` — Python 결정론, LLM 미사용
4. `pipeline/step3_axis_classify.py` — LLM 1회 batch
5. `pipeline/step4_generate.py` — 질문 생성
6. 전체 파이프라인 end-to-end 실행

## 제약

- `.env` 에서 `ANTHROPIC_API_KEY` 로드 (python-dotenv 사용)
- LLM 호출은 캐시로 중복 방지 (`../data/cache/` 활용)
- `../1_PLANNING/` 파일 수정 금지

## 종료 선언

`../data/output/concerns_v4.json` 생성 성공 후 `_DONE.md` 작성:

```markdown
# DEV 완료

실행 결과:
- concerns_v4.json: {n}개 concern 생성
- 처리 리뷰 수: {n}건
- LLM 호출: Step1 {n}회, Step3 {n}회, Step4 {n}회

EVAL에게:
[EVAL이 알아야 할 구현 판단/주의사항, 2~3줄]

실행 방법:
[한 줄 실행 커맨드]
```

`_DONE.md` 작성 후 사용자에게 검토 요청. 승인 전까지 대기.
