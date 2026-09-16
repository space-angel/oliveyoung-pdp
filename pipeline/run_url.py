"""제품 링크 하나로 파이프라인을 끝까지 돌린다 (PER-194).

    .venv/bin/python pipeline/run_url.py --url "https://www.oliveyoung.co.kr/store/goods/getGoodsDetail.do?goodsNo=A000000211119"

## 무엇을 하는가

    crawl    올리브영에서 그 제품 리뷰를 수집한다            (네트워크 · 레이트리밋)
    catalog  **수집분에서** 런 전용 카탈로그를 만든다
    ingest   원문/조건/파생 3층으로 적재 + 입력 계약 검증
    tag      (리뷰 × aspect) 태깅                          (LLM · 실비)
    gates    동일성·중복·방향성·충분성 4게이트 + rejected[]
    claims   질문–답 쌍 생성                                (LLM · 실비)
    report   런 요약 — 무엇을 쟀고 **무엇을 못 쟀는지**

전부 `data/runs/<runId>/` 안이다. 정본(`data/input` · `data/output` ·
`eval/reports` · `eval/gold`)은 읽지도 쓰지도 않는다 — `assert_isolated()` 가 검사한다.

## 카탈로그를 수집분에서 만드는 이유

크롤러는 요청한 `goodsNo` 하나로 부르지만, 돌아오는 리뷰의 `goodsNo` 는 변형 SKU라
**요청 값과 다른 경우가 흔하다**(제품당 평균 4종). 요청 값만 카탈로그에 넣으면 입수가
미등록 `goodsNo` 에러로 멈춘다 — 조용한 폴백이 없기 때문이다 (PER-171).

그래서 수집분에 실제로 나타난 `goodsNo` 를 전부 한 `productId` 아래 등록한다.
**정본 카탈로그는 건드리지 않는다.** 새 제품을 정본에 집어넣으면 그 파일로 만든
53제품짜리 리포트가 전부 재현 실패한다.

## 이 런은 재현율을 재지 않는다

재현율은 사람이 만든 정답지(골든셋, PER-178)와 맞춰야 나온다. 새 제품에는 정답지가
없으므로 **못 잰다.** 못 잰 것을 안 적으면 "재현율이 안 나왔다"와 "재현율이 나쁘다"를
구분할 수 없다 — v4 리포트가 떨어진 검사 둘을 합격 산식에서 빼놓고 "합격"을 띄운
것과 같은 실패다.

그래서 `run.json` 의 `evaluation.golden` 은 `false` 이고 사유가 함께 적힌다.
쟤는 것과 못 재는 것을 나눠 적는다.

    잴 수 있다   인용 원문 일치율 · 생성률 · 탈락 사유 분포 · 조건 셀 분포
    못 잰다      재현율 · 방향 일치율 — 정답지가 없다

## 비용이 드는 단계는 확인을 요구한다

`crawl` 은 올리브영에 실제 요청이 나가고, `tag` 와 `claims` 는 LLM 실비가 든다.
`--check` 로 **아무것도 하지 않고** 계획과 비용을 먼저 본다. 실행하려면 `--yes` 다.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from catalog import SCHEMA_VERSION as CATALOG_SCHEMA_VERSION  # noqa: E402
from contracts import KNOWN_FIELDS  # noqa: E402
from policy import SnapshotPolicy  # noqa: E402
from workspace import Workspace, WorkspaceError  # noqa: E402

ROOT = Path(__file__).parents[1]
CRAWLER = ROOT / "crawler/oliveyoung_crawler.py"

# PDP 링크에서 goodsNo 를 뽑는다. 올리브영 상품 코드는 'A' + 숫자 12자리다.
GOODS_NO_PATTERN = re.compile(r"\bA\d{12}\b")
STEPS = ("crawl", "catalog", "ingest", "tag", "gates", "claims", "report")

# 실비가 드는 단계. --yes 없이는 돌리지 않는다.
COSTLY = {
    "crawl": "올리브영에 실제 요청이 나간다 (레이트리밋 · 제품당 수 분)",
    "tag": "LLM 태깅 — 리뷰 500건 기준 약 $0.1",
    "claims": "LLM 생성 — 제품 1개 기준 약 $1~2",
}


class RunUrlError(Exception):
    """진입점 규칙 위반. 조용히 고치지 않는다."""


# --- URL ---


def goods_no_from(url: str) -> str:
    """PDP 링크에서 `goodsNo` 를 뽑는다.

    형식이 안 맞으면 **추측하지 않고 멈춘다.** 잘못 뽑으면 엉뚱한 제품을 수집하고,
    그 사실이 끝까지 드러나지 않는다 — 리뷰는 정상적으로 쌓이기 때문이다.
    """
    found = GOODS_NO_PATTERN.findall(url or "")
    if not found:
        raise RunUrlError(
            f"링크에서 goodsNo 를 못 찾았다: {url!r}\n"
            "  기대 형식: https://www.oliveyoung.co.kr/store/goods/getGoodsDetail.do"
            "?goodsNo=A000000211119\n"
            "  (goodsNo 는 'A' + 숫자 12자리다. 링크 대신 --goods 로 바로 줄 수도 있다)")
    unique = sorted(set(found))
    if len(unique) > 1:
        raise RunUrlError(
            f"링크에 goodsNo 가 여럿이다: {unique}\n"
            "  어느 제품인지 사람이 정해야 한다 — --goods 로 하나만 준다")
    return unique[0]


def run_id_for(goods_no: str) -> str:
    return f"oy-{goods_no}"


# --- 카탈로그 ---


def build_run_catalog(reviews: list[dict], goods_no: str) -> dict:
    """**수집분에서** 런 전용 카탈로그를 만든다.

    변형 SKU 를 포함해 실제로 나타난 `goodsNo` 를 전부 한 `productId` 아래 등록한다.
    빠뜨리면 입수가 미등록 `goodsNo` 에러로 멈춘다 (PER-171 — 조용한 폴백 없음).

    `renewalPolicy` 는 `unobserved` 다. 한 번 수집한 것으로 세대 경계를 알 수 없고,
    모르는 것을 `single` 로 적으면 세대가 섞인 근거가 한 제품으로 집계된다 (PER-172).
    """
    if not reviews:
        raise RunUrlError("수집분이 비었다 — 카탈로그를 만들 수 없다")

    seen: dict[str, int] = {}
    for row in reviews:
        g = (row.get("goodsNo") or "").strip()
        if g:
            seen[g] = seen.get(g, 0) + 1
    if goods_no not in seen:
        # 요청한 상품의 리뷰가 하나도 안 왔다는 뜻이다. 그대로 두면 "요청 제품"과
        # "수집 제품"이 다른 채로 파이프라인이 돈다.
        raise RunUrlError(
            f"요청한 goodsNo {goods_no} 의 리뷰가 수집분에 없다.\n"
            f"  수집된 goodsNo: {sorted(seen)[:5]}{' …' if len(seen) > 5 else ''}\n"
            "  → 링크가 맞는지, 크롤이 끝까지 돌았는지 본다")

    first = next(r for r in reviews if (r.get("goodsNo") or "").strip() == goods_no)
    display = (first.get("productKey") or first.get("productName") or "").strip()
    category = (first.get("category") or "").strip()
    if not display or not category:
        raise RunUrlError(
            f"수집분에 제품명·카테고리가 없다 (goodsNo={goods_no}) — 크롤러 출력을 확인한다")

    return {
        "_meta": {
            "schemaVersion": CATALOG_SCHEMA_VERSION,
            "issue": "PER-194",
            "description": (
                "런 전용 카탈로그. 수집분에 나타난 goodsNo 를 한 productId 아래 등록한다. "
                "정본 data/input/product_catalog.json 과 별개이며 커밋되지 않는다."),
            "builtFrom": "crawl",
            "requestedGoodsNo": goods_no,
        },
        "products": [{
            "productId": "p001",
            "displayName": display,
            "category": category,
            "requestedGoodsNo": goods_no,
            "lineageId": "L001",
            # 한 번 수집한 것으로 세대 경계를 알 수 없다. 모르는 것을 정했다고
            # 적지 않는다 — 그 사실이 주장의 limitation 으로 나간다 (PER-172).
            "renewalPolicy": {
                "policy": "unobserved", "fromMonth": None, "toMonth": None, "evidence": None},
            "notes": ["런 카탈로그 — 수집분에서 생성 (PER-194)"],
            "goodsNos": [
                {"goodsNo": g, "source": "crawl_request" if g == goods_no else "observed_variant"}
                for g in sorted(seen)
            ],
        }],
    }


def assert_crawl_contract(reviews: list[dict]) -> None:
    """수집분이 입력 계약의 **필드 집합**을 지키는지 먼저 본다.

    입수에서도 잡히지만, 거기까지 가면 크롤 비용을 이미 낸 뒤다. 크롤 직후에
    한 번 보고 크롤러가 바뀐 것인지 여기서 말해 준다.
    """
    if not reviews:
        raise RunUrlError("수집분이 비었다")
    unknown = sorted({k for row in reviews for k in row} - set(KNOWN_FIELDS))
    if unknown:
        raise RunUrlError(
            f"계약에 없는 필드가 수집분에 있다: {unknown}\n"
            "  크롤러가 바뀌었다. 새 필드가 원문·조건·파생·드롭 중 어디인지 사람이\n"
            "  정해야 한다 — pipeline/contracts.py 를 고친 뒤 다시 돌린다")


# --- 실행 ---


@dataclass
class Plan:
    url: str | None
    goods_no: str
    run_id: str
    ws: Workspace
    steps: tuple[str, ...]
    target: int

    def costly_steps(self) -> list[str]:
        return [s for s in self.steps if s in COSTLY]

    def describe(self) -> str:
        lines = [
            f"제품   goodsNo={self.goods_no}" + (f"  ({self.url})" if self.url else ""),
            f"런     {self.run_id}  →  {self.ws.base.relative_to(ROOT)}/",
            f"단계   {' → '.join(self.steps)}",
            f"목표   리뷰 {self.target:,}건",
        ]
        costly = self.costly_steps()
        if costly:
            lines.append("비용이 드는 단계:")
            lines += [f"  {s:8s} {COSTLY[s]}" for s in costly]
        lines += [
            "",
            "이 런은 재현율을 재지 않는다 — 이 제품에는 사람이 만든 정답지가 없다.",
            "  잴 수 있다  인용 원문 일치율 · 생성률 · 탈락 사유 분포",
            "  못 잰다     재현율 · 방향 일치율",
        ]
        return "\n".join(lines)


def make_plan(args) -> Plan:
    goods_no = (args.goods or "").strip() or goods_no_from(args.url)
    run_id = args.run_id or run_id_for(goods_no)
    ws = Workspace.for_run(run_id)
    # 런이 정본을 가리키면 여기서 멈춘다. 크롤 전에 본다 — 비용을 내고 나서
    # "정본을 덮을 뻔했다"를 아는 것은 늦다.
    ws.assert_isolated()
    steps = tuple(s.strip() for s in args.steps.split(",") if s.strip()) if args.steps else STEPS
    unknown = [s for s in steps if s not in STEPS]
    if unknown:
        raise RunUrlError(f"모르는 단계: {unknown}\n  아는 단계: {', '.join(STEPS)}")
    return Plan(args.url, goods_no, run_id, ws, steps, args.target)


def _run(cmd: list[str], what: str) -> None:
    print(f"\n$ {' '.join(str(c) for c in cmd)}")
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        raise RunUrlError(f"{what} 실패 (종료 코드 {result.returncode}) — 위 출력을 본다")


def _read_json(path: Path):
    return json.loads(path.read_text())


def step_crawl(plan: Plan, python: str) -> None:
    plan.ws.ensure_dirs()
    _run([python, str(CRAWLER), "--goods", plan.goods_no,
          "--target", str(plan.target), "--output", str(plan.ws.reviews_input)], "크롤")


def step_catalog(plan: Plan) -> None:
    reviews = _read_json(plan.ws.reviews_input)
    assert_crawl_contract(reviews)
    catalog = build_run_catalog(reviews, plan.goods_no)
    plan.ws.catalog.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n")
    goods = catalog["products"][0]["goodsNos"]
    print(f"[catalog] 리뷰 {len(reviews):,}건 · goodsNo {len(goods)}종 → "
          f"{plan.ws.catalog.relative_to(ROOT)}")


def step_ingest(plan: Plan) -> SnapshotPolicy:
    import ingest as ingest_mod

    reviews = _read_json(plan.ws.reviews_input)
    # 오늘 수집분은 정의상 정본 기준월보다 새롭다. 런에서만 파생을 허용하고,
    # 데이터에서 가져왔다는 사실을 run.json 에 남긴다 (PER-194).
    snapshot = SnapshotPolicy.derive(r["reviewDate"] for r in reviews)
    records, meta, prof = ingest_mod.ingest(
        input_path=plan.ws.reviews_input,
        catalog_path=plan.ws.catalog,
        snapshot=snapshot,
    )
    ingest_mod.write(records, meta, prof,
                     output_path=plan.ws.reviews,
                     meta_path=plan.ws.reviews_meta,
                     profile_path=None)   # 런은 eval/reports 에 쓰지 않는다
    print(f"[ingest] {len(records):,}건 → {plan.ws.reviews.relative_to(ROOT)} "
          f"(리센시 {snapshot.cutoff_month}~ · 데이터에서 파생)")
    return snapshot


def step_tag(plan: Plan, python: str, model: str) -> None:
    label = f"run_{plan.run_id}"
    _run([python, str(ROOT / "pipeline/tag.py"), "submit",
          "--model", model, "--label", label, "--reviews", str(plan.ws.reviews)], "태깅 제출")
    _run([python, str(ROOT / "pipeline/tag.py"), "collect", "--label", label], "태깅 수거")
    import tag as tag_mod
    tag_mod.ensure_current(reviews_path=plan.ws.reviews,
                           tags_out=plan.ws.tags, meta_out=plan.ws.tags_meta)


def step_gates(plan: Plan) -> dict:
    from ledger import load_inputs, run_gates

    records, tags, catalog = load_inputs(plan.ws.reviews, plan.ws.tags, plan.ws.catalog)
    run = run_gates(records, tags, catalog)
    plan.ws.ledger.write_text("".join(
        json.dumps(r.as_dict(), ensure_ascii=False) + "\n" for r in run.ledger.rows))
    summary = {"rows": len(run.ledger.rows), "passedClaims": len(run.passed_claims)}
    plan.ws.ledger_summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(f"[gates] 원장 {summary['rows']:,}행 · 통과 주장 {summary['passedClaims']:,}건")
    return summary


def step_claims(plan: Plan, python: str) -> None:
    _run([python, str(ROOT / "pipeline/generate.py"), "--run", plan.run_id], "claim 생성")


def step_report(plan: Plan, snapshot: SnapshotPolicy | None) -> dict:
    """런 요약. **무엇을 못 쟀는지**를 같이 적는다."""
    claims = []
    if plan.ws.claims.exists():
        claims = [json.loads(l) for l in plan.ws.claims.read_text().splitlines() if l.strip()]
    quotes = [e for c in claims for e in c.get("evidence", [])]
    report = {
        "issue": "PER-194",
        "runId": plan.run_id,
        "goodsNo": plan.goods_no,
        "url": plan.url,
        "ranAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "steps": list(plan.steps),
        "snapshot": snapshot.as_dict() if snapshot else None,
        "measured": {
            "claims": len(claims),
            "quotes": len(quotes),
            "conditional": sum(1 for c in claims if c.get("claimType") == "conditional"),
            "byDirection": _count(c.get("direction") for c in claims),
            "byDecisionAxis": _count(c.get("decisionAxis") for c in claims),
        },
        # 못 잰 것을 안 적으면 "안 나왔다"와 "나빴다"를 구분할 수 없다.
        # v4 리포트가 떨어진 검사 둘을 합격 산식에서 빼놓고 합격을 띄운 것과 같은 실패다.
        "evaluation": {
            "golden": False,
            "recall": None,
            "directionAgreement": None,
            "reason": (
                "이 제품에는 사람이 만든 정답지(골든셋 · PER-178)가 없다. "
                "재현율과 방향 일치율은 정답지와 맞춰야 나오므로 재지 않았다. "
                "재려면 eval/label_concern_golden.py 로 이 제품 번들을 라벨링해야 한다."),
            "measurable": ["인용 원문 일치율", "생성률", "탈락 사유 분포", "조건 셀 분포"],
            "notMeasurable": ["재현율", "방향 일치율"],
        },
        "paths": {
            "reviews": str(plan.ws.reviews_input.relative_to(plan.ws.root)),
            "catalog": str(plan.ws.catalog.relative_to(plan.ws.root)),
            "claims": str(plan.ws.claims.relative_to(plan.ws.root)),
        },
    }
    plan.ws.run_meta.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"\n[report] claim {len(claims):,}건 · 인용 {len(quotes):,}개 "
          f"→ {plan.ws.run_meta.relative_to(plan.ws.root)}")
    print("         재현율은 재지 않았다 — 이 제품에는 정답지가 없다")
    return report


def _count(values) -> dict:
    out: dict[str, int] = {}
    for v in values:
        if v:
            out[v] = out.get(v, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def main() -> None:
    ap = argparse.ArgumentParser(
        description="제품 링크 하나로 파이프라인을 끝까지 돌린다 (PER-194)")
    ap.add_argument("--url", help="올리브영 PDP 링크")
    ap.add_argument("--goods", help="goodsNo 를 직접 준다 (링크 대신)")
    ap.add_argument("--run-id", help=f"런 ID. 생략하면 oy-<goodsNo>")
    ap.add_argument("--steps", help=f"쉼표로 구분. 생략하면 전부 ({', '.join(STEPS)})")
    ap.add_argument("--target", type=int, default=500, help="제품당 목표 리뷰 수 (기본 500)")
    ap.add_argument("--model", default="claude-haiku-4-5", help="태깅 모델")
    ap.add_argument("--python", default=".venv/bin/python", help="하위 명령이 쓸 인터프리터")
    ap.add_argument("--check", action="store_true",
                    help="아무것도 하지 않고 계획과 비용만 본다")
    ap.add_argument("--yes", action="store_true",
                    help="비용이 드는 단계(crawl · tag · claims)를 실제로 돌린다")
    args = ap.parse_args()

    if not args.url and not args.goods:
        raise SystemExit("FAIL: --url 이나 --goods 중 하나는 필요하다")

    plan = make_plan(args)
    print(plan.describe())

    if args.check:
        print("\n--check 라 아무것도 하지 않았다. 실제로 돌리려면 --yes 를 준다.")
        return
    costly = plan.costly_steps()
    if costly and not args.yes:
        raise SystemExit(
            f"\nFAIL: 비용이 드는 단계가 있다 ({', '.join(costly)}).\n"
            "  계획만 보려면 --check, 실제로 돌리려면 --yes 를 준다.")

    plan.ws.ensure_dirs()
    snapshot = None
    for step in plan.steps:
        if step == "crawl":
            step_crawl(plan, args.python)
        elif step == "catalog":
            step_catalog(plan)
        elif step == "ingest":
            snapshot = step_ingest(plan)
        elif step == "tag":
            step_tag(plan, args.python, args.model)
        elif step == "gates":
            step_gates(plan)
        elif step == "claims":
            step_claims(plan, args.python)
        elif step == "report":
            step_report(plan, snapshot)


if __name__ == "__main__":
    try:
        main()
    except (RunUrlError, WorkspaceError) as exc:
        raise SystemExit(f"FAIL: {exc}")
