# Scrapling 마이그레이션 PoC 결과

> ## ✅ 포팅 완료 (2026-08-10)
> 신규 크롤러 **`oliveyoung_crawler.py`** 로 대체 완료. 실전 검증까지 끝남.
>
> | 항목 | 결과 |
> |---|---|
> | **본 수집 (50개 제품)** | **50/50 제품 × 500건 = 25,000건** — 누락·부분수집 0 |
> | reviewId 중복 | 0건 (전역·제품내 모두) |
> | 필수 필드 결손 | 0건 (`reviewId`/`content`/`productName`/`goodsNo`/`requestedGoodsNo`) |
> | 도움순 정렬 | **50/50 제품 전부 내림차순 유지** ✅ |
> | usefulPoint 범위 | 75,480.0 → 1.65 (소수값 29.4% — float 보존으로 손실 0) |
> | 파이프라인 호환 | ✅ `extract_context_tag.py --input` 에 그대로 투입 가능 |
>
> 결과물: `data/reviews_50products.json` (25,000건) + `_report.json`
>
> **중요 정정**: 기존 제품은 **판매 종료가 아니었다.**
> 구 크롤러 두 개의 대상 제품을 전수 검증한 결과 **50/50 + 5/5 전부 정상 수집**됨.
> 구 크롤러가 실패한 원인은 제품이 아니라 **엔드포인트가 죽어서**였다 (§5-1).
> → 제품 목록 교체 불필요, 그대로 JSON 이관함.
>
> ```bash
> python oliveyoung_crawler.py                    # products_50.json, 제품당 500건
> python oliveyoung_crawler.py --products products_legacy5.json --target 500
> ```

- **작성일**: 2026-08-08 (2026-08-10 정량 검증 + 100건 상한 규명 반영)
- **목적**: 기존 `undetected-chromedriver` + CDP performance 로그 크롤러를 Scrapling으로 교체 가능한지 검증
- **검증 스크립트**: `poc_scrapling.py` (프로젝트 루트)
- **대상 제품**: `A000000158513` — 메이크프렘 세이프 미 릴리프 모이스처 클렌징밀크 (리뷰 14,332건)
- **대상 URL**: PC 상품상세 `https://www.oliveyoung.co.kr/store/goods/getGoodsDetail.do?goodsNo=A000000158513&...`

> 기존 크롤러가 쓰던 제품들(`A000000232724` 등)은 판매 종료로 조회 불가 → 현재 판매 중인 제품으로 교체해서 검증함.

---

## 1. 결론 요약

**엔진 교체(uc → Scrapling)는 검증 완료. 수집량 문제도 해결됨. 남은 작업은 파서 수정.**

| | 상태 |
|---|---|
| Scrapling으로 봇 차단 통과 | ✅ 검증됨 |
| XHR 가로채기 (CDP 대체) | ✅ 검증됨 |
| 리뷰 탭 · 정렬 조작 | ✅ 검증됨 (shadow DOM JS 불필요) |
| 리뷰 수집 + 도움순 정렬 유지 | ✅ 검증됨 |
| **제품당 200건 확보** | ✅ **초과 달성 — `size=50`으로 도움순 500건** (§4-1) |
| 기존 `parse_review()` 재사용 | ❌ **5개 필드 100% 전멸 + 크래시 + 도움수 37% 손상** (§6-1) |

> **2026-08-10 핵심 정정**: "도움순 100건 상한"은 **서버 건수 제한이 아니었다.**
> cursor API는 **요청 10회**가 상한이고, 총량 = `size × 10`.
> `size=10`이라 100건에서 멈춘 것.
> **`size=50`(상한)으로 제품당 도움순 500건**을 정렬 그대로 받는다.
> → 앞서 검토하던 (a)/(b)/(c) 수집 스펙 선택지는 **불필요해짐**.

---

## 2. 환경 구성 (완료됨)

```bash
# .venv = Python 3.13.3
.venv/bin/pip install "./Scrapling-main[fetchers]"   # scrapling 0.4.12, playwright 1.61.0, patchright 1.61.2
.venv/bin/scrapling install                           # playwright chromium
.venv/bin/python -m patchright install chromium       # StealthySession은 patchright 엔진을 씀
```

`scrapling install`은 playwright 브라우저만 받는다. `StealthyFetcher`는 patchright를 쓰므로
`patchright install chromium`을 **따로** 실행해야 한다 (이번엔 캐시 공유로 즉시 완료됨).

---

## 3. ✅ 검증된 것

### 3-1. 봇 차단 통과
`StealthyFetcher.fetch(headless=False)` 로 접속 시 "잠시만 기다려주세요" 화면 없이 정상 로드.
페이지 title이 실제 상품명으로 확인됨. 여러 회차 재현.

기존의 `wait_for_challenge()` title 폴링(최대 30초)은 불필요.

### 3-2. XHR 가로채기 — CDP performance 로그 완전 대체
`page_setup` 콜백에서 `page.on("response", ...)` 등록 → navigation 이전에 리스너가 붙고
`page_action` 실행 내내 살아있음. 기존의 로그 드레인/`getResponseBody` 유실 문제 없음.

**중요**: Scrapling 내장 `capture_xhr` 옵션은 **URL 정규식으로만** 필터한다
(`Scrapling-main/scrapling/engines/_browsers/_base.py:176`).
올리브영은 정렬 조건이 POST 바디에 있어서 URL만으로는 구분 불가 →
`page_setup` + 커스텀 리스너로 `request.post_data`까지 보는 방식이 맞다.
(기존 CDP 필터와 동일 조건을 그대로 재현 가능)

리스너 안에서 body를 읽으면 sync API 재진입 위험이 있어,
`page_action` 안에서 `drain()`으로 나중에 읽는 구조로 처리함.

### 3-3. 리뷰 탭 · 정렬 클릭 — shadow DOM JS 불필요
기존 `deepQueryAll` JS 없이 Playwright 로케이터로 성공.

| 동작 | 성공한 방법 | 실패한 방법 |
|---|---|---|
| 리뷰 탭 | `get_by_text(re.compile(r"^리뷰\s*\(?\d"))` | `get_by_role("link", name=/^리뷰/)`, `a[name=reviewInfo]`, `a[href*=reviewInfo]` (전부 TimeoutError) |
| 도움순 | `get_by_role("button", name="도움순")` | — (1순위에서 성공) |

관측된 컨트롤: `<button class="ReviewArea_btn-review__gZoOZ">리뷰 14,332건`,
`<button class="ReviewArea_review-thumbs__LR3HK">리뷰 더보기`,
`<button class="GoodsDetailTabs_tab-item__tgAnU ...">리뷰&셔터14,332`
→ CSS 클래스가 해시 붙은 CSS Modules라 **클래스 셀렉터는 쓰면 안 됨**. 텍스트/역할 기반으로 잡아야 함.

### 3-4. 수집 + 정렬 품질
- UI 스크롤만으로 **40건** (cursor 4페이지 × 10)
- 페이지 내 `fetch()`로 API 직접 이어받아 **100건**
- `usefulPoint` 내림차순 유지 확인: `[738.0, 720.0, 660.0, 660.0, 594.0, 594.0] ... [176.0, 165.0, 165.0]`
- 본문 없는 항목 0건

### 3-5. 속도
40~100건 수집에 **32~44초** (페이지 로드 포함).
기존 크롤러는 모든 대기가 `time.sleep(2)` 고정이라 제품당 수 분.

### 3-6. cursor API 직접 페이지네이션 — 페이지 내 fetch만 가능
UI가 40건에서 멈춘 뒤에도 `page.evaluate`로 페이지 안에서 `fetch()`를 돌리면 계속 받아올 수 있다.

```js
fetch(url, { method:'POST', headers:{'Content-Type':'application/json'},
             body: JSON.stringify(body), credentials:'omit', mode:'cors' })
```

⚠️ **`credentials: 'include'` 를 쓰면 "Failed to fetch"로 실패한다.**
www → m 크로스오리진이고 서버가 자격증명 허용을 안 해서 브라우저가 막는다.
앱 자체 XHR도 쿠키를 안 싣는다. → `'omit'` 필수.

---

## 4. ❌ 검증 안 된 것 / 실패한 것

### ~~4-1. 제품당 200건 미달~~ → ✅ **해결됨** 〔2026-08-10〕

**결론: 100건은 서버 건수 상한이 아니라 `size=10`의 부작용이었다. 진짜 상한은 요청 10회.**

`probe_depth.py` 실측 (`USEFUL_SCORE_DESC`, 동일 제품):

| size | 총 수집 | 고유 | 호출 횟수 | 종료 사유 | usefulPoint 범위 | 내림차순 |
|---|---|---|---|---|---|---|
| 10 | 100건 | 100 | 11회 | 11번째가 empty batch | 900.0 → 66.0 | ✅ |
| 30 | **300건** | 300 | 11회 | 11번째가 empty batch | 900.0 → **22.5** | ✅ |

**호출 횟수는 둘 다 11회로 동일한데 총량만 3배** → 가설 판정 **H1: 요청 횟수 상한**.
즉 `총 수집량 = size × 10`.

- `size=30`이면 **도움순 300건**을 정렬 그대로 확보 → 제품당 200건 목표 **초과 달성**
- 앞선 "도움순 100건 상한" 기록은 전부 `size=10`으로 측정한 탓. **폐기**
- 앞서 관측한 "기본정렬 300건 max=225 / 도움순 100건 max=738" 대비도 무의미해짐
  (같은 `size=30` 조건에서 도움순이 900.0 → 22.5 를 전부 커버)

### 4-1b. `size` 상한 = 50 → **제품당 500건** 〔2026-08-10 확정〕

`probe_size_ceiling.py` — size별로 첫 페이지 1회씩만, 8초 간격으로 요청해 레이트리밋과 분리:

| size | status | 반환 건수 | 판정 |
|---|---|---|---|
| 30 | 200 | 30 | ✅ 수용 |
| **50** | 200 | **50** | ✅ **수용 (상한)** |
| 100 | 200 | 0 | ❌ 빈 응답 |
| 200 | 200 | 0 | ❌ 빈 응답 |
| 500 | 200 | 0 | ❌ 빈 응답 |

`size=50` 전체 수집 결과:
```
호출 1~10: 각 +50 → 누적 500건
호출 11  : empty batch — 요청 횟수 상한 도달
```

| 항목 | 값 |
|---|---|
| 최적 size | **50** |
| 제품당 확보량 | **500건** (고유 500, 중복 0) |
| 호출 횟수 | 11회 |
| usefulPoint 범위 | 900.0 → 20.0 |
| 도움순 내림차순 | ✅ 유지 |

**주의사항**
- `size>50`은 **에러가 아니라 빈 배열**을 반환한다 (status 200). 조용히 실패하므로
  수집 0건일 때 size를 먼저 의심할 것
- 앞서 `size=50`이 403이었던 것은 **레이트리밋**이었음 (§4-4 기록 정정)
- 500건은 이 제품 전체 리뷰 14,332건 중 상위 3.5%

**500건보다 더 필요하면** — 정렬/필터 합집합으로 확장 가능 (§5-2b):
`USEFUL_SCORE_DESC` 500 + `RECOMMENDED_DESC` 500 + `reviewType:PHOTO` 500 을 `reviewId`로 dedupe.
단 겹치는 구간이 커서 실제 순증은 측정 필요 (size=30 기준으로는 298→361건이었음).

### 4-1c. 레거시 HTML 크롤링 경로 — 사망 확인 〔2026-08-10〕
velog 글(tinyriot) 방식 = 구 PC PDP의 서버렌더 리뷰 목록 + 페이지 버튼(`data-page-no`) 파싱.
해당 경로가 아직 유효한지 `probe_legacy_reviews.py` / `probe_legacy_params.py` 로 확인함.

`getGdasListAjax.do` 는 **레거시 마크업을 반환하긴 한다**:
```html
<ul class="prd_review_list2"> <li class="no_data"> <p>등록된 상품평이 없습니다.</p> </li> </ul>
<div class="pageing"> </div>
```
하지만 **입력과 무관하게 항상 동일한 292 bytes**:

| 요청 | status | 응답 크기 |
|---|---|---|
| 실제 goodsNo | 200 | 292 bytes |
| 존재하지 않는 goodsNo (`A999999999999`) | 200 | 292 bytes |
| 빈 goodsNo | 200 | 292 bytes |

파라미터 10종 조합(`gdasSort` 01/03/05, `itemNo`, `dispCatNo`, `pageIdx`/`page`, 풀셋 등) 전부 동일.
→ **템플릿만 남고 데이터소스가 끊긴 데드 스텁.** 리뷰가 신규 서비스(`m.oliveyoung.co.kr`)로 이전되며 버려진 껍데기.
`getGdasList.do` / `getGdasListJson.do` 는 PDP HTML(8,913 bytes)을 그대로 돌려줌 — 역시 리뷰 없음.

**→ 블로그의 Selenium + BeautifulSoup HTML 파싱 방식은 현재 사용 불가.**

### 4-1d. 실행 시간 이상치 — 원인 미규명
위 300건 수집 실행의 자체 계측 소요시간이 **11,959초(약 3.3시간)**로 찍혔다.
API 호출 10회는 로그상 정상 진행됐고(각 +30건), 앞선 40~100건 실행은 32~44초였다.
페이지 로드나 종료 단계에서 장시간 블로킹된 것으로 보이나 **원인 미확인**.
다제품 순회 설계 전에 반드시 재현/규명 필요 (타임아웃 상한을 걸 것).

### 4-2. `page.request` (Playwright APIRequestContext) — 항상 403
브라우저 쿠키는 공유하지만 별도 HTTP 스택이라 WAF가 HTML 403 페이지를 돌려준다.
size와 무관하게 실패. **페이지 내 `fetch()`만 사용 가능.**

### 4-3. API 전용 경로 (UI 조작 생략)
`--api-only`로 페이지만 열고 바로 cursor API를 치는 경로는 **1회 성공 후 이후 403**.
UI 워밍업(리뷰 탭 클릭 → 앱이 첫 cursor 요청을 발생) 없이도 안정적인지 **미확정**.
현재까지 안정적으로 동작한 조합은 **UI 워밍업 → 페이지 내 fetch로 이어받기**.

### ~~4-4. cursor API `size` 상한 — 미분리~~ → **해결됨, §4-1b 참조**
초기 측정에서 `size=50`이 403이었으나 이는 **레이트리밋**이었다.
8초 간격을 두고 재측정한 결과 **50까지 정상 수용, 100 이상은 빈 배열**. 상한 = **50**.
(이 절의 원래 표는 레이트리밋에 오염된 값이라 폐기)

### 4-5. 레이트리밋 — 실측 및 대응 확정 〔2026-08-10〕

**성격: 순간 속도가 아니라 누적 호출량 기준.** 50개 제품 본 수집 과정에서 3회 실측:

| 실행 | 페이싱 | 결과 |
|---|---|---|
| 1차 | 제품 간격 3초 (실질 ~60회/분) | 10개 제품(**100콜**) 후 차단. 이후 "8개 실패 → 2~3개 회복" 주기 반복 |
| 2차 | 제품 간격 **12초** | **70콜/26분**에서 차단 — 간격을 4배 늘렸는데 더 일찍 막힘 |
| 3차 | **12회/분 전역 페이싱** + 사전 10분 대기 | **43개 제품 439콜 완주**, 감속 1회 |

2차가 1차보다 나빴던 이유: 1차 종료 10분 뒤에 시작해 **예산이 회복되기 전**이었다.
→ 제품 간 간격을 늘리는 것은 대책이 아니다. **전역 호출 속도**를 제한해야 한다.

**차단 시 증상**: `page.evaluate` 의 fetch 가 `TypeError: Failed to fetch` (status=0).
페이지 로드 자체는 정상(`blocked=False`)이므로 봇 차단과 구분된다.

**대응 (`Pacer` 클래스에 구현)**
- `--rate` 로 분당 호출 수 제한 (기본 12)
- 차단 감지 시 속도를 영구적으로 ×0.6 (하한 4회/분) + 해당 제품 쿨다운 후 재시도
- 3차 실행에서 12 → 7.2회/분으로 1회 자동 감속, 이후 끝까지 완주

**실전 수치**: 439콜 / 약 63분 / 실효 7회/분. 50개 제품 25,000건 기준 **약 1~1.5시간**.

⚠️ 차단 직후 재실행하면 예산이 회복되지 않아 즉시 다시 막힌다. **10분 이상 간격**을 둘 것.
   중단되더라도 `--resume` 으로 완료 제품을 건너뛰고 이어받을 수 있다.

### 4-6. 그 외 미테스트
- `headless=True` (전부 headful로만 검증)
- `disable_resources=True` (속도 옵션)
- `StealthySession` 재사용한 다제품 연속 수집
- 모바일 PDP(`m.oliveyoung.co.kr`)가 여전히 구 엔드포인트를 쓰는지 — **PC 페이지만 관측함**

---

## 5. 발견된 사이트 변경사항

### 5-1. 리뷰 엔드포인트가 바뀜
```
구: POST https://m.oliveyoung.co.kr/review/api/v2/reviews          (기존 크롤러가 가로채던 것)
신: POST https://m.oliveyoung.co.kr/review/api/v2/reviews/cursor   (PC 페이지도 모바일 도메인을 호출)
```

**요청 (첫 페이지)**
```json
{"goodsNumber":"A000000158513","page":0,"size":10,"sortType":"USEFUL_SCORE_DESC","reviewType":"ALL"}
```
**요청 (2페이지 이후 — 커서 방식)**
```json
{"goodsNumber":"A000000158513","size":10,"sortType":"USEFUL_SCORE_DESC","reviewType":"ALL",
 "cursorId":63640111,"cursorScore":528,"cursorCount":null}
```
**응답 구조**
```
top : ['status','code','message','pagination','data','totalCnt','pageData']
data: ['goodsReviewList','nextCursorId','nextCursorScore','nextCursorCount','hasNext','loginRequired']
```
응답의 `nextCursorId`/`nextCursorScore`/`nextCursorCount` → 다음 요청의 `cursorId`/`cursorScore`/`cursorCount`.

### 5-2. sortType 값 — 유효한 것은 2개뿐 〔2026-08-10 실측〕
| 값 | 의미 | 결과 |
|---|---|---|
| `USEFUL_SCORE_DESC` | 도움순 (UI '도움순' 버튼) | ✅ 동작 |
| `RECOMMENDED_DESC` | 페이지 진입 시 기본 정렬 | ✅ 동작 |
| `LATEST` | — | ✗ empty batch |
| `CREATED_DATE_DESC` | — | ✗ empty batch |
| `REVIEW_SCORE_DESC` | — | ✗ empty batch |
| `REVIEW_SCORE_ASC` | — | ✗ empty batch |
| `USEFUL_SCORE_ASC` | — | ✗ empty batch |

기존 크롤러가 "도움순"이라 부르며 잡던 `RECOMMENDED_DESC`는 지금은 **기본 정렬**이다.
지금의 도움순은 `USEFUL_SCORE_DESC`.
알 수 없는 sortType은 에러가 아니라 **빈 배열**을 돌려준다 (조용히 실패하므로 주의).

### 5-2c. 변형 SKU 합산 — cursor API는 요청 상품만 주지 않는다 〔2026-08-10〕

cursor API는 요청한 `goodsNumber` 외에 **같은 제품의 용량/기획 변형 리뷰도 함께** 반환한다.
50개 제품 수집 결과 **36/50개 제품에서 변형이 섞였고, 제품당 평균 3.1종**의 `goodsNo` 가 나왔다.

예 (메디힐 마데카소사이드 세럼 요청 → 4종 합산):
```
A000000211119  379건  40+40ml            ← 요청한 것
A000000205643   82건  40ml 단품
A000000226449   34건  1+1+10ml 한정기획
A000000217541    5건  증량 기획
```

**다른 제품이 아니라 같은 제품의 다른 SKU**이므로 리뷰 분석에는 오히려 유리하다.
다만 출처 추적을 위해 파서가 두 필드를 모두 남긴다:

| 필드 | 의미 |
|---|---|
| `goodsNo` | 리뷰가 실제로 달린 상품 (변형 SKU일 수 있음) |
| `requestedGoodsNo` | 크롤러가 요청한 상품 — **제품 단위 집계·이어받기는 이 필드 기준** |

⚠️ 제품별 건수를 셀 때 `goodsNo` 로 세면 안 된다 (50개 요청에 93종이 나온다).

### 5-2b. 필터 파라미터 〔2026-08-10 실측〕
| 파라미터 | 동작 여부 | 비고 |
|---|---|---|
| `reviewType: "PHOTO"` | ✅ 동작 | 사진 리뷰만. 합집합에 **+63건** 기여 |
| `reviewType: "ALL"` | ✅ 기본값 | |
| `reviewType: "TEXT"` | ✗ empty batch | 유효한 값이 아님 |
| `reviewScores: [5]` 등 평점 필터 | ❌ **무시됨** | 필터해도 결과가 무필터와 동일 (신규 0건) |

**합집합 실측** (동일 제품): 도움순 + 기본정렬 = 298건, 여기에 사진필터 추가 → **361건**.
단 `size=30`으로 도움순만 300건이 나오므로, 합집합 전략은 300건 이상이 필요할 때만 의미 있음.

### 5-3. 함께 관측된 리뷰 관련 엔드포인트
| 엔드포인트 | 내용 |
|---|---|
| `GET /review/api/v2/reviews/options/{goodsNo}/count` | 옵션별 리뷰 수 |
| `GET /review/api/v2/reviews/{goodsNo}/stats` | 평점 통계 |
| `GET /review/api/v1/reviews/{goodsNo}/summary` | 요약 |
| `POST /review/api/v2/reviews/photo-reviews` | **사진만** (`reviewId`/`photoReviewList`/`createdDateTime` 3필드). 리뷰로 착각하면 데이터 오염됨 |

---

## 6. 기존 `parse_review()`에 미치는 영향

신규 페이로드의 리뷰 객체 키 (16개, 실측):
```
reviewId, content, goodsDto, reviewScore, hasPhoto, isRepurchase,
isMonthUseReview, isMonthOverReview, reviewType, usefulPoint,
photoReviewList, profileDto, createdDateTime, recommendCount, isMyReview, isRecommended
```

| 기존 파서가 읽는 것 | 신규 페이로드 | 영향 |
|---|---|---|
| `skinInfoDto.skinType / skinTone / skinConcerns` | **`skinInfoDto` 자체가 없음** | `skinType`·`skinTone` 전부 `""`, `skinConcerns` 전부 `[]` |
| `satisfactionTags` | **없음** | 전부 `[]` |
| `usagePeriodTag` | **없음** | 전부 `""`. 대신 `isMonthUseReview` / `isMonthOverReview` |
| `usefulPoint` → `int(...)` | **float** (`738.0`, `4.5`, `0.9`) | 소수 절삭 (`0.9` → `0`). `recommendCount`가 별도로 존재 |
| `goodsDto.*` | `goodsNumber, itemNumber, legacyGoodsNumber, goodsName, optionName` | 호환 OK |
| `profileDto.*` | `memberNickname, profileImageUrl, isShutterbrity, isTopReviewer, reviewerRank, profileKey, skinType, skinTone, skinTrouble, isSkinTypeMatched, isSkinToneMatched` | 닉네임/랭크 OK. **피부정보가 여기로 이동** |

### 6-1. 정량 검증 결과 〔2026-08-10, RECOMMENDED_DESC 300건 실측〕

**(1) 크래시 — 2/300건 (0.7%)**
```
AttributeError: 'NoneType' object has no attribute 'strip'
crawl_target_200.py:95  review["option"] = goods.get("optionName", "").strip()
```
`optionName`이 **키 자체는 있고 값이 `null`** 이라 `.get(k, "")` 기본값이 안 먹는다.
단일 옵션 상품에서 발생. → `(goods.get("optionName") or "").strip()` 로 수정 필요.

**(2) 크래시 회피 후 — 빈 값이 되는 필드**

| 필드 | 빈 값 | 비율 | 판정 |
|---|---|---|---|
| `skinType` | 300/300 | 100.0% | ❌ 전멸 |
| `skinTone` | 300/300 | 100.0% | ❌ 전멸 |
| `skinConcerns` | 300/300 | 100.0% | ❌ 전멸 |
| `satisfactionTags` | 300/300 | 100.0% | ❌ 전멸 |
| `usagePeriodTag` | 300/300 | 100.0% | ❌ 전멸 |
| `reviewerRank` | 208/300 | 69.3% | ⚠️ 원래 희소 (정상일 수 있음) |
| `reviewImages` | 152/300 | 50.7% | ⚠️ 사진 없는 리뷰 (정상) |
| `profileImageUrl` | 34/300 | 11.3% | ⚠️ 정상 |
| `option` | 2/300 | 0.7% | ⚠️ 위 크래시 건 |

**(3) `usefulPoint` float → `int()` 절삭 손실 — 심각**

| 항목 | 건수 | 비율 |
|---|---|---|
| 소수점이 있는 항목 | 292/300 | **97.3%** |
| **0으로 절삭됨** | 112/300 | **37.3%** |

예: `12.6→12`, `0.75→0`, `0.9→0`, `22.5→22`
도움 표시가 실제로 있는 리뷰의 **37%가 `likes=0`으로 기록**된다.
`usefulPoint`는 정수 카운트가 아니라 **가중 점수(float)** 로 바뀌었다.
→ `int()` 벗기고 float 유지할 것. 순수 카운트가 필요하면 `recommendCount`(300/300 존재)를 쓸 것.

**(4) 손실 필드의 대체 가능성**

| 구 필드 | 대체 | 커버리지 |
|---|---|---|
| `skinInfoDto.skinType` | `profileDto.skinType` | 251/300 (83.7%) ✅ |
| `skinInfoDto.skinTone` | `profileDto.skinTone` | 236/300 (78.7%) ✅ |
| `skinInfoDto.skinConcerns` | `profileDto.skinTrouble` | 244/300 (81.3%) ✅ (단수형·스키마 상이) |
| `usagePeriodTag` | `isMonthUseReview`(1건) / `isMonthOverReview`(126건) | ⚠️ boolean 2개로 축소 — 기간 문자열 복원 불가 |
| `satisfactionTags` | **없음** | ❌ 대체 필드 부재 |

→ 피부정보 3종은 `profileDto`에서 **복구 가능**.
   `usagePeriodTag`는 정보량이 줄고, `satisfactionTags`는 **복구 불가**.

**(6) 파이프라인 영향 — 없음** 〔2026-08-10 확인〕

`step2_review_analysis/` (`extract_context_tag.py`, `merge_tags.py`, `question_maker.py` 총 1,101줄)가
리뷰 객체에서 실제로 읽는 필드는 **4개뿐**:

| 필드 | 신규 페이로드 | 참조 위치 |
|---|---|---|
| `reviewId` | ✅ 있음 | `extract_context_tag.py:139,235` |
| `content` | ✅ 있음 | `extract_context_tag.py:140,192` |
| `productName` | ✅ 있음 (`goodsDto.goodsName`) | `extract_context_tag.py:236,242` |
| `goodsNo` | ✅ 있음 (`goodsDto.goodsNumber`) | `extract_context_tag.py:243` |

나머지(`contextTags`, `decisionExpression`, `mergedTags` 등)는 **파이프라인이 스스로 만든 중간 산출물**이지
크롤러 입력이 아니다.

→ **전멸한 5개 필드(`skinType`/`skinTone`/`skinConcerns`/`satisfactionTags`/`usagePeriodTag`)는
   다운스트림에서 아무도 쓰지 않는다. 소실돼도 파이프라인은 그대로 동작한다.**
   `usefulPoint` 절삭 문제도 파이프라인은 `rating`/`likes`를 안 읽으므로 영향 없음
   (단 리뷰 품질 정렬·필터링에 쓸 거면 여전히 고쳐야 함).

**(5) 데이터 무결성** — `reviewId` 중복 0건, 본문 없는 항목 0건 ✅

---

## 7. 재현 방법

```bash
cd /Users/banjax.index/development/product/oliveyoung-crawler
source .venv/bin/activate

# 기본: UI 워밍업 → API 이어받기 (가장 안정적인 조합)
python poc_scrapling.py --target 100 --api-size 10

# 정렬 바꿔서
python poc_scrapling.py --target 300 --api-size 30 --sort RECOMMENDED_DESC

# UI 조작 없이 API만 (불안정)
python poc_scrapling.py --api-only --target 300 --api-size 30

# 다른 제품
python poc_scrapling.py --url "<PDP URL>" --target 100

# ── 상한/우회 측정용 프로브 ──
python probe_size_ceiling.py --sizes 30,50,100 --gap 8   # size 상한 (=50) 확정한 스크립트
python probe_depth.py --sizes 10,30 --cap 600   # size별 도달 한계 (H1 확정한 스크립트)
python probe_bypass_100.py                       # 정렬/필터 조합별 합집합
python probe_legacy_reviews.py                   # 레거시 엔드포인트 생존
python probe_legacy_params.py                    # 레거시 파라미터 조합
```

주요 옵션: `--headless`, `--fast`(disable_resources), `--real-chrome`, `--target`, `--api-size`, `--sort`, `--api-only`

출력:
- `data/poc_scrapling_<goodsNo>.json` — 수집된 원본 리뷰 (0건이면 덮어쓰지 않음)
- `data/poc_scrapling_debug.json` — 엔드포인트 인벤토리, 커서 시퀀스, DOM probe, 에러 로그

**연속 실행 주의**: 세션을 8~9회 빠르게 반복하면 403에 걸린다. 실행 사이에 텀을 둘 것.

---

## 8. 다음 작업

### ~~수집 스펙 재정의~~ → 해결됨
`size=30` × 요청 10회 = **도움순 300건**. 선택지 (a)/(b)/(c) 불필요. 기존 200건 목표 그대로 진행 가능.

### ~~`satisfactionTags` 소실 대응~~ → 해결됨
파이프라인이 읽는 필드는 `reviewId`/`content`/`productName`/`goodsNo` 4개뿐이고 전부 신규 페이로드에 존재.
전멸한 5개 필드는 다운스트림 미사용 (§6-1 (6)). **블로커 아님.**

### 남은 블로커
없음. 아래는 측정/구현 항목.

### ~~`size` 상한 확정~~ → 해결됨
**size=50, 제품당 500건** (§4-1b). 그 이상은 빈 배열.

### 측정 (코드 작성 전)
1. **실행시간 이상치 규명** (§4-1d, 3.3시간) — 타임아웃 상한 설정
2. **레이트리밋 안전 간격** — 제품 간 sleep 값.
   현재까지: 요청 간 8초는 안전, 브라우저 세션 8~9회 연속 반복은 차단
3. **(선택) 500건 초과가 필요하면** 정렬/필터 합집합 순증 측정 (§4-1b 하단)

### ~~포팅~~ → ✅ 완료 (2026-08-10) — `oliveyoung_crawler.py`

구현된 내용:
- `parse_review()` 신규 스키마 대응 — `optionName` null 크래시 수정, `usefulPoint` float 보존,
  피부정보 `profileDto` 재매핑, `isMonthUseReview`/`isMonthOverReview`/`recommendCount` 추가
- 두 크롤러를 1개 모듈로 통합, 제품목록·목표건수·정렬 전부 CLI/JSON 파라미터화
- `StealthySession` 으로 브라우저 1개 재사용하며 다제품 순회
- UI 조작 없이 PDP 로드 → 페이지 내 fetch로 cursor API 직접 호출 (리뷰 탭·정렬 클릭 불필요)
- `size>50` 자동 클램프, 호출 실패 백오프 재시도, 제품마다 증분 저장, 실패 제품 리포트

실전 검증 (2026-08-10):
```
5개 제품 × 500건 = 2,500건 / 약 50초 / 중복 0 / 결손 0 / 도움순 5/5 유지
usefulPoint 범위 예: 클리오 쿠션 6534.0 → 30.0
```

미해결(영향 없음): 피부정보 코드→라벨 매핑 미발견 (API·필터 UI·JS 번들 모두 탐색 실패).
> **2026-09-03 해소** — 실브라우저로 PDP 리뷰 위젯(Lit/Shadow DOM)에서 26종 전부 실측.
> `data/input/skin_codebook.json` 참조. 아래 본문은 2026-08 시점 기록이라 그대로 둔다.
`skinType=A01~A07`, `skinTone=B01~B06`, `skinTrouble=C01~C13` 을 **코드 그대로 저장**한다.
파이프라인이 쓰지 않는 필드라 블로커 아님. 매핑을 찾으면 `oliveyoung_crawler.py` 상단
`SKIN_*_LABELS` dict를 채우면 라벨 필드가 자동 추가된다.

### ~~구 크롤러 정리 / 제품 목록 이관~~ → ✅ 완료 (2026-08-10)
- 구 크롤러 2개를 **`_archive/`** 로 이동 (`_archive/README.md` 에 동작 불가 사유 기록)
- 제품 목록 JSON 이관 및 전수 검증:

| 파일 | 제품 수 | 출처 | 검증 |
|---|---|---|---|
| `products_50.json` | 50 | `oliveyoung_review_crawler.py` | **50/50 조회 성공** |
| `products_legacy5.json` | 5 | `crawl_target_200.py` | **5/5 조회 성공** |

`products_50.json` 카테고리 분포: 에센스/세럼 15 · 크림 10 · 베이스메이크업 10 · 아이메이크업 8 · 립메이크업 7
(제품명은 구 코드의 주석에서 `productKey` 로 추출, 누락 0)

`--products` 생략 시 기본으로 `products_50.json` 을 읽는다.

### 남은 작업 (선택)
1. **피부 코드 라벨 매핑** — 찾으면 `SKIN_*_LABELS` 채우기
2. **실행시간 이상치**(§4-1d) — 신규 크롤러에서는 재현되지 않았으나, 대량 순회 시 관측되면 조사
3. **500건 초과가 필요하면** `--union` 사용 (도움순+기본정렬+사진리뷰 합집합)

---

## 9. 참고 위치

| 대상 | 경로 |
|---|---|
| **✅ 신규 크롤러 (프로덕션)** | **`oliveyoung_crawler.py`** |
| 제품 목록 (50개, 기본값) | `products_50.json` |
| 제품 목록 (5개) | `products_legacy5.json` |
| **본 수집 결과 (25,000건)** | **`data/reviews_50products.json`** + `_report.json` |
| 5개 제품 수집 (2,500건) | `data/reviews_500.json` + `_report.json` |
| 50개 제품 조회 검증 | `data/validate_50.json` + `_report.json` |
| 구 크롤러 (동작 불가, 보관) | `_archive/` + `_archive/README.md` |
| PoC 스크립트 (메인) | `poc_scrapling.py` |
| 레거시 엔드포인트 생존 확인 | `probe_legacy_reviews.py` |
| 레거시 파라미터 조합 탐색 | `probe_legacy_params.py` |
| 100건 상한 우회 탐색 (정렬/필터) | `probe_bypass_100.py` |
| size별 상한 측정 (H1 확정) | `probe_depth.py` |
| **`size` 상한 확정 (=50, 500건)** | `probe_size_ceiling.py` |
| Scrapling 소스 | `Scrapling-main/scrapling/` |
| XHR 캡처 구현 | `Scrapling-main/scrapling/engines/_browsers/_base.py:150-180` |
| fetch 옵션 문서 | `Scrapling-main/docs/fetching/stealthy.md`, `dynamic.md` |
