# 리뷰 질문 검수 — 외부 라벨러용 앱 (PER-178)

골든셋(PER-179/180)을 여러 사람이 나눠 만들 수 있게 한 도구. 라벨러가 보는 개념은 넷이다:
**질문·답, 색칠된 근거 문장(긍정/부정/중립), 놓친 리뷰, 아니에요 이유 4개.** 나머지(라벨 ID·출처·조건·방향·
실패유형 키·평가상태)는 서버가 채운다. 계약 검증은 `pipeline/golden_contract.py` 그대로다 — 이 앱이 규격을
느슨하게 만들지 않는다. 화면 흐름 목업: https://claude.ai/code/artifact/6468fd3e-8e27-4ee9-a545-e9a0ae64fb16

```
apps/label-review/
  server.py            HTTP (표준 라이브러리). `--port` 인자. Vercel 은 `handler` 클래스를 그대로 쓴다
  service.py           배정·판정·완료 규칙. 일상어 사유 4개 → PER-177 키 매핑 (REASONS)
  store.py             LocalStore(JSONL) / SupabaseStore(REST, urllib)
  sync_supabase.py     Supabase ↔ 정본(eval/gold) 동기화 CLI: push · pull · status
  admin.py             관리자 진행 현황 — 저장소를 읽기만 한다(번들 40개 상태·요약·라벨러 표)
  public/index.html    프론트 한 파일. 다크 톤 (toss.im/simplicity-24 토큰: #111213 · #0099FF · #4BEC8B · #DDF34F)
  public/admin.html    관리자 화면 한 파일
  supabase/schema.sql  테이블 4개 (labelers · assignments · labels · decisions)
  Dockerfile           컨테이너 호스트용 (컨텍스트는 저장소 루트, 제외는 루트 .dockerignore)
../../render.yaml      Render 블루프린트 (runtime python, 빌드 없음)
```

## 로컬로 켜기

```bash
python3 apps/label-review/server.py             # http://127.0.0.1:8180
python3 apps/label-review/server.py --port 9000
LABEL_ADMIN_KEY=비밀 python3 apps/label-review/server.py      # → http://127.0.0.1:8180/admin (관리자 현황)
```

`pipeline/ingest.py` 는 필요 없다 — 번들 고정물 `eval/gold/v5_concern_golden_sample.jsonl` 이 리뷰 원문을 포함하고
서버는 `data/intermediate/` 를 읽지 않는다.

기본 저장소는 정본 파일이다: `eval/gold/v5_concern_golden_labels.jsonl` (라벨) · `eval/gold/v5_concern_golden_assignments.jsonl`
(라벨러·배정·결정). 라벨이 이미 있는 번들(B01~B05)은 배정에서 자연히 빠지므로 **B06 부터** 나간다.

정본을 건드리지 않고 시험할 때: `LABEL_REVIEW_DATA_DIR=/tmp/lr python3 apps/label-review/server.py`

## 환경변수

| 변수 | 무엇 | 기본 |
|---|---|---|
| `SUPABASE_URL` | Supabase 프로젝트 URL (`https://xxxx.supabase.co`). `SUPABASE_SERVICE_KEY` 와 둘 다 있으면 SupabaseStore | 없음 → LocalStore |
| `SUPABASE_SERVICE_KEY` | **service role** 키. 서버와 운영자의 `sync_supabase.py` 에서만 쓴다. anon 키가 아니고, 브라우저에 절대 내보내지 않는다 (RLS 정책이 없어 anon 은 전부 차단) | 없음 |
| `LABEL_ACCESS_CODE` | 라벨러에게 줄 접속 코드. 비우면 이름만으로 들어온다 | 없음 |
| `LABEL_MAX_BUNDLES` | 한 사람이 받을 수 있는 번들 수. `0` 은 무제한 | `2` |
| `LABEL_ASSIGNMENT_TTL_HOURS` | 손 놓은 활성 배정을 다른 사람에게 넘기는 시한(시간) | `48` |
| `LABEL_ADMIN_KEY` | 관리자 화면 키. 비어 있으면 `/admin` 과 `/api/admin/status` 는 404 로 닫힌다. 설정하면 `/admin` 에서 키를 한 번 넣고(브라우저 localStorage 에만 저장) 진행 현황을 본다. API 는 `?key=` 또는 헤더 `X-Admin-Key` 로 같은 값을 받는다 | 없음 |
| `LABEL_REVIEW_DATA_DIR` | LocalStore 전용 — 정본 대신 이 디렉터리의 `labels.jsonl` · `assignments.jsonl` 에 쓴다 | 정본 `eval/gold/` |

로컬에서는 저장소 루트 `.env` 에 넣어도 된다(`.env.example` 참고, **커밋 금지**). `server.py` 는 `.env` 를 읽지 않는다 —
`sync_supabase.py` 만 `.env` 를 읽는다. 서버에 줄 때는 호스트의 환경변수로 넣는다.

## 배포 (Render + Supabase)

1. **Supabase 프로젝트 생성** → SQL Editor 에서 `apps/label-review/supabase/schema.sql` 실행. 테이블 4개가 생기고 RLS 가
   켜진다(정책 없음 → service role 만 통과). Project Settings → API 에서 URL 과 **service_role** 키를 받아 둔다.
2. **정본을 먼저 올린다** — B01~B05 라벨이 DB 에 있어야 라벨러 배정에서 빠진다:
   ```bash
   # .env 에 SUPABASE_URL / SUPABASE_SERVICE_KEY 를 넣고
   python3 apps/label-review/sync_supabase.py push --dry-run   # 몇 건 올릴지 확인
   python3 apps/label-review/sync_supabase.py push             # 있는 행은 건너뜀 (덮어쓰려면 --overwrite)
   ```
3. **Render 에 올린다.** Dashboard → New → **Blueprint** → 이 저장소 → `render.yaml` 을 읽어 `label-review` 웹 서비스를 만든다
   (runtime python, 빌드 없음, `startCommand: python3 apps/label-review/server.py --port $PORT`). 또는 New → Web Service 에서
   같은 시작 명령을 손으로 넣어도 된다. Docker 로 올리려면 `apps/label-review/Dockerfile` 을 고르고 Docker Context 를 저장소 루트로.
4. **환경변수** — Render 서비스의 Environment 탭에 위 표의 값을 넣는다. `render.yaml` 은 전부 `sync: false` 라 대시보드에서
   직접 넣어야 한다(`.env` 는 커밋하지 않는다). 최소: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `LABEL_ACCESS_CODE`.
5. **배포 → 확인.** 로그 첫 줄이 `저장소 SupabaseStore · 접속 코드 있음` 이어야 한다. `LocalStore` 로 뜨면 환경변수가 안 들어간
   것이다 — 그 상태로 라벨러를 받으면 라벨이 컨테이너 안 파일에 쌓여 재배포 때 사라진다.
6. **진행 확인** — 둘 중 편한 쪽:
   - 브라우저: `https://<서비스>/admin` 에서 `LABEL_ADMIN_KEY` 를 한 번 넣는다 (번들 40개 상태·요약·라벨러 표, 읽기 전용).
   - 터미널(운영자 로컬):
     ```bash
     python3 apps/label-review/sync_supabase.py status     # 번들별 라벨 수 / 배정 상태 / 결정 수, 마지막에 완료 n / 40
     ```
   상태 판정 규칙(관리자 화면): **done** = 배정 done, 또는 라벨 있음 + 놓친 질문 답변(`no_missed`/`human`), 또는 배정 없이 라벨만(B01~B05);
   **active / expired** = 배정 status; 그 외 **free**. 관리자 화면은 읽기 전용이라 상태를 바꾸거나 배정을 풀지 않는다 — 푸는 건
   `sync_supabase.py` 나 SQL 로 한다.
7. **마감 후 정본으로 되돌린다:**
   ```bash
   python3 apps/label-review/sync_supabase.py pull --dry-run
   python3 apps/label-review/sync_supabase.py pull             # 계약 검증 → labelId 기준 병합(기존 유지, 새 것만 추가)
   python3 eval/label_concern_golden.py validate               # 게이트
   bash scripts/verify.sh
   ```
   `pull` 은 `labels.label` 전부를 `golden_contract.validate_label` 로 검증하고, 실패한 라벨은 병합하지 않은 채 stderr 에
   `labelId` 와 이유를 찍고 종료코드 1 을 낸다. 같은 `labelId` 가 정본에 이미 있으면 정본을 유지한다(`--overwrite` 면 DB 우선).
   누가 어떤 라벨을 만들었는지(labelId → labelerId · 이름 · created_at)와 결정·배정 전체는
   `eval/gold/v5_concern_golden_supabase_snapshot.json` 사이드카에 남는다 — 라벨러 간 일치도 분석용이고, 라벨 파일에는 라벨러
   필드를 넣지 않는다(계약에 없다). 병합된 라벨 파일과 스냅샷을 함께 커밋한다.

### 이미지에 들어가야 하는 파일

`service.py` 가 저장소 루트 기준 상대 경로로 읽는다. 빌드 컨텍스트는 **저장소 루트**여야 하고, `Dockerfile` 이 아래 존재를 빌드
시점에 확인한다(빠지면 빌드 실패). 루트 `.dockerignore` 로 `data/intermediate` · `data/cache` · `data/output` · 큰 원본 리뷰
JSON · `legacy` · `docs` · `.env` 를 뺀다 — 남는 컨텍스트는 3~4 MB 다.

| 경로 | 왜 |
|---|---|
| `apps/label-review/{server,service,store}.py` · `public/index.html` | 앱 본체 |
| `pipeline/{golden_contract,codebook,contracts,tag_contract,sample_concern_golden,policy,catalog}.py` · `pipeline/failure_taxonomy.json` | 계약·조건 어휘·방향 계산 |
| `pipeline/prompts/tag/v1.md` | `tag_contract` 가 참조 |
| `eval/{label_concern_golden,label_concern_golden_web,concern_candidates}.py` · `eval/aspect_keywords.json` | 조건 표기·읽기 보조 사전·후보 점검 |
| `eval/gold/v5_concern_golden_sample.jsonl` | 번들 40개, 리뷰 원문 포함 — 이게 있어 `ingest.py` 가 필요 없다 |
| `eval/gold/v5_concern_golden_candidates.jsonl` | 라벨러가 판정하는 후보 |
| `eval/gold/v5_concern_golden_labels.jsonl` · `_assignments.jsonl` | LocalStore 폴백용(Supabase 가 있으면 읽지 않는다) |
| `data/input/skin_codebook.json` · `data/input/product_catalog.json` | 조건 코드 도메인 · 제품 동일성 |

## 테스터 초대 안내문

복사해서 보내면 된다. `<링크>` · `<코드>` 만 채운다.

> 안녕하세요! 화장품 리뷰를 읽고 "구매 전에 물어볼 만한 질문"이 리뷰에 근거가 있는지 봐 주시는 일이에요.
>
> - 링크: `<링크>`
> - 접속 코드: `<코드>`
> - 이름은 기록용으로만 써요 (누가 어느 제품을 봤는지 구분). 화면에 그대로 뜨거나 밖으로 나가지 않아요.
>
> **데스크톱(노트북) 브라우저를 권해요.** 리뷰를 여러 개 같이 읽는 화면이라 휴대폰에서는 좁아요.
>
> 들어가시면 제품 1개가 배정되고, **첫 화면에서 30초 정도 짧은 안내가 자동으로 진행**돼요. 그 뒤엔 질문마다
> "맞아요 / 고칠게요 / 아니에요" 중 하나를 고르고, 마지막에 "놓친 질문이 있는지"만 답하시면 끝이에요.
>
> 제품 1개에 **30분 안팎**이 걸려요. 1~2개 부탁드리는데, **하나만 해 주셔도 충분**해요.
> 중간에 나가도 괜찮아요 — **같은 브라우저로 다시 들어오면 이어서** 하실 수 있어요.
>
> 막히는 게 있으면 편하게 말씀 주세요. 감사합니다!

## 배정·판정 규칙

- 배정: 라벨 0·활성 배정 없음인 번들 중 번호가 가장 낮은 것 1개. `assignments.bundle_id` 가 PK 라 두 사람이 동시에 잡으면 두 번째가 다음 번들로 간다.
  한 사람은 `LABEL_MAX_BUNDLES`(기본 2)개까지, `LABEL_ASSIGNMENT_TTL_HOURS`(기본 48)를 넘긴 활성 배정은 다른 사람에게 넘어간다.
- 맞아요 → `candidate_accepted` / 고칠게요 → `candidate_edited` / 아니에요 → `candidate_rejected` + `failureReasons`
  (한 사람만 → `overfit_question`, 뭉뚱그림 → `overbroad_question`, 같은 얘기 → `duplicate_claim`, 리뷰에 없음 → `unsupported_claim`).
- 기각인데 후보 인용이 원문과 달라 계약을 못 통과하면 라벨은 안 만들고 `decisions` 에만 남긴다 (화면에 그렇게 알린다).
- 완료는 후보를 전부 판단하고 "놓친 질문" 에 답(추가 또는 없어요)했을 때만.
- 방향은 근거에서 계산(`derive_direction`), 언급 없음(`silent`)은 따로 표시 — `docs/DECISION_PER178_SILENCE_IS_NOT_EVIDENCE.md`.

## 동기화 CLI 요약

```bash
python3 apps/label-review/sync_supabase.py push   [--overwrite] [--dry-run]   # 정본 → DB (labels·assignments·labelers·decisions upsert)
python3 apps/label-review/sync_supabase.py pull   [--overwrite] [--dry-run]   # DB → 정본 (계약 검증 → labelId 병합 + 스냅샷 사이드카)
python3 apps/label-review/sync_supabase.py status                              # 번들별 진행 표, 완료 n / 40
```

자격증명은 `.env`(저장소 루트, 있으면)와 환경변수에서 읽고 환경변수가 우선한다. 없으면 한국어 에러로 종료코드 2.
DB 의 `assignments` 는 `bundle_id` 가 PK 이고 status 체크가 `active|done` 이라, 로컬 파일의 `expired` 이력 행은 `push` 가 올리지 않는다(번들당 살아 있는 행 1개).

테스트: `python3 -m unittest pipeline.test_label_review pipeline.test_label_review_admin pipeline.test_sync_supabase`
