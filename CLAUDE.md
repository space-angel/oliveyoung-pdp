# oliveyoung-pdp — 작업 규칙

## 이 브랜치의 범위

`main` 은 **실행 경로만** 둔다 — 제품 링크를 받아 리뷰를 수집하고 질문–답 쌍을 만드는 데
필요한 코드와 최소 입력. 진입점은 `pipeline/run_url.py` 이고 구조는 `README.md` 에 있다.

평가·측정·결정 이력은 **`archive` 브랜치**에 있다. 여기서 그것들을 다시 만들지 않는다 —
필요하면 꺼내 쓴다.

```bash
git checkout archive -- data/input/reviews_50products.json   # 25K 정본 스냅샷
git checkout archive -- eval/ docs/ scripts/                 # 측정 · 결정 문서 · 병합 게이트
```

`archive` 에 있는 것: `eval/`(측정 스크립트·리포트 128개·골든셋) · `data/output/` ·
`docs/`(PER-170~193 결정 문서) · `legacy/v4/` · `scripts/verify.sh` · `apps/label-review` ·
25K 스냅샷.

**`archive` 를 `main` 에 병합하지 않는다.** 되돌리려면 그 브랜치에서 작업한다.

## 데이터 규칙

- `data/input/` 은 **read-only로 취급한다.** 새 수집분은 `data/runs/<runId>/` 로 간다.
- `data/runs/`, `data/intermediate/`, `data/cache/` 는 gitignore. 재실행하면 다시 생긴다.
- **모든 런 산출은 `data/runs/<runId>/` 안이다.** 경로는 `pipeline/workspace.py` 가 소유하고,
  `Workspace.assert_isolated()` 가 정본(`data/input`·`data/output`·`eval/*`)을 가리키지
  않는지 **크롤 전에** 검사한다. 비용을 내고 나서 아는 것은 늦다.
- **집계 단위는 `productId`다.** `goodsNo`(변형 SKU)를 그룹핑 키로 쓰지 않는다.
- **제품 동일성은 카탈로그 레이어가 소유한다** (PER-171). 리뷰 행의 `productKey` 문자열을
  읽지 말고 `goodsNo` 를 `pipeline/catalog.py` 에 물어라. **미등록 `goodsNo`는 조용히
  폴백하지 않고 에러다.** 런은 **수집분에서** 자기 카탈로그를 만든다 — 크롤러가 돌려주는
  `goodsNo` 는 요청 값과 다른 경우가 흔해서(제품당 평균 4종), 빠뜨리면 입수가 멈춘다.
- **리뉴얼은 별개 제품이다** (PER-172). 세대 경계의 키는 **`(goodsNo, reviewDate)`** 다.
  **`renewalPolicy`에 `null`을 쓰지 않는다** — 정하지 않았으면 `unobserved` 로 명시하고,
  그 사실이 주장의 `limitation` 으로 나간다.
- **리센시 컷은 24개월 창이다.** 정본 스냅샷은 `SNAPSHOT_LATEST_MONTH` 로 고정이고 **올리지
  않는다** — 올리면 그 값으로 만든 리포트가 전부 재현 실패한다. 런은 수집분에서 파생하되
  `derived: true` 를 기록에 남긴다 (`policy.SnapshotPolicy`).
- **컷은 드롭이 아니라 `rejected[]` 행이다.** 리뷰를 지우면 재현율을 영영 못 잰다.
- **작성자 키는 `NFC(userName)` 원문이다** (PER-170). 중복 판정 단위는 `(작성자 키, productId)`
  이고 근거 카운트는 리뷰 수가 아니라 **고유 작성자 수**다.
- **태그 단위는 리뷰가 아니라 `(리뷰 × aspect)`다** (PER-175). 한 리뷰가 "발색은 좋은데
  지속력은 별로"라고 말한다 — 실측으로 한 리뷰 안 방향 갈림 20.5%, 별점과 태그 방향 불일치
  12.9%다. **별점을 방향의 대리값으로 쓰지 않는다.**
- **조건 코드는 코드북(`data/input/skin_codebook.json`, 26종) 도메인 안이어야 한다** (PER-176).
  도메인 밖 코드·라벨(`건성`)·축 혼용은 조용히 새 세그먼트가 되지 않고 **에러다.**
- **입력이 계약을 위반하면 파이프라인이 멈춘다.** 필수 필드 결측·공백, `reviewId` 타입/중복,
  `rating` 범위, 날짜 파싱 실패, 모르는 필드, 미등록 `goodsNo` 전부 에러다.
  위반 클래스는 `pipeline/test_contract.py` 가 고정한다.
- **`usagePeriod`는 조건축이 아니다 — 데이터에 필드가 없다.** 본문에서 기간을 추정하지 않는다.
- **인용 대조는 보이지 않는 문자만 접는다** (`tag_contract.fold_invisible`). **공백 전체
  squeeze는 금지** — 띄어쓰기를 지운 편집까지 통과시킨다.
- **신뢰도 사전 점수는 필터가 아니라 가중치다** (PER-174). **`usefulPoint`를 좋아요 수로
  쓰지 않는다** — 올리브영의 자체 정렬 점수이고 크롤러가 그 순서로 수집했다.
  **가중치는 코드 상수가 아니라 `pipeline/trust_weights.json` 이다.**
- **충분성은 세 조건의 AND 다** (PER-186). `U ≥ N_min AND U/D ≥ R_min AND S ≥ S_min`.
  **분모는 주제를 언급한 고유 작성자 `D`이고 셀 크기 `S`가 아니다** — 말하지 않은 사람을
  어느 쪽 근거로도 세지 않는다(PER-178). **반대가 1명이어도 다수 방향으로 뭉개지 않는다** —
  컷이 아니라 `single_dissent` 한계로 남긴다.
- **aspect 택소노미 14종은 동결이다.** 확장은 근거를 대고 결정 문서를 고친다.
- **인용 상한이 방향을 통째로 지우지 않는다** (`context_layout._truncate`). 상한 때문에 한
  방향이 한 줄도 안 남으면 그 방향의 1위를 자리 배정한다 — 실측에서 긍정 309 대 부정 6인
  셀의 상위 8건이 전부 긍정이었다.

## 서술 규칙

- 실행 로그·리포트 파일로 확인되지 않은 수치는 쓰지 않는다. 근거 파일 경로를 함께 남긴다.
- **못 잰 것을 안 적지 않는다.** 새 제품 런은 골든셋이 없어 재현율을 못 잰다 —
  `run.json` 의 `evaluation` 에 사유와 함께, 잴 수 있는 것과 못 재는 것을 나눠 적는다.
  비워 두면 "안 나왔다"와 "나빴다"를 구분할 수 없다.
- claim 의 인용문은 **원문 부분문자열이어야 한다.** 요약·재구성은 인용이 아니다.

## 커밋 규칙

- `<type>: <요약>` — `feat:` `fix:` `refactor:` `data:` `docs:` `chore:` `merge:`
- 본문에 수치와 근거 파일 경로를 적는다. 무관한 정리와 로직 변경을 한 커밋에 섞지 않는다.
- 대안을 비교했으면 `기각:` 트레일러를 남긴다. 진행한 것만 쌓으면 "지난번엔 왜 안 했지?"에
  답할 수 없다.
- 코드 변경은 계약 테스트를 함께 낸다 — `python3 -m unittest discover -s pipeline -p 'test_*.py'`.
  "에러를 낸다"는 완료 조건은 테스트로 고정한다.
- `.env` 는 절대 커밋하지 않는다. 키가 필요하면 `.env.example` 을 갱신한다.
- 리뷰 데이터의 `userName`·`profileImageUrl`은 프로덕션 PDP에 그대로 노출되는 값이고
  개인정보법상 제약이 없음을 확인했다 (2026-09-03). 중복 판정에 필요하므로 드롭하지 않는다.

## 테스트

정본 스냅샷을 대조하는 계약 테스트 36건은 **스냅샷이 없으면 건너뛴다.** 지운 게 아니라
`archive` 에 있는 것이고, 받아오면 다시 돈다.

```bash
git checkout archive -- data/input/reviews_50products.json
python3 -m unittest discover -s pipeline -p 'test_*.py'
```
