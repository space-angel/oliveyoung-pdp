# v4 — 동결 (frozen)

v5 작업 중 **이 디렉터리는 고치지 않는다.** v4는 비교 기준선이고, 코드가 바뀌면
`data/output/concerns_v4.json`·`eval/reports/eval_report_v4.*` 수치의 재현 경로가 끊긴다.

## 왜 남겨두나

`docs/V5_SPRINT_PLAN.md` #6-6 / PER-201이 **v4 대비 비교 리포트**를 요구한다 —
인용 정확도 88.8%→?, 카테고리 분포 1/5→?, 리스크 질문 3/5→?.
그 수치의 출처가 이 코드다.

## 구성

```
legacy/v4/
├── pipeline/     step0 전처리 → step1 클레임 → step2 집계 → step3 축분류 → step4 생성
│   ├── run_pipeline.py    러너 (v5 러너는 pipeline/run_v5.py)
│   ├── schemas.py         v4 데이터 모델 (v5 계약은 pipeline/contracts.py)
│   └── prompts/           step1 · step3 · step4 프롬프트 v1
└── eval/
    ├── eval_v4.py         v4 평가 하네스
    └── golden_set.json    v4 골든셋 15문항 (5제품 × 3) — v5 골든셋 100건은 PER-177~181
```

산출물·리포트는 저장소 표준 위치에 그대로 둔다 (문서 여러 곳이 이 경로를 인용한다):

- `data/output/concerns_v4.json` — concern 30개 / 5제품
- `eval/reports/eval_report_v4.{md,json}`, `eval_report_v4_raw.json`
- `data/input/reviews_200_normalized.json`, `data/input/v4_reviews_500.json` — v4 입력

## 재실행 (필요할 때만)

```bash
python3 legacy/v4/pipeline/run_pipeline.py --steps 0        # 전처리만, LLM 없음
python3 legacy/v4/pipeline/run_pipeline.py --steps 0,1,2,3,4  # step1·3·4는 ANTHROPIC_API_KEY 필요
python3 legacy/v4/eval/eval_v4.py
```

중간 산출물 파일명(`data/intermediate/step*_*.json`)은 v5(`v5_*`)와 겹치지 않는다.

## v5로 이관된 것 / 안 된 것

| v4 | v5 |
|---|---|
| `step0_preprocess.py` — 행의 `productKey`로 그룹핑 | `pipeline/ingest.py` — `goodsNo` → 카탈로그 `productId` (PER-171) |
| `schemas.py` — 평면 Review | `pipeline/contracts.py` — 원문/조건/파생 3층 (PER-173) |
| 조건축 없음 (`skin_type_hint`가 step2에서 유실 — v4 FAIL 2건의 원인) | `condition.skinType`·`skinTrouble`·`option`, 미기재는 별도 세그먼트 |
| 중복 처리 없음 | `derived.authorKey`·`contentHash` → 게이트2 (PER-183) |
| step1~4 (LLM) | PER-175 / PER-182~195 로 재작성 예정 — v4 코드를 고쳐 쓰지 않는다 |

v4를 "PASS"로 요약하지 않는다 — 3개 조건 PASS / 2개 FAIL이고, 두 FAIL은 종합 합격
산식에 빠져 있었다. 자세한 사실 경계는 `README.md`와 `docs/V5_INPUTS_AND_LEGACY_AUDIT.md` §2.
