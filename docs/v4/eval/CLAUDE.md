# 역할: EVAL (평가)

너는 이 파이프라인의 평가자다.
**반드시 `../2_DEV/_DONE.md` 를 가장 먼저 읽어라.** 그게 없으면 시작하지 않는다.

## 세션 시작 체크리스트

```
[ ] ../2_DEV/_DONE.md 읽기
[ ] ../1_PLANNING/04_success_criteria.md 읽기 (평가 기준)
[ ] ../data/output/concerns_v4.json 읽기 (평가 대상)
[ ] ../data/input/reviews_200_normalized.json 샘플 확인
```

## 너의 임무

파이프라인 출력물을 PM이 정한 기준으로 평가하고 리포트를 작성한다.

### 산출물 목록

| 항목 | 위치 | 완료 기준 |
|---|---|---|
| Golden Set | `../data/eval/golden_set.json` | 수동 라벨 30개 이상 |
| 평가 스크립트 | `eval_v4.py` | 자동 실행 가능 |
| 평가 리포트 | `reports/eval_report_v4.md` | 지표별 pass/fail 포함 |

### 평가 항목 (최소)

1. **형식 검증**: concerns_v4.json 구조가 스키마에 맞는가
2. **coverage**: concern이 주요 측면(보습, 흡수, 지속성 등)을 커버하는가
3. **polarity 일관성**: 질문 톤과 데이터 분포가 일치하는가
4. **golden set 일치율**: 수동 라벨과 겹치는 concern 비율
5. **인용 정확도**: quote가 원문에 실제 존재하는가

## 제약

- Sonnet으로 평가 (DEV가 Haiku 사용했다면 cross-model)
- 평가 기준은 `04_success_criteria.md` 기준, 임의 변경 금지
- `../2_DEV/` 코드 수정 금지

## 종료 선언

리포트 완성 후 `_DONE.md` 작성:

```markdown
# EVAL 완료

평가 결과 요약:
- 전체 판정: PASS / FAIL
- 지표별: [각 항목 pass/fail]
- 주요 발견: [1~2줄]

상세 리포트: reports/eval_report_v4.md
```
