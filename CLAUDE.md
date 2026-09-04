# oliveyoung-pdp — 작업 규칙

## 이 저장소의 역할

올리브영 PDP 리뷰 → 구매 고민 질문(concern) 생성. 크롤러와 파이프라인이 한 저장소에 있다.
구조와 현재 상태는 `README.md`, 근거 인벤토리는 `docs/V5_INPUTS_AND_LEGACY_AUDIT.md`.

**작업 대상은 `pipeline/`(v5)다.** v4는 `legacy/v4/`로 동결했다 — 비교 기준선이므로 고치지 않는다. v5는 v4 코드를 수정해 쓰지 않고 새로 쓴다. v5 입력 계약은 `pipeline/contracts.py`(원문/조건/파생 3층), 단계 목록은 `pipeline/run_v5.py --list`.

## 데이터 규칙

- `data/input/` 은 **read-only로 취급한다.** 새 수집분은 파일명을 바꿔 추가하고 기존 파일을 덮어쓰지 않는다.
- `data/intermediate/`, `data/cache/` 는 gitignore. 재실행하면 다시 생긴다.
- `data/output/` 과 `eval/reports/` 는 **커밋한다.** 평가 수치의 1차 근거다.
- 집계 단위는 `productId`(50개, `data/input/product_catalog.json`)다. `goodsNo`(153개)는 변형 SKU가 섞인 단위이므로 그룹핑 키로 쓰지 않는다.
- **제품 동일성은 카탈로그 레이어가 소유한다** (PER-171). 리뷰 행의 `productKey` 문자열을 읽지 말고 `goodsNo`를 `pipeline/catalog.py`에 물어라. **미등록 `goodsNo`는 조용히 폴백하지 않고 에러다.** 매핑을 코드 상수로 들지 않는다. 카탈로그는 생성물이므로 손으로 고치지 말고 `pipeline/build_product_catalog.py`를 다시 돌린다. 규칙·운영 절차는 `docs/PRODUCT_CATALOG.md`.
- **리뉴얼은 별개 제품이다** (PER-172). 세대 경계의 키는 `goodsNo`가 아니라 **`(goodsNo, reviewDate)`** 다 — `goodsNo` 교체는 멀티-SKU 제품 36개 중 1개만 리뉴얼이고, 세대가 SKU 코드 안쪽에서 갈리는 제품이 11개다. 세대는 별개 `productId` + 같은 `lineageId`. **`renewalPolicy`에 `null`을 쓰지 않는다** — 정하지 않았으면 `unobserved`로 명시하고, 그 사실이 주장의 `limitation`으로 나간다. 현 스냅샷은 전 제품 `unobserved`라 **리뉴얼 컷의 실효는 0**이다.
- **리센시 컷은 스냅샷 최신 월 기준 24개월(`2024-09`~)이다.** `today` 기준 롤링을 쓰면 재현성이 깨지므로 금지. 새 수집분이 들어오면 `pipeline/policy.py`의 `SNAPSHOT_LATEST_MONTH`를 갱신해야 하고, 안 하면 입수가 에러를 낸다. 근거는 `docs/DECISION_PER172_RENEWAL_AND_RECENCY.md`.
- **두 컷 모두 드롭이 아니라 `rejected[]` 행이다.** 리뷰를 지우면 재현율을 영영 못 잰다.
- **작성자 키는 `NFC(userName)` 원문이다** (PER-170 확정). 중복 판정 단위는 `(작성자 키, productKey)`이고 근거 카운트는 리뷰 수가 아니라 **고유 작성자 수**를 센다. 게이트를 안 걸면 카운트가 코퍼스의 19.7% 부풀고, 본문 해시는 그 중 12.1%만 잡는다. 근거·한계는 `docs/DECISION_PER170_AUTHOR_IDENTIFIER.md`.
- **태그 단위는 리뷰가 아니라 `(리뷰 × aspect)`다** (PER-175). 한 리뷰가 "발색은 좋은데 지속력은 별로"라고 말한다 — 실측으로 한 리뷰 안 방향 갈림 20.5%, 별점과 태그 방향 불일치 12.9%다. **별점을 방향의 대리값으로 쓰지 않는다.** 같은 리뷰에 같은 aspect를 두 번 넣지 않고, 방향이 갈리는데 나눌 수 없으면 태그를 만들지 않는다.
- **`skinTypeHint`는 라벨이 아니라 코드(A01~A07)로 받는다.** 조건축과 같은 어휘여야 집계에서 합쳐진다 — v4는 라벨로 받아 힌트를 통째로 잃었고 그게 카테고리 분포 FAIL의 원인이었다. 프로필과 불일치하면(표본 28건 중 6건) **자동으로 덮어쓰지 않고 플래그만 남긴다.**
- **인용 대조는 보이지 않는 문자만 접는다** (`tag_contract.fold_invisible`). 원문에 CRLF 46.3%, Zs 공백 변종 1.1%가 섞여 있어 접지 않으면 정상 인용이 탈락한다. 글자·띄어쓰기 수는 그대로라 재구성은 여전히 탈락한다. **공백 전체 squeeze는 금지** — 띄어쓰기를 지운 편집까지 통과시킨다. 중복 판정(`contentHash`)에는 이 정규화가 필요 없다(증가분 0).
- **aspect 택소노미 14종은 동결이다.** PER-201 v4 대비 비교의 축이라 바꾸면 개선분이 귀속되지 않는다. 확장은 별도 이슈에서 근거를 대고 `docs/DECISION_PER175_TAGGING_CONTRACT.md`를 고친다.
- **`eval/gold/`는 손으로 만든 평가 고정물이다 — 재생성되지 않는다.** 표본이 달라지면 정답셋이 통째로 무의미해지므로 `sample_tag_pilot.py --check`가 게이트에 들어 있다. 실패하면 표본을 덮어쓰기 전에 원인부터 본다. 규칙은 `eval/gold/README.md`.
- `profileImageUrl`은 키에 넣지 않고 **감사 필드로 유지한다.** 한 이름에 서로 다른 URL이 2개 이상 나타나면 분리 후보로 플래그하되 자동 분리는 하지 않는다 (현 스냅샷 0건).

## 서술 규칙 — 이게 포트폴리오 방어선이다

- 실행 로그·리포트 파일로 확인되지 않은 수치는 쓰지 않는다. 근거 파일 경로를 함께 남긴다.
- v4 결과를 "PASS"로만 요약하지 않는다. 카테고리 분포 1/5, 리스크 질문 3/5는 FAIL이었고 종합 합격 산식에 빠져 있었다.
- concern의 인용문(`positiveSnippets`/`negativeSnippets`)은 **원문 부분문자열이어야 한다.** v4에서 88.8%였고, 원문에 없는 수치를 생성한 사례가 있었다. 요약·재구성은 인용이 아니다.

## 커밋 규칙

- v5 변경은 v4 베이스라인 위에 쌓는다. `git log -p`가 개선 근거이므로, 무관한 정리와 로직 변경을 한 커밋에 섞지 않는다.
- `legacy/v4/`와 v4 산출물(`data/output/concerns_v4.json`, `eval/reports/eval_report_v4.*`)은 건드리지 않는다. PER-201 v4 대비 비교의 근거다.
- v5 코드는 계약 테스트를 함께 낸다 — `python3 -m unittest discover -s pipeline -p 'test_*.py'`. "에러를 낸다"는 완료 조건은 테스트로 고정한다.
- `.env` 는 절대 커밋하지 않는다. 키가 필요하면 `.env.example` 을 갱신한다.
- 리뷰 데이터의 `userName`·`profileImageUrl`은 프로덕션 PDP에 그대로 노출되는 값이고 개인정보법상 제약이 없음을 확인했다 (2026-09-03). 중복 판정에 필요하므로 드롭하지 않는다.
