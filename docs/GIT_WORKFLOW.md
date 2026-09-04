# 브랜치 전략

작성: 2026-09-03 · 담당 1인 · 원격 `origin` = `space-angel/oliveyoung-pdp` (PRIVATE)

`git log -p`가 개선 근거다(`CLAUDE.md`). PER-201(v4 대비 비교)과 9-1(개선 귀속)이 **이슈별 diff**를 요구하므로, 히스토리는 "무엇을 바꿔서 무엇이 좋아졌는지"를 스스로 설명해야 한다. 아래 규칙은 전부 그 목적에서 나온다.

---

## 1. 브랜치 3종만 쓴다

| 종류 | 이름 | 규칙 |
|---|---|---|
| 정본 | `main` | 항상 `origin/main`과 동일. 직접 커밋하지 않는다 |
| 이슈 | `per-<번호>-<ascii-slug>` | 이슈 1개 = 브랜치 1개 = 워크트리 1개. 예: `per-171-product-catalog` |
| 실험 | `exp/<주제>` | **병합하지 않는다.** 리포트만 이슈 브랜치로 옮기고 브랜치는 버린다 |

Linear가 제안하는 한글 브랜치명(`wonderhy11/per-170-블로커-작성자-…`)은 쓰지 않는다 — `git worktree list`에서 깨져 보이고 셸에서 다루기 나쁘다. 이름에 `per-171`이 들어 있으면 Linear 연결은 그대로 동작한다.

실험 브랜치를 병합하지 않는 이유: 임계값·프롬프트 탐색은 대부분 기각된다. 기각된 시도를 main에 남기면 "무엇이 현재 설정인지"가 흐려진다. 채택된 값은 **설정 파일의 버전 변경**으로 이슈 브랜치에서 들어온다(PRD §9).

## 2. 병합은 rebase → `merge --no-ff`

```bash
# 이슈 브랜치에서
git fetch origin
git rebase origin/main
bash scripts/verify.sh

# 병합
git switch main
git merge --no-ff per-171-product-catalog -m "merge: PER-171 제품 동일성 카탈로그"
git push origin main
git branch -d per-171-product-catalog
```

- **rebase**: 내용은 선형으로 유지 → `git bisect`가 동작하고 diff 읽기가 쉽다
- **`--no-ff`**: 머지 커밋 하나가 이슈 경계를 남긴다 → 이슈 전체 diff를 한 번에 뽑을 수 있다

```bash
git diff <merge>^1...<merge>^2      # 그 이슈가 바꾼 전부
git log --grep=PER-171 -p           # 커밋 단위로
```

**squash 하지 않는다.** `decide:` 커밋 하나하나가 결정 이력이고, PER-170처럼 결정을 뒤집은 이력도 근거다(해시 → 원문 정정). 뭉개면 손실이다.

이미 push한 이슈 브랜치는 rebase하지 않는다 — 그 경우 `git merge origin/main`으로 main을 받아온 뒤 병합한다.

## 3. 병합 게이트

```bash
bash scripts/verify.sh
```

계약 테스트 + 생성물 재현 확인(`--check` 2종)을 돌린다. 병합 전과 병합 후 **양쪽에서** 돌린다 — 병합 자체가 생성물과 소스를 어긋나게 만들 수 있다.

### 생성물은 병합하지 말고 재생성한다

`data/input/product_catalog.json`, `eval/reports/*.json`, `data/intermediate/v5_*`는 생성물이다. 브랜치마다 다시 만들어지니 충돌이 잦고, 손으로 병합하면 소스와 어긋난 파일이 커밋된다.

**충돌 시:** 어느 쪽도 고르지 않고 생성기를 다시 돌린다.

```bash
git checkout --ours data/input/product_catalog.json   # 아무 쪽이나 (곧 덮어쓴다)
python3 pipeline/build_product_catalog.py
python3 pipeline/ingest.py
bash scripts/verify.sh
```

생성기가 출력에 시각을 넣지 않고 소스 sha256만 기록하는 이유가 이것이다 — 같은 소스면 바이트가 같아서 충돌이 재생성으로 해소된다.

## 4. 마일스톤 경계에 annotated tag

```bash
git tag -a m2-data-contract -m "#2 데이터 계약 & 입수 태깅 완료 (PER-169~176)"
git push origin m2-data-contract
```

| 태그 | 시점 |
|---|---|
| `m2-data-contract` | #2 완료 — 25K가 v5 스키마로 적재, 입력 계약 고정 |
| `m3-golden-set` | #3 완료 — 택소노미 + 골든셋 100건 |
| `m4-gates` / `m5-claims` / `m6-judge` | 각 마일스톤 완료 |
| **`v5-judgement`** | **09-16 판정 시점.** 리포트 수치의 고정 참조점 |

PRD §8-2("버전을 서로 연결한다")를 태그로 만족시킨다. 리포트에 적힌 수치가 어느 코드에서 나왔는지를 태그로 되짚을 수 있어야 나빠졌을 때 되돌릴 수 있다.

## 5. 커밋 규약

`<type>: <요약> (PER-xxx)`

| type | 쓰는 곳 |
|---|---|
| `decide:` | 결정 기록 — 문서 + 근거 스크립트/리포트 |
| `feat:` `fix:` `refactor:` | 코드 |
| `data:` | 수집분·생성 데이터 |
| `eval:` | 평가 리포트만 갱신 |
| `docs:` `chore:` | 문서·잡일 |
| `merge:` | 이슈 병합 커밋 |

본문에는 **수치와 근거 파일 경로**를 적는다. "무관한 정리와 로직 변경을 한 커밋에 섞지 않는다"(`CLAUDE.md`).

### 본문에 붙이는 트레일러 3종

템플릿은 `.gitmessage`에 있다(`git config commit.template .gitmessage`로 연결).

```
기각: <검토했지만 하지 않은 것 + 사유>
원인: <계약위반 | 데이터 | 코드결함 | 프롬프트·모델 | 문서드리프트 | 외부의존>
재발방지: <테스트·게이트 경로> | 없음 (<사유>)
```

**`기각:` — 대안을 비교했으면 필수.** 이 규칙은 [토스 QA 플랫폼 팀의 핫픽스 기록 방식](https://toss.tech)에서 가져왔다. 진행한 것만 쌓으면 "지난번엔 왜 안 했지?"에 답할 수 없고, 판단이 사람 기억에만 남는다. 사유가 쌓이면 그게 기준이 된다.

우리 출력 스키마의 `rejected[]`와 같은 사상이다(PRD §6) — *통과한 것만 남기면 정밀도는 측정되지만 재현율은 영영 측정되지 않는다.* 커밋도 마찬가지다. 실제 사례:

- PER-170 — `기각: profileImageUrl 조합키(식별력 증가분 0, 작성자 12명 거짓 분리) / 작성자 1표 포기(잔존 오염 19.7%)`
- PER-171 — `기각: 표시명을 키로 유지(v4에서 이름 드리프트 10쌍·브랜드 오기 1건 발생)`

**`원인:` — `fix:` 커밋에만.** 6분류로 고정한다. 나중에 `git log --grep='^원인: 데이터' --oneline | wc -l`로 **무엇이 되풀이되는지**를 센다. 되풀이되는 원인이 다음에 먼저 막을 것이다.

| 원인 | 예 |
|---|---|
| `계약위반` | 미등록 `goodsNo`를 조용히 폴백, 스키마 검증 누락 |
| `데이터` | 스냅샷·생성물이 소스와 어긋남 (v4 맵 브랜드 오기) |
| `코드결함` | 로직 버그 |
| `프롬프트·모델` | 인용 파라프레이즈, 출력 형식 위반 |
| `문서드리프트` | 코드는 맞고 문서·이슈 설명이 낡음 (해소된 블로커가 표에 남아 오판을 부름) |
| `외부의존` | 올리브영 API 변화, 레이트리밋, 코드북 조회 실패 |

**`재발방지:` — `fix:` 커밋에만.** 테스트나 게이트 경로를 적는다. 못 걸었으면 `없음 (사유)`라고 **적는다.** 비워두지 않는 게 요점이다 — 미이행이 보이지 않으면 "적어둔 다짐"으로 끝난다. `CLAUDE.md`의 "'에러를 낸다'는 완료 조건은 테스트로 고정한다"가 이 트레일러의 근거다.

### 기록을 어렵게 만들지 않는다

토스가 한 번 틀린 지점이 이것이다 — 사후기록을 번거롭게 만들면 행동이 아니라 **기록이 줄어든다.** 그래서:

- 트레일러는 **해당될 때만** 쓴다. `docs:` 커밋에 `원인:`을 강제하지 않는다
- 템플릿을 파일로 둬서 빈 화면에서 시작하지 않는다
- 초안은 도구가 채우고 사람은 검토·보탠다(이 저장소에서는 Claude가 초안을 쓴다)

### 마일스톤마다 원인 분포를 센다

태그를 붙일 때 한 번씩 돌린다. 이건 회고 자료이자 다음에 막을 것의 목록이다.

```bash
git log m2-data-contract..HEAD --grep='^원인:' --pretty=%b | grep '^원인:' | sort | uniq -c | sort -rn
git log --grep='^재발방지: 없음' --oneline    # 미이행 목록
```

## 6. 워크트리 (Orca)

- 브랜치 1개 = 워크트리 1개. 워크트리 이름은 이슈 키로.
- **`git stash` 금지** — 스택이 워크트리 간 공유다(`CLAUDE.md`). 작업을 치워둘 땐 WIP 커밋을 쓴다.
- 병합 후 브랜치와 워크트리를 함께 정리한다. 안 지우면 `main`이 어디인지 헷갈린다.
- 다른 워크트리에서 진행 중인 작업이 있으면 **먼저 그쪽을 병합**하고 rebase한다. 2026-09-03에 코드북(PER-169)이 병합 안 된 채로 남아 있어서, 이 워크트리가 "블로커가 아직 살아 있다"고 오판했다.

## 7. 푸시

- 이슈 브랜치도 push한다(백업). 원격은 PRIVATE.
- `main`은 병합 직후 push. main이 로컬에만 있으면 개선 근거가 한 머신에 묶인다.
- `.env`는 절대 커밋하지 않는다(`CLAUDE.md`).

---

## 부록 — 자주 쓰는 명령

```bash
# 새 이슈 시작
git switch main && git pull
git switch -c per-177-failure-taxonomy

# 이슈 전체 diff (병합 후)
git log --merges --oneline | head            # 머지 커밋 찾기
git diff <merge>^1...<merge>^2

# v4 대비 비교용 — v5 도입 전 상태
git diff 00daebe..HEAD -- pipeline/ eval/

# 마일스톤 사이에 바뀐 것
git diff m2-data-contract..m3-golden-set --stat
```
