# 결정: claim 출력 스키마 — 근거 없이는 객체가 없다 (PER-189)

작성: 2026-09-15 · 이슈 [PER-189](https://linear.app/banjax/issue/PER-189) · PRD §6 · 적용 범위: 생성(PER-191~195) · judge(PER-196~198) · 화면(PER-202~204)
강제 지점: [`pipeline/claim_contract.py`](../pipeline/claim_contract.py) · 테스트 [`pipeline/test_claim_contract.py`](../pipeline/test_claim_contract.py) 44건
근거: [`eval/reports/claim_schema_per189.json`](../eval/reports/claim_schema_per189.json) (`eval/measure_claim_schema.py`)
관련: [`DECISION_PER178_SILENCE_IS_NOT_EVIDENCE.md`](DECISION_PER178_SILENCE_IS_NOT_EVIDENCE.md) · [`DECISION_PER185_POLARITY_GATE.md`](DECISION_PER185_POLARITY_GATE.md) · [`DECISION_PER186_SUFFICIENCY.md`](DECISION_PER186_SUFFICIENCY.md) · [`DECISION_PER187_CONTEXT_LAYOUT.md`](DECISION_PER187_CONTEXT_LAYOUT.md) · [`DECISION_PER188_REJECTED_LEDGER.md`](DECISION_PER188_REJECTED_LEDGER.md)

> PRD §6 — *"이 문서에서 가장 중요한 한 가지. 평가를 나중에 붙이려고 하면 안 붙는다. 스키마에 처음부터 넣는다."*

---

## 0. 한 줄 요약

| 결정 | 값 |
|---|---|
| `evidence[]` 가 비면 | **객체가 만들어지지 않는다** — 검증에서 걸러내는 게 아니라 생성자가 막는다 |
| 모델이 쓰는 필드 | `aspect` · `question` · `answer` · `evidence[]` **넷뿐** |
| `support`·`direction`·`confidence` | **코드가 채운다.** 모델 초안에 있으면 에러 |
| `failureReason` | **그대로 노출 필터** — `null` 인 것만 화면에 나간다 |
| `condition` | `skinType` · `skinTrouble` · `option`. **`usagePeriod` 는 항상 `null`** |
| `confidence` | 소수점 점수가 아니라 **단정/완곡 + 사유** |
| 검증 실패 | 폐기 + 로그, **재시도 2회까지** (PRD §5-3) |

실측: 골든셋 라벨 **26건 중 22건**이 이 스키마로 표현된다. 인용 **96개 전부** 원문 부분문자열. 표현 안 되는 4건은 스키마 결함이 아니라 경계다 (§6).

---

## 1. 문제 — 평가는 나중에 안 붙는다

v4 는 질문을 먼저 만들고 근거를 나중에 붙였다. 그래서 인용이 원문 부분문자열인 비율이 **88.8%** 였고, 원문에 없는 수치를 생성한 사례가 있었다(`클리오_03` 의 "14시간"). 이건 프롬프트를 고쳐서 될 문제가 아니다 — **근거 없이도 문장이 존재할 수 있는 구조**였던 것이 원인이다.

## 2. 결정 — `evidence[]` 가 비면 객체가 없다

`Claim.__post_init__` 이 막는다. 검증 함수가 나중에 걸러내는 방식이 **아니다.**

```python
Claim(..., evidence=())        # → ClaimContractError
```

걸러내는 방식이면 "일단 만들고 나중에 출처를 붙인다" 가 가능해지고, 그 순간 PRD §1.2 가 막으려던 것이 그대로 일어난다. 구조에서 막으면 그 경로 자체가 없다.

`test_claim_contract.EvidenceIsStructural` 이 고정한다 — 객체 생성과 직렬화 되읽기 양쪽에서.

## 3. 결정 — 모델이 쓰는 필드와 코드가 채우는 필드를 가른다

```
모델    aspect · question · answer · evidence[{reviewId, quote, stance}]
코드    claimId · productId · condition · claimType · direction · support
        · rejected · failureReasons · confidence · limitations · meta
```

`assert_model_draft()` 가 초안에 코드 몫이 섞이면 에러를 낸다.

**왜 이렇게까지 하나.** `support` 수치(U+/U−/D/S)를 모델이 쓰면 **침묵을 근거로 세지 않는다는 규칙**(PER-178)이 모델의 재량이 된다. "40명 중 3명이 언급" 을 "40명 중 3명만 불만" 으로 쓰는 것을 막는 유일한 방법은 그 수를 코드가 세는 것이다.

`direction` 도 마찬가지다. 방향은 **인용된 근거가 아니라 셀 전체의 고유 작성자**에서 나온다 (PER-185). 모델은 자기가 인용한 3건만 보므로 애초에 계산할 수 없는 값이다. 계산할 수 없는 것을 쓰게 두면 그럴듯한 값이 온다.

`stance` 는 모델의 몫으로 남겼다 — **문장의 절대 긍/부정**이고(골든셋 v2 규칙) 그 문장을 읽어야 정해지기 때문이다. 주장에 대한 찬반이 아니다: "지속력이 별로" 는 주장이 무엇이든 `negative` 다.

## 4. 결정 — `failureReason` 이 그대로 노출 필터다

노출되는 것은 `failureReason` 이 `null` 인 claim 뿐이다 (`Claim.exposable`). 별도의 노출 플래그를 두지 않았다 — 두면 "실패로 표시했는데 화면에는 나가는" 상태가 만들어진다.

유형은 `pipeline/failure_taxonomy.json` 8종(PER-177)이 정본이고 **코드 상수로 들지 않는다.** 복수 실패는 `failureReasons[]` 에 전부 남기고 심각도(critical > high > medium > low) 순으로 정렬하며, `failureReason` 은 그 첫 원소다. 정렬이 어긋나거나 첫 원소가 다르면 에러다 — 대표 사유가 흔들리면 "무엇 때문에 안 나갔나" 가 집계마다 달라진다.

`null` 은 **"평가 완료 후 실패 없음"** 이지 아홉 번째 유형이 아니다.

## 5. 결정 — 조건축과 `confidence`

### 5-1. `usagePeriod` 는 필드를 두되 항상 `null`

이슈 설명문의 `condition{skinType, option, usagePeriod}` 는 두 군데가 낡았다.

| | 설명문 | 실제 |
|---|---|---|
| `skinTrouble` | 없음 | **있다** — 조건축이고 게이트4 셀 축이다 |
| `usagePeriod` | 조건축 | **비목표**. 값을 넣으면 에러 |

`docs/V5_SPRINT_PLAN.md` §132 가 이미 확정해 뒀다 — *"condition은 `skinType`·`skinTrouble`·`option`만 쓴다"*. `usagePeriod` 는 데이터에 필드가 없고 `isMonthUseReview`/`isMonthOverReview` 는 불리언 리뷰 종류지 기간이 아니다.

**필드를 지우지 않고 남긴 이유**는 PER-187 의 `EXCLUDED_AXES` 와 같다 — 지워 두면 다음 사람이 "왜 없지" 하고 본문에서 추정해 채운다. 추정하면 조건축이 아니라 **짐작이 세그먼트가 된다.** 그래서 자리를 남기고 값을 넣으면 에러를 내며, 에러 메시지가 사유를 들고 있다.

### 5-2. `null`(무관) 과 `"미기재"`(세그먼트) 는 다르다

축 값은 셋 중 하나다 — `null` · `["미기재"]` · 코드 배열. 섞으면 에러다.

`null` 은 "이 주장은 그 축과 무관하다"(제품 전체), `"미기재"` 는 "프로필을 안 밝힌 리뷰들의 세그먼트" 다. 합치면 미기재가 **"모든 조건"** 으로 둔갑한다. 코드와 함께 담는 것도 같은 이유로 막는다 — `["A02", "미기재"]` 는 "건성이면서 건성인지 안 밝힌" 이라는 뜻이 없는 집합이다.

> 이 규칙은 **실측에서 빠뜨린 것을 찾아 고쳤다.** 첫 구현은 코드북 도메인만 봤고, 골든셋 변환에서 `skinType="미기재"` 4건이 통째로 탈락했다. 스키마가 골든셋보다 좁았던 것이다.

문자열 하나(`"A02"`)를 주는 것은 여전히 에러다 — 글자 단위로 쪼개져 조용히 다른 세그먼트가 된다.

### 5-3. `confidence` 는 소수점이 아니라 말투다

`0.73` 같은 점수를 두지 않았다. **그 숫자를 만들 근거가 없고**, 화면에 나가는 순간 독자가 정밀도로 읽는다.

우리가 실제로 아는 것은 이산적인 사실이다 — 방향이 갈렸는가 · 침묵이 과반인가 · 말한 사람이 적은가 · 게이트가 한계를 남겼는가. 그게 그대로 말투를 정한다.

```json
"confidence": {
  "band": "hedged",
  "reasons": ["direction_mixed", "silence_dominates"],
  "inputs": { "direction": "mixed", "supportAuthors": 14, "silentAuthors": 318, … }
}
```

**왜 완곡해졌는지가 같이 나가는 것**이 요점이다 — judge 와 화면이 같은 사유를 읽는다. 사유 어휘는 게이트가 남긴 한계 코드를 그대로 받아 쓴다(PER-185·186·188). 여기서 새 어휘를 만들면 게이트의 한계와 화면의 말투가 서로 다른 말을 한다.

기본 임계값(`silent_ratio_hedge=0.5` · `min_assertive_authors=8`)은 **잠정**이고 교정은 PER-200 이다. 정책은 `ConfidencePolicy` 로 갈아끼운다.

## 6. 실측 — 스키마가 실제 주장을 담는가

스키마를 새로 만들면 "잘 만들었다"는 말밖에 할 게 없다. 그래서 **이미 있는 진짜 주장** 26건(골든셋 라벨, PER-178·179)을 이 스키마로 옮겨 봤다.

| | |
|---|--:|
| 골든셋 라벨 | 26 |
| **claim 스키마로 표현됨** | **22** |
| 표현 안 됨 | 4 |
| 인용 원문 부분문자열 | **96 / 96 (100%)** |
| 노출 / 보류 | 20 / 2 |

**표현 안 되는 4건은 전부 `aspect` 가 `null` 인 라벨이다.** 골든셋은 택소노미 14종 **밖** 주장을 적을 수 있게 허용하지만, 파이프라인의 단위가 `(셀 × aspect)` 라 claim 은 aspect 없이 존재할 수 없다. **스키마 결함이 아니라 경계다.**

이 4건은 PER-188 의 역추적이 `untraceable` 로 센 것과 **같은 건들이다** — 재현율 분모가 26 이 아니라 **22** 인 이유가 여기서도 같은 수로 나온다. 임계값을 바꿔도 되살아나지 않고, 되살리려면 택소노미를 넓혀야 한다 (PER-212).

보류 2건은 `unsupported_claim` 1 · `polarity_mismatch` 1 이고, 골든셋의 `candidate_rejected`(음성 정답)라 **보류로 잡히는 것이 맞다.**

### 이 변환은 버리는 코드가 아니다

judge(PER-196·197)는 생성물과 골든셋을 **같은 모양**으로 놓고 비교해야 한다. 골든셋 → claim 변환은 그때 그대로 쓰인다.

## 7. 기각한 대안

- **검증 함수에서 `evidence` 빈 것을 걸러내기.** §2 — 걸러내는 구조는 "나중에 출처를 붙인다"를 허용한다
- **`confidence` 를 0~1 실수로.** §5-3 — 만들 근거가 없는 정밀도이고 화면에서 오독된다
- **모델에게 `support` 를 쓰게 하고 코드가 검산.** 검산이 통과해도 모델이 침묵을 어떻게 셌는지는 모른다. 셀 수 있는 쪽이 세는 게 맞다
- **`usagePeriod` 필드를 스키마에서 지우기.** §5-1 — 지우면 다음 사람이 추정해 채운다
- **`"미기재"` 를 `null` 로 합치기.** §5-2 — 미기재가 "모든 조건"으로 둔갑한다
- **노출 플래그를 따로 두기.** §4 — `failureReason` 과 둘로 갈리면 "실패인데 나가는" 상태가 생긴다
- **`aspect` 를 선택 필드로 만들어 골든셋 4건까지 담기.** §6 — 담을 수는 있지만 그 claim 은 어느 셀에도 속하지 않아 집계·재현율·judge 어디에도 못 들어간다. 담기는 것과 쓸 수 있는 것은 다르다
- **재시도를 3회 이상.** PRD §5-3 이 2회로 정했다. 늘리면 "될 때까지" 가 되고, 폐기 로그가 개선의 단서가 되지 못한다

## 8. 남는 한계

- **생성기가 없다.** 이 모듈은 LLM 을 부르지 않는다 (PER-191). 지금 검증한 22건은 **사람이 만든 주장**이지 파이프라인 산출물이 아니다
- **`support` 가 번들 40건 기준이다.** 그래서 실측에서 **단정(assertive)이 0건**이고 `silence_dominates` 가 전건에 붙는다 — 40건 표본에서는 침묵이 구조적으로 크다. **정책의 실패로 읽으면 안 되고**, 전수 셀에서 계산하는 생성기에서 다시 봐야 의미가 있다
- **`rejected[]` 와 `limitations` 가 비어 있다.** 골든셋 라벨은 게이트를 거친 산출물이 아니라 채울 값이 없다. 생성기가 원장(PER-188)에서 채운다
- **인용 원문성의 전수 강제와 측정은 PER-190 의 몫이다.** 여기서는 원문을 넘겨받은 경우에만 대조한다
- **골든셋 26건 중 25건이 모델 후보에서 왔다** (번들 5개 중 4개에 `source=human` 이 0건). 그래서 "스키마가 담을 수 있는 주장의 모양" 도 그만큼 후보 모델의 모양이다
