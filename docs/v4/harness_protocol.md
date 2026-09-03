# 하네스 운용 프로토콜

## 세션 시작 방법

각 역할의 Claude Code 세션은 **해당 폴더에서 시작**한다.

```bash
# PM 세션
cd /Users/banjax.index/development/product/OLY/concern-pipeline-v4/1_PLANNING
claude  # 이 폴더의 CLAUDE.md 자동 로드

# DEV 세션
cd /Users/banjax.index/development/product/OLY/concern-pipeline-v4/2_DEV
claude

# EVAL 세션
cd /Users/banjax.index/development/product/OLY/concern-pipeline-v4/3_EVAL
claude
```

## 진행 순서

```
[사용자] PM 세션 시작
    ↓
[PM] 4개 파일 작성 + _DONE.md
    ↓
[사용자] 1_PLANNING/ 검토 → "go" 승인
    ↓
[사용자] DEV 세션 시작
    ↓
[DEV] 파이프라인 구현 + 실행 + _DONE.md
    ↓
[사용자] 2_DEV/ + concerns_v4.json 검토 → "go" 승인
    ↓
[사용자] EVAL 세션 시작
    ↓
[EVAL] 평가 + 리포트 + _DONE.md
    ↓
[사용자] 결과 검토, 필요 시 DEV 재세션
```

## 재작업 규칙

- EVAL 결과가 FAIL이면 사용자가 판단해서 DEV 재세션
- DEV 재세션은 `_DONE.md` 를 `_DONE_v1.md` 로 rename 후 시작
- 재세션 DEV는 EVAL의 `_DONE.md` 를 읽고 어디를 고쳐야 하는지 파악

## 상태 확인

```bash
# 현재 진행 상태 한눈에 보기
ls 1_PLANNING/_DONE.md 2_DEV/_DONE.md 3_EVAL/_DONE.md 2>/dev/null && echo "전부 완료" || echo "진행 중"
```
