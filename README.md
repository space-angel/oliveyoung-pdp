# oliveyoung-pdp

올리브영 PDP 리뷰에서 **구매 결정을 막는 불확실성**을 리뷰에 근거해 하나씩 해소하는 파이프라인(v5)과, 그 입력을 만드는 크롤러.

Linear: [올리브영 PDP 개선 (PRD 기반)](https://linear.app/banjax/project/올리브영-pdp-개선-prd-기반-e9faaf419498) · 실질 데드라인 **09-16**(판정된 수치가 나와야 하는 시점) · 문서 지도는 맨 아래

---

## 1. 요약이 아니라 결정 지원이다

같은 리뷰 데이터를 쓰지만 최적화 대상이 반대다.

| | 요약 Task | **결정 지원 Task (이 프로젝트)** |
|---|---|---|
| 성공 | 원문을 잘 압축했는가 | 결정에 필요한 정보를 줬는가 |
| 실패 | 정보 누락 | **없는 확신을 만들어냄** |
| 평가 단위 | 요약문 전체 | **주장 1개** |
| 근거가 없을 때 | 짧게 요약 | **말하지 않는다** |

요약은 정보를 잃는 게 실패지만, 결정 지원은 **근거 없는 확신을 주는 게** 실패다. 그래서 이 저장소의 모든 설계는 "덜 보여주고 틀리지 않기"로 기울어 있다.

### 가치 단위 = 주장–근거 쌍

무엇을 하나 만들어서 평가할지 정하는 것이 첫 결정이었다.

| 후보 | 문제 |
|---|---|
| 제품 요약문 1개 | 틀려도 어디가 틀렸는지 모른다 → 평가 불가 |
| 태그·칩 목록 | 근거가 안 붙는다 → "촉촉함"이 왜 나왔는지 추적 불가 |
| **주장 + 그 주장을 뒷받침하는 리뷰 + 라벨** | **검증 가능한 최소 단위. 하나씩 켜고 끌 수 있다** |

단위를 정하면 생성·필터링·평가·UI가 같은 단위로 정렬된다.

### 성공은 사람의 행동으로 정의한다

모델 지표(질문 품질 점수)는 부지표다. 주지표는 **이탈 없이 결정까지 간 비율**과 **질문을 펼친 뒤 근거 리뷰까지 읽은 비율**이다. 다만 이 프로젝트는 미출시 PoC라 트래픽이 0이므로 **직접 측정이 불가능하다** — 사용자 세션 기반 대리 측정으로 대체하고, 그 사실을 문서에 남긴다(#8, PER-205~208).

---

## 2. 설계 원칙 4개

**① 생성하지 말고 선별한다.** LLM은 리뷰가 이미 말한 것을 재배치할 뿐이다. 리뷰에 없는 정보가 출력에 나타나면 기능이 아니라 사고다. LLM의 역할을 판단이 아니라 번역으로 좁힌다.

**② 모든 문장은 출처로 되돌아갈 수 있어야 한다.** 사후에 출처를 붙이는 게 아니라, **출처 없이는 문장이 만들어질 수 없게** 구조에서 강제한다 — `evidence[]`가 비면 주장 객체 자체가 생성되지 않는다.

**③ 자연어 옆에는 항상 범주값을 둔다.** 문자열만 오면 "별로다"밖에 말할 수 없다. 같은 호출에서 방향성·유형·실패사유를 함께 뱉게 하면 사후 채점 없이 정밀도·재현율이 계산된다.

**④ 자율성은 기능이 아니라 비용이다.** 고객에게 바로 나가는 정보에서는 재현 가능한 경로가 탐색 능력보다 가치 있다. 그래서 에이전트가 아니라 **절차형 그래프**다 — 실행 경로 고정, 비용·지연 예측 가능, 실패 지점을 단계별로 특정.

---

## 3. 무엇을 LLM에 시키고 무엇을 코드로 내리는가

> **입력이 같으면 출력이 같아야 하는 지점에는 LLM을 두지 않는다.**

| 판단 | 담당 | 이유 |
|---|---|---|
| 리뷰 문장의 의미 파악 | LLM | 비정형 텍스트, 대체 불가 |
| 표현 정규화·동의어 병합 | LLM | 언어 문제 |
| 자연어 질문·답 작성 | LLM | 언어 문제 |
| 근거 방향(긍/부정) | LLM | 문맥 필요. **단, 별점과 교차 검증** |
| 같은 제품인가 | **규칙** | 카탈로그의 책임 (`pipeline/catalog.py`) |
| 중복 리뷰인가 | **규칙 + 임베딩** | 결정론적으로 가능 |
| 몇 건 이상이어야 하는가 | **규칙** | 임계값은 제품 결정이지 모델 판단이 아니다 |
| 무엇을 상위 N개로 보여줄까 | **규칙** | 랭킹 기준은 명시적이어야 재현·설명 가능 (`pipeline/trust.py`) |

---

## 4. 파이프라인 — 단계와 현재 상태

```
catalog → ingest → tag → gates → claims → judge
```

`pipeline/run_v5.py --list`가 정본이다. **미구현 단계를 부르면 조용히 건너뛰지 않고 담당 이슈 번호와 함께 에러**를 낸다.

| 단계 | 하는 일 | 상태 | 이슈 |
|---|---|---|---|
| `catalog` | `goodsNo` → `productId`. 제품 동일성 확정 | **구현** | PER-171 |
| `ingest` | 25K를 원문/조건/파생 3층으로 적재. LLM 없음 | **구현** | PER-173 |
| `tag` | 전수 aspect/polarity 태깅 | **구현** — 25,000건 실행 완료 (`zai.glm-4.7`, 태그 38,251개) | PER-175 |
| `gates` | 동일성 → 중복 → 방향성 → 충분성 4게이트 + `rejected[]` | **구현** | PER-182~188 |
| `claims` | 주장 생성 + 인용 원문 부분문자열 강제 + 스키마 검증 | 스키마·검증기 **구현** / 생성기 미구현 | PER-189~195 |
| `judge` | 루브릭 judge(생성과 다른 모델) + 전수 평가 | 미구현 | PER-196~201 |

### 근거 선별 — 게이트 4개가 하는 일

```
[Recall]  주제별로 관련 리뷰를 넓게 수집     → 놓치지 않기
   ↓
[게이트]  동일성 → 중복 → 방향성 → 충분성   → 틀린 근거 제거
   ↓
[정렬]    무엇→조건→근거→방향→충분성 순서   → 판단 순서 강제 (구현)
```

| 게이트 | 판정 | 실측된 필요성 |
|---|---|---|
| 1 동일성 **(구현)** | 정규화 제품 ID 일치, 옵션 질문이면 같은 옵션만, 리뉴얼 세대 + 리센시 컷 | `goodsNo` 153개 → `productId` 53개(계보 50). 옵션 문자열 798개는 색상 **452개**라, 정규화 없이는 색상 질문의 근거가 잘게 쪼개진다 |
| 2 중복 **(구현)** | 본문 해시 + **동일 작성자 1표** + 의미 유사 클러스터(PER-184 — **모델만 선정, 미적용**) | 안 걸면 카운트가 **22.4% 부푼다**. 25,000건은 독립 근거 **19,392건**이고, 해시가 잡는 몫은 제거량의 12.4%뿐이다 |
| 3 방향성 **(구현)** | polarity를 `(리뷰 × 주제)` 단위로. 별점과 교차 검증, 불일치는 플래그. **탈락 없음** | 한 리뷰가 "발색은 좋은데 지속력은 별로"라고 말한다. N≥8 셀 395개 중 **343개(86.8%)가 `혼재`**(반대 1명도 혼재 — PER-178 §9)이고 그 중 **202개는 소수가 태거 잡음 밖**이다. 별점 불일치 2,289건의 **56.4%가 "좋은데 X는 별로"** — 별점으로 방향을 정하면 그만큼이 사라진다 |
| 4 충분성 **(구현)** | `U ≥ N_min` **AND** `U/D ≥ R_min` **AND** `S ≥ S_min` — 낮은 쪽이 아니라 높은 쪽. 분모 D 는 **주제를 언급한 작성자**다 | 전수 태그 기준 (셀×aspect) 후보 7,689건 중 통과 2,420건. **무조건부 71.4% vs 조건부 22.9%** — 조건부가 1/3 비율로 살아남는다 |

**반대 근거는 버리지 않는다.** 방향이 갈리면 `혼재`로 표시하고 양쪽 비율을 함께 낸다 — 리뷰가 갈린다는 사실 자체가 구매자에게 유용하다. 반대가 **1명이어도** 다수 방향으로 뭉개지 않고 `single_dissent` 한계로 남긴다 (골든셋 26건 중 8건이 이 경우다).

**침묵은 근거가 아니다.** 주제를 말하지 않은 작성자는 긍정도 부정도 아니므로 비율의 분모에 넣지 않고 `silentAuthors`로 따로 센다 — "40명 중 3명이 언급"을 "40명 중 3명만 불만"으로 바꾸지 않는다.

**살아남은 근거는 순서대로 놓는다 — 5단 배치 (구현).** 게이트 통과분을 `무엇 → 조건 → 근거 → 방향 → 충분성` 자료 구조로 배치한다. 순서를 정하지 않으면 실제로 정해지는 것은 태거의 출력 순서다. 인용 정렬은 신뢰도(PER-174) 내림차순이고, 입력을 20회 섞어도 payload 가 바이트 동일하다. **상한을 걸어도 방향을 지우지 않는다** — `p019 × 트러블/자극`(긍정 309 · 부정 6)에서 신뢰도 상위 8건은 전부 긍정이라, 그냥 자르면 4단이 "부정 6명"인데 3단에 부정 인용이 한 줄도 없다. 2단의 조건축은 `skinType`·`skinTrouble` 뿐이고 **`usagePeriod`·계절은 데이터에 없어 기각**했다 — 뺀 이유가 주석이 아니라 출력(`EXCLUDED_AXES`)에 남는다.

**버린 것도 남긴다 — `rejected[]` 원장 (구현).** 통과한 것만 남기면 정밀도는 측정되지만 **재현율은 영영 측정되지 않는다**(PRD §6). 탈락 사유 어휘는 `pipeline/reject_registry.py` 한 곳이 소유하고(사유 11종 · 한계 5종), **미등록 사유로 탈락시키면 행 생성 시점에 에러**다. 원장 12,597행이 입력을 남김없이 설명한다 — 리뷰 17,672 통과 + 7,328 탈락 = 25,000, 주장 2,420 통과 + 5,269 탈락 = 7,689. **게이트3 행은 0이고 그건 누락이 아니라 결정이다**(부정 근거는 틀린 근거가 아니다 — PER-185). 골든셋 claim 을 게이트로 되짚으면 재현율이 나온다: 26건 중 되짚을 수 있는 22건에서 **16건 생성(0.7273)**, **미스 6건은 전부 `게이트4/과소근거`** 다 (`docs/DECISION_PER188_REJECTED_LEDGER.md`, `eval/reports/rejected_ledger_per188.json`).

### 출력 스키마 — 평가 가능성을 구조에 박는다

`claim` 하나가 질문–답 쌍 + `condition` + `direction` + `evidence[]` + `support` + `rejected[]` + `failureReason` + `confidence`다. 설계 의도가 명확한 두 필드:

- **`evidence[]`가 비면 객체 자체가 생성되지 않는다** (원칙 ②)
- **`rejected[]`가 이 스키마의 숨은 핵심이다** — 통과한 것만 남기면 정밀도는 측정되지만 **재현율은 영영 측정되지 않는다.** 버린 리뷰와 사유를 남긴다

`failureReason`이 그대로 노출 필터가 된다 — `null`인 것만 화면에 나간다. 실패 시 기본값은 **아무것도 안 보여주는 것**이다.

---

## 5. 확정된 입력 계약

지금까지의 결정과 근거. 전부 실측 리포트가 딸려 있다. **정본은 [`docs/INPUT_CONTRACT.md`](docs/INPUT_CONTRACT.md)** (PER-176) — 아래 표는 그 요약이다.

| 결정 | 내용 | 근거 |
|---|---|---|
| **집계 단위** | `productId`(50). `goodsNo`(153)는 변형 SKU 혼재라 그룹핑 키로 쓰지 않는다. **미등록 ID는 조용히 폴백하지 않고 에러** | [`docs/PRODUCT_CATALOG.md`](docs/PRODUCT_CATALOG.md) · `eval/reports/product_catalog_coverage.json` |
| **작성자 키** | `NFC(userName)` 원문. 중복 판정 단위는 `(작성자 키, productId)`이고 카운트는 **고유 작성자 수** | [`docs/DECISION_PER170_AUTHOR_IDENTIFIER.md`](docs/DECISION_PER170_AUTHOR_IDENTIFIER.md) · `eval/reports/author_identity_per170.json` |
| **입력 3층** | 원문(무가공) / 조건(세그먼트 축) / 파생(재계산 가능) | [`pipeline/contracts.py`](pipeline/contracts.py) · `eval/reports/v5_ingest_profile.json` |
| **조건축** | `skinType`(단일) · `skinTrouble`(다중) · `option`. `usagePeriod`는 데이터에 없어 제외. **조건 코드는 코드북 도메인 밖이면 에러** — 라벨·축 혼용 포함 | 같음 · `pipeline/codebook.py` |
| **미기재 취급** | "조건 없음"이 아니라 **별도 세그먼트**. `segment`는 절대 null이 아니다 | 같음 (§7-1 '조건 누락' 실패 방지) |
| **계약 강제** | 위반 입력은 조용한 폴백 없이 **에러**. 위반 클래스 12종 전부 확인 | [`docs/INPUT_CONTRACT.md`](docs/INPUT_CONTRACT.md) · `eval/reports/input_contract_per176.json` |
| **스킨 코드북** | A01~A07 · B01~B06 · C01~C13 **26종 DOM 실측**. 라벨은 입수가 아니라 표기 단계에서 붙인다 | `data/input/skin_codebook.json` · `crawler/verify_skin_codebook.py` |
| **리뉴얼** | **별개 제품**(PRD 권장안 채택). 세대 경계 키는 `goodsNo`가 아니라 `(goodsNo, reviewDate)`. 외부 근거로 **5개 계보 확정**(세대 분할 3 + `single` 2), 나머지 45개는 `unobserved` **명시** — `null`은 허용하지 않는다 | [`docs/DECISION_PER172_RENEWAL_AND_RECENCY.md`](docs/DECISION_PER172_RENEWAL_AND_RECENCY.md) · `eval/reports/renewal_recency_per172.json` |
| **리센시 컷** | 스냅샷 최신 월(2026-08) 기준 **24개월 = `2024-09`~**. `today` 롤링은 재현성과 충돌해 쓰지 않는다 | 같음 |
| **태그 단위** | 리뷰가 아니라 **`(리뷰 × aspect)`**. 한 리뷰 안 방향 갈림 20.5% · 별점 불일치 12.9%라 별점을 방향 대리값으로 쓰지 않는다 | [`docs/DECISION_PER175_TAGGING_CONTRACT.md`](docs/DECISION_PER175_TAGGING_CONTRACT.md) · `eval/reports/v5_tag_pilot.md` |
| **인용 정규화** | 원문 부분문자열이되 **보이지 않는 문자만 접는다**(CRLF 46.3% · 공백 변종 1.1%). 공백 전체 squeeze 금지 | 같음 |
| **신뢰도 사전 점수** | **필터가 아니라 가중치**. 채택 신호는 `contentLength`(0.6)·`uniqueContent`(0.4)·`onTopic`(0.5, 태깅 후). `usefulPoint`는 올리브영의 정렬 점수라 **쓰지 않는다**(`recommendCount`와 순위상관 0.008) | [`docs/DECISION_PER174_TRUST_PRIOR.md`](docs/DECISION_PER174_TRUST_PRIOR.md) · `eval/reports/trust_signals_per174.json` |

### 조건부 진실 — 이 도메인의 핵심 설계

"촉촉해요"는 건성에게 참이고 지성에게 거짓이다. "발색이 예뻐요"는 21호에서 참이고 23호에서 거짓이다. 리뷰의 주장은 무조건 참이 아니라 **조건부로 참**이다.

**모든 주장은 `조건 → 결과` 형태로 정규화한다.**

- ❌ "촉촉함이 오래간다"
- ⭕ "건성 기준, 6시간 이상 유지된다 (건성 리뷰 23건 중 19건)"

UI 정리가 아니라 정확도 문제다. 조건을 붙이면 상충하는 리뷰가 모순이 아니라 **세그먼트 차이**로 정리되고, 상충 때문에 버려야 했던 근거가 살아난다.

**단, 이게 실제로 몇 개 살아남는지는 가정이 아니라 측정 대상이다.** 조건 기재율이 절반 수준이고(아래) aspect까지 교차하면 셀이 N_min 경계에 걸린다 → 커버리지 측정을 별도 이슈로 세웠다(PER-200).

---

## 6. 데이터 현황 (25K 스냅샷)

`eval/reports/v5_ingest_profile.json` — `pipeline/ingest.py` 재실행으로 재현된다.

| 항목 | 값 | 시사점 |
|---|---|---|
| 리뷰 / 제품 | 25,000 / **53 `productId`** (계보 50) | `goodsNo` 153개를 카탈로그가 50계보로 묶고, 그중 3개를 리뉴얼 세대로 나눈다 (미해결 0) |
| 조건 기재율 | `skinType` 57.1% · `skinTrouble` 53.9% · `option` 68.1% | 절반은 미기재 세그먼트로 간다 |
| 중복 | 고유 `(작성자, 제품)` 19,401 → **초과 표 5,599건 (22.4%)**. 게이트2 통과 = 독립 근거 **19,392건** | 게이트2 없이는 "리뷰 N건" 숫자가 거짓. PER-170 측정치(19,389)와 12 차이는 세대 분할분 |
| 동일 본문 | 587그룹 | 템플릿·복붙. 본문 해시로 잡히는 건 초과 표의 12.1%뿐 |
| `productId×skinType` 셀 | 407개 중 N≥8이 314 → **작성자 dedup 후 297** | 리센시 컷(2024-09) 적용 후에는 **284** |
| 평점 분포 | 5점 85%, 1~2점 **407건(1.6%)** | 부정 신호가 희소 클래스 → 층화 표본 필수 |
| 수집 기간 | 2018.12 ~ 2026.08 (2026년이 74.6%) | 24개월 컷 확정 — 잔존 88.8%, N≥8 셀 294→284 (PER-172) |
| 리뉴얼 신호 | 멀티 `goodsNo` 계보 36개 중 교체형 **1개** / 본문 언급 303건(1.21%) | `goodsNo` 교체는 자동 신호가 아니다. 세대는 사람이 외부 근거로 확정한다 |
| 리뉴얼 컷 순증분 | 리센시 24개월 기준 **7건** (컷 없으면 170건) | 리센시 컷이 이전 세대의 96%를 이미 흡수한다 — 세대 확정은 컷 값에 대한 보험이다 |
| 신뢰도 사전 점수 | 중앙 0.623 · 서로 다른 점수 **757개** | 25,000건에 757개뿐이라 동점이 흔하다 → 정렬에 결정적 tiebreak 필수 (PER-174) |
| 좋아요 신호 | `usefulPoint`↔`recommendCount` 순위상관 **0.008** | 둘은 같은 것이 아니다. `usefulPoint`는 크롤러의 수집 정렬 키(153/153 goodsNo 단조)라 쓰지 않는다 |

---

## 7. 시작하기

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env        # ANTHROPIC_API_KEY 입력

.venv/bin/python pipeline/run_v5.py --list   # 단계와 구현 상태
.venv/bin/python pipeline/run_v5.py          # 구현된 단계까지 (catalog → ingest → tag → gates)
                                             # tag 는 태깅을 다시 돌리지 않는다 — 정본이 있는지만 확인한다
bash scripts/verify.sh                       # 계약 테스트 + 데이터 없이 도는 재현 확인
bash scripts/verify.sh --full                # + 스냅샷 재현 확인 (병합 게이트는 이쪽)
```

```bash
# 크롤러 (별도 venv 권장 — 브라우저 포함으로 무겁다)
python3 -m venv crawler/.venv
crawler/.venv/bin/pip install -r crawler/requirements.txt
crawler/.venv/bin/python crawler/oliveyoung_crawler.py --products crawler/products_50.json --target 500
```

### 제품 링크 하나로 돌려보기 (PER-194)

```bash
URL="https://www.oliveyoung.co.kr/store/goods/getGoodsDetail.do?goodsNo=A000000211119"

.venv/bin/python pipeline/run_url.py --url "$URL" --check   # 계획과 비용만 본다
.venv/bin/python pipeline/run_url.py --url "$URL" --yes     # 실제로 돌린다
```

crawl → catalog → ingest → tag → gates → claims → report 를 한 번에 돈다.
전부 `data/runs/<runId>/` 안이고 **정본은 읽지도 쓰지도 않는다** — `Workspace.assert_isolated()`
가 크롤 전에 검사한다. `data/runs/` 는 gitignore 다.

| 알아둘 것 | |
|---|---|
| 비용 | `crawl` 은 올리브영에 실제 요청, `tag`·`claims` 는 LLM 실비. `--yes` 없이는 안 돈다 |
| 카탈로그 | **수집분에서** 만든다. 변형 SKU(제품당 평균 4종)를 빠뜨리면 입수가 미등록 `goodsNo` 에러로 멈춘다 |
| 리센시 | 오늘 수집분은 정본 기준월보다 새롭다. 런에서만 데이터에서 파생하고 `derived: true` 를 남긴다 |
| **재현율** | **재지 않는다.** 이 제품에는 사람이 만든 정답지가 없다 — `run.json` 의 `evaluation` 에 사유와 함께 적힌다 |

무료 단계만 따로 돌릴 수도 있다 — `--steps catalog,ingest,gates,report`.

### 구조

```
pipeline/    v5 — 작업 대상
  contracts.py             입력 계약 3층
  catalog.py               goodsNo → productId. 미등록은 에러. 세대는 (goodsNo, 날짜)로 가른다
  policy.py                리뉴얼 취급 · 리센시 컷 (PER-172) · 충분성 임계값 (PER-186). 게이트가 소비한다
  gates.py                 게이트1 동일성 (PER-182) · 게이트2 중복 (PER-183). 탈락은 드롭이 아니라 rejected[] 행
  polarity.py              게이트3 방향성 (PER-185). 유일하게 탈락시키지 않는 게이트 — 판정과 플래그만 낸다
  sufficiency.py           게이트4 충분성 (PER-186). 판정 단위가 리뷰가 아니라 주장이라 모듈이 따로다
  context_layout.py        5단 배치 (PER-187). 판정을 다시 하지 않는다 — 순서대로 놓고 게이트3·4가 같은 셀인지 대조한다
  reject_registry.py       탈락 사유 어휘의 정본 (PER-188). 게이트3은 사유 0개 — 누락이 아니라 결정이다
  ledger.py                통합 rejected[] 원장 + 골든셋 역추적 (PER-188). 재현율의 유일한 단서
  claim_contract.py        claim 출력 스키마 + 검증기 (PER-189). evidence 가 비면 객체가 안 만들어진다
  run_meta.py              재현 meta 계약 (PER-193). 모르는 값을 0 으로 깔지 않고 unavailable + 사유로 남긴다
  quote_gate.py            인용 원문성 (PER-190). 사후 지표가 아니라 생성 게이트 — 폐기 → 대체 → 주장 폐기
  condition_render.py      조건 → 결과 표기 (PER-192). 저장은 코드, 라벨은 여기서만 붙인다
  embedding_contract.py    임베딩 버전 계약 (PER-184). 모델·어휘 버전은 묶여서만 움직인다 — 부분 교체는 에러
  embedding_config.json    후보·선정 모델·revision — 코드가 아니라 여기서 고친다
  option_norm.py           옵션 → 색상 키 정규화 (PER-182). LLM 없음
  option_markers.json      판촉 어휘 — 코드가 아니라 여기서 고친다
  build_product_catalog.py 카탈로그 생성기 (--check 로 재현 확인)
  ingest.py                25K → v5 레코드 (LLM 없음, 재실행 일치). assert_matches_ingest 가
                           측정 스크립트의 기반이 이 산출물과 같은지 대조한다 — 다르면 에러
  trust.py                 신뢰도 사전 점수 (PER-174). 필터가 아니라 가중치
  trust_weights.json       신호별 가중치 — 코드가 아니라 여기서 고친다
  run_v5.py                단계 레지스트리
  workspace.py             경로 배치 (PER-194). canonical() 은 기존 상수와 같고, for_run() 은 data/runs/ 로 격리
  run_url.py               제품 링크 하나로 크롤 → claim 까지. 비용 드는 단계는 --yes 를 요구한다
  test_*.py                계약 테스트 (catalog·policy·ingest·tag·trust·option_norm·gates·polarity·sufficiency)
legacy/v4/   v4 동결 — 비교 기준선. 고치지 않는다
crawler/     올리브영 cursor API 크롤러 (상품당 최대 500건)
eval/        평가 스크립트 + 리포트 (커밋됨 — 수치의 1차 근거)
scripts/     verify.sh (병합 게이트)
data/
  input/         스냅샷 · product_catalog.json · skin_codebook.json (커밋)
  intermediate/  중간 산출물 (gitignore) — v4는 step*_*, v5는 v5_*
  output/        concerns_*.json (커밋)
```

---

## 8. 재현성 — 개선을 귀속시키기 위한 규칙

PRD §5-2가 "같은 입력 → 같은 출력"을 요구한다. 이게 안 되면 점수가 좋아져도 **무엇을 바꿔서 좋아졌는지 알 수 없다.**

- 각 단계는 **순수 함수**. 샘플링에는 시드를 고정하고 출력에 기록한다
- 생성물에 **시각을 기록하지 않고** 입력 sha256을 기록한다 → 재실행 시 바이트가 같다
- 모든 출력의 `meta`에 프롬프트 버전·모델 ID·임계값·시드·입력 스냅샷 해시를 남긴다
- 완료 조건("에러를 낸다")은 **테스트로 고정**한다 — `python3 -m unittest discover -s pipeline -p 'test_*.py'`
- 병합 게이트 `bash scripts/verify.sh --full`을 병합 전·후 양쪽에서 돌린다. 인자 없이 돌리면 `data/intermediate/`가 필요한 검사 10종을 건너뛴다 — 클론 직후용이다
- 브랜치·커밋 규약은 [`docs/GIT_WORKFLOW.md`](docs/GIT_WORKFLOW.md) — 커밋에 `기각:`/`원인:`/`재발방지:` 트레일러를 남겨 **하지 않기로 한 판단도 기록한다**

---

## 9. 평가 설계 — 골든셋이 생성기보다 먼저다

```
① 골든셋 100건 (사람)   정답 라벨. 가장 느리고 가장 믿을 만하다
      ↓ 보정
② 자동 루브릭 (LLM)     전수. 골든셋과의 일치율을 먼저 검증한 뒤 사용
      ↓ 검증
③ 행동 지표 (사용자)    최종 판단 기준 (이 프로젝트는 대리 측정)
```

**순서가 요점이다.** judge를 먼저 만들고 골든셋을 나중에 만들면 judge가 틀렸을 때 알아챌 방법이 없다. judge는 **생성 모델과 다른 모델**을 쓴다 — 같은 모델이 자기 출력을 채점하면 편향을 공유한다.

**실패 유형 택소노미를 지표보다 먼저 정의한다.** 치명 2종(근거 없는 주장 / 잘못된 귀속)은 비율이 아니라 **0건 목표**로 관리한다.

**False Positive를 최우선으로 본다.** 놓친 주장(FN)은 사용자가 리뷰를 직접 읽으면 원래 상태로 돌아갈 뿐이지만, 틀린 주장(FP)은 잘못된 확신으로 구매하게 만들고 **되돌릴 수 없다.** 임계값은 정밀도 쪽으로 기울인다.

---

## 10. 만들지 않을 것

명시적으로 정해두지 않으면 슬금슬금 들어온다.

| 비목표 | 이유 |
|---|---|
| 리뷰에 없는 정보 보강 (성분 DB·전문가 의견) | 출처 추적이 깨진다 |
| 제품 간 순위 매기기 | 리뷰로 뒷받침되지 않는다 |
| 개인화 추천 | 지금 태스크는 "이 제품을 설명하기"다 |
| 감정 점수·종합 별점 재계산 | 이미 별점이 있다. 중복 지표는 혼란만 만든다 |
| 부정 리뷰 숨기기 | 신뢰를 깨는 가장 빠른 방법 |
| A/B 테스트 · 하드 샘플 저장소 · 퓨샷 리트리빙 | 트래픽 0, 운영 실패 소스 없음 → 이 프로젝트 범위 밖 |
| `usagePeriod` 조건축 | 데이터에 필드가 없다 |

---

## 11. v4 베이스라인 (비교 기준선)

v4는 `legacy/v4/`로 **동결**했다. v5는 v4 코드를 수정해 쓰지 않고 새로 쓰며, v4는 개선 폭을 재는 기준으로만 남는다(PER-201).

v4 평가 결과([`eval/reports/eval_report_v4.md`](eval/reports/eval_report_v4.md), 5제품·리뷰 499건·concern 30개)는 **3개 조건 PASS / 2개 FAIL**이다 — 카테고리 분포 1/5, 리스크 질문 3/5가 FAIL이었고 **"종합 합격" 산식에 그 2개가 빠져 있었다.** 인용 정확도 88.8%(127/143)이고 불일치 16건은 오탈자가 아니라 파라프레이즈였다(원문에 없는 수치를 생성한 사례 포함).

**v4를 "PASS"로 서술하면 방어할 수 없다.** 두 FAIL은 `skin_type_hint`가 step2에서 유실되는 단일 구조적 갭에서 나왔고, v5의 조건축(`condition`)이 그 갭을 정면으로 메운다.

v5가 넘어야 하는 선: **인용 정확도 100%** (생성 시점에 원문 부분문자열 강제), 카테고리 분포·리스크 질문을 합격 산식에 포함, 치명 실패 2종 0건.

---

## 12. 남은 미결

| 미결 | 내용 | 이슈 |
|---|---|---|
| 부정 신호 표본 | 1~2점 407건(1.6%)으로 "위험 신호 재현율"을 어떻게 측정할지. 아직 이슈 미할당 — FP 임계값 튜닝(PER-199)과 함께 정한다 | `docs/V5_INPUTS_AND_LEGACY_AUDIT.md` §5-3 |
| 리뉴얼 세대 (나머지 45계보) | 5개는 외부 근거로 확정했다. 나머지는 `unobserved`이고, 현행 24개월 컷에서 순증분이 작아 우선순위는 낮다 — **리센시 컷을 완화하려면 먼저 확정해야 한다**(36개월에서 순증분 7→47) | PER-172 → PER-182 |

---

## 문서 지도

| 문서 | 내용 |
|---|---|
| [`docs/INPUT_CONTRACT.md`](docs/INPUT_CONTRACT.md) | **입력 계약 정본** — 집계 단위 · 유효성 · 조건축 · 컷 · PII 와 그 강제 지점 |
| [`docs/GIT_WORKFLOW.md`](docs/GIT_WORKFLOW.md) | 브랜치 전략 · 커밋 규약 · 병합 게이트 |
| [`docs/V5_SPRINT_PLAN.md`](docs/V5_SPRINT_PLAN.md) | 마일스톤 9개 / 이슈 42개, 비용·커버리지 실측, 스코프 가드 |
| [`docs/PRODUCT_CATALOG.md`](docs/PRODUCT_CATALOG.md) | 제품 동일성 레이어 규칙·운영 절차 |
| [`docs/DECISION_PER170_AUTHOR_IDENTIFIER.md`](docs/DECISION_PER170_AUTHOR_IDENTIFIER.md) | 작성자 식별자 결정과 근거 |
| [`docs/DECISION_PER172_RENEWAL_AND_RECENCY.md`](docs/DECISION_PER172_RENEWAL_AND_RECENCY.md) | 리뉴얼 취급 · 리센시 컷 결정과 근거 |
| [`docs/DECISION_PER175_TAGGING_CONTRACT.md`](docs/DECISION_PER175_TAGGING_CONTRACT.md) | 태깅 계약 — 태그 단위 · 힌트 · 인용 정규화 · 택소노미 동결 |
| [`docs/DECISION_PER174_TRUST_PRIOR.md`](docs/DECISION_PER174_TRUST_PRIOR.md) | 신뢰도 사전 점수 — 6개 신호 실측 · 가중치 근거 · `usefulPoint` 기각 |
| [`docs/DECISION_PER182_OPTION_IDENTITY.md`](docs/DECISION_PER182_OPTION_IDENTITY.md) | 게이트1 동일성 — 옵션 정규화 규칙 · 과대병합 회귀 사례 · 컷 비용 |
| [`docs/DECISION_PER183_DUPLICATE_GATE.md`](docs/DECISION_PER183_DUPLICATE_GATE.md) | 게이트2 중복 — 두 축이 대체하지 않는다는 실측 · 판정/게이트 순서 · `independentReviews` 정의 |
| [`docs/DECISION_PER185_POLARITY_GATE.md`](docs/DECISION_PER185_POLARITY_GATE.md) | 게이트3 방향성 — 탈락 없는 게이트 · 별점 교차검증 3층 · 방향 정의의 소유자 · 태거 잡음 주석 · v4 2건 재확인 |
| [`docs/DECISION_PER186_SUFFICIENCY.md`](docs/DECISION_PER186_SUFFICIENCY.md) | 게이트4 충분성 — 세 조건 AND · 분모는 언급자(D) · 소수 의견 정책 · 임계값 민감도 |
| [`docs/DECISION_PER187_CONTEXT_LAYOUT.md`](docs/DECISION_PER187_CONTEXT_LAYOUT.md) | 5단 배치 — 문구가 아니라 자료의 순서 · 사용기간·계절 기각 · 상한이 방향을 지우지 않는다 |
| [`docs/DECISION_PER188_REJECTED_LEDGER.md`](docs/DECISION_PER188_REJECTED_LEDGER.md) | `rejected[]` 원장 — 사유 레지스트리 · 게이트3 방향불일치 기각 · 골든셋 역추적으로 재현율 |
| [`docs/DECISION_PER184_EMBEDDING_MODEL.md`](docs/DECISION_PER184_EMBEDDING_MODEL.md) | 로컬 한국어 임베딩 — 후보 4종 실측 · KURE-v1 선정 · 모델/어휘 버전 동반 이동 계약 |
| [`docs/DECISION_PER189_CLAIM_SCHEMA.md`](docs/DECISION_PER189_CLAIM_SCHEMA.md) | claim 스키마 — 근거 없으면 객체 미생성 · 모델/코드 필드 분리 · failureReason 이 노출 필터 |
| [`docs/DECISION_PER193_REPRODUCIBILITY_META.md`](docs/DECISION_PER193_REPRODUCIBILITY_META.md) | 재현 meta — 귀속을 위한 기록 · `unavailable` 은 사유와 함께 · 현재 충족 0/33 |
| [`docs/DECISION_PER190_QUOTE_FIDELITY.md`](docs/DECISION_PER190_QUOTE_FIDELITY.md) | 인용 원문성 — 생성 게이트 · v4 88.8% 재현 · 우리 규칙 86.7% · v4 §5 정정 1건 |
| [`docs/DECISION_PER192_CONDITION_NORMALIZATION.md`](docs/DECISION_PER192_CONDITION_NORMALIZATION.md) | 조건 정규화 — 라벨 저장 기각 · 갈린 셀 13개 · **전체로는 우연과 구별 안 됨** · 중복 게이트가 117→34 |
| [`docs/DECISION_PER178_GOLDEN_LABELING_SPEC.md`](docs/DECISION_PER178_GOLDEN_LABELING_SPEC.md) | 주장 골든셋 규격 — 라벨 1건의 모양 · 층화 번들 40개 · 블라인드 절차 · v4 15문항 미이관 근거 |
| [`docs/DECISION_PER178_SILENCE_IS_NOT_EVIDENCE.md`](docs/DECISION_PER178_SILENCE_IS_NOT_EVIDENCE.md) | 침묵은 근거가 아니다 — U+/U−/D/S 보존, 단계별 유지 규칙, 아마존·NN/G·자기선택 편향 사례 |
| [`eval/gold/README.md`](eval/gold/README.md) | 평가 고정물(표본·정답셋) 규칙과 재현 절차 |
| [`docs/V5_INPUTS_AND_LEGACY_AUDIT.md`](docs/V5_INPUTS_AND_LEGACY_AUDIT.md) | 입력 인벤토리 · 25K 프로파일 · 레거시 감사 |
| [`docs/SCRAPLING_MIGRATION_POC.md`](docs/SCRAPLING_MIGRATION_POC.md) | 크롤러 설계 근거 (엔드포인트·size 상한·레이트리밋 실측) |
| [`docs/PDP_EXPERIMENT_CONTEXT.md`](docs/PDP_EXPERIMENT_CONTEXT.md) | PDP 화면 명세 + 스킨 코드북 |
| [`legacy/v4/README.md`](legacy/v4/README.md) | v4 동결 범위와 재실행 방법 |
| `docs/v4/` | v4 하네스 문서 원본 (planning / dev / eval) |
