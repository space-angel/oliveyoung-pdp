# oliveyoung-pdp

올리브영 제품 링크 하나를 넣으면 **리뷰를 수집해 구매 결정용 질문–답 쌍**을 만든다.

```bash
URL="https://www.oliveyoung.co.kr/store/goods/getGoodsDetail.do?goodsNo=A000000211119"

.venv/bin/python pipeline/run_url.py --url "$URL" --check   # 계획과 비용만 본다
.venv/bin/python pipeline/run_url.py --url "$URL" --yes     # 실제로 돌린다
```

산출물은 `data/runs/<runId>/claims.jsonl` 이다.

```json
{
  "question": "복합성인데 이거 하나로 속건조까지 잡힐까?",
  "verdict": "속보습감은 좋다는 평이 많지만, 덜 느껴진다는 말도 있다",
  "answer": "속건조가 잡히고 피부가 탱글해진다는 평이 많고 …",
  "condition": {"skinType": ["A03"], "skinTrouble": null, "option": null},
  "direction": "mixed",
  "support": {"positiveAuthors": 47, "negativeAuthors": 3, "spokeAuthors": 50,
              "silentAuthors": 45, "cellAuthors": 95},
  "evidence": [{"reviewId": 38875488, "quote": "원문 그대로", "stance": "negative"}]
}
```

> 이 브랜치(`main`)는 **실행 경로만** 둔다. 평가·측정·결정 이력·골든셋·발표 자료는
> `archive` 브랜치에 있다 — `git checkout archive -- <경로>`.

---

## 무엇을 하는가

```
crawl    올리브영에서 그 제품 리뷰를 수집한다          네트워크 · 레이트리밋
catalog  수집분에서 런 전용 카탈로그를 만든다
ingest   원문/조건/파생 3층으로 적재 + 입력 계약 검증
tag      (리뷰 × aspect) 로 aspect·방향 태깅          LLM · 약 $0.1 / 500건
gates    동일성 · 중복 · 방향성 · 충분성 4게이트
claims   질문–답 쌍 생성                              LLM · 약 $1~2 / 제품
report   런 요약 — 무엇을 쟀고 무엇을 못 쟀는지
```

단계를 골라 돌릴 수도 있다 — `--steps catalog,ingest,gates,report` (비용 없는 것만).

## 이 파이프라인이 지키는 것

**AI 는 가운데 두 칸만 맡는다.** 문장을 읽고(`tag`) 쓰는(`claims`) 자리다.
앞뒤는 규칙이다 — 같은 걸 넣으면 같은 게 나와야 하는 자리이기 때문이다.
자율 에이전트가 아니라 순서가 고정된 파이프라인이라 어느 단계에서 어긋났는지 찾을 수 있다.

| 규칙 | 어디서 | 안 지키면 |
|---|---|---|
| **인용은 원문 부분문자열이어야 한다** | `quote_gate.py` | 아니면 그 claim 을 **내지 않는다**. 사후 필터가 아니라 생성 게이트다 |
| **근거가 비면 문장이 안 만들어진다** | `claim_contract.py` | `evidence` 가 비면 객체 생성 자체가 실패한다 |
| **침묵은 근거가 아니다** | `polarity.py` · `sufficiency.py` | 말하지 않은 사람은 어느 쪽으로도 안 센다. 분모는 **말한 사람** |
| **세는 단위는 고유 작성자** | `gates.py` | 같은 사람이 재구매하며 또 쓴 표를 여러 번 세지 않는다 |
| **미등록 `goodsNo` 는 에러** | `catalog.py` | 조용히 폴백하지 않는다 |
| **조건은 코드북 안의 코드만** | `codebook.py` | 도메인 밖 값은 새 세그먼트가 되지 않고 에러다 |
| **탈락은 삭제가 아니라 사유가 붙은 행** | `ledger.py` | 버린 것을 안 남기면 놓친 답을 영영 못 되짚는다 |

## 시작하기

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env        # ANTHROPIC_API_KEY 입력

# 크롤러는 별도 venv 권장 (브라우저 포함으로 무겁다)
python3 -m venv crawler/.venv
crawler/.venv/bin/pip install -r crawler/requirements.txt

python3 -m unittest discover -s pipeline -p 'test_*.py'   # 계약 테스트
```

## 알아둘 것

| | |
|---|---|
| **비용** | `crawl` 은 올리브영에 실제 요청, `tag`·`claims` 는 LLM 실비. `--yes` 없이는 안 돈다 |
| **격리** | 모든 산출은 `data/runs/<runId>/` 안. `Workspace.assert_isolated()` 가 크롤 전에 검사한다 |
| **카탈로그** | **수집분에서** 만든다. 변형 SKU(제품당 평균 4종)를 빠뜨리면 입수가 미등록 `goodsNo` 로 멈춘다 |
| **리센시** | 24개월 창. 오늘 수집분은 데이터에서 파생하고 `derived: true` 를 남긴다 |
| **재현율** | **재지 않는다.** 새 제품에는 사람이 만든 정답지가 없다 — `run.json` 의 `evaluation` 에 사유가 적힌다 |

마지막 줄이 중요하다. 못 잰 것을 안 적으면 *"재현율이 안 나왔다"* 와 *"재현율이 나쁘다"* 를
구분할 수 없다. 그래서 `run.json` 은 **잴 수 있는 것**(인용 원문 일치율 · 생성률 ·
탈락 사유 분포)과 **못 재는 것**(재현율 · 방향 일치율)을 나눠 적는다.

## 구조

```
pipeline/
  run_url.py               링크 하나로 끝까지 — 진입점
  workspace.py             경로 배치. canonical() 과 for_run() 두 종류뿐
  contracts.py             입력 계약 3층 (원문/조건/파생)
  catalog.py               goodsNo → productId. 미등록은 에러. 세대는 (goodsNo, 날짜)로 가른다
  codebook.py              조건 어휘 26종. 도메인 밖은 에러
  policy.py                리센시 · 리뉴얼 · 충분성 임계값. 게이트가 소비한다
  ingest.py                수집분 → v5 레코드 (LLM 없음, 재실행 일치)
  tag.py                   (리뷰 × aspect) 태깅 — 배치 API
  tag_contract.py          태그 계약 + 인용 대조 (보이지 않는 문자만 접는다)
  trust.py                 신뢰도 사전 점수. 필터가 아니라 가중치
  gates.py                 게이트1 동일성 · 게이트2 중복
  polarity.py              게이트3 방향성 — 유일하게 탈락시키지 않는 게이트
  sufficiency.py           게이트4 충분성 — U ≥ N AND U/D ≥ R AND S ≥ S
  ledger.py                통합 rejected[] 원장
  reject_registry.py       탈락 사유 어휘의 정본
  context_layout.py        5단 배치. 상한이 방향을 통째로 지우지 않는다
  generate.py              claim 생성기
  claim_contract.py        claim 스키마 + 검증기
  quote_gate.py            인용 원문성 — 생성 게이트
  condition_render.py      조건 → 결과 표기
  run_meta.py              재현 meta 계약
  prompts/                 tag · claim 프롬프트 (버전별)
  *.json                   가중치 · 어휘 — 코드가 아니라 여기서 고친다
  test_*.py                계약 테스트
crawler/                   올리브영 cursor API 크롤러
data/
  input/                   product_catalog.json · skin_codebook.json
  runs/                    링크로 돌린 런 (gitignore)
  intermediate/ cache/     중간 산출물 (gitignore)
```

## archive 브랜치

평가·기록물은 `archive` 에 있다. 필요하면 꺼내 쓴다.

```bash
git checkout archive -- data/input/reviews_50products.json   # 25K 정본 스냅샷
git checkout archive -- eval/ docs/                          # 측정 스크립트 · 리포트 · 결정 문서
```

| `archive` 에 있는 것 | |
|---|---|
| `data/input/reviews_50products.json` | 25,000건 정본 스냅샷 (36MB) |
| `eval/` | 측정 스크립트 · 리포트 128개 · 골든셋 · 공유 페이지 |
| `data/output/` | v4 · v5 산출물 |
| `docs/` | 결정 문서 (PER-170~193) · 입력 계약 · 브랜치 전략 |
| `legacy/v4/` | v4 동결 — 비교 기준선 |
| `scripts/verify.sh` | 병합 게이트 (리포트 재현 대조) |
| `apps/label-review` | 골든셋 라벨 검수 앱 |

정본 스냅샷을 받아오면 건너뛰던 계약 테스트 36건이 다시 돈다.
