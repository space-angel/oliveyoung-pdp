# 06 — 데이터 모델 (구현 반영판)

> 이 문서는 DEV 구현이 끝난 뒤 실제 산출물 기준으로 갱신한 데이터 모델이다.
> 기획 원안은 [02_data_model.md](02_data_model.md), 변경 배경은 [05_improved_pipeline.md](05_improved_pipeline.md)와 [ARTICLE_enum_fixation.md](../2_DEV/ARTICLE_enum_fixation.md) 참고.
> 원안 대비 바뀐 지점은 각 섹션에 **변경** 표기로 명시한다.

---

## 1. 입력 데이터

### `reviews_200_normalized.json`

배열(Array) 구조. 총 500건. **(원안과 동일 — 변경 없음)**

| 필드 | 타입 | 유효 여부 | 설명 |
|---|---|---|---|
| `reviewId` | int | ✅ | 리뷰 고유 ID |
| `productName` | str | ✅ | 제품명 (5종) |
| `productKey` | str | ✅ | 제품 키 (그룹핑 기준, productName과 동일) |
| `goodsNo` | str | ✅ | 올리브영 상품 번호 |
| `content` | str | ✅ | 리뷰 본문 (평균 213자, 최소 29자, 최대 1367자) |
| `rating` | int | ✅ | 별점 1~5 |
| `likes` | int | ✅ | 도움이 됐어요 수 |
| `isRepurchase` | bool | ✅ | 재구매 의향 (13.6%만 true) |
| `reviewDate` | str | ✅ | 작성일 (YYYY.MM.DD) |
| `option` | str | ✅ | 구매 옵션명 |
| `category` | str | ✅ | 제품 카테고리 |
| `reviewImages` | list[str] | ✅ | 이미지 URL 목록 (285/500건 존재) |
| `skinType` | str | ❌ | **전체 공란** — 사용 불가 |
| `skinTone` | str | ❌ | **전체 공란** — 사용 불가 |
| `skinConcerns` | list | ❌ | **전체 공란** — 사용 불가 |
| `satisfactionTags` | list | ❌ | **전체 공란** — 사용 불가 |
| `usagePeriodTag` | str | △ | 대부분 공란 |
| `userName` | str | ✅ | 사용자명 (익명화 무관) |
| `reviewerRank` | str\|null | △ | 대부분 null |

> **주의**: 구조화 메타데이터(피부 타입, 피부 고민, 만족 태그)는 **전부 비어 있다**. 모든 피부 관련 정보는 `content` 텍스트에서만 추출 가능.

---

## 2. 중간 데이터

> **변경**: 원안은 Step 2(추출)·Step 3(클러스터) 2개 산출물이었으나, 구현은 전처리를 Step 0으로 분리하고 분류를 Step 3로 신설해 **4개 산출물**(step0~3)이 됐다. 파일명·필드도 아래와 같이 확정됐다.

### 2.1. Step 0 출력 — 전처리 & 그룹핑

`intermediate/step0_grouped.json` · **(신규)**

제품별로 리뷰를 묶고, 본문 30자 미만을 거른 뒤 별점→감성 사전값을 부착한다.

```json
{
  "달바 퍼스트 스프레이 세럼": {
    "reviews": [
      {
        "reviewId": 12345,
        "productKey": "달바 퍼스트 스프레이 세럼",
        "content": "리뷰 본문",
        "rating": 5,
        "likes": 312,
        "isRepurchase": false,
        "category": "에센스/세럼",
        "sentimentPrior": "positive"
      }
    ],
    "stats": { "total": 100, "avgRating": 4.2, "repurchaseCount": 14 }
  }
}
```

| 필드 | 타입 | 설명 |
|---|---|---|
| `sentimentPrior` | enum | 별점 기반 사전 감성: 1~2 `negative` / 3 `neutral` / 4~5 `positive` |
| `stats.avgRating` | float | 제품 평균 별점 |
| `stats.repurchaseCount` | int | 재구매 의향 리뷰 수 |

### 2.2. Step 1 출력 — 리뷰별 측면 추출 (LLM)

`intermediate/step1_claims.json` · **(원안 Step 2 추출에 해당)**

> **변경 ①**: `aspect`는 자유 텍스트가 아니라 **닫힌 enum 14종 중 하나**만 들어온다.
> **변경 ②**: 구조가 "리뷰별 aspects 배열"에서 **"제품별 claim 평면 리스트"**로 바뀌었다 (claim마다 `reviewId`를 보유).
> **변경 ③**: 필드명이 `skinTypeHint` → `skin_type_hint`(snake_case).

```json
{
  "달바 퍼스트 스프레이 세럼": [
    {
      "aspect": "보습감",
      "sentiment": "positive",
      "snippet": "한참동안 촉촉함이 유지됩니다",
      "skin_type_hint": "민감성",
      "reviewId": 12345,
      "productKey": "달바 퍼스트 스프레이 세럼"
    }
  ]
}
```

**aspect enum (14종, 고정):**

```
보습감 · 흡수력 · 발림감/텍스처 · 지속성 · 향 · 광택/윤기 · 커버력
발색 · 유분/번들거림 · 트러블/자극 · 탄력/탱탱함 · 분사력 · 밀착력 · 가성비
```

| 필드 | 타입 | 제약 | 설명 |
|---|---|---|---|
| `aspect` | enum | 14종 중 하나 | 측면. 어디에도 안 맞으면 claim 생략 |
| `sentiment` | enum | positive\|negative\|neutral | 감성 |
| `snippet` | str | 30자 이내 | 근거 원문 일부 |
| `skin_type_hint` | str\|null | — | 리뷰어가 피부 타입 언급 시만 (건성/지성/복합성/민감성/트러블성) |

### 2.3. Step 2 출력 — 측면 집계 & 클러스터링 (규칙)

`intermediate/step2_clusters.json` · **(원안 Step 3 클러스터에 해당)**

> **변경 ①**: `topSnippets`(단일 리스트) → `positiveSnippets` + `negativeSnippets`로 **감성별 분리**.
> **변경 ②**: `neutralCount`, `isMixed`, `concernCategory` 필드 신설.
> **변경 ③**: `aspectLabel`은 서술형이 아니라 **enum 값 그대로**("광택/윤기").

```json
{
  "달바 퍼스트 스프레이 세럼": [
    {
      "clusterId": "달바 퍼_000",
      "aspectLabel": "광택/윤기",
      "reviewIds": [34646658, 27588807, 15408487],
      "posCount": 12,
      "negCount": 2,
      "neutralCount": 0,
      "positiveSnippets": ["피부에서 광이나요", "광채가 뿜뿜"],
      "negativeSnippets": ["유분감만 남아서 번들번들"],
      "isMixed": true,
      "concernCategory": null
    }
  ]
}
```

| 필드 | 타입 | 설명 |
|---|---|---|
| `clusterId` | str | `{productKey 앞 4글자}_{idx:03d}` |
| `aspectLabel` | enum | 측면 enum 값 |
| `reviewIds` | list[int] | 클러스터 소속 리뷰. **`MIN_SUPPORT=5` 미만이면 클러스터 자체가 제외** |
| `posCount`/`negCount`/`neutralCount` | int | 감성별 claim 수 |
| `positiveSnippets`/`negativeSnippets` | list[str] | likes 높은 순 상위 3건 |
| `isMixed` | bool | `posCount≥2 && negCount≥2` (의견 충돌) |
| `concernCategory` | null | Step 3에서 채움 (이 단계에선 `null`) |

### 2.4. Step 3 출력 — 카테고리 분류 (LLM)

`intermediate/step3_classified.json` · **(신규 단계)**

Step 2 클러스터에 4축 카테고리를 채운다. 구조는 Step 2와 동일하고 `concernCategory`만 값이 들어간다.

```json
{
  "달바 퍼스트 스프레이 세럼": [
    { "clusterId": "달바 퍼_000", "aspectLabel": "광택/윤기", "concernCategory": "실사용" }
  ]
}
```

| 필드 | 타입 | 제약 | 설명 |
|---|---|---|---|
| `concernCategory` | enum | 적합성\|리스크\|실사용\|비교 | 4축 외 값/누락 시 `실사용`으로 보정 |

---

## 3. 최종 출력 데이터

### `output/concerns_v4.json`

> **변경**: top-level에 `version`, `totalProducts`, `totalConcerns` 추가. `concernId` 포맷이 `c001` → `{productKey 앞 4글자}_{번호:02d}`.

```json
{
  "generatedAt": "2026-06-29T08:21:00Z",
  "version": "v4",
  "totalProducts": 5,
  "totalConcerns": 30,
  "products": [
    {
      "productKey": "달바 퍼스트 스프레이 세럼",
      "productName": "달바 퍼스트 스프레이 세럼",
      "category": "에센스/세럼",
      "totalReviews": 100,
      "concerns": [
        {
          "concernId": "달바 퍼_01",
          "question": "광택이 너무 강하면 번들거려 보일 수도 있나요?",
          "category": "실사용",
          "supportingReviewIds": [34646658, 27588807, 15408487],
          "positiveCount": 12,
          "negativeCount": 2,
          "positiveSnippets": ["피부에서 광이나요"],
          "negativeSnippets": ["유분감만 남아서 번들번들"],
          "confidence": 0.75
        }
      ]
    }
  ]
}
```

---

## 4. 필드 정의 (최종 출력)

| 필드 | 타입 | 제약 | 설명 |
|---|---|---|---|
| `generatedAt` | str | ISO8601 | 생성 시각 (UTC) · **신규** |
| `version` | str | — | 파이프라인 버전 ("v4") · **신규** |
| `totalProducts` | int | — | 제품 수 · **신규** |
| `totalConcerns` | int | — | concern 총 수 · **신규** |
| `productKey` | str | not null | 제품 식별자 (입력과 동일) |
| `concerns` | list | 3~7개/제품 | 구매 고민 질문 목록 |
| `concernId` | str | — | `{productKey 앞 4글자}_{번호:02d}` |
| `question` | str | 15~60자, 의문형 | 실사용자가 구매 전 물어볼 법한 질문 |
| `category` | enum | 적합성\|리스크\|비교\|실사용 | 구매 고민 유형 |
| `supportingReviewIds` | list[int] | ≥5개 | 근거 리뷰 ID 목록 |
| `positiveCount` | int | ≥0 | 해당 측면에 긍정적인 리뷰 수 |
| `negativeCount` | int | ≥0 | 해당 측면에 부정적인 리뷰 수 |
| `positiveSnippets` | list[str] | 1~3개 | 대표 긍정 발화 |
| `negativeSnippets` | list[str] | 0~3개 | 대표 부정 발화 |
| `confidence` | float | 0.0~1.0 | 파이프라인 생성 신뢰도 |

---

## 5. concern 카테고리 정의

| 값 | 의미 | 예시 질문 |
|---|---|---|
| `적합성` | 내 피부/상황에 맞는지 | "복합성 피부에 T존 유분이 생기나요?" |
| `리스크` | 부작용, 트러블 가능성 | "민감성 피부에 자극이 올 수 있나요?" |
| `실사용` | 발림감, 지속성, 사용법 | "메이크업 위에 뭉치지 않나요?" |
| `비교` | 다른 제품 대비 차별점 | "이전 버전보다 보습력이 올라갔나요?" |

---

## 6. ⚠️ 알려진 갭 — `skin_type_hint` 미전파

설계 의도와 실제 구현이 어긋난 지점이다. 데이터 모델 차원에서 명시해 둔다.

- **의도**: `적합성` 카테고리는 *aspect + `skin_type_hint` 조합*으로 Step 3에서 판정한다.
- **실제**: `skin_type_hint`는 **Step 1에서 추출만 되고**, Step 2 집계 스키마(`ClaimGroup` / `step2_clusters.json`)에 해당 필드가 **없어** 클러스터에 보존되지 않는다. 따라서 Step 3 분류 LLM에 **전달되지 않는다**.
- **영향**: 적합성 판정의 핵심 입력이 끊겨, 분류가 `실사용`으로 쏠린다(실측 약 80% 편중). 적합성·비교 축이 과소 대표된다.
- **수정 방향**: Step 2에서 클러스터별 `skinTypeHints`(빈도 집계)를 보존하고, Step 3 입력 payload에 포함시킨다.
