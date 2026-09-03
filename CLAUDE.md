# oliveyoung-pdp — 작업 규칙

## 이 저장소의 역할

올리브영 PDP 리뷰 → 구매 고민 질문(concern) 생성. 크롤러와 파이프라인이 한 저장소에 있다.
구조와 현재 상태는 `README.md`, 근거 인벤토리는 `docs/V5_INPUTS_AND_LEGACY_AUDIT.md`.

## 데이터 규칙

- `data/input/` 은 **read-only로 취급한다.** 새 수집분은 파일명을 바꿔 추가하고 기존 파일을 덮어쓰지 않는다.
- `data/intermediate/`, `data/cache/` 는 gitignore. 재실행하면 다시 생긴다.
- `data/output/` 과 `eval/reports/` 는 **커밋한다.** 평가 수치의 1차 근거다.
- 집계 단위는 `productKey`(50개)다. `goodsNo`(153개)는 변형 SKU가 섞인 단위이므로 그룹핑 키로 쓰지 않는다.
- **작성자 식별자는 `authorHash`뿐이다** (PER-170 확정). 입수 파서가 `HMAC-SHA256(AUTHOR_HASH_SALT, NFC(userName))[:16]`로 바꿔 넣고 `userName` 원문은 저장하지 않는다. `profileImageUrl`·`reviewImages`는 드롭. 중복 판정 단위는 `(authorHash, productKey)`이고 근거 카운트는 고유 `authorHash` 수를 센다. 근거·한계는 `docs/DECISION_PER170_AUTHOR_IDENTIFIER.md`.
- v5 파이프라인은 `data/input/reviews_50products.json`(PII 포함 원본)을 직접 읽지 않는다. 비식별 파생본만 입력으로 받고, PII 필드가 남아 있으면 **에러를 낸다** — 조용히 드롭하지 않는다.

## 서술 규칙 — 이게 포트폴리오 방어선이다

- 실행 로그·리포트 파일로 확인되지 않은 수치는 쓰지 않는다. 근거 파일 경로를 함께 남긴다.
- v4 결과를 "PASS"로만 요약하지 않는다. 카테고리 분포 1/5, 리스크 질문 3/5는 FAIL이었고 종합 합격 산식에 빠져 있었다.
- concern의 인용문(`positiveSnippets`/`negativeSnippets`)은 **원문 부분문자열이어야 한다.** v4에서 88.8%였고, 원문에 없는 수치를 생성한 사례가 있었다. 요약·재구성은 인용이 아니다.

## 커밋 규칙

- v5 변경은 v4 베이스라인 위에 쌓는다. `git log -p`가 개선 근거이므로, 무관한 정리와 로직 변경을 한 커밋에 섞지 않는다.
- `.env` 는 절대 커밋하지 않는다. 키가 필요하면 `.env.example` 을 갱신한다.
- 커밋된 원본 스냅샷 3개(`reviews_50products.json`, `reviews_200_normalized.json`, `v4_reviews_500.json`)에는 `userName`·`profileImageUrl`·`reviewImages`가 남아 있다. read-only 규칙에 따라 그대로 두되, **이 저장소는 비공개 유지가 전제다.** 원격 공개 전환은 파일 삭제로 부족하고 `9ea3ade`를 포함한 히스토리 재작성이 필요하다.
- `AUTHOR_HASH_SALT`는 `.env`에만 둔다. 솔트가 바뀌면 스냅샷 간 `authorHash`가 달라져 중복 판정이 깨지므로, 교체 시 `AUTHOR_HASH_VERSION`을 올리고 전체 재해시한다.
