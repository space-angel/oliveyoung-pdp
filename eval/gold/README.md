# `eval/gold/` — 사람이 만든 평가 고정물

여기 있는 파일은 **재생성되지 않는다.** 스크립트를 다시 돌려도 안 나온다 — 사람이 판단해서
만든 것이고, 모델 산출물을 채점하는 기준이다. 그래서 `data/intermediate/`(재생성물, gitignore)가
아니라 커밋되는 위치에 둔다.

## 이름 규칙

`<대상>_<용도>` 로 쓴다. 무엇을 채점하는 기준인지가 이름에서 보여야 한다.

| 접두어 | 무엇을 채점하나 | 이슈 |
|---|---|---|
| `v5_tag_pilot_*` · `v5_tags_pilot_gold` | **입수 태깅** — (리뷰 × aspect) 단위 aspect/polarity | PER-175 |
| (예정) `v5_concern_golden_*` | **주장·질문 생성** 결과. 별개 파일이다 | PER-179~181 |

두 골든셋은 단위가 다르다 — 태깅은 (리뷰 × aspect) 행이고, 생성 골든셋은 주장 단위다.
같은 디렉터리에 있어도 섞어 쓰지 않는다.

## 현재 파일 (PER-175)

| 파일 | 무엇 |
|---|---|
| `v5_tag_pilot_sample.jsonl` | 표본 200건 전문. 시드 20260904 층화 추출의 **고정물** |
| `v5_tag_pilot_meta.json` | 시드·층별 가중치·입력 스냅샷 sha256 |
| `v5_tags_pilot_gold.jsonl` | 손으로 만든 태그 365개. **채점 기준** |

### 키는 `reviewId` 뿐이다

정답셋 행에는 `productId` 가 없다 (의도적). 리뉴얼 세대가 갈리면(PER-172) 같은 `goodsNo` 의
`productId` 가 날짜에 따라 나뉘므로, `productId` 로 키를 잡으면 나중에 깨진다.

`v5_tag_pilot_sample.jsonl` 안의 `productId` 는 **추출 시점의 스냅샷**이고 참고값이다.
채점(`eval/validate_tags.py --against`)은 `(reviewId, aspect)` 로만 대조한다.

### 재현 절차와 막히는 지점

```bash
python3 pipeline/ingest.py            # 25K 적재
python3 pipeline/sample_tag_pilot.py --check   # 표본이 고정물과 같은지
```

- 새 수집분이 들어오면 `ingest.py` 가 `assert_snapshot_current()` 에서 **멈춘다**
  (`pipeline/policy.SNAPSHOT_LATEST_MONTH` 갱신 필요, PER-172). 재현이 그 지점에서 막히면
  스냅샷이 바뀐 것이지 표본이 틀린 게 아니다.
- `--check` 가 실패하면 표본을 덮어쓰기 전에 멈춰라. 표본이 달라지면 이 정답셋이 통째로
  무의미해진다.
