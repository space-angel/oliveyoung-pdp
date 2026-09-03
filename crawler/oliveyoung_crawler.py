#!/usr/bin/env python3
"""
올리브영 리뷰 크롤러 (Scrapling / cursor API 방식)

기존 oliveyoung_review_crawler.py + crawl_target_200.py 를 대체하는 통합 크롤러.
undetected-chromedriver + CDP performance 로그 → Scrapling(Patchright) + 페이지 내 fetch.

검증 근거는 docs/SCRAPLING_MIGRATION_POC.md 참조.

동작 방식
---------
1. StealthySession으로 브라우저 1개를 띄우고 제품마다 PDP를 연다
   (봇 차단 통과 + 쿠키/오리진 확보용. UI 조작은 하지 않는다)
2. 페이지 컨텍스트 안에서 fetch로 cursor API를 직접 호출한다
   POST https://m.oliveyoung.co.kr/review/api/v2/reviews/cursor
3. 응답의 nextCursor* 를 다음 요청의 cursor* 로 넘기며 페이지네이션

수집량
------
  총량 = size × 요청 10회.  size 상한이 50이므로 **제품당 최대 500건**.
  size>50 은 에러가 아니라 빈 배열을 반환하므로 절대 올리지 말 것.

사용법
------
  source .venv/bin/activate
  python oliveyoung_crawler.py                              # products_50.json, 제품당 500건
  python oliveyoung_crawler.py --target 200
  python oliveyoung_crawler.py --products products_legacy5.json
  python oliveyoung_crawler.py --goods A000000158513        # 단일/복수 goodsNo
  python oliveyoung_crawler.py --union                      # 합집합으로 500건 초과 수집
  python oliveyoung_crawler.py --headless

제품 목록 파일 형식 (JSON):
  [{"goodsNo": "A000000158513", "category": "클렌징", "productKey": "메이크프렘 클렌징밀크"}]
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from scrapling.fetchers import StealthySession

# ===== API 상수 (2026-08 실측) =====
CURSOR_URL = "https://m.oliveyoung.co.kr/review/api/v2/reviews/cursor"
PDP_URL = "https://www.oliveyoung.co.kr/store/goods/getGoodsDetail.do?goodsNo={goods}"

MAX_SIZE = 50          # 서버 상한. 초과 시 빈 배열 반환(에러 아님)
MAX_CALLS = 10         # 서버 상한. 11번째 호출은 항상 empty batch
SORT_HELPFUL = "USEFUL_SCORE_DESC"    # 도움순
SORT_DEFAULT = "RECOMMENDED_DESC"     # 페이지 기본 정렬

# --union 에서 쓰는 수집 조건들 (reviewId로 dedupe)
UNION_PASSES = [
    ("도움순", {"sortType": SORT_HELPFUL, "reviewType": "ALL"}),
    ("기본정렬", {"sortType": SORT_DEFAULT, "reviewType": "ALL"}),
    ("사진리뷰", {"sortType": SORT_HELPFUL, "reviewType": "PHOTO"}),
]

IMAGE_BASE = "https://image.oliveyoung.co.kr/uploads/images/gdasEditor/"
PROFILE_IMAGE_BASE = "https://image.oliveyoung.co.kr/uploads/images/mbrProfile/"

# 피부정보는 코드값으로 온다 (profileDto.skinType/skinTone/skinTrouble).
# 라벨 사전은 코드에 박지 않고 data/input/skin_codebook.json 에서 읽는다.
# (출처: PDP 리뷰 위젯 DOM 실측 2026-09-03. 근거는 코드북의 _meta.evidence)
# 파일이 없으면 라벨 없이 코드만 저장한다 — 구 동작과 동일.
CODEBOOK_PATH = Path(__file__).resolve().parents[1] / "data/input/skin_codebook.json"


def load_skin_codebook(path=CODEBOOK_PATH):
    """코드북 파일 → (skinType, skinTone, skinTrouble) 라벨 dict 3개."""
    try:
        book = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"  ! 코드북 없음 ({path}) — 피부정보를 코드 그대로 저장한다")
        return {}, {}, {}
    return book["skinType"], book["skinTone"], book["skinTrouble"]


SKIN_TYPE_LABELS, SKIN_TONE_LABELS, SKIN_TROUBLE_LABELS = load_skin_codebook()

DEFAULT_OUTPUT = "data/reviews.json"

# 제품 목록은 JSON 파일로 관리한다 (--products).
#   products_50.json      — 50개 (구 oliveyoung_review_crawler.py 목록, 2026-08-10 50/50 조회 확인)
#   products_legacy5.json — 5개  (구 crawl_target_200.py 목록)
# --products / --goods 를 모두 생략하면 이 기본값을 쓴다.
DEFAULT_PRODUCTS_FILE = "products_50.json"
DEFAULT_PRODUCTS = [
    {"goodsNo": "A000000158513", "category": "클렌징", "productKey": "메이크프렘 세이프미 클렌징밀크"},
]


# ─────────────────────────────────────────────────────────────
# 페이지 컨텍스트 안에서 실행되는 fetch.
# page.request(APIRequestContext)는 별도 HTTP 스택이라 WAF가 403으로 막는다.
# credentials:'omit' 필수 — 'include'면 크로스오리진(www→m) CORS로 실패한다.
# ─────────────────────────────────────────────────────────────
JS_POST = """
async ({url, body}) => {
    try {
        const res = await fetch(url, {
            method: 'POST',
            headers: {'Content-Type':'application/json','Accept':'application/json, text/plain, */*'},
            body: JSON.stringify(body),
            credentials: 'omit',
            mode: 'cors',
        });
        return { ok:true, status: res.status, text: await res.text() };
    } catch (e) {
        return { ok:false, status:0, text:String(e) };
    }
}
"""


def parse_review(raw, category="", product_key="", requested_goods_no=""):
    """cursor API 응답의 리뷰 1건 → 평탄한 dict

    구 parse_review 대비 변경점 (docs/SCRAPLING_MIGRATION_POC.md §6-1):
      - optionName 이 null 로 오는 경우가 있어 `or ""` 로 방어 (구버전은 여기서 크래시)
      - usefulPoint 는 float(가중 점수). int() 절삭하면 37%가 0이 되므로 그대로 보존
      - skinInfoDto 가 사라지고 피부정보가 profileDto 로 이동. 값은 코드(A01/B02/C03…)
      - satisfactionTags / usagePeriodTag 는 신규 API에 없음 (대체 필드 부재)
    """
    goods = raw.get("goodsDto") or {}
    profile = raw.get("profileDto") or {}
    photos = raw.get("photoReviewList") or []

    profile_img = profile.get("profileImageUrl")
    skin_trouble = profile.get("skinTrouble")
    if not isinstance(skin_trouble, list):
        skin_trouble = [skin_trouble] if skin_trouble else []

    review = {
        # ── 리뷰 본체 ──
        "reviewId": raw.get("reviewId"),
        "content": raw.get("content", ""),
        "rating": raw.get("reviewScore"),
        "usefulPoint": raw.get("usefulPoint"),        # float 유지 (절삭 금지)
        "recommendCount": raw.get("recommendCount"),  # 순수 카운트
        "reviewDate": raw.get("createdDateTime", ""),
        "reviewType": raw.get("reviewType", ""),      # NORMAL / GIFT / OFFLINE
        "isRepurchase": raw.get("isRepurchase", False),
        "isMonthUseReview": raw.get("isMonthUseReview", False),
        "isMonthOverReview": raw.get("isMonthOverReview", False),
        "hasPhoto": raw.get("hasPhoto", False),
        # ── 상품 ──
        # goodsNo 는 리뷰가 실제로 달린 상품. cursor API는 요청한 상품의
        # 용량/기획 변형(같은 제품의 다른 SKU) 리뷰도 함께 반환하므로
        # goodsNo != requestedGoodsNo 인 경우가 흔하다 (제품당 평균 4종).
        "goodsNo": goods.get("goodsNumber", ""),
        "requestedGoodsNo": requested_goods_no,
        "productName": goods.get("goodsName", ""),
        "option": (goods.get("optionName") or "").strip(),
        "category": category,
        "productKey": product_key,
        # ── 작성자 ──
        "userName": profile.get("memberNickname", ""),
        "reviewerRank": profile.get("reviewerRank"),  # 정수 랭킹
        "isTopReviewer": profile.get("isTopReviewer", False),
        "profileImageUrl": PROFILE_IMAGE_BASE + profile_img if profile_img else "",
        # ── 피부정보 (코드값) ──
        "skinType": profile.get("skinType") or "",
        "skinTone": profile.get("skinTone") or "",
        "skinTrouble": skin_trouble,
        # ── 이미지 ──
        "reviewImages": [IMAGE_BASE + p["imagePath"] for p in photos if p.get("imagePath")],
    }

    # 라벨 매핑이 채워져 있으면 사람이 읽는 값도 함께 저장
    if SKIN_TYPE_LABELS or SKIN_TONE_LABELS or SKIN_TROUBLE_LABELS:
        review["skinTypeLabel"] = SKIN_TYPE_LABELS.get(review["skinType"], "")
        review["skinToneLabel"] = SKIN_TONE_LABELS.get(review["skinTone"], "")
        review["skinTroubleLabels"] = [SKIN_TROUBLE_LABELS.get(c, c) for c in skin_trouble]

    return review


class Pacer:
    """전역 호출 속도 제한기 (적응형).

    올리브영은 누적 호출량 기준으로 차단하는 것으로 보인다. 실측:
      - 1차: 빠르게 100콜 → 차단, 이후 주기적으로 재차단
      - 2차: 제품 간격을 4배 늘렸는데도 70콜/26분에서 차단
        (1차가 태운 예산이 회복되기 전에 시작한 탓으로 추정)

    따라서 고정 대기가 아니라 분당 호출 수를 제한하고,
    차단당하면 그 속도를 영구적으로 낮춘다.
    """

    def __init__(self, rate_per_min, min_rate=4.0):
        self.rate = float(rate_per_min)
        self.min_rate = min_rate
        self.last = 0.0
        self.calls = 0
        self.throttled = 0

    def wait(self):
        interval = 60.0 / self.rate
        delta = time.time() - self.last
        if delta < interval:
            time.sleep(interval - delta)
        self.last = time.time()
        self.calls += 1

    def slow_down(self, factor=0.6):
        """차단당했을 때 호출. 이후 속도를 영구적으로 낮춘다."""
        old = self.rate
        self.rate = max(self.min_rate, self.rate * factor)
        self.throttled += 1
        if self.rate < old:
            print(f"       ↓ 호출 속도 {old:.0f} → {self.rate:.0f}회/분 로 조정")

    def stats(self):
        return {"calls": self.calls, "final_rate": round(self.rate, 1), "throttles": self.throttled}


def call_cursor(page, body, retries=2, backoff_ms=4000):
    """cursor API 1회 호출. 네트워크 실패(레이트리밋) 시 짧게 재시도.

    긴 대기는 여기서 하지 않는다 — 레이트리밋은 제품 단위 쿨다운(§crawl_product)에서
    처리해야 호출 예산을 낭비하지 않는다.
    """
    for attempt in range(retries):
        r = page.evaluate(JS_POST, {"url": CURSOR_URL, "body": body})
        if r["ok"]:
            if r["status"] != 200:
                return r["status"], None
            try:
                return 200, json.loads(r["text"])
            except Exception:
                return 200, None
        if attempt < retries - 1:
            page.wait_for_timeout(backoff_ms)
    return 0, None  # status=0 = fetch 자체 실패 = 레이트리밋


def collect_pass(page, goods_no, conditions, target, size, pacer, log_prefix=""):
    """단일 조건으로 cursor 페이지네이션. (raw 리뷰 리스트, 종료사유) 반환"""
    out, state, calls = [], None, 0
    while len(out) < target and calls < MAX_CALLS:
        pacer.wait()
        body = {"goodsNumber": goods_no, "size": size, **conditions}
        if state is None:
            body["page"] = 0
        else:
            if not state.get("hasNext"):
                return out, "hasNext=False"
            body["cursorId"] = state.get("nextCursorId")
            body["cursorScore"] = state.get("nextCursorScore")
            body["cursorCount"] = state.get("nextCursorCount")

        status, payload = call_cursor(page, body)
        calls += 1
        if status != 200:
            pacer.slow_down()
            return out, f"status={status}"
        data = (payload or {}).get("data") or {}
        batch = data.get("goodsReviewList") or []
        if not batch:
            return out, "empty batch"
        out.extend(batch)
        state = data
        if log_prefix:
            print(f"{log_prefix} {len(out)}건", flush=True)
    return out, "target 도달" if len(out) >= target else "호출 상한"


def crawl_product(session, product, args, idx, total, pacer):
    """제품 1개 수집. (parsed reviews, 진단 dict) 반환"""
    goods_no = product["goodsNo"]
    category = product.get("category", "")
    product_key = product.get("productKey", "")
    info = {"goodsNo": goods_no, "productKey": product_key}

    seen, raws = set(), []

    def action(page):
        page.wait_for_timeout(args.warmup * 1000)
        title = page.title() or ""
        info["title"] = title
        blocked = ("잠시만" in title) or ("기다" in title) or len(title) <= 5
        info["blocked"] = blocked
        if blocked:
            print(f"  [{idx}/{total}] {goods_no} ❌ 봇 차단 (title={title[:30]!r})")
            return page

        passes = UNION_PASSES if args.union else [("도움순", {"sortType": args.sort, "reviewType": "ALL"})]
        info.setdefault("passes", {})
        for name, cond in passes:
            if len(raws) >= args.target:
                break
            remaining = args.target - len(raws)
            batch, why = collect_pass(
                page, goods_no, cond, remaining, args.size, pacer,
                log_prefix=f"       {name}:",
            )
            added = 0
            for r in batch:
                rid = r.get("reviewId")
                if rid and rid not in seen:
                    seen.add(rid)
                    raws.append(r)
                    added += 1
            info["passes"][name] = {"fetched": len(batch), "new": added, "stop": why}
            info["rate_limited"] = why == "status=0"
            print(f"       {name}: +{added}건 (수신 {len(batch)}, {why})      ")
            if why == "status=0":
                break  # 레이트리밋 — 남은 pass 시도해봐야 낭비
        return page

    started = time.time()
    try:
        session.fetch(PDP_URL.format(goods=goods_no), page_action=action)
    except Exception as exc:
        info["error"] = f"{type(exc).__name__}: {exc}"
        print(f"  [{idx}/{total}] {goods_no} ❌ {type(exc).__name__}: {str(exc)[:80]}")
        return [], info

    info["elapsed"] = round(time.time() - started, 1)

    reviews = []
    for r in raws:
        parsed = parse_review(r, category, product_key, goods_no)
        if not parsed["productName"]:
            parsed["productName"] = product_key or info.get("title", "").split("|")[0].strip()
        reviews.append(parsed)

    info["count"] = len(reviews)
    name = (reviews[0]["productName"] if reviews else product_key)[:34]
    mark = "✅" if len(reviews) >= args.target else ("⚠️ " if reviews else "❌")
    print(f"  [{idx}/{total}] {mark} {len(reviews):4d}건 · {info['elapsed']:6.1f}s · {name}")
    return reviews, info


def crawl_with_cooldown(session, product, args, idx, total, pacer):
    """레이트리밋을 만나면 쿨다운 후 같은 제품을 재시도한다.

    올리브영은 대략 API 100회쯤에서 차단하고 수 분 뒤 풀린다(실측 §레이트리밋).
    차단된 채로 다음 제품으로 넘어가면 목록 전체를 태우게 되므로,
    여기서 멈춰 서서 기다렸다가 같은 제품을 다시 시도한다.
    """
    for attempt in range(args.max_retries + 1):
        reviews, info = crawl_product(session, product, args, idx, total, pacer)
        if reviews and not info.get("rate_limited"):
            return reviews, info
        if not info.get("rate_limited") or attempt == args.max_retries:
            if info.get("rate_limited"):
                info["gave_up"] = True
            return reviews, info
        cooldown = args.cooldown * (attempt + 1)
        got = f"{len(reviews)}건 확보 후 " if reviews else ""
        print(f"       ⏳ 레이트리밋 — {got}{cooldown}초 쿨다운 후 재시도 "
              f"({attempt + 1}/{args.max_retries})")
        time.sleep(cooldown)
    return reviews, info


def load_existing(path, target):
    """이어받기: 기존 결과에서 목표를 채운 제품은 건너뛴다.

    (기존 리뷰 리스트, 완료된 goodsNo 집합) 반환
    """
    if not path.exists():
        return [], set()
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return [], set()
    if not isinstance(rows, list) or not rows:
        return [], set()
    # 요청 기준(requestedGoodsNo)으로 센다. goodsNo 는 변형 SKU라 요청 단위와 다르다.
    # requestedGoodsNo 가 없는 구버전 출력은 이어받기 대상에서 제외한다.
    counts = {}
    for r in rows:
        key = r.get("requestedGoodsNo")
        if key:
            counts[key] = counts.get(key, 0) + 1
    done = {g for g, c in counts.items() if c >= target}
    # 목표 미달 제품의 리뷰는 버리고 다시 수집한다 (부분 수집분 혼입 방지)
    kept = [r for r in rows if r.get("requestedGoodsNo") in done]
    return kept, done


def load_products(args):
    if args.goods:
        return [{"goodsNo": g.strip(), "category": "", "productKey": ""} for g in args.goods.split(",")]

    path = Path(args.products) if args.products else Path(DEFAULT_PRODUCTS_FILE)
    if not path.exists():
        if args.products:
            raise SystemExit(f"제품 목록 파일을 찾을 수 없습니다: {path}")
        return DEFAULT_PRODUCTS  # 기본 파일도 없으면 내장 1개로 폴백

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise SystemExit(f"제품 목록 파일은 비어있지 않은 JSON 배열이어야 합니다: {path}")
    missing = [i for i, p in enumerate(data) if not isinstance(p, dict) or not p.get("goodsNo")]
    if missing:
        raise SystemExit(f"goodsNo 가 없는 항목이 있습니다 ({path}): 인덱스 {missing[:5]}")
    print(f"제품 목록: {path} ({len(data)}개)")
    return data


def main():
    ap = argparse.ArgumentParser(description="올리브영 리뷰 크롤러 (Scrapling)")
    ap.add_argument("--products", help=f"제품 목록 JSON 파일 (기본: {DEFAULT_PRODUCTS_FILE})")
    ap.add_argument("--goods", help="단일/복수 goodsNo (쉼표 구분)")
    ap.add_argument("--target", type=int, default=MAX_SIZE * MAX_CALLS, help=f"제품당 목표 (기본 {MAX_SIZE*MAX_CALLS})")
    ap.add_argument("--size", type=int, default=MAX_SIZE, help=f"cursor page size (상한 {MAX_SIZE})")
    ap.add_argument("--sort", default=SORT_HELPFUL, help="sortType (USEFUL_SCORE_DESC / RECOMMENDED_DESC)")
    ap.add_argument("--union", action="store_true", help="정렬/필터 합집합으로 500건 초과 수집")
    ap.add_argument("--output", default=DEFAULT_OUTPUT)
    ap.add_argument("--rate", type=float, default=12.0,
                    help="분당 API 호출 수 상한. 차단당하면 자동으로 낮아짐 (기본 12)")
    ap.add_argument("--product-gap", type=float, default=3.0, help="제품 간 간격(초)")
    ap.add_argument("--cooldown", type=float, default=180.0,
                    help="레이트리밋 시 쿨다운(초). 재시도마다 배수로 늘어남 (기본 180)")
    ap.add_argument("--max-retries", type=int, default=3, help="레이트리밋 재시도 횟수 (기본 3)")
    ap.add_argument("--resume", action="store_true",
                    help="기존 출력 파일에서 목표를 채운 제품은 건너뛰고 이어받기")
    ap.add_argument("--warmup", type=float, default=3.0, help="페이지 로드 후 대기(초)")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--real-chrome", action="store_true")
    args = ap.parse_args()

    if args.size > MAX_SIZE:
        print(f"⚠️  size={args.size} 는 상한({MAX_SIZE}) 초과 — 서버가 빈 배열을 반환합니다. {MAX_SIZE}로 조정합니다.")
        args.size = MAX_SIZE

    products = load_products(args)
    total = len(products)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print("=" * 66)
    print("올리브영 리뷰 크롤러 (Scrapling / cursor API)")
    print(f"제품 {total}개 × 최대 {args.target}건  |  size={args.size} "
          f"sort={args.sort}{' +union' if args.union else ''}")
    print(f"시작: {datetime.now():%Y-%m-%d %H:%M:%S}")
    print("=" * 66)

    all_reviews, done = ([], set())
    if args.resume:
        all_reviews, done = load_existing(out_path, args.target)
        if done:
            products = [p for p in products if p["goodsNo"] not in done]
            print(f"이어받기: 완료 {len(done)}개 건너뜀 ({len(all_reviews)}건 유지), "
                  f"남은 {len(products)}개")
            total = len(products)
            if not products:
                print("모든 제품이 이미 목표를 채웠습니다.")
                return 0

    report = []
    pacer = Pacer(args.rate)
    est = total * (args.target / args.size) / args.rate
    print(f"페이싱: {args.rate:.0f}회/분  →  예상 소요 최소 {est:.0f}분 "
          f"(총 {int(total * args.target / args.size)}회 호출)")
    session = StealthySession(
        headless=args.headless,
        real_chrome=args.real_chrome,
        network_idle=False,
        load_dom=True,
        timeout=90000,
    )

    try:
        with session:
            for i, product in enumerate(products, 1):
                reviews, info = crawl_with_cooldown(session, product, args, i, total, pacer)
                all_reviews.extend(reviews)
                report.append(info)
                out_path.write_text(json.dumps(all_reviews, ensure_ascii=False, indent=2), encoding="utf-8")
                if i < total:
                    time.sleep(args.product_gap)
    except KeyboardInterrupt:
        print("\n⚠ 중단됨 — 현재까지 수집분 저장")

    out_path.write_text(json.dumps(all_reviews, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path = out_path.with_name(out_path.stem + "_report.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── 요약 ──
    full = [r for r in report if r.get("count", 0) >= args.target]
    partial = [r for r in report if 0 < r.get("count", 0) < args.target]
    fail = [r for r in report if not r.get("count")]
    limited = [r for r in report if r.get("rate_limited")]

    def why_of(r):
        if r.get("error"):
            return r["error"]
        if r.get("blocked"):
            return "봇 차단 (페이지 로드 실패)"
        if r.get("rate_limited"):
            return "레이트리밋 (재시도 소진)" if r.get("gave_up") else "레이트리밋"
        return "리뷰 없음 / 판매종료 추정"

    print()
    print("=" * 66)
    print(f"완료: {datetime.now():%Y-%m-%d %H:%M:%S}")
    print("=" * 66)
    print(f"  총 리뷰   : {len(all_reviews)}건")
    print(f"  목표 달성 : {len(full)}/{total}   부분 수집: {len(partial)}   실패: {len(fail)}")
    ps = pacer.stats()
    print(f"  API 호출  : {ps['calls']}회  ·  최종 속도 {ps['final_rate']}회/분  ·  감속 {ps['throttles']}회")
    for r in full:
        print(f"    ✅ {r['count']:4d}건  {r.get('productKey') or r['goodsNo']}")
    for r in partial:
        print(f"    ⚠️  {r['count']:4d}건  {r.get('productKey') or r['goodsNo']}  ({why_of(r)})")
    for r in fail:
        print(f"    ❌    0건  {r.get('productKey') or r['goodsNo']}  ({why_of(r)})")

    variants = {}
    for r in all_reviews:
        variants.setdefault(r.get("requestedGoodsNo", ""), set()).add(r.get("goodsNo", ""))
    mixed = {k: v for k, v in variants.items() if len(v) > 1}
    if mixed:
        avg = sum(len(v) for v in variants.values()) / max(len(variants), 1)
        print(f"\n  ℹ️  변형 SKU 포함: {len(mixed)}/{len(variants)}개 제품 "
              f"(제품당 평균 {avg:.1f}종의 goodsNo)")
        print(f"     같은 제품의 용량/기획 변형 리뷰가 합산됨. "
              f"원 요청은 requestedGoodsNo 필드로 추적 가능")

    if limited:
        print(f"\n  ⏳ 레이트리밋 영향 제품 {len(limited)}개.")
        print(f"     쿨다운 후 아래로 이어받으세요 (완료분은 건너뜁니다):")
        print(f"     python {Path(__file__).name} --products {args.products or DEFAULT_PRODUCTS_FILE} "
              f"--target {args.target} --output {out_path} --resume")
    print(f"\n  저장: {out_path}  (진단: {report_path})")
    return 0 if all_reviews else 1


if __name__ == "__main__":
    sys.exit(main())
