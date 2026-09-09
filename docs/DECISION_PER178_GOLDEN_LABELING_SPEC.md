# 주장 골든셋 — 라벨링 규격과 작업 도구 (PER-178)

작성: 2026-09-09 · 이슈 [PER-178](https://linear.app/banjax/issue/PER-178) (마일스톤 #3, due 09-07 → 실제 09-09)
후속: PER-179 파일럿 40건 → PER-180 100건 → PER-197 judge 일치율
근거 파일: `eval/gold/v5_concern_golden_meta.json` · `eval/reports/v4_golden_migration_per178.json`
강제 지점: [`pipeline/golden_contract.py`](../pipeline/golden_contract.py) · [`pipeline/sample_concern_golden.py`](../pipeline/sample_concern_golden.py) · [`eval/label_concern_golden.py`](../eval/label_concern_golden.py)
계약 테스트: `pipeline/test_golden_contract.py` (39) · `pipeline/test_sample_concern_golden.py` (14)

> 이 문서의 수치는 위 두 근거 파일과 `python3 pipeline/sample_concern_golden.py` 실행 로그에서 나왔다.
> 라벨은 아직 0건이다 — 라벨링 자체는 PER-179 다.

PRD §7-2: 골든셋은 "가장 느리고 가장 믿을 만한" 층이다. 규격이 흔들리면 이후 모든 점수가 흔들린다.
그래서 이 이슈는 **라벨을 만들지 않고, 라벨의 모양·표본·도구·위반 조건**을 코드와 테스트로 고정한다.

---

## 0. 결정 요약

| 항목 | 결정 | 강제 |
|---|---|---|
| 라벨 단위 | **claim 1개** = 질문–답 · 조건 · 방향 · 근거(reviewId+인용+입장) · 실패유형 · 평가상태 | `golden_contract.validate_label` |
| 읽는 단위 | **번들** = 제품(또는 제품 × 조건 셀) 하나의 리뷰 ≤40건. 라벨은 번들 안에서만 만든다 | `evidence.reviewId ∈ bundle` |
| 표본 | 번들 40개 (파일럿 16) · 제품 40개 / 5카테고리 비례 · 종류 5 (제품 16 · skinType 10 · option 6 · skinTrouble 4 · 미기재 4) | `sample_concern_golden.py --check` |
| 층화 | 번들 안 평점 층 12/8/8/12 (1~2★ / 3★ / 4★ / 5★), 층별 weight 기록 | 같음 |
| 시드 | `20260909`. 시각 미기록, 입력 sha256 기록 | `test_sample_concern_golden.test_seed_is_fixed` |
| 모집단 컷 | 리센시 통과 → (작성자, 제품) 1건 → v4 골든셋 5제품 제외 → 고유 작성자 8명 미만 제품 제외 | 메타 `excludedProducts` |
| 조건 | 축마다 `null`(무관) / `"미기재"` / 코드. 셀 번들은 그 셀을 강제. **조건부 주장의 근거는 그 세그먼트 리뷰만** | `_validate_condition` |
| 인용 | 원문 부분문자열 (`fold_invisible` 만 접는다). squeeze 금지 | `_validate_evidence` |
| 카운트 | 리뷰 수가 아니라 고유 작성자 수 | `support_counts` |
| 실패유형 | `pipeline/failure_taxonomy.json` (`failure-taxonomy-v1`, PER-177 8키). 코드 상수 아님. severity 순 정렬, 첫 원소가 대표 | `load_failure_taxonomy` |
| 블라인드 | 워크시트에는 원문·조건 코드·별점·작성자 키만. 파이프라인·v4 산출물 없음 | `render_worksheet` |
| v4 15문항 | **옮기지 않는다.** 근거 155개 중 리센시 통과 24개, N_min 충족 0/15, 내용 필드 부재 | `measure_v4_golden_migration.py` |
| 도구 | CLI 4명령 `show / add / validate / stats`. 위반 라벨은 파일에 쓰지 않는다 | `eval/label_concern_golden.py` |
| 게이트 | `verify.sh` 가 번들 재현 · 라벨 계약 · 마이그레이션 리포트 재현을 돈다 | `scripts/verify.sh` |

---

## 1. 라벨 1건의 구성

이슈가 정한 다섯 항목(주장·조건·방향·근거 리뷰 ID·실패유형)에 **답·인용·입장·평가상태·소요시간**을 더했다.
더한 이유는 각각 후속 이슈의 요구다.

```jsonc
{
  "labelId": "B01-1",
  "bundleId": "B01",                 // 어느 번들에서 만들었나
  "productId": "p002",               // 세대 단위 (PER-171/172). 번들과 같아야 한다
  "aspect": "발색",                   // 14종 택소노미 또는 null (동결, PER-175)
  "question": "…?",                  // 구매자의 질문
  "answer": "…",                     // 리뷰가 주는 답 — 질문만 있으면 사용자가 답을 직접 찾아야 한다 (PRD §6)
  "condition": {                     // 축마다 null(무관) / "미기재" / 코드
    "skinType": "A02", "skinTrouble": null, "option": null
  },
  "direction": "mixed",              // positive | negative | mixed — 별점이 아니라 문장의 방향
  "evidence": [                      // 근거. 번들 안 리뷰만
    {"reviewId": 61368405, "stance": "support", "quote": "원문 부분문자열"},
    {"reviewId": 61380240, "stance": "oppose",  "quote": "원문 부분문자열"}
  ],
  "failureReasons": [],              // 정상 claim 은 []. 실패 사례면 PER-177 8키 (정렬됨)
  "evaluation": "complete",          // complete | not_evaluable (후자는 notes 필수)
  "notes": null,
  "minutesSpent": 6                  // PER-179 가 요구하는 건당 소요 시간
}
```

| 필드 | 왜 있는가 | 위반 시 |
|---|---|---|
| `answer` | question–answer 쌍이 가치 단위 (PRD §6, 이슈 5-3). judge 는 답의 지지 여부를 본다 | 빈 값 → 에러 |
| `evidence[].quote` | 잘못된 귀속(`misattribution`)은 인용 없이는 판정할 수 없다 (PER-177 §2) | 원문 부분문자열이 아니면 에러 |
| `evidence[].stance` | `mixed` 를 표현하려면 근거마다 입장이 필요하다. U(지지 작성자)와 반대 수를 따로 센다 | `support`/`oppose` 밖 → 에러 |
| `failureReasons[]` (배열) | 복수 실패를 대표 하나로 뭉개면 동시 발생한 치명 실패가 집계에서 사라진다 (PER-177 §4-3) | 택소노미 밖·미정렬 → 에러 |
| `evaluation` | "자료 부족"은 아홉 번째 실패가 아니라 평가 상태다 (PER-177 §4-3 ①) | `not_evaluable` 에 notes 없음 → 에러 |
| `minutesSpent` | PER-179 "건당 소요 시간을 기록한다 → 나머지 60건의 일정 추정 근거" | 양수 아님 → 에러 |

### 1-1. `null` 과 `"미기재"` 는 다르다

`condition.skinType = null` 은 "이 주장은 피부타입과 무관하다"(향·분사력·용기 같은 제품 전체 주장)이고,
`"미기재"` 는 "프로필을 밝히지 않은 리뷰들의 세그먼트에서 관측됐다"다. 둘을 합치면 미기재 리뷰(42.9%)가
모든 세그먼트의 근거로 새어 들어간다 — PER-177 §3 '조건 누락'의 정확한 발생 경로다.

그래서 **조건부 주장의 근거는 그 세그먼트의 리뷰만**이다. 건성(A02) 주장에 미기재 리뷰를 넣으면
에러이고, 미기재 세그먼트 주장에 건성 리뷰를 넣어도 에러다. 테스트
`test_conditional_claim_rejects_missing_evidence` · `test_missing_segment_claim_rejects_stated_evidence` 가 고정한다.

### 1-2. 실패 라벨은 골든셋의 일부다

`failureReasons` 가 비지 않은 라벨은 "이런 claim 이 나오면 실패다"라는 **음성 정답**이다. judge 일치율
(PER-197)은 정상 claim 을 통과시키는 것만이 아니라 실패 claim 을 잡는 것도 잰다. 라벨러는 리뷰를 읽으며
자연스럽게 떠오르는 잘못된 일반화("누구나 촉촉하다")를 그대로 적고 `missing_condition` 을 붙일 수 있다.

v4 사례(PER-177 §5)는 출력을 본 뒤 만든 분석이라 여기에 넣지 않는다.

---

## 2. 표본 — 왜 층화이고 어떻게 뽑았나

### 2-1. 실측 (`v5_concern_golden_meta.json`)

```
코퍼스 25,000
  → 리센시 통과 (2024-09~)           22,194   (88.8%)
  → (작성자, 제품) 1건               17,681   (초과 표 4,513 제거)
  → v4 골든셋 5제품(계보) 제외         p007 · p015 · p028 · p040 · p043
  → 고유 작성자 8명 미만 제품 제외      p051 (7명)
  = 후보 제품 45  →  번들 40 (제품당 최대 1)
```

| 번들 종류 | 개수 | 뜻 |
|---|---|---|
| `product` | 16 | 제품 전체 리뷰에서 층화. 제품 단위 주장 + 리뷰별 조건을 보고 만드는 조건부 주장 |
| `skinType` | 10 | A01·A02·A03·A04·A06 각 2개 (덜 쓰인 세그먼트부터 배정) |
| `option` | 6 | 옵션 세그먼트 N≥8 |
| `skinTrouble` | 4 | C01·C06·C07·C08 |
| `missing` | 4 | `skinType = 미기재` 셀. "조건 없음이 아니다" 규칙을 라벨에서 검증한다 |

카테고리 배분은 후보 제품 수 비례(최대 잉여법): 에센스/세럼 12 · 베이스 8 · 크림 8 · 아이 7 · 립 5.
v4 카테고리 분포 FAIL(1/5)의 재발을 표본 단계에서 막는다. 파일럿 16개는 두 겹 라운드로빈(종류 → 카테고리)으로
앞에 배치해 5종류 모두(4·3·3·3·3)와 5카테고리 모두(4·4·4·3·1)를 덮는다.

### 2-2. 번들 안 층화

| 층 | 목표 | 실제 합 (40번들) | 코퍼스 비율이라면 |
|---|---|---|---|
| 1~2★ | 12 | **131** (10.3%) | 1.6% |
| 3★ | 8 | 210 | 3.3% |
| 4★ | 8 | 289 | 9.6% |
| 5★ | 12 | 637 | 85.4% |
| 합 | 40 | 1,267 | |

번들 40개 중 25개가 40건 꽉 찼고 최소는 8건(셀 크기 = N_min). 모자란 층은 낮은 평점부터 채운다.
**번들 안 부정 비율(10.3%)을 코퍼스 수치로 읽으면 안 된다** — 층별 `weight` 로 되돌린다.

한계: **1~2★ 이 0건인 번들이 13개**다 (skinType 5 · skinTrouble 3 · option 2 · product 2 · missing 1).
셀의 모집단 자체에 부정 리뷰가 없기 때문이고, 그 셀에서는 리스크 질문의 정답이 안 나온다. 이건 표본 결함이
아니라 데이터의 모양이다 — PER-179 가 이 13개에서 부정 주장이 0건인지 확인하고, 필요하면 셀 종류 할당을
바꾼다(시드는 그대로, `SCOPE_QUOTA` 만 → 새 표본 = 새 버전).

### 2-3. 왜 이렇게 컷했나 (기각한 대안)

- **리센시 컷 전 리뷰를 포함**: 기각. 파이프라인이 근거로 못 쓰는 리뷰로 정답을 만들면 커버리지 미달이
  정답셋 탓인지 생성기 탓인지 가를 수 없다. 태깅 파일럿(PER-175)은 태깅이 전수라 포함했지만 claim 은 다르다.
- **작성자 중복 리뷰 유지**: 기각. 근거 카운트가 고유 작성자 수(PER-170)라 라벨러가 세는 수와 도구가 세는
  수가 어긋난다. 하나만 남기고 `support_counts` 가 작성자 수를 센다.
- **v4 5제품 포함**: 기각. v4 concern 30개와 골든셋 15문항을 이미 본 제품이라 블라인드가 성립하지 않는다.
  v4 대비 비교(PER-201)는 v4 자체 골든셋으로 한다.
- **aspect 로 층화**: 불가. 전수 태깅(PER-175 배치)이 아직 없다. 카테고리 비례 배분이 대리다. PER-180 이
  요구하는 "롱테일 aspect 포함"은 라벨 후 `stats` 의 `byAspect` 로 확인한다.
- **`(product × skinType)` 셀만으로 번들 구성**: 기각. 제품 전체 주장(향·용기·가성비)이 빠진다. 제품 번들 16개.

---

## 3. 블라인드 절차

라벨러가 읽는 문서는 [`eval/gold/LABELING_GUIDE.md`](../eval/gold/LABELING_GUIDE.md) 다 — 아래 절차를 판단 규칙·예시·거부 메시지 대처까지 풀어 썼다.

1. `show B01` 로 워크시트를 만든다. 내용은 원문(보이지 않는 문자만 접음) · 별점 · 월 · 조건 코드와 라벨 ·
   작성자 키. **파이프라인 결과·태그·v4 산출물은 없다.** 별점은 원문 데이터라 보이지만 `direction` 은
   문장으로 정한다 — 한 리뷰 안 방향 갈림 20.5%, 별점–태그 불일치 12.9% (PER-175).
2. 번들 안에서 claim 을 만든다. 번들 밖 리뷰를 찾아보지 않는다(도구가 막는다).
3. `add B01` 로 입력. 계약 위반이면 저장되지 않고 이유가 나온다.
4. 번들 순서대로 진행. 파일럿은 B01~B16, 라벨 40건이 되면 PER-179 종료 조건.
5. 규격 결함(애매한 판정·조작적 정의 부족·조건 표기 불가)은 `notes` 에 적고 PER-178 로 되먹인다.

---

## 4. v4 골든셋 15문항 마이그레이션 — 하지 않는다

`eval/reports/v4_golden_migration_per178.json` (재현: `python3 eval/measure_v4_golden_migration.py`).

| 단계 | 근거 ID | 문항 |
|---|---|---|
| v4 `supportingReviewIds` | 155 | 15 |
| v5 코퍼스(25K)에 존재 | **30** | 10 (파티온 3문항 · 클리오 2문항은 0개) |
| 리센시 통과 | **24** | 10 |
| N_min=8 충족 | — | **0** |

구조는 옮겨진다 — `productKey` 15/15 가 카탈로그로 풀린다. 그러나 근거는 살아남지 않고, v5 가 요구하는
`answer · condition · direction · evidence.quote · evidence.stance · failureReasons` 는 v4 에 **필드가 없다.**
채우려면 원문을 다시 읽어야 하므로 "변환"이 아니라 "재라벨"이다. 재라벨하더라도 그 5제품은 블라인드가
깨져 있다.

→ v4 15문항은 `legacy/v4/eval/golden_set.json` 에 그대로 두고 PER-201 의 v4 쪽 기준으로만 쓴다.

---

## 5. 도구

```bash
python3 pipeline/ingest.py                              # 입수 (한 번)
python3 pipeline/sample_concern_golden.py --check       # 번들 40개가 고정물과 같은지
python3 eval/label_concern_golden.py show B01           # 워크시트 → data/intermediate/v5_concern_golden_worksheets/B01.md
python3 eval/label_concern_golden.py add B01            # 대화식 입력 → 검증 → eval/gold/v5_concern_golden_labels.jsonl
python3 eval/label_concern_golden.py add B01 --from x.json   # JSON 파일로 입력 (수정 재입력용)
python3 eval/label_concern_golden.py validate           # 라벨 전체 계약 검증 — verify.sh 가 돈다
python3 eval/label_concern_golden.py stats              # 진척 · 층 · 실패유형 · 건당 소요 시간
```

**웹 UI** (`eval/label_concern_golden_web.py`, 표준 라이브러리만, 127.0.0.1 만 듣는다):

```bash
python3 eval/label_concern_golden_web.py          # → http://127.0.0.1:8178
```

왼쪽 번들 목록(진척) · 가운데 리뷰(원문을 드래그 → support/oppose 버튼으로 근거 추가) · 오른쪽 claim 폼.
저장은 CLI 와 같은 `validate_label` 을 거치고 같은 라벨 파일에 쓴다 — 위반이면 저장되지 않고 이유가 뜬다.
소요 시간은 번들을 열거나 직전 저장 이후의 경과로 자동 채우되 손으로 고칠 수 있다. 라벨 삭제도 여기서 한다.
테스트 `pipeline/test_label_web.py` (7).

`add` 는 `failureReasons` 정렬을 대신 해주고, 셀 번들에서는 그 셀을 조건 기본값으로 채운다. 나머지는
사람이 정한다. 저장 후 `support 작성자 N / oppose M` 을 바로 보여줘 과소 근거를 라벨 시점에 알 수 있다.

라벨 파일을 손으로 고쳤으면 `validate` 를 돌린다. `validate --rewrite` 는 정규 표기로 다시 쓰는 것뿐이고
내용을 바꾸지 않는다.

---

## 6. 실패 유형 어휘의 위치

PER-177 이 8키·심각도·순번을 확정했고(`docs/DECISION_PER177_FAILURE_TAXONOMY.md`, 병합 대기), PER-181 이
설정 파일로 옮기기로 돼 있다. 라벨 도구가 어휘를 지금 필요로 해서 **`pipeline/failure_taxonomy.json`** 을
`failure-taxonomy-v1` 로 만들고 최소 로더(`golden_contract.load_failure_taxonomy`)를 두었다. 내용은 PER-177 §2
표와 1:1 이다. PER-181 은 이 파일에 검사 목록·버전 거부 규칙을 더하면 된다 — 코드 상수는 어디에도 없다.

---

## 8. B안 — 모델 후보 + 사람 검수 (2026-09-09 추가 결정)

첫 라벨 실측이 **건당 30분**이었고, 그중 80%가 "질문 짜내기"였다(라벨러 진술). 40건 = 20시간이라 PER-179 마감을 못 지킨다.
A안(모델 없는 선택지: aspect 칩·문장 후보·질문 틀)을 먼저 넣었지만 라벨러가 여전히 느리다고 판단해 B안으로 간다.

| 골든셋이 필요한 이유 | B안에서 지키는 방법 | 강제 |
|---|---|---|
| judge 신뢰도 검증 | 후보를 사람이 채점한 기록이 judge 과제와 같은 모양이다 — 오히려 잘 맞는다 | — |
| 실패 사례(음성 정답) | 기각한 후보 = `candidate_rejected` + `failureReasons` 필수 | `golden_contract` |
| 생성기가 놓친 것(재현율) | 번들마다 `source=human` 1개 이상. `stats.bundlesWithoutHumanLabel` 이 경고 | `label_concern_golden.summarize` |
| 생성기와의 독립성 | 후보 모델 이름을 후보 파일에 기록. **생성기(PER-189)·judge(PER-196)는 그 모델·프롬프트를 쓰지 않는다** | `import --model` 필수 |
| 출처 추적 | 라벨 `source` 4종 + `candidateId`. PER-197·201 은 출처별로 갈라 본다 | `LABEL_FIELDS` 15 |

API 를 부르지 않는다 — 인증 정보를 저장소에 두지 않기로 했고, 라벨러가 구독제 모델에 하네스를 붙여 넣는 편이 빠르다.
하네스(`eval/concern_candidates.py export`)는 지시문(`eval/prompts/concern_candidates_v1.md`, sha256 기록) + 번들 리뷰 + 출력
JSON 규격 한 파일이다. 모델 출력은 `import` 가 계약에 비춰 **버리지 않고 위반을 기록**한다(`contractErrors`) —
인용이 원문에 없는 후보는 그 자체가 `unsupported_claim` 의 자연 표본이다.

후보 파일 `eval/gold/v5_concern_golden_candidates.jsonl` 은 커밋한다. 사람의 결정이 `candidateId` 로 가리키는 고정물이기
때문이다. **후보는 정답이 아니다.** 정답은 검수된 라벨이다.

기각한 대안: 세션 안의 Claude 가 후보를 쓰는 것 — 모델 ID·프롬프트가 파일로 남지 않아 재현이 안 되고, 저장소 규칙
"Claude 가 라벨을 대신 쓰지 않는다"와 충돌한다. API 스크립트 — 키가 저장소에 없고, 지금 단계에서 1인 라벨러에게는 하네스가 더 빠르다.

## 7. PER-179 로 넘기는 것

- 파일럿 번들 B01~B16, 라벨 목표 40건. `stats` 의 `minutesPerLabel` 이 남은 60건의 추정 근거.
- 1~2★ 0건 번들 13개에서 부정 주장이 실제로 0건인지. 그렇다면 `SCOPE_QUOTA` 재조정을 PER-178 로 되먹임.
- `not_evaluable` 라벨의 사유 — 조작적 정의가 부족한 유형이 드러나는 자리.
- `aspect = null` 라벨의 `notes` — 14종 밖 주제의 실측 (택소노미 변경은 별도 이슈).
