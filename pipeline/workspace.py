"""파이프라인이 읽고 쓰는 경로를 한 곳에서 정한다 (PER-194).

## 왜 필요한가

지금까지 경로는 모듈마다 상수였다 — `ingest.py` 는 `data/input/reviews_50products.json`
을 읽고 `data/intermediate/v5_reviews.jsonl` 로 쓴다. 스냅샷 하나를 정본으로 못박는
설계였고, 그 덕에 "어느 데이터로 만든 수치인가"가 흔들린 적이 없다.

그런데 **제품 링크 하나를 받아 돌려보는** 일이 생겼다. 상수를 그대로 두면 새 수집분이
정본 스냅샷을 덮어쓴다. 상수를 바꾸면 25,000건으로 만든 리포트가 전부 재현 실패한다.
둘 다 안 된다.

그래서 경로를 **작업공간(Workspace)** 으로 묶고 두 종류만 만든다.

    Workspace.canonical()      정본 — 지금까지와 **완전히 같은 경로**
    Workspace.for_run("oy-A000000211119")
                               런 — data/runs/<runId>/ 아래로 전부 격리

`canonical()` 이 기존 경로와 한 글자도 다르지 않다는 것이 이 모듈의 계약이고,
`test_workspace.py` 가 상수 하나하나를 대조해 고정한다. 여기가 어긋나면 기존 리포트가
조용히 다른 파일을 보게 된다.

## 런은 정본을 건드리지 않는다

런 작업공간의 모든 경로는 `data/runs/<runId>/` 안이다. 정본 파일(`data/input/*`,
`data/output/claims_v5.*`, `eval/reports/*`, `eval/gold/*`)은 **읽지도 쓰지도 않는다** —
`assert_isolated()` 가 그걸 검사한다. 읽기까지 막는 이유는 카탈로그다: 정본 카탈로그를
읽어 쓰면 미등록 `goodsNo` 에러가 나거나(PER-171), 더 나쁘게는 새 제품을 정본 카탈로그에
집어넣고 싶어진다. 런은 **자기 카탈로그를 따로 만든다.**

`data/runs/` 는 gitignore 다. 재실행하면 다시 생기고, 정본이 아니다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).parents[1]

# 런 ID 는 경로가 되므로 좁게 잡는다. 상위 디렉터리 탈출(`..`)과 구분자를 막는다.
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

# 런이 손대면 안 되는 정본. 접두어로 비교한다.
CANONICAL_PREFIXES = ("data/input", "data/output", "eval/reports", "eval/gold")


class WorkspaceError(Exception):
    """작업공간 규칙 위반. 조용히 고치지 않는다."""


@dataclass(frozen=True)
class Workspace:
    """한 번의 실행이 읽고 쓰는 경로 묶음.

    `run_id` 가 `None` 이면 정본이다. 값이 있으면 전부 `data/runs/<runId>/` 아래다.
    """

    run_id: str | None = None
    root: Path = ROOT

    # --- 생성자 ---

    @classmethod
    def canonical(cls, root: Path = ROOT) -> "Workspace":
        """정본 스냅샷. 지금까지의 경로와 **완전히 같다.**"""
        return cls(run_id=None, root=root)

    @classmethod
    def for_run(cls, run_id: str, root: Path = ROOT) -> "Workspace":
        if not RUN_ID_PATTERN.match(run_id):
            raise WorkspaceError(
                f"런 ID 형식 위반: {run_id!r}\n"
                "  영숫자로 시작하고 영숫자·점·밑줄·하이픈만, 64자 이내다.\n"
                "  경로가 되는 값이라 좁게 잡는다 — 구분자가 들어가면 런 밖으로 쓴다."
            )
        return cls(run_id=run_id, root=root)

    # --- 성질 ---

    @property
    def is_canonical(self) -> bool:
        return self.run_id is None

    @property
    def base(self) -> Path:
        """런의 뿌리. 정본은 저장소 루트다."""
        return self.root if self.is_canonical else self.root / "data/runs" / self.run_id

    def _p(self, canonical: str, scoped: str) -> Path:
        return self.root / canonical if self.is_canonical else self.base / scoped

    # --- 입력 ---

    @property
    def reviews_input(self) -> Path:
        """수집 원본. 정본은 read-only 스냅샷이고, 런은 크롤러가 방금 쓴 파일이다."""
        return self._p("data/input/reviews_50products.json", "reviews.json")

    @property
    def catalog(self) -> Path:
        """제품 동일성. 런은 **자기 카탈로그**를 쓴다 — 정본에 새 goodsNo 를 넣지 않는다."""
        return self._p("data/input/product_catalog.json", "product_catalog.json")

    # --- 중간 산출 ---

    @property
    def reviews(self) -> Path:
        return self._p("data/intermediate/v5_reviews.jsonl", "v5_reviews.jsonl")

    @property
    def reviews_meta(self) -> Path:
        return self._p("data/intermediate/v5_reviews_meta.json", "v5_reviews_meta.json")

    @property
    def tags(self) -> Path:
        return self._p("data/intermediate/v5_tags.jsonl", "v5_tags.jsonl")

    @property
    def tags_meta(self) -> Path:
        return self._p("data/intermediate/v5_tags_meta.json", "v5_tags_meta.json")

    @property
    def ledger(self) -> Path:
        return self._p("data/intermediate/v5_rejected.jsonl", "v5_rejected.jsonl")

    @property
    def ledger_summary(self) -> Path:
        return self._p("data/intermediate/v5_gate_ledger.json", "v5_gate_ledger.json")

    # --- 산출 ---

    @property
    def claims(self) -> Path:
        return self._p("data/output/claims_v5.jsonl", "claims.jsonl")

    @property
    def claims_meta(self) -> Path:
        return self._p("data/output/claims_v5_meta.json", "claims_meta.json")

    @property
    def claims_csv(self) -> Path:
        return self._p("data/output/claims_v5.csv", "claims.csv")

    @property
    def run_meta(self) -> Path:
        """런이 무엇으로 만들어졌는지. 정본에는 없다 — 단계마다 meta 가 따로 있다."""
        if self.is_canonical:
            raise WorkspaceError("정본에는 런 메타가 없다 — 단계별 meta 를 봐라")
        return self.base / "run.json"

    # --- 규칙 ---

    def ensure_dirs(self) -> None:
        """런 디렉터리를 만든다. 정본에는 아무것도 하지 않는다 — 이미 있고, 만들 권한도 없다."""
        if self.is_canonical:
            return
        self.base.mkdir(parents=True, exist_ok=True)

    def assert_isolated(self) -> None:
        """런이 정본을 건드리지 않는지 검사한다.

        읽기까지 막는다. 정본 카탈로그를 읽어 쓰면 미등록 `goodsNo` 에러가 나거나
        (PER-171), 더 나쁘게는 새 제품을 정본 카탈로그에 집어넣고 싶어진다.
        """
        if self.is_canonical:
            return
        bad = []
        for name in ("reviews_input", "catalog", "reviews", "reviews_meta", "tags",
                     "tags_meta", "ledger", "ledger_summary", "claims", "claims_meta",
                     "claims_csv"):
            path = getattr(self, name)
            rel = path.relative_to(self.root).as_posix()
            if rel.startswith(CANONICAL_PREFIXES):
                bad.append(f"  {name}: {rel}")
        if bad:
            raise WorkspaceError(
                f"런 {self.run_id!r} 의 경로가 정본을 가리킨다:\n" + "\n".join(bad))


CANONICAL = Workspace.canonical()
