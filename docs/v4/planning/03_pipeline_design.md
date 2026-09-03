# 03 — 파이프라인 설계

## 개요

총 4단계. LLM은 Step 2와 Step 4에서만 사용한다.
중간 산출물을 파일로 저장해 디버깅과 재실행을 용이하게 한다.

```
[Step 1] 전처리 & 그룹핑
    ↓ intermediate/step1_grouped.json
[Step 2] 리뷰별 측면 추출 (LLM)
    ↓ intermediate/step2_extracted.json
[Step 3] 측면 클러스터링 & 집계 (Rule-based)
    ↓ intermediate/step3_clusters.json
[Step 4] 구매 고민 질문 생성 (LLM)
    ↓ output/concerns_v4.json
```

---

## Step 1 — 전처리 & 그룹핑

**목적**: 원본 리뷰를 파이프라인에서 처리하기 좋은 형태로 정비

**처리 내용**:
- `productKey` 기준으로 리뷰를 5개 그룹으로 분리
- 너무 짧은 리뷰 필터 (기준: 본문 30자 미만 제외)
- `likes` 가중치 필드 추가 (좋아요 수 → 리뷰 중요도 신호)
- 별점을 sentiment_prior로 변환 (1~2: negative, 3: neutral, 4~5: positive)

**LLM 사용**: 없음

**출력**: `intermediate/step1_grouped.json`
```json
{
  "달바 퍼스트 스프레이 세럼": {
    "reviews": [...],
    "stats": {"total": 100, "filtered": 97, "avgRating": 4.2}
  }
}
```

---

## Step 2 — 리뷰별 측면 추출 (LLM)

**목적**: 각 리뷰에서 언급된 제품 측면과 감성을 구조화

**처리 내용**:
- 리뷰 본문을 LLM에 전달
- 각 리뷰에서 언급된 aspect(측면)와 sentiment(긍/부정) 추출
- 텍스트 내 피부 타입 언급 추출 (건성/지성/복합성/민감성/트러블성)
- 카테고리별 측면 사전 힌트 제공 (스킨케어: 보습/흡수/트러블 / 색조: 발색/지속성/커버)

**LLM 사용**: 예 — 리뷰 배치 단위 (비용 절감을 위해 10~20건씩 배치 처리 권장)

**프롬프트 핵심**:
- 역할: "한국어 화장품 리뷰 분석가"
- 추출 대상: aspect명(한국어), sentiment(positive/negative/neutral), snippet(근거 문장), skinTypeHint(있으면)
- 없는 정보는 null 반환 (hallucination 금지)
- 응답 형식: JSON only

**캐시**: `data/cache/step2_cache.json` — reviewId 기준으로 이미 처리된 리뷰는 재호출 생략

**출력**: `intermediate/step2_extracted.json`

---

## Step 3 — 측면 클러스터링 & 집계 (Rule-based)

**목적**: 같은 구매 고민을 다루는 리뷰들을 묶어 빈도/감성 집계

**처리 내용**:
- Step 2 출력의 aspect명을 정규화 (동의어 통합: "보습감"="보습력"="촉촉함")
- 동의어 사전은 카테고리별로 정의 (DEV가 hardcode 가능)
- 제품별로 aspect 빈도 집계
- 클러스터 생성 기준:
  - 해당 aspect를 언급한 리뷰 ≥5건
  - positive + negative 모두 존재 (의견 충돌) 또는 negative ≥2건 (리스크 신호)
- 클러스터에 `posCount`, `negCount`, `topSnippets`(likes 높은 순 상위 3건) 부여

**LLM 사용**: 없음 (동의어 사전 기반 rule-based)

**예외 처리**:
- 5건 미만 aspect: concerns에서 제외 (근거 부족)
- 동의어 사전에 없는 신규 aspect: 로그 출력 후 "기타"로 분류

**출력**: `intermediate/step3_clusters.json`

---

## Step 4 — 구매 고민 질문 생성 (LLM)

**목적**: 클러스터를 소비자 관점의 자연스러운 구매 고민 질문으로 변환

**처리 내용**:
- 제품별로 클러스터 목록 전달
- LLM이 각 클러스터를 하나의 구매 고민 질문으로 표현
- concern `category` 분류 (적합성/리스크/실사용/비교)
- 최종 출력: 제품당 3~7개 질문 (클러스터가 많으면 상위 N개 선택: likes 가중 합산 점수 기준)

**LLM 사용**: 예 — 제품별 1회 호출 (5회 총 호출)

**프롬프트 핵심**:
- 역할: "올리브영 PDP UX 카피라이터"
- 질문 조건: 의문형 종결, 15~60자, 실제 사용자가 검색창에 칠 법한 표현
- 금지: 제품명 반복, 광고성 표현, "~인가요?"보다 "~나요?" 선호(구어체)
- 응답 형식: JSON only

**출력**: `output/concerns_v4.json`

---

## 전체 실행 흐름

```
python run_pipeline.py
  --input data/input/reviews_200_normalized.json
  --output data/output/concerns_v4.json
  --steps 1,2,3,4          # 특정 단계만 재실행 가능
  --skip-cache             # Step 2 캐시 무시
```

각 단계는 독립 스크립트 (`step1.py`, `step2.py`, ...)로 분리해 단계별 단독 실행 가능.

---

## 비용 추정 (참고)

| 단계 | LLM 호출 수 | 예상 토큰 |
|---|---|---|
| Step 2 | ~25회 (20건 배치 × 5제품) | ~150K input / ~50K output |
| Step 4 | 5회 (제품당 1회) | ~5K input / ~2K output |
| **합계** | ~30회 | ~200K 토큰 |

> Haiku 모델 기준 $0.10 내외 예상. 캐시 활용 시 반복 실행 비용 최소화.

---

## 설계 의도 (DEV에게)

1. **Step 2를 리뷰 단위로 분리한 이유**: 제품별 일괄 처리보다 오류 격리가 쉽고, 캐시 효율이 높음
2. **Step 3을 Rule-based로 한 이유**: LLM 클러스터링은 실행마다 결과가 달라 평가가 어려움. 동의어 사전은 DEV가 직접 관리
3. **질문 수를 3~7개로 제한한 이유**: UX 상 7개 초과 시 사용자가 읽지 않음; 3개 미만이면 제품 정보가 부족하다는 신호
