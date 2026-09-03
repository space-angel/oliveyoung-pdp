# v5 입력 자료 인벤토리 & 레거시 감사

작성: 2026-09-03 · 대상: PER-158 (25K 리뷰 기반 Concern Pipeline v5)
검증 방법: 파일 실측(md5·레코드 수·reviewId 교집합) + 문서 인용 역추적

> 이 저장소는 **git 저장소가 아니다**. 삭제는 복구 불가 → 정리는 `rm` 대신 `_trash/` 격리 후 확인.

---

## 1. v5가 실제로 필요한 자료 (위치 확정)

파이프라인 자산과 크롤러 자산이 **서로 다른 프로젝트에 분리**돼 있다.

| 용도 | 경로 | 실측 내용 |
|---|---|---|
| **v5 입력 데이터** | `oliveyoung-crawler/data/reviews_50products.json` | 25,000건 / 34MB |
| 수집 런 로그 | `oliveyoung-crawler/data/reviews_50products_report.json` | 상품별 fetched·rate_limited·elapsed |
| 현행 크롤러 | `oliveyoung-crawler/oliveyoung_crawler.py` | cursor API + Scrapling, 상품당 상한 500건 |
| 크롤러 근거 문서 | `oliveyoung-crawler/docs/SCRAPLING_MIGRATION_POC.md` | size 상한 50·sortType 2종·레이트리밋 실측 |
| **v4 코드** | `OLY/concern-pipeline-v4/2_DEV/pipeline/step0~4*.py` | step0 전처리 → step1 클레임 → step2 집계 → step3 축분류 → step4 생성 |
| v4 성공 기준 | `OLY/concern-pipeline-v4/1_PLANNING/04_success_criteria.md` | Gate / Specificity ≥1.3 / Relevance ≥3.5 |
| v4 데이터 모델 | `OLY/concern-pipeline-v4/1_PLANNING/06_data_model_updated.md` | §6에 `skin_type_hint` 유실 갭 문서화 |
| **v4 평가 코드·리포트** | `OLY/concern-pipeline-v4/3_EVAL/eval_v4.py`, `reports/eval_report_v4.{md,json}` | 아래 §2 수치의 1차 근거 |
| v4 골든셋 | `OLY/concern-pipeline-v4/data/eval/golden_set.json` | 제품당 3문항 × 5제품 = 15문항 |
| v4 입력 | `OLY/concern-pipeline-v4/data/input/reviews_200{,_normalized}.json` | 각 500건 (crawler 쪽 동명 파일과 **내용 다름**) |
| v4 산출물 | `OLY/concern-pipeline-v4/data/output/concerns_v4.json` | 30 concern / 5제품 |
| PDP 화면 명세 | `oliveyoung-crawler/PDP_EXPERIMENT_CONTEXT.md` | 컴포넌트 A/B/C + 스킨코드→라벨 표 (#3 마일스톤 입력) |
| v2/v3 파이프라인 | `oliveyoung-crawler/step2_review_analysis/` + `OLY/concern-pipeline-v2/` | 3단계 (태그추출→병합→질문생성) |

---

## 2. 사실 경계 정정 — v4 결과의 1차 근거는 존재한다

Linear 프로젝트 설명에는 "v4 PASS·Gate 6/6·인용 정확도는 요약 원장에만 근거가 있다"고 적혀 있으나,
`3_EVAL/reports/eval_report_v4.md` + `eval_report_v4.json`이 **1차 산출물로 남아 있다**.
다만 리포트 자체가 밝힌 판정은 "조건부 PASS"다.

| 지표 | 기준 | 결과 | 판정 |
|---|---|---|---|
| Gate (구조 6항목) | 전항목 | 6/6 | PASS |
| Specificity | ≥1.3 | 1.67 | PASS |
| Relevance | ≥3.5 | 3.56 | PASS (여유 0.06) |
| 감성 혼재 | 제품당 ≥1 | 5/5 | PASS |
| **카테고리 분포** | 제품당 ≥3/4종 | **1/5** | **FAIL** |
| **리스크 질문 존재** | ≥4/5 제품 | **3/5** | **FAIL** |
| 인용 정확도 | 참고 | 88.8% (127/143) | 참고 |
| Golden Set 일치율 | 참고 | 86.7% (13/15) | 참고 |
| Polarity 일관성 | 참고 | 93.3% (28/30) | 참고 |

- "종합 합격" 산식은 Gate+Specificity+Relevance 3개만 본다. **2개 FAIL은 산식에 포함되지 않았다.**
- 인용 불일치 16건은 오탈자가 아니라 **파라프레이즈**로 확인됨 (500건 전체 재검색 실패, difflib 0.08~0.44).
  최악 사례: `클리오_03` 인용 "14시간 넘게" — 원문에 **없는 수치를 생성**.
- 두 FAIL과 Golden Set 미스 2건이 동일 원인(`skin_type_hint` 유실 → 적합성 축 부재)으로 교차 확인됨.

→ 포트폴리오 서술 시 "v4 PASS"만 쓰면 방어 불가. "3개 조건 PASS / 2개 FAIL, 원인은 단일 구조적 갭"이 정확한 문장.

---

## 3. 25K 데이터셋 프로파일 (PER-159 입력 계약 초안)

`data/reviews_50products.json` 실측.

### 3-1. 치명적 발견 — "50개 상품"은 goodsNo가 아니다

```
productKey 고유: 50      ← 사람이 인식하는 제품 단위
goodsNo    고유: 153     ← 기획/옵션 SKU 단위
상품당(goodsNo) 리뷰: min=1 / median=79 / max=500
goodsNo 중 500건 미달: 139개 (1건짜리도 존재)
```

cursor API가 요청 상품뿐 아니라 **변형 SKU 리뷰를 합산해 반환**한다
(`SCRAPLING_MIGRATION_POC.md` §5-2c에 이미 기록됨).
→ **집계 단위를 `productKey`로 고정해야 한다.** goodsNo로 그룹핑하면 1~7건 상품 139개가 생겨
v4의 "supportingReviewIds ≥ 5" Gate가 대량 실패한다.

### 3-2. 스키마 (24필드, 결측 0)

```
reviewId(int) content rating usefulPoint recommendCount reviewDate
reviewType{NORMAL,OFFLINE,GIFT,OL_YOUNG} isRepurchase isMonthUseReview
isMonthOverReview hasPhoto goodsNo requestedGoodsNo productName option
category productKey userName reviewerRank isTopReviewer profileImageUrl
skinType skinTone skinTrouble reviewImages
```

- **reviewId 중복 0건**, 여러 goodsNo에 걸친 reviewId 0건 → 식별자는 깨끗하다.
- `requestedGoodsNo` ≠ `goodsNo` 인 행이 곧 §3-1의 SKU 합산 흔적. 계보 추적에 쓸 수 있다.

### 3-3. 분포 — 평가 설계에 직접 영향

| 항목 | 값 | 시사점 |
|---|---|---|
| 평점 | 5점 21,362 / 4점 2,403 / 3점 828 / 2점 184 / 1점 223 | **85%가 5점.** 부정 신호 총 407건(1.6%) → "위험 신호 재현율"은 희소 클래스 문제. 상품별 층화 표본 필수 |
| 본문 길이 | min 22 / med 90 / p95 478 / max 1468자 | 빈 본문·10자 미만 **0건**. 길이 필터 불필요 |
| 동일 본문 중복 | 587종 | 템플릿·복붙 리뷰. 근거 카운트 부풀림 방지용 dedup 규칙 필요 |
| 수집 기간 | 2018.12.26 ~ 2026.08.10 | 7년 8개월. **리센시 컷 기준 결정 필요** (v4는 리센시 미고려) |
| 카테고리 | 에센스/세럼 7,500 · 크림 5,000 · 베이스 5,000 · 아이 4,000 · 립 3,500 | 5카테고리, 상품당 500 정렬로 인위적 균형 |

### 3-4. 개인정보 최소화 대상 (PER-158 완료 기준 항목)

| 필드 | 성격 | 권고 |
|---|---|---|
| `userName` | 사용자 닉네임 | **유지 확정** (PER-170) — 게이트2 작성자 1표의 유일한 키다. 드롭하면 카운트가 19.7% 오염된다. `docs/DECISION_PER170_AUTHOR_IDENTIFIER.md` |
| `profileImageUrl` | 프로필 이미지 URL | **감사 필드로 유지** (PER-170) — 조합키로는 식별력 증가분 0이지만 동명이인 상한을 재측정할 유일한 신호다 |
| `reviewImages` | 리뷰 첨부 이미지 URL | v5 텍스트 파이프라인 미사용 → 드롭 (PDP 렌더링은 별도 조회) |
| `reviewerRank`, `isTopReviewer` | 등급 | 가중치로 쓸 계획 없으면 드롭 |

### 3-5. ~~미해결 블로커~~ 해소 — 스킨 코드 사전 〔2026-09-03 DOM 실측〕

관측 코드(25,000건): `skinType` = **A01~A07**(7종, 기재 57.1%), `skinTone` = **B01~B06**(6종, 54.8%),
`skinTrouble` = **C01~C13**(13종, 53.9%, 최대 2개 선택 · 88조합).
*본 문서 구버전의 `A01~A04` / `B01~B04` 표기는 축소 오기였다. `SCRAPLING_MIGRATION_POC.md:512`가 처음부터 맞았다.*

**API 조회 실패는 사실이다** — `data/input/skin_codes_probe_result.json`: 후보 엔드포인트 8개 전부 실패
(400 / `data:null` / fetch 실패). 다만 그건 **API 경로가 없다는 뜻이지 라벨이 없다는 뜻이 아니었다.**
실제 브라우저 세션으로 PDP 리뷰 위젯(Lit / Shadow DOM)에 접근하니 라벨이 그대로 나왔다:
필터 시트의 `{id, text}` 칩 배열과 리뷰 카드의 `_getSkinTypeText / _getSkinToneText / _getSkinTroubleText`,
두 경로가 서로 일치. HTTP 직접 호출은 WAF 403이라 실브라우저가 필요하다.

| 산출물 | 경로 |
|---|---|
| **코드북 (정본)** | `data/input/skin_codebook.json` — 26종, `_meta`에 출처·근거·제약 |
| 커버리지 검증 | `python crawler/verify_skin_codebook.py` → 미지 코드 0 |
| 화면 캡처 | `docs/evidence/skin_filter_modal_2026-09-03.jpg` |
| 라벨 표 (사본) | `PDP_EXPERIMENT_CONTEXT.md` §6 |

→ 크롤러는 상수 하드코딩 대신 `load_skin_codebook()`으로 이 파일을 읽는다.
v4 적합성 축 복구(PER-162)의 사전 의존성은 풀렸다.

---

## 4. 레거시 감사 — oliveyoung-crawler

### A. 유지 (v5·문서 근거)

```
oliveyoung_crawler.py            현행 크롤러
products_50.json                 50 productKey 목록 (문서 8곳 인용)
products_legacy5.json            5제품 목록 — 5개 전부 products_50.json에 포함, 25K에도 존재
data/reviews_50products.json     v5 입력 (25,000건)
data/reviews_50products_report.json
docs/SCRAPLING_MIGRATION_POC.md  크롤러 유일 근거 문서
PDP_EXPERIMENT_CONTEXT.md        #3 화면 설계 입력 + 스킨코드 사전
data/probe_skin_codes.json       스킨코드 조회 실패의 유일한 근거 (§3-5)
.env / .gitignore / .venv(387MB)
```

### B. 삭제 안전 — 재생성 가능 또는 완전 중복 (검증 완료)

| 대상 | 크기 | 근거 |
|---|---|---|
| `Scrapling-main/` | 5.2M | upstream 공개 repo 사본, **version 0.4.12 = venv 설치본과 동일**. 모든 코드는 `from scrapling.fetchers import`로 설치본을 씀. 참조 0건 |
| `data/_archive/reviews_50products_ratelimited.json` | **14M** | 10,050건 중 **10,036건(99.9%)이 25K에 포함**. 레이트리밋 실패 런 잔여물 (문서 인용 0건) |
| `data/validate_50.json` | 3.3M | 2,500건 **100% 25K에 포함** |
| `data/reviews_500.json` | 2.9M | 2,500건 중 2,492건(99.7%) 25K에 포함 |
| `data/reviews_5products.json` | 700K | `data/reviews_200.json`과 **md5 동일** (완전 중복) |
| `data/_archive/reviews_partial_10~40.json` | 3.1M | `reviews_partial_50.json`의 부분 스냅샷 |
| `data/_archive/concern_tags_interim.json` | 660K | `concern_tags.json`의 중간본 |
| `__pycache__/`, `step2_review_analysis/__pycache__/` | 32K | 재생성 |
| `.DS_Store` ×2 | 20K | — |

**소계 약 30MB** (.venv 387MB는 유지 대상)

주의: `data/reviews_final.json`은 `data/_archive/reviews_partial_50.json`과 **하드링크**(동일 inode).
한쪽만 지워도 용량은 줄지 않고, 둘 다 지우면 964건이 사라진다.

### C. 보류 — 문서 인용이 살아 있음

| 대상 | 인용처 |
|---|---|
| `data/reviews_200.json` (500건) | `PDP_EXPERIMENT_CONTEXT.md` §3-2 (5회 인용). 25K와 겹침 13%뿐 → 별개 데이터 |
| `data/reviews_final.json` (964건) | `_PIPELINE.md` Step 3 입력. 25K와 겹침 26% → 별개 데이터 |
| `data/concerns_v2.json` | `PDP_EXPERIMENT_CONTEXT.md` §3-1 — PDP 렌더링 스키마의 근거 |
| `data/merged_tags{,_5products}.json`, `data/results{,_5products}.json`, `data/sample_reviews_context_tag.json` | `_PIPELINE.md` 전 단계 예시 |
| `data/validate_50_report.json`, `data/poc_scrapling_debug.json` | `SCRAPLING_MIGRATION_POC.md` 실측 인용 |

→ v2/v3 파이프라인 자체는 은퇴했지만, 위 파일들은 **문서의 근거**로 살아 있다.
문서를 폐기하지 않는 한 데이터도 남겨야 한다.

### D. 아카이브 이동 권고 — 일회성 프로브

`probe_bypass_100.py` `probe_depth.py` `probe_legacy_params.py` `probe_legacy_reviews.py`
`probe_size_ceiling.py` `probe_skin_codes.py` `poc_scrapling.py` (총 ~70KB)
+ 산출물 `data/probe_*.json` `data/legacy_*.html` `data/smoke{,_report}.json` `data/poc_scrapling_*.json`

전부 `SCRAPLING_MIGRATION_POC.md`의 실측 근거를 만든 일회성 스크립트다.
`poc_scrapling.py`는 `oliveyoung_crawler.py`에 흡수됐다(파일 상단 docstring 명시).
루트를 비우려면 `_archive/probes/`로 이동. **삭제는 비권고** — 문서 수치의 재현 경로다.

### E. 구조 제안

현재 루트에 크롤러·프로브·v2 파이프라인·PDP 명세가 섞여 있다. v5 착수 전 분리 권고:

```
oliveyoung-crawler/
├── oliveyoung_crawler.py
├── products_50.json  products_legacy5.json
├── data/            ← 25K + 현행 산출물만
├── docs/            ← SCRAPLING_MIGRATION_POC.md, PDP_EXPERIMENT_CONTEXT.md, 이 문서
└── _archive/
    ├── crawlers/    ← 기존 2개 (README.md 유지)
    ├── probes/      ← §D
    └── pipeline_v2/ ← step2_review_analysis/ + §C 데이터
```

v5 코드는 `OLY/concern-pipeline-v5/`로 (v4 하네스 구조 승계), 이 저장소는 **크롤러+데이터 소스**로 역할 고정.

---

## 5. v5 착수 전 결정해야 할 것

1. ~~**집계 단위**~~ — **해소** (2026-09-03, PER-171). 카탈로그 레이어(`data/input/product_catalog.json`)가 `goodsNo`(167) → `productId`(50)를 소유하고, 미등록 ID는 에러다. `docs/PRODUCT_CATALOG.md`
2. **리센시 컷** — 2018년 리뷰 포함 여부 (§3-3)
3. **부정 신호 표본** — 1~2점 407건(1.6%)으로 "위험 신호 재현율"을 어떻게 측정할지 (§3-3)
4. ~~**PII 드롭 목록**~~ — **해소** (2026-09-03, PER-170). 닉네임·프로필 URL은 프로덕션 PDP 공개 값이고 법적 제약이 없음을 확인해 **드롭 논점 자체가 사라졌다.** 식별자는 중복 판정 정확도로만 결정 — 작성자 키 = `NFC(userName)`, `profileImageUrl`은 감사 필드로 유지. 결정과 근거: `docs/DECISION_PER170_AUTHOR_IDENTIFIER.md`, 실측: `eval/reports/author_identity_per170.json`
5. **스킨 코드 사전 출처** — `PDP_EXPERIMENT_CONTEXT.md` §6이 실측인지 추정인지 (§3-5). PER-162 선행 조건
6. **v4 인용 검증** — 88.8%의 원인은 파라프레이즈. Step 4를 "추출"로 강제할지, 별도 검증 단계를 붙일지 (v4 권고사항 2)
