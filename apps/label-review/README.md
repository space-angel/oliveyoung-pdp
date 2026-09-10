# 리뷰 질문 검수 — 외부 라벨러용 앱 (PER-178)

골든셋(PER-179/180)을 여러 사람이 나눠 만들 수 있게 한 도구. 라벨러가 보는 개념은 넷이다:
**질문·답, 색칠된 근거 문장(긍정/부정/중립), 놓친 리뷰, 아니에요 이유 4개.** 나머지(라벨 ID·출처·조건·방향·
실패유형 키·평가상태)는 서버가 채운다. 계약 검증은 `pipeline/golden_contract.py` 그대로다 — 이 앱이 규격을
느슨하게 만들지 않는다. 화면 흐름 목업: https://claude.ai/code/artifact/6468fd3e-8e27-4ee9-a545-e9a0ae64fb16

```
apps/label-review/
  server.py            HTTP (표준 라이브러리). Vercel 은 `handler` 클래스를 그대로 쓴다
  service.py           배정·판정·완료 규칙. 일상어 사유 4개 → PER-177 키 매핑 (REASONS)
  store.py             LocalStore(JSONL) / SupabaseStore(REST, urllib)
  public/index.html    프론트 한 파일. 다크 톤 (toss.im/simplicity-24 토큰: #111213 · #0099FF · #4BEC8B · #DDF34F)
  supabase/schema.sql  테이블 4개 (labelers · assignments · labels · decisions)
```

## 로컬로 켜기

```bash
python3 pipeline/ingest.py                      # data/intermediate 가 비어 있을 때만
python3 apps/label-review/server.py             # http://127.0.0.1:8180
```

기본 저장소는 정본 파일이다: `eval/gold/v5_concern_golden_labels.jsonl` (라벨) · `eval/gold/v5_concern_golden_assignments.jsonl`
(배정·결정). 라벨이 이미 있는 번들(B01~B05)은 배정에서 자연히 빠지므로 **B06 부터** 나간다.

정본을 건드리지 않고 시험할 때: `LABEL_REVIEW_DATA_DIR=/tmp/lr python3 apps/label-review/server.py`

## 여러 사람이 쓰기 (Supabase)

1. Supabase 프로젝트에서 `supabase/schema.sql` 실행.
2. 서버 환경변수: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`(service role — 서버에서만), `LABEL_ACCESS_CODE`(참여자에게 줄 코드).
3. 배포. Vercel 이면 `api/index.py` 에서 `from server import handler` 로 노출하고 `eval/gold/*.jsonl`·`data/input/*.json`·
   `pipeline/`·`eval/` 을 함수에 포함시킨다(`vercel.json` `functions.includeFiles`). 다른 호스트도 `server.py` 를 그대로 돌리면 된다.
4. 정본으로 되돌리기: Supabase `labels.label` 을 내려받아 `eval/gold/v5_concern_golden_labels.jsonl` 에 합치고
   `python3 eval/label_concern_golden.py validate` 로 게이트를 돈다. (스크립트는 필요해질 때 붙인다 — 지금은 라벨이 한 파일이라 손으로도 된다.)

## 배정·판정 규칙

- 배정: 라벨 0·활성 배정 없음인 번들 중 번호가 가장 낮은 것 1개. `assignments.bundle_id` 가 PK 라 두 사람이 동시에 잡으면 두 번째가 다음 번들로 간다.
- 맞아요 → `candidate_accepted` / 고칠게요 → `candidate_edited` / 아니에요 → `candidate_rejected` + `failureReasons`
  (한 사람만 → `overfit_question`, 뭉뚱그림 → `overbroad_question`, 같은 얘기 → `duplicate_claim`, 리뷰에 없음 → `unsupported_claim`).
- 기각인데 후보 인용이 원문과 달라 계약을 못 통과하면 라벨은 안 만들고 `decisions` 에만 남긴다 (화면에 그렇게 알린다).
- 완료는 후보를 전부 판단하고 "놓친 질문" 에 답(추가 또는 없어요)했을 때만.
- 방향은 근거에서 계산(`derive_direction`), 언급 없음(`silent`)은 따로 표시 — `docs/DECISION_PER178_SILENCE_IS_NOT_EVIDENCE.md`.

테스트: `python3 -m unittest pipeline.test_label_review`
