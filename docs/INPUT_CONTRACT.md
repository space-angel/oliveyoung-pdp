# v5 입력 계약 (PER-176)

작성: 2026-09-04 · 이슈 [PER-176](https://linear.app/banjax/issue/PER-176) (마일스톤 #2 마감, due 09-09)
근거 데이터: `data/input/reviews_50products.json` (25,000건, sha256 `04e089d0…4903a773`)
근거 스크립트: [`eval/measure_input_contract.py`](../eval/measure_input_contract.py) → [`eval/reports/input_contract_per176.json`](../eval/reports/input_contract_per176.json)
강제 지점: [`pipeline/contracts.py`](../pipeline/contracts.py) · [`pipeline/codebook.py`](../pipeline/codebook.py) · [`pipeline/catalog.py`](../pipeline/catalog.py) · [`pipeline/policy.py`](../pipeline/policy.py)
계약 테스트: [`pipeline/test_contract.py`](../pipeline/test_contract.py)

> 이 문서의 모든 수치는 위 스크립트 재실행으로 재현된다. 재실행 명령은 §8.

앞선 결정(PER-169·170·171·172·173·175)을 **하나의 입력 계약**으로 고정한다. 결정의 논거는
각 결정 문서에 있고, 이 문서는 **파이프라인이 지켜야 하는 조항과 그 강제 지점**만 모은다.

---

## 0. 계약 요약

| 조항 | 값 | 강제 지점 | 위반 시 |
|---|---|---|---|
| **집계 단위** | `productId` — 계보 50 / 세대 53 | `catalog.resolve_goods_no()` | `UnknownGoodsNoError` |
| **식별자** | `reviewId` (양의 정수, 스냅샷 내 유일) | `contracts._validate_required` · `ingest.ingest` | `ContractError` |
| **작성자 키** | `NFC(userName)` 원문 | `contracts.author_key` | `ContractError` (공백·결측) |
| **중복 단위** | `(authorKey, productId)`, 카운트는 **고유 작성자 수** | 게이트2 (PER-183) | — |
| **조건축** | `skinType`(단일) · `skinTrouble`(다중) · `option`(자유 문자열) | `contracts.CONDITION_AXES` | `ContractError` |
| **조건 어휘** | 코드북 26종(A01~A07·B01~B06·C01~C13). 라벨 금지 | `codebook.assert_code()` | `ContractError` |
| **미기재** | 조건 없음이 아니라 **별도 세그먼트** `미기재` | `contracts.MISSING_SEGMENT` | `segment` 는 절대 null 이 아니다 |
| **리센시 컷** | 스냅샷 최신 월 기준 24개월 (`2024-09`~) | `policy.recency_gate()` | `rejected[]` 행 (드롭 아님) |
| **리뉴얼 컷** | 세대는 별개 `productId` + 같은 `lineageId` | `policy.renewal_gate()` | `rejected[]` 행 / `limitation` |
| **스냅샷 스키마** | 계약이 아는 25필드와 **정확히** 일치 | `contracts.assert_row_schema()` | `ContractError` |
| **비목표** | `usagePeriod` 조건축 (§5), `skinTone` 조건축 (§4) | — | — |

**계약 위반은 조용한 폴백 없이 전부 에러다.** 이게 이 문서의 완료 조건이고, §7에서
위반 클래스 12종을 실제로 넣어본 결과를 싣는다.

---

## 1. 집계 단위는 `productId` 다

```python
from catalog import load_catalog
product_id = load_catalog().resolve_goods_no(row["goodsNo"], row["reviewDate"])
```

**리뷰 행의 `productKey` 문자열도, `goodsNo` 도 그룹핑 키가 아니다.** 세 후보를 같은
잣대로 잰 결과다(`N_min=8`, 작성자 dedup 후):

| 단위 | 개수 | 리뷰/단위 (min·중앙·max) | N<8 단위 | 사장되는 리뷰 | 세대를 가르는가 | 이름 오기에 안전한가 |
|---|---|---|---|---|---|---|
| `goodsNo` | 153 | 1 · 79 · 500 | 26 | **93건** | 아니오 | — |
| **`productId`** | **53** | 7 · 500 · 500 | 2 | 11건 | **예** | **예** |
| 행의 `productKey` | 50 | 159 · 500 · 500 | 0 | 0건 | **아니오** | 아니오 |

### `goodsNo` 를 쓰지 않는 이유 — 게이트 실패가 아니라 **파편화**다

cursor API 가 요청 상품뿐 아니라 변형 SKU 리뷰를 합산해 반환한다
(`docs/SCRAPLING_MIGRATION_POC.md` §5-2c). 그래서 한 제품이 여러 `goodsNo` 로 흩어진다.

- 제품 53개 중 **35개(66.0%)가 복수 `goodsNo`**, 최대 **17개**(`p050` 피지오겔 DMT 페이셜크림)
- `goodsNo` 로 묶으면 같은 제품에 대한 주장이 **최대 17번, 서로 겹치지 않는 근거로** 따로 생성된다.
  사용자에게 나가는 "리뷰 23건 중 19건" 카운트가 그만큼 쪼개진다
- 리뷰 8건 미만인 SKU 26개에 갇힌 **93건**은 어떤 주장도 뒷받침하지 못한 채 사라진다

> **전제 정정 (2026-09-04).** 이 이슈와 `docs/PRODUCT_CATALOG.md` §2-4, `docs/V5_INPUTS_AND_LEGACY_AUDIT.md` §3-1 은
> "`goodsNo` 로 그룹핑하면 리뷰 1~7건 상품이 **139개** 생겨 충분성 게이트가 대량 실패한다"고 적어
> 왔다. **139는 리뷰 1~7건이 아니라 크롤 목표 500건에 미달한 `goodsNo` 의 수다** (감사 문서
> §3-1 의 바로 윗줄 "goodsNo 중 500건 미달: 139개"를 아랫줄이 잘못 옮겼다). 실측하면 1~7건은
> **26개**다.
>
> 더 중요한 건 **"게이트가 대량 실패한다"는 방향도 틀렸다**는 것이다. `goodsNo` 단위로 세면
> 통과 셀이 **429개**로 `productId` 단위 297개보다 **많다** — 셀이 잘게 쪼개져 수만 늘기 때문이다.
> 문제는 게이트 통과 수가 아니라 **셀이 답하는 질문의 단위**다. "이 SKU 에 대해"는 구매자가
> 묻는 질문이 아니다. 그래서 이 계약의 논거는 게이트 실패율이 아니라 위의 파편화·사장이다.

### 행의 `productKey` 를 쓰지 않는 이유 — 세대를 못 가르고, 이미 틀린 적이 있다

숫자만 보면 행의 `productKey` 가 제일 깨끗하다(50개, 사장 0건). 쓰지 않는 이유는 두 가지다.

1. **리뉴얼 세대를 가르지 못한다 (PER-172).** 한 이름 아래 두 세대가 섞인 계보가 **3개** 있다 —
   `헤라 블랙 쿠션 파운데이션` → `p017`/`p053`, `에스쁘아 비벨벳 커버쿠션` → `p011`/`p052`,
   `컬러그램 누디 블러 틴트` → `p004`/`p051`. 이름으로 묶으면 제형이 바뀌기 전 리뷰가
   현행 제품의 근거로 들어간다
2. **이름은 이미 틀린 적이 있다.** v4 매핑의 `닥터자르트 레드 블레미쉬 클리어 수딩 크림` 은
   실제로 **닥터지(Dr.G)** 제품이었다 (`eval/reports/product_catalog_coverage.json` 의 `nameDrift`).
   제품 동일성을 행 문자열에 맡기면 이런 오기가 집계까지 그대로 간다

**따라서 제품 동일성은 카탈로그 레이어가 소유한다.** 규칙과 운영 절차는
[`docs/PRODUCT_CATALOG.md`](PRODUCT_CATALOG.md). 미등록 `goodsNo` 는 폴백하지 않고 `UnknownGoodsNoError` 다.

---

## 2. 계보 50 · 세대 53 — 어느 쪽을 언제 쓰는가

| 수 | 뜻 | 쓰는 곳 |
|---|---|---|
| **50** (`lineageId`) | 사람이 인식하는 제품 | "50개 상품을 다뤘다"는 서술, 커버리지 분모 |
| **53** (`productId`) | 리뷰가 유효한 **세대** | 집계·게이트·주장 생성의 실제 키 |

53 = 크롤 대상 50 + 리뉴얼 이전 세대 3(`p051`·`p052`·`p053`). 두 수를 섞어 쓰면 커버리지가
조용히 틀리므로, 리포트에는 항상 어느 쪽인지 적는다.

---

## 3. 유효성 · 식별자 — 무엇이 보증되고 무엇이 에러인가

### 3-1. 필수 필드 (`contracts.REQUIRED_FIELDS`)

`reviewId` · `content` · `rating` · `reviewDate` · `userName`. **없거나, null 이거나, 공백뿐이면 에러다.**
나머지 원문 필드는 없으면 `null` 로 들어간다(§3-4 의 스키마 검사가 그 상황을 먼저 잡는다).

빈 값을 통과시키면 조용히 틀리는 경로가 두 개 생긴다.

- **빈 `userName`** → 작성자 키가 빈 문자열이 되어 **서로 다른 사람이 한 사람으로 합쳐진다.**
  중복 게이트(PER-183)가 근거를 과소 계수하고, 그 사실은 카운트가 이상해진 뒤에야 보인다
- **빈 `content`** → 근거 0자짜리 리뷰가 주장을 뒷받침한다

### 3-2. 식별자

| 항목 | 계약 | 25K 실측 |
|---|---|---|
| `reviewId` 타입 | 양의 정수 (bool 제외) | 위반 0 |
| `reviewId` 중복 | 스냅샷 내 유일 | **0건** |
| `reviewId` 가 여러 `goodsNo` 에 걸침 | 없어야 한다 | **0건** |
| `rating` | 1~5 정수 | 범위 밖 0 (1점 223 · 2점 184 · 3점 828 · 4점 2,403 · 5점 21,362) |
| `goodsNo` | 카탈로그 등록분 | 미등록 **0개** |

식별자는 깨끗하다. **깨끗하다는 사실이 검사를 뺄 이유는 아니다** — 다음 수집분이 같으리라는
보장이 없고, 검사가 없으면 달라진 순간을 알 방법이 없다.

### 3-3. `reviewDate` 는 리센시 컷의 입력이다

`2026.07.19` / `2026-07-19` 두 표기를 받고 **월로 파싱되지 않으면 에러**다(`policy.month_of`).

이전 구현은 파싱 실패 시 `derived.reviewYearMonth` 를 조용히 `null` 로 뒀다. 리센시
컷(PER-172)이 그 값을 읽으므로, 막지 않으면 컷이 뒤 단계에서 터지거나 조용히 빗나간다.
현 스냅샷은 형식 위반 0건, 기간 `2018.12.26`~`2026.08.10` 이다.

### 3-4. 스냅샷 스키마 — 필드가 늘거나 줄면 멈춘다

`assert_row_schema()` 가 행의 필드 집합이 계약이 아는 25필드(원문 18 + 드롭 6 + `reviewId`)와
**정확히 같은지** 본다. 모르는 필드도, 사라진 필드도 에러다.

모르는 필드를 조용히 버리지 않는 이유: 새 필드는 원문/조건/파생/드롭 중 **어디에 속하는지
사람이 정해야 한다.** 버리고 넘어가면 그 결정이 영영 일어나지 않는다. 값 검증은 레코드
단위지만 필드 집합이 바뀐 것은 스냅샷 전체의 사건이므로, 검사는 파일을 읽는 경계(`ingest`)에 둔다.

---

## 4. 조건축 — `skinType` · `skinTrouble` · `option`

```
skinType     단일  코드 A01~A07   → SingleCondition  (segment 1개)
skinTrouble  다중  코드 C01~C13   → MultiCondition   (segment N개)
option       단일  자유 문자열     → SingleCondition  (도메인 없음)
```

| 축 | 기재율 | 도메인 | 도메인 밖 값 |
|---|---|---|---|
| `skinType` | 57.11% | 코드북 7종 | 0 |
| `skinTrouble` | 53.92% | 코드북 13종 | 0 |
| `option` | 68.06% | 없음 (25K 에서 797종) | — |

### 어휘는 코드다. 라벨이 아니다

**조건 코드는 코드북(`data/input/skin_codebook.json`, PDP DOM 실측 26종) 도메인 안이어야 한다.**
도메인 밖 코드는 폴백하지 않고 `ContractError` 이고, 세 가지 위반을 구분해 알려준다.

- 도메인 밖 코드 (`A99`) — 실제로 새 코드가 생겼다면 `crawler/verify_skin_codebook.py` 로
  코드북을 다시 뽑는다. **추정으로 채우지 않는다**
- 코드가 아닌 라벨 (`건성`) — **v4 가 피부 힌트를 라벨로 받아 조건축과 어휘가 갈렸고, 그게
  카테고리 분포 FAIL 의 원인이었다.** 그 재발 경로를 여기서 막는다
- 축 혼용 (`skinType` 자리에 `B03`) — 조용히 통과하면 세그먼트가 하나 늘고 집계가 틀린다

검증하지 않으면 잘못된 값이 **그대로 새 세그먼트가 된다.** 셀이 하나 늘 뿐 아무 데서도
에러가 나지 않으므로, 사고는 집계 수치가 틀어진 뒤에야 발견된다.

`skinTrouble` 은 **배열이어야 한다.** 문자열을 그대로 받으면 `"C05"` 가 문자 단위로 쪼개져
세그먼트 `['0','5','C']` 가 된다. 조합을 하나의 키(`C01+C05`)로 묶지도 않는다 — 25K 에서
조합이 89종이라 셀이 즉시 희소해진다.

라벨(`A02` → `건성`)은 **표기 단계에서만** 붙인다. 입수 산출물에 라벨은 들어가지 않는다.

### `skinTone` 은 조건축이 아니다

코드북에는 있고(B01~B06, 기재율 54.8%) 원문층에도 싣지만 세그먼트 축으로 쓰지 않는다.
축을 늘리면 `제품 × 축` 셀이 곱으로 늘어 N_min 경계에 걸리고, 톤 관련 주장은 대부분
`option`(호수)으로 이미 조건화되기 때문이다. 코드 도메인 검증은 걸어 둔다 — 나중에 축으로
승격할 때 이미 검증된 값이어야 한다.

---

## 5. `usagePeriod` 는 조건축에서 제외한다 — 데이터에 필드가 없다

PRD 는 사용 기간 조건축을 언급하지만 **스냅샷 25필드 어디에도 사용 기간 필드가 없다.**
비슷한 이름의 필드 두 개가 있으나 조건축이 되지 못한다.

| 필드 | 실제 의미 | 왜 축이 안 되는가 |
|---|---|---|
| `isMonthUseReview` | "한 달 사용 리뷰" 여부 (불리언) | 기간이 아니라 리뷰 종류다. 값이 2단계뿐 |
| `isMonthOverReview` | "한 달 이상" 여부 (불리언) | 같음 |

**본문에서 추정하지 않는다.** 추정한 기간으로 조건을 붙이면 "3개월 쓴 사람 기준"이라는
문장이 리뷰에 없는 정보가 되고, 그건 설계 원칙 ①(생성하지 말고 선별한다) 위반이다.
이 비목표는 `README.md` §10 에 이미 올라 있고, 이 계약이 그 근거를 고정한다.

새 수집분에서 실제 사용 기간 필드가 관측되면 §3-4 의 스키마 검사가 **모르는 필드**로
멈춘다 — 그때 축으로 승격할지 정하면 된다.

---

## 6. 미기재 · 컷 · PII

### 6-1. 미기재는 "조건 없음"이 아니라 별도 세그먼트다

조건축의 절반 가까이가 미기재다(`skinType` 42.9%). 이들을 조건 없음으로 취급하면 두 가지가
동시에 깨진다 — 미기재 리뷰가 **모든 세그먼트의 근거로 새어 들어가거나**, 반대로 통째로
버려져 근거의 절반이 사라진다.

```
condition.skinType = {"code": null, "stated": false, "segment": "미기재"}
```

`stated` 가 기재 여부를 들고, `segment` 는 **절대 null 이 아니다.** 미기재 세그먼트의 주장은
조건을 붙이지 않고 낸다("전체 리뷰 기준") — 조건이 없는 게 아니라 **조건을 모른다**는 뜻이고,
그 사실이 주장의 한계로 따라간다 (PRD §7-1 '조건 누락' 실패 방지).

### 6-2. 두 컷 모두 드롭이 아니라 `rejected[]` 행이다

| 컷 | 기준 | 현 스냅샷 효과 |
|---|---|---|
| 리센시 | 스냅샷 최신 월(2026-08) 기준 24개월 = `2024-09`~ | 22,194건 통과 (**88.78%**) |
| 리뉴얼 | 세대 구간 밖 (`separate` 인 계보만) | 전 계보 `unobserved` → 실효 0 |

`today` 기준 롤링 윈도우는 재현성(§5-2 "생성물에 시각을 기록하지 않는다")과 충돌하므로 쓰지
않는다. 새 수집분이 들어오면 `assert_snapshot_current()` 가 에러를 내
`policy.SNAPSHOT_LATEST_MONTH` 를 다시 정하게 만든다.

**리뷰를 지우지 않는다.** 통과한 것만 남기면 정밀도는 측정되지만 재현율은 영영 측정되지
않는다. 근거는 [`docs/DECISION_PER172_RENEWAL_AND_RECENCY.md`](DECISION_PER172_RENEWAL_AND_RECENCY.md).

### 6-3. PII 최소화 결과 (PER-170)

`userName`·`profileImageUrl` 은 올리브영 프로덕션 PDP 에서 리뷰만 열면 누구나 보는 값이고
개인정보법상 제약이 없음을 확인했다(2026-09-03). **따라서 드롭 여부는 PII 위험이 아니라
필요성으로 정했다.**

| 필드 | 처리 | 이유 |
|---|---|---|
| `userName` | **원문 유지** (raw + `derived.authorKey`) | 중복 게이트에 필요. 없으면 카운트가 22.4% 부푼다 |
| `profileImageUrl` | **드롭** | 조합키로 쓰면 식별력 증가분 0, 작성자 12명이 거짓 분리된다 |
| `reviewImages` | **드롭** | v5 텍스트 파이프라인 미사용 |

**최소화의 결과: v5 레코드에 남는 개인 관련 값은 `raw.userName` 과 `derived.authorKey` 둘뿐이다.**
`authorKey` 는 `NFC(userName)` 이고, 25K 에서 고유 작성자 13,876명 · NFC 정규화가 필요한 이름 0건이다.

`profileImageUrl` 은 드롭하되 **감사 리포트에서는 계속 본다** — 한 이름에 서로 다른 URL 이
2개 이상 나타나면 동명이인 분리 후보로 플래그한다(자동 분리는 하지 않는다, 현 스냅샷 0건).
근거는 [`docs/DECISION_PER170_AUTHOR_IDENTIFIER.md`](DECISION_PER170_AUTHOR_IDENTIFIER.md).

---

## 7. 완료 조건 — 위반은 실제로 에러가 난다

"조용한 폴백 금지"는 문서에 적어두면 지켜지지 않는다. 위반 클래스마다 실제로 입력을 넣어
보고, 하나라도 통과하면 측정 스크립트가 **종료 코드 1** 로 실패한다.

`eval/reports/input_contract_per176.json` → `enforcement`: **12/12 위반 클래스가 에러**.

| 위반 클래스 | 예시 입력 | 예외 | 조 |
|---|---|---|---|
| 필수 필드 결측 | `content: null` | `ContractError` | §3-1 |
| 필수 필드 공백 | `userName: "   "` | `ContractError` | §3-1 |
| `reviewId` 타입 | `reviewId: "1"` | `ContractError` | §3-2 |
| `reviewId` 비양수 | `reviewId: 0` | `ContractError` | §3-2 |
| `reviewId` 중복 | 같은 id 2행 | `ContractError` | §3-2 |
| `rating` 범위 밖 | `rating: 7` | `ContractError` | §3-2 |
| `rating` 타입 | `rating: "4"` | `ContractError` | §3-2 |
| 날짜 파싱 불가 | `reviewDate: "어제"` | `ContractError` | §3-3 |
| 조건 코드 도메인 밖 | `skinType: "A99"` | `ContractError` | §4 |
| 조건 코드가 라벨 | `skinType: "건성"` | `ContractError` | §4 |
| 조건축 혼용 | `skinType: "B03"` | `ContractError` | §4 |
| 다중 조건축이 문자열 | `skinTrouble: "C05"` | `ContractError` | §4 |
| 모르는 필드 | `usagePeriod: "3개월"` | `ContractError` | §3-4 |
| 미등록 `goodsNo` | `A000000999999` | `UnknownGoodsNoError` | §1 |
| 스냅샷이 정책보다 새로움 | `2026-09` 리뷰 | `PolicyError` | §6-2 |

`ContractError` 는 `ValueError` 를 상속한다 — 호출부가 이미 `ValueError` 로 잡고 있어도 계약
위반이 빠져나가지 않게 하려는 것이다. 코드북 위반(`UnknownConditionCodeError`)은 입수
경계에서 `ContractError` 로 감싸 `reviewId` 문맥을 붙인다.

**계약을 켜도 25K 산출물은 바이트가 같다.** 계약은 현행 스냅샷의 결과를 바꾸지 않고,
무엇을 거부하는지만 바꾼다 (`pipeline/ingest.py --check` 통과, `eval/reports/v5_ingest_profile.json` 무변경).

이 표는 [`pipeline/test_contract.py`](../pipeline/test_contract.py) 33케이스로 고정돼 있고
`scripts/verify.sh` 가 병합 전에 돌린다.

---

## 8. 재현

```bash
python3 eval/measure_input_contract.py     # → eval/reports/input_contract_per176.json
python3 pipeline/ingest.py                 # → data/intermediate/v5_reviews*.{jsonl,json}
python3 pipeline/ingest.py --check         # 재실행 바이트 일치
python3 -m unittest discover -s pipeline -p 'test_*.py'
bash scripts/verify.sh                     # 병합 게이트
```

산출물 `data/intermediate/v5_reviews_meta.json` 의 `contract` 블록에 그 실행이 실제로 강제한
규칙과 코드북 sha256 이 함께 남는다 — 산출물만 보고 어느 계약 아래 나온 것인지 알 수 있다.

---

## 9. 후속

| 항목 | 어디로 |
|---|---|
| `rejected[]` 사유 코드 집계 (`recency_cut` / `renewal_cut`) | PER-200 (커버리지 측정) |
| `limitation: renewal_unobserved` 를 주장 스키마에 노출 | PER-189~195 (claims) |
| `reviewerRank`·`isTopReviewer` 드롭 확정 | PER-174 (신뢰도 사전 점수) |
| `skinTone` 을 조건축으로 승격할지 | 열어 둔다. 승격하려면 셀 희소화를 먼저 측정한다 |
| 새 수집분이 들어왔을 때의 절차 | `SNAPSHOT_LATEST_MONTH` 갱신 → 컷 비용 재측정 → 카탈로그 재생성 |
