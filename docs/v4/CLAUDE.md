# Concern Pipeline V4 — 하네스 루트

## 이 프로젝트의 목적

올리브영 PDP 리뷰 데이터에서 "구매 고민 질문"을 자동 생성하는 LLM 파이프라인(v4)을 만든다.
v3의 4대 실패 패턴을 구조적으로 해결하는 것이 핵심 목표.

## 하네스 구조

이 프로젝트는 세 역할이 독립 세션으로 동작한다:

| 폴더 | 역할 | 세션 시작 위치 |
|---|---|---|
| `1_PLANNING/` | PM — 문제 정의, 설계, 성공 기준 | `cd 1_PLANNING` |
| `2_DEV/` | DEV — 파이프라인 구현 | `cd 2_DEV` |
| `3_EVAL/` | EVAL — 평가 및 리포트 | `cd 3_EVAL` |

## 단계 전환 규칙

1. 각 역할은 자신의 폴더에 `_DONE.md` 를 작성해야 종료 선언 가능
2. 단계 전환은 **사용자 승인** 후 다음 역할 세션 시작
3. 다음 역할 세션은 이전 역할의 `_DONE.md` 를 가장 먼저 읽는다

## 공유 데이터

```
data/
├── input/          ← 모든 역할 read-only
│   ├── reviews_200_normalized.json   (500건 정규화 리뷰)
│   ├── reviews_200.json              (원본)
│   └── product_canonical_map.json    (SKU → 정식명 매핑)
├── intermediate/   ← DEV write, EVAL read
├── output/         ← DEV write (concerns_v4.json)
├── cache/          ← DEV write (Step 1 캐시)
└── eval/           ← EVAL write (reports, golden_set)
```

## 환경

```bash
cd /Users/banjax.index/development/product/OLY/concern-pipeline-v4
python3 -m venv .venv && source .venv/bin/activate
pip install anthropic python-dotenv
# .env 파일에 ANTHROPIC_API_KEY=sk-ant-... 추가
```
