# 02 — 데이터 모델

## 입력 데이터

### `reviews_200_normalized.json`

배열(Array) 구조. 총 500건.

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

## 중간 데이터 (DEV 자유 설계)

아래는 권장 구조. DEV가 파이프라인 단계에 맞게 조정 가능.

### Step 2 출력: `intermediate/step2_extracted.json`

```json
[
  {
    "reviewId": 12345,
    "productKey": "달바 퍼스트 스프레이 세럼",
    "aspects": [
      {
        "aspect": "보습감",
        "sentiment": "positive",
        "snippet": "한참동안 촉촉함이 유지됩니다",
        "skinTypeHint": "민감성"
      },
      {
        "aspect": "트러블",
        "sentiment": "negative",
        "snippet": "뿌려보고 바로 자극감이 엄청났어요",
        "skinTypeHint": null
      }
    ]
  }
]
```

### Step 3 출력: `intermediate/step3_clusters.json`

```json
{
  "달바 퍼스트 스프레이 세럼": [
    {
      "clusterId": "c001",
      "aspectLabel": "피부 트러블/자극",
      "reviewIds": [111, 222, 333],
      "posCount": 12,
      "negCount": 8,
      "topSnippets": ["뒤집어졌어요", "트러블 없이 잘 씁니다"]
    }
  ]
}
```

---

## 최종 출력 데이터

### `output/concerns_v4.json`

```json
{
  "generatedAt": "2025-XX-XXTXX:XX:XXZ",
  "products": [
    {
      "productKey": "달바 퍼스트 스프레이 세럼",
      "productName": "달바 퍼스트 스프레이 세럼",
      "category": "에센스/세럼",
      "totalReviews": 100,
      "concerns": [
        {
          "concernId": "c001",
          "question": "민감하거나 예민한 피부도 자극 없이 쓸 수 있나요?",
          "category": "리스크",
          "supportingReviewIds": [101, 205, 318],
          "positiveCount": 12,
          "negativeCount": 8,
          "positiveSnippets": [
            "최고로 민감한 저 같은 피부타입이 쓰기에도 자극적이지 않아요"
          ],
          "negativeSnippets": [
            "뿌리자마자 따가운 게 느껴지고 다음날 양볼에 여드름이"
          ],
          "confidence": 0.87
        }
      ]
    }
  ]
}
```

### 필드 정의

| 필드 | 타입 | 제약 | 설명 |
|---|---|---|---|
| `productKey` | str | not null | 제품 식별자 (입력과 동일) |
| `concerns` | list | 3~7개/제품 | 구매 고민 질문 목록 |
| `question` | str | 15~60자, 의문형 | 실사용자가 구매 전 물어볼 법한 질문 |
| `category` | enum | 적합성\|리스크\|비교\|실사용 | 구매 고민 유형 |
| `supportingReviewIds` | list[int] | ≥5개 | 근거 리뷰 ID 목록 |
| `positiveCount` | int | ≥0 | 해당 측면에 긍정적인 리뷰 수 |
| `negativeCount` | int | ≥0 | 해당 측면에 부정적인 리뷰 수 |
| `positiveSnippets` | list[str] | 1~3개 | 대표 긍정 발화 |
| `negativeSnippets` | list[str] | 0~3개 | 대표 부정 발화 |
| `confidence` | float | 0.0~1.0 | 파이프라인 생성 신뢰도 |

### concern `category` 정의

| 값 | 의미 | 예시 질문 |
|---|---|---|
| `적합성` | 내 피부/상황에 맞는지 | "복합성 피부에 T존 유분이 생기나요?" |
| `리스크` | 부작용, 트러블 가능성 | "민감성 피부에 자극이 올 수 있나요?" |
| `실사용` | 발림감, 지속성, 사용법 | "메이크업 위에 뿌려도 뭉치지 않나요?" |
| `비교` | 다른 제품 대비 차별점 | "이전 버전보다 보습력이 올라갔나요?" |
