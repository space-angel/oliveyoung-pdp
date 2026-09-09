# 골든셋 후보 생성 — 다른 모델을 위한 작업 지침 (PER-178)

이 문서는 **사람이 아니라 모델(에이전트)이 읽고 그대로 수행**하는 지침이다. 당신의 일은 리뷰 묶음 40개를 읽고
구매 고민 질문–답 후보를 JSON 파일로 저장하는 것이다. 후보는 사람이 검수해 채택·수정·기각한다.
**정답을 만드는 것이 아니다** — 리뷰가 실제로 말하는 것만 적고, 확신이 없으면 만들지 않는다.

## 0. 작업 위치

```
/Users/banjax.index/orca/workspaces/oliveyoung-pdp/per-178        ← 이 워크트리에서만 작업한다
```

다른 워크트리·브랜치에서 하지 않는다. 결과 파일이 이 브랜치의 도구와 짝이어야 한다.

## 1. 하지 말 것 (블라인드 규칙)

이 후보는 파이프라인 산출물을 채점하는 골든셋의 재료다. 채점 대상을 미리 보면 순환이 된다.

- **읽지 않는다:** `eval/gold/v5_concern_golden_labels.jsonl`(사람 라벨), `eval/gold/v5_tags_pilot_gold.jsonl`,
  `data/output/`, `legacy/v4/`, `eval/reports/`, `pipeline/prompts/`, 올리브영 사이트.
- **번들 밖 리뷰를 찾지 않는다.** `data/intermediate/v5_reviews.jsonl` 도 열지 않는다. 각 번들 파일 하나가 그 번들의 전부다.
- **수정하지 않는다:** `eval/gold/` 아래 파일 전부, `pipeline/`, `eval/*.py`. 당신이 쓰는 파일은 §3 의 출력 폴더 안 JSON 만이다.
- 별점으로 방향을 정하지 않는다. 문장으로 정한다.

## 2. 입력

```
data/intermediate/v5_concern_golden_harness/B01.md … B40.md
```

없으면 먼저 만든다 (LLM 호출 없음, 몇 초):

```bash
python3 pipeline/ingest.py                              # data/intermediate/v5_reviews.jsonl 이 없을 때만
python3 eval/concern_candidates.py export --phase all   # 하네스 40개 생성
```

각 `Bxx.md` 는 **자기완결적**이다 — 역할·규칙·출력 형식·리뷰 전문이 한 파일에 있다. 번들 하나를 작업할 때
그 파일 **하나만** 읽는다. 다른 번들 파일을 참고하지 않는다(제품이 다르다).

파일 머리에 "**셀 번들**" 이라고 적혀 있으면 모든 후보의 `condition` 에 그 세그먼트가 들어가야 한다. "**제품 번들**"이면
조건은 `null` 이 기본이고, 특정 세그먼트 리뷰들만 말하는 결과일 때만 코드를 붙인다.

## 3. 출력

```
data/intermediate/v5_concern_golden_candidates_raw/B01.json … B40.json
```

- 파일 하나 = 번들 하나. 파일명은 번들 ID 와 정확히 같다 (`B01.json`).
- 내용은 `Bxx.md` 끝의 "출력 형식" 에 있는 JSON **하나**. 코드 블록 표시·설명 문장 없이 JSON 만 쓴다.
- `bundleId` 는 그 파일의 번들 ID.
- 후보는 **3~6개**. 여러 리뷰가 반복하는 주제부터. 자극·트러블·악화·파손 같은 리스크 주제가 리뷰에 있으면 **반드시 하나** 넣는다.
- `quote` 는 그 리뷰 본문에서 **글자 그대로 복사한 연속 구간**. 한 글자도 바꾸지 않는다. 띄어쓰기·오타도 그대로.
  이 규칙을 어기면 그 후보는 자동으로 "계약 위반" 표시가 붙는다 (버려지진 않지만 사람이 고쳐야 한다).
- `reviewId` 는 그 번들 파일에 있는 것만. `evidence` 는 리뷰당 하나.
- 방향이 갈리면 `direction: "mixed"`, 그때 `answer` 는 부정 관측을 주어로 (예: "구순염이 생겼다는 리뷰가 있다").
  생겼다 = `support`, 안 생겼다 = `oppose`.
- `aspect` 는 14개 중 하나, 없으면 `null` 로 두고 `note` 에 주제를 적는다. 14개 밖의 단어를 `aspect` 에 넣지 않는다.
- 답에 원문에 없는 숫자·인과·근거 수를 넣지 않는다.

## 4. 순서

1. B01 부터 B40 까지 번호순으로. 파일럿(B01~B16)을 먼저 끝낸다.
2. 번들마다: `Bxx.md` 읽기 → 후보 작성 → `Bxx.json` 저장 → 다음 번들. 번들 간에 기억을 이어 쓰지 않는다.
3. 40개가 끝나면 (또는 파일럿 16개만 끝났으면) 들여오기를 한 번 돌려 계약 통과율을 확인한다:

```bash
python3 eval/concern_candidates.py import-dir --model "<당신의 모델 이름과 버전, 날짜>"
python3 eval/concern_candidates.py status
```

`--model` 은 정확히 적는다 (예: `"GPT-5 Codex (2026-09-09)"`). 이 값이 후보 파일에 남고, 나중에 파이프라인 생성기와
judge 는 이 모델을 피한다. 모른다고 비워두지 않는다.

4. `import-dir` 출력에서 `계약 통과` 가 후보 수보다 적으면 대부분 인용(`quote`)이 원문과 다른 것이다.
   해당 번들의 `Bxx.json` 에서 그 인용을 원문에서 다시 복사해 고치고, `--replace` 로 다시 들여온다:

```bash
python3 eval/concern_candidates.py import B07 data/intermediate/v5_concern_golden_candidates_raw/B07.json --model "<같은 값>" --replace
```

   단, 이미 사람 결정(라벨)이 붙은 번들은 교체가 거부된다. 그때는 손대지 않고 사람에게 남긴다.

## 5. 끝났을 때 보고할 것

- 처리한 번들 수, 후보 총수, 계약 통과 수 (`status` 출력 그대로)
- 후보를 3개 미만으로 낸 번들과 이유 (리뷰가 구체적 주제를 말하지 않음 등)
- 리스크 주제를 넣지 못한 번들 (리뷰에 부정 경험이 없음)
- `aspect: null` 로 둔 후보의 `note` 에 적은 주제 목록 — 14종 밖 주제의 실측이 된다

커밋은 하지 않는다. `eval/gold/v5_concern_golden_candidates.jsonl` 이 갱신된 상태로 두면 사람이 확인 후 커밋한다.
