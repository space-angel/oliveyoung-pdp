# oliveyoung-pdp

올리브영 PDP 리뷰에서 **구매 고민 질문(concern)** 을 생성하는 파이프라인과, 그 입력을 만드는 크롤러를 한 저장소에서 관리한다.

Linear: [올리브영 PDP 개선 (PRD 기반)](https://linear.app/banjax/project/올리브영-pdp-개선-prd-기반-e9faaf419498) · 현재 작업 [PER-158](https://linear.app/banjax/issue/PER-158)

## 왜 이 저장소가 생겼나

v4까지 자산이 두 곳에 흩어져 있었다 — 크롤러는 `oliveyoung-crawler/`, 파이프라인은 `OLY/concern-pipeline-v4/`.
둘 다 git 관리를 받지 않아서 "어떤 데이터로 낸 수치인지"가 파일 mtime 말고는 남지 않았다.
v5는 25,000건 규모라 재현성이 결과의 신뢰도를 좌우하므로, **수집 → 생성 → 평가를 한 히스토리에 묶는다.**

기존 두 디렉터리는 **그대로 보존**한다. 이 저장소는 사본에서 출발하며, 레거시 분류 결과는
[`docs/V5_INPUTS_AND_LEGACY_AUDIT.md`](docs/V5_INPUTS_AND_LEGACY_AUDIT.md) §4에 있다.

## 구조

```
crawler/     올리브영 cursor API 크롤러 (상품당 최대 500건)
pipeline/    step0 전처리 → step1 클레임 추출 → step2 집계 → step3 축분류 → step4 질문생성
eval/        평가 스크립트 + 골든셋 + 리포트
data/
  input/         수집 결과·정규화 입력 (커밋됨 — 평가 수치의 근거)
  intermediate/  step0~3 중간 산출물 (gitignore, 재생성 가능)
  output/        concerns_*.json (커밋됨)
docs/
  SCRAPLING_MIGRATION_POC.md    크롤러 설계 근거 (엔드포인트·size 상한·레이트리밋 실측)
  PDP_EXPERIMENT_CONTEXT.md     PDP 화면 명세 + 스킨코드 라벨 표
  V5_INPUTS_AND_LEGACY_AUDIT.md 입력 인벤토리 · 25K 프로파일 · 레거시 감사
  v4/                           v4 하네스 원본 (planning / dev / eval 산출물)
```

## 시작하기

```bash
# 파이프라인·평가
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env        # ANTHROPIC_API_KEY 입력
.venv/bin/python pipeline/run_pipeline.py --steps 0,1,2,3,4
.venv/bin/python eval/eval_v4.py

# 크롤러 (별도 venv 권장 — 브라우저 포함으로 무겁다)
python3 -m venv crawler/.venv
crawler/.venv/bin/pip install -r crawler/requirements.txt
crawler/.venv/bin/python crawler/oliveyoung_crawler.py --products crawler/products_50.json --target 500
```

## 현재 상태 — v4 베이스라인

첫 커밋은 **v4 원본을 무수정 이식**한 것이다. v5 변경은 그 위에 쌓이므로 `git log -p`가 곧 개선 근거가 된다.

v4 평가 결과 ([`eval/reports/eval_report_v4.md`](eval/reports/eval_report_v4.md), 5제품·리뷰 499건·concern 30개):

| 지표 | 기준 | 결과 | 판정 |
|---|---|---|---|
| Gate (구조 6항목) | 전항목 | 6/6 | PASS |
| Specificity | ≥1.3 | 1.67 | PASS |
| Relevance | ≥3.5 | 3.56 | PASS |
| 감성 혼재 | 제품당 ≥1 | 5/5 | PASS |
| 카테고리 분포 | 제품당 ≥3/4종 | 1/5 | **FAIL** |
| 리스크 질문 존재 | ≥4/5 제품 | 3/5 | **FAIL** |
| 인용 정확도 | 참고 | 88.8% (127/143) | 참고 |
| Golden Set 일치율 | 참고 | 86.7% (13/15) | 참고 |

"종합 합격" 산식은 앞의 3개만 본다 — **2개 FAIL은 산식에 포함되지 않았다.**
두 FAIL과 Golden Set 미스는 모두 `skin_type_hint`가 step2에서 유실되는 단일 구조적 갭에서 나온다
([`docs/v4/planning/06_data_model_updated.md`](docs/v4/planning/06_data_model_updated.md) §6).
인용 불일치 16건은 오탈자가 아니라 파라프레이즈이며, 원문에 없는 수치를 생성한 사례가 포함된다.

**v4를 "PASS"로 서술하면 방어할 수 없다.** 정확한 문장은 "3개 조건 PASS / 2개 FAIL, 원인은 단일 구조적 갭".

## v5 착수 전 미결 사항

`docs/V5_INPUTS_AND_LEGACY_AUDIT.md` §5 참조. 가장 먼저 걸리는 것:

- **집계 단위** — 25K 데이터의 `productKey`는 50개지만 `goodsNo`는 153개다 (cursor API가 변형 SKU 리뷰를 합산). goodsNo로 그룹핑하면 리뷰 1~7건 상품이 139개 생겨 `supportingReviewIds ≥ 5` Gate가 대량 실패한다.
- **부정 신호 희소성** — 평점 5점이 85%, 1~2점은 407건(1.6%). "위험 신호 재현율"을 어떻게 측정할지 정해야 한다.
- ~~**스킨 코드 사전**~~ — **해소 (2026-09-03).** API 8개 엔드포인트는 전부 실패했지만(`data/input/skin_codes_probe_result.json`) 실브라우저로 PDP 리뷰 위젯 DOM을 열어 A01~A07 · B01~B06 · C01~C13 **26종 전부 실측**했다. 정본은 `data/input/skin_codebook.json`, 검증은 `python crawler/verify_skin_codebook.py`. 구 `PDP_EXPERIMENT_CONTEXT.md` §6 수동 표는 A01/A02가 뒤바뀐 추정이었다.
