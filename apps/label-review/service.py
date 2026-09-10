"""
라벨 검수 앱 — 업무 규칙 (PER-178 외부 라벨러용).

라벨러가 보는 개념은 넷이다: 질문·답, 색칠된 근거 문장(좋다/나쁘다/말만), 놓친 리뷰, 아니에요 이유 4개.
나머지(labelId·출처·조건·평가상태·방향·실패유형 키)는 **여기서 채운다.** 계약 검증은 `golden_contract.validate_label`
그대로 — 이 앱이 규격을 느슨하게 만들지 않는다.

  배정        라벨이 하나도 없고 활성 배정도 없는 번들 중 번호가 가장 낮은 것 1개. 이미 라벨이 있는 번들(B01~B05)은 자연히 건너뛴다
  판정        accept → candidate_accepted / edit → candidate_edited / reject → candidate_rejected + failureReasons
  기각 사유    일상어 4개 → PER-177 키 (REASONS)
  놓친 질문    source=human. "없어요" 도 결정으로 남긴다 (no_missed)
  완료        후보를 모두 결정했을 때만. 사람 claim 0 이면 경고를 돌려주되 막지는 않는다 (명시적 선택이 조건보다 우선)
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pipeline"))
sys.path.insert(0, str(ROOT / "eval"))

from codebook import load_codebook  # noqa: E402
from concern_candidates import auto_checks, load_candidates  # noqa: E402
from contracts import MISSING_SEGMENT  # noqa: E402
from golden_contract import GoldenContractError, derive_direction, load_failure_taxonomy, support_counts, validate_label  # noqa: E402
from label_concern_golden import _condition_line  # noqa: E402
from label_concern_golden_web import load_aspect_keywords, aspect_hits  # noqa: E402
from sample_concern_golden import load_bundles  # noqa: E402
from tag_contract import fold_invisible  # noqa: E402

# 라벨러가 보는 일상어 → 택소노미 키. 8종 중 라벨러가 판단할 수 있는 4개만 노출한다.
# auto_checks 의 문구는 내부 도구용(실패유형 키가 들어 있다). 라벨러 화면에는 일상어로 바꿔 보낸다. 없는 키는 보내지 않는다.
LABELER_CHECKS = {
    "thin_evidence": "근거로 쓴 리뷰가 적어요. 아래 \"같은 얘기를 하는 리뷰\"에 더 있는지 봐주세요.",
    "generalizes_from_silence": "답이 \"대부분\"처럼 뭉뚱그리는데, 실제로 이 얘기를 한 사람은 몇 명뿐이에요. 말 안 한 사람은 근거가 아니에요.",
    "question_broad": "질문이 너무 넓어요. 무엇을 묻는지 한 가지로 좁혀지는지 봐주세요.",
    "number_not_in_quotes": "답에 있는 숫자가 리뷰 문장에는 없어요.",
}


def labeler_checks(checks: list[dict]) -> list[dict]:
    return [{"key": c["key"], "level": "warn", "text": LABELER_CHECKS[c["key"]]} for c in checks if c["key"] in LABELER_CHECKS]


REASONS = [
    {"key": "one_person", "failure": "overfit_question", "title": "한 사람만 한 얘기예요", "desc": "특수한 상황이라 다른 구매자의 질문이 되기 어려워요"},
    {"key": "too_broad", "failure": "overbroad_question", "title": "너무 뭉뚱그린 질문이에요", "desc": "\"좋은가요?\"처럼 무엇을 묻는지 알 수 없어요"},
    {"key": "duplicate", "failure": "duplicate_claim", "title": "앞 질문과 같은 얘기예요", "desc": "표현만 다르고 답이 같아요"},
    {"key": "not_in_reviews", "failure": "unsupported_claim", "title": "리뷰에 없는 얘기예요", "desc": "답이나 숫자가 색칠된 문장에서 나오지 않아요"},
]
REASON_BY_KEY = {r["key"]: r for r in REASONS}
STANCE_LABEL = {"positive": "좋다고 함", "negative": "나쁘다고 함", "neutral": "말은 하는데 좋다 나쁘다 없음"}


class ServiceError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def labeler_id(name: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z가-힣]+", "", name.strip())[:12] or "labeler"
    return f"{slug}-{hashlib.sha256(name.strip().encode('utf-8')).hexdigest()[:6]}"


class Service:
    def __init__(self, store, access_code: str | None = None):
        self.store = store
        self.access_code = access_code
        self.bundles = load_bundles()
        self.candidates = load_candidates()
        self.codebook = load_codebook()
        self.taxonomy = load_failure_taxonomy()
        self.keywords = load_aspect_keywords()

    # ---------- 세션 ----------
    def start(self, name: str, code: str | None) -> dict:
        if not name or not name.strip():
            raise ServiceError("이름을 알려주세요")
        if self.access_code and (code or "") != self.access_code:
            raise ServiceError("접속 코드가 맞지 않아요", 403)
        lid = labeler_id(name)
        self.store.upsert_labeler({"labelerId": lid, "name": name.strip()})
        return {"labelerId": lid, "name": name.strip()}

    # ---------- 배정 ----------
    def _labels_by_bundle(self) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        for l in self.store.labels():
            out.setdefault(l["bundleId"], []).append(l)
        return out

    def current_assignment(self, lid: str) -> dict | None:
        for a in self.store.assignments():
            if a["labelerId"] == lid and a["status"] == "active":
                return a
        return None

    def assign_next(self, lid: str, name: str) -> dict | None:
        """활성 배정이 있으면 그것. 없으면 라벨 0·배정 없음인 가장 낮은 번들을 잡는다."""
        mine = self.current_assignment(lid)
        if mine:
            return mine
        taken = {a["bundleId"] for a in self.store.assignments()}
        labeled = set(self._labels_by_bundle())
        for bid in sorted(self.bundles):
            if bid in taken or bid in labeled:
                continue
            a = {"bundleId": bid, "labelerId": lid, "labelerName": name, "status": "active"}
            try:
                self.store.upsert_assignment(a)
            except Exception:
                continue  # 동시에 다른 사람이 잡았다 — 다음 번들
            return a
        return None

    # ---------- 번들 화면 ----------
    def _bundle_candidates(self, bid: str) -> list[dict]:
        return [c for c in self.candidates if c["bundleId"] == bid]

    def _decided(self, bid: str) -> dict[str, dict]:
        out = {}
        for d in self.store.decisions():
            if d["bundleId"] == bid and d.get("candidateId"):
                out[d["candidateId"]] = d
        return out

    def _review_view(self, r: dict) -> dict:
        return {
            "reviewId": r["reviewId"], "rating": r["raw"]["rating"], "month": r["derived"]["reviewYearMonth"],
            "author": r["derived"]["authorKey"], "conditionLine": _condition_line(r, self.codebook),
            "content": fold_invisible(r["raw"]["content"]),
        }

    def bundle_view(self, lid: str) -> dict:
        a = self.current_assignment(lid)
        if not a:
            return {"assignment": None}
        b = self.bundles[a["bundleId"]]
        cands = self._bundle_candidates(b["bundleId"])
        decided = self._decided(b["bundleId"])
        reviews = {r["reviewId"]: r for r in b["reviews"]}
        authors = {r["reviewId"]: r["derived"]["authorKey"] for r in b["reviews"]}
        total_authors = len(set(authors.values()))
        views = []
        for c in cands:
            k = c["candidate"]
            quotes = []
            for e in k.get("evidence") or []:
                r = reviews.get(e["reviewId"])
                if not r:
                    continue
                content = fold_invisible(r["raw"]["content"])
                i = content.find(e["quote"])
                quotes.append({
                    "reviewId": e["reviewId"], "stance": e["stance"], "stanceLabel": STANCE_LABEL.get(e["stance"], e["stance"]),
                    "quote": e["quote"], "rating": r["raw"]["rating"], "conditionLine": _condition_line(r, self.codebook),
                    "before": content[max(0, i - 90):i] if i >= 0 else "", "after": content[i + len(e["quote"]):i + len(e["quote"]) + 90] if i >= 0 else "",
                    "verbatim": i >= 0,
                })
            spoke = {authors[e["reviewId"]] for e in k.get("evidence") or [] if e["reviewId"] in authors}
            by = {s: len({authors[e["reviewId"]] for e in k.get("evidence") or [] if e.get("stance") == s and e["reviewId"] in authors}) for s in ("positive", "negative", "neutral")}
            views.append({
                "candidateId": c["candidateId"], "aspect": k.get("aspect"), "question": k.get("question"), "answer": k.get("answer"),
                "direction": derive_direction(k.get("evidence") or [], b) if k.get("evidence") else None,
                "condition": k.get("condition"), "quotes": quotes, "counts": {**by, "silent": total_authors - len(spoke)},
                "contractErrors": c.get("contractErrors") or [], "checks": labeler_checks(auto_checks(k, b, cands)),
                "stanceMigrated": bool(c.get("stanceMigrated")),
                "decision": decided.get(c["candidateId"]),
                "missed": self.missed_reviews(b, k, exclude={e["reviewId"] for e in k.get("evidence") or []}),
            })
        my_labels = [l for l in self._labels_by_bundle().get(b["bundleId"], [])]
        scope = b["scope"]
        scope_label = None
        if scope["axis"]:
            seg = scope["segment"]
            scope_label = seg if scope["axis"] == "option" or seg == MISSING_SEGMENT else f"{self.codebook.label(scope['axis'], seg)}({seg})"
        return {
            "assignment": a,
            "bundle": {"bundleId": b["bundleId"], "displayName": b["displayName"], "category": b["category"], "reviewCount": len(b["reviews"]),
                       "scope": scope, "scopeLabel": scope_label, "authors": total_authors},
            "candidates": views,
            "reviews": [self._review_view(r) for r in b["reviews"]],
            "aspectHits": aspect_hits(b["reviews"], self.keywords),
            "reasons": REASONS,
            "stanceLabels": STANCE_LABEL,
            "labels": my_labels,
            "noMissed": any(d["bundleId"] == b["bundleId"] and d["kind"] == "no_missed" for d in self.store.decisions()),
        }

    # ---------- 놓친 근거 (문자열 매칭, 모델 없음) ----------
    def missed_reviews(self, b: dict, k: dict, exclude: set[int], limit: int = 8) -> list[dict]:
        words = list(self.keywords.get(k.get("aspect") or "", []))
        text = " ".join([k.get("question") or "", k.get("answer") or ""] + [e.get("quote") or "" for e in k.get("evidence") or []])
        stems = set()
        for t in re.sub(r"[^가-힣A-Za-z0-9\s]", " ", text).split():
            if 2 <= len(t) <= 8:
                stems.add(t[:3])
                if len(t) >= 4:
                    stems.add(t[:2])
        stems -= {"바르", "발라", "입술", "색이", "색은", "생각", "정도", "다른", "때문", "제가", "저는", "이번", "처음", "리뷰", "있다", "있고"}
        scored = []
        for r in b["reviews"]:
            if r["reviewId"] in exclude:
                continue
            content = fold_invisible(r["raw"]["content"])
            ha = [w for w in words if w in content]
            hq = [w for w in stems if w in content]
            if not ha and not hq:
                continue
            sents = [s.strip() for s in re.split(r"(?<=[.!?…~])\s+|\n+", content) if len(s.strip()) >= 4]
            sents = [s for s in sents if any(w in s for w in ha + hq)][:3]
            if sents:
                scored.append({"reviewId": r["reviewId"], "score": len(ha) * 2 + len(hq), "hits": sorted(set(ha + hq))[:4], "sentences": sents,
                               "rating": r["raw"]["rating"], "conditionLine": _condition_line(r, self.codebook)})
        scored.sort(key=lambda x: -x["score"])
        return scored[:limit]

    # ---------- 판정 ----------
    def _next_label_id(self, bid: str) -> str:
        existing = {l["labelId"] for l in self.store.labels()}
        n = 1
        while f"{bid}-{n}" in existing:
            n += 1
        return f"{bid}-{n}"

    def _base_label(self, b: dict, k: dict) -> dict:
        return {
            "labelId": self._next_label_id(b["bundleId"]), "bundleId": b["bundleId"], "productId": b["productId"],
            "aspect": k.get("aspect"), "question": k.get("question"), "answer": k.get("answer"),
            "condition": k.get("condition") or {"skinType": None, "skinTrouble": None, "option": None},
            "evidence": k.get("evidence") or [], "failureReasons": [], "evaluation": "complete", "notes": None,
        }

    def decide(self, lid: str, payload: dict) -> dict:
        a = self.current_assignment(lid)
        if not a:
            raise ServiceError("배정된 제품이 없어요", 409)
        b = self.bundles[a["bundleId"]]
        cid = payload.get("candidateId")
        cand = next((c for c in self._bundle_candidates(b["bundleId"]) if c["candidateId"] == cid), None)
        if not cand:
            raise ServiceError("이 제품의 후보가 아니에요")
        if cid in self._decided(b["bundleId"]):
            raise ServiceError("이미 판단한 후보예요", 409)
        kind = payload.get("kind")
        k = dict(cand["candidate"])
        label = self._base_label(b, k)
        label["minutesSpent"] = max(0.5, float(payload.get("minutesSpent") or 0.5))
        label["candidateId"] = cid
        if kind == "accept":
            label["source"] = "candidate_accepted"
        elif kind == "edit":
            edits = payload.get("edits") or {}
            for f in ("question", "answer", "aspect", "evidence", "condition"):
                if f in edits:
                    label[f] = edits[f]
            label["source"] = "candidate_edited"
            label["notes"] = (edits.get("notes") or "").strip() or None
        elif kind == "reject":
            reason = REASON_BY_KEY.get(payload.get("reason") or "")
            if not reason:
                raise ServiceError("이유를 하나 골라주세요")
            label["source"] = "candidate_rejected"
            label["failureReasons"] = [reason["failure"]]
            label["notes"] = reason["title"]
        else:
            raise ServiceError("판정은 accept / edit / reject 중 하나예요")
        label["direction"] = derive_direction(label["evidence"], b) if label["evidence"] else "neutral"

        saved_label = None
        error = None
        try:
            saved_label = validate_label(label, b, self.taxonomy)
        except GoldenContractError as e:
            error = str(e)
            if kind != "reject":
                raise ServiceError(self._friendly(error))
        if saved_label:
            self.store.add_label(saved_label, lid)
        self.store.add_decision({"bundleId": b["bundleId"], "labelerId": lid, "candidateId": cid, "kind": kind,
                                 "reason": payload.get("reason"), "labelId": saved_label["labelId"] if saved_label else None,
                                 "contractError": error})
        out = {"kind": kind, "labelId": saved_label["labelId"] if saved_label else None}
        if saved_label:
            out["counts"] = support_counts(saved_label, b)
            out["direction"] = saved_label["direction"]
        elif error:
            out["note"] = "후보의 인용이 원문과 달라 정답 파일에는 넣지 않고 기각 기록만 남겼어요"
        return out

    def add_human(self, lid: str, payload: dict) -> dict:
        a = self.current_assignment(lid)
        if not a:
            raise ServiceError("배정된 제품이 없어요", 409)
        b = self.bundles[a["bundleId"]]
        if payload.get("none"):
            self.store.add_decision({"bundleId": b["bundleId"], "labelerId": lid, "candidateId": None, "kind": "no_missed", "reason": None, "labelId": None})
            return {"kind": "no_missed"}
        label = self._base_label(b, {
            "aspect": payload.get("aspect"), "question": payload.get("question"), "answer": payload.get("answer"),
            "condition": payload.get("condition") or self._default_condition(b), "evidence": payload.get("evidence") or [],
        })
        label.update({"source": "human", "candidateId": None, "minutesSpent": max(0.5, float(payload.get("minutesSpent") or 0.5)),
                      "notes": (payload.get("notes") or "").strip() or None})
        label["direction"] = derive_direction(label["evidence"], b) if label["evidence"] else "neutral"
        try:
            saved = validate_label(label, b, self.taxonomy)
        except GoldenContractError as e:
            raise ServiceError(self._friendly(str(e)))
        self.store.add_label(saved, lid)
        self.store.add_decision({"bundleId": b["bundleId"], "labelerId": lid, "candidateId": None, "kind": "human", "reason": None, "labelId": saved["labelId"]})
        return {"kind": "human", "labelId": saved["labelId"], "counts": support_counts(saved, b), "direction": saved["direction"]}

    def _default_condition(self, b: dict) -> dict:
        cond = {"skinType": None, "skinTrouble": None, "option": None}
        s = b["scope"]
        if s["axis"]:
            cond[s["axis"]] = [s["segment"]] if s["axis"] == "skinTrouble" else s["segment"]
        return cond

    def complete(self, lid: str) -> dict:
        a = self.current_assignment(lid)
        if not a:
            raise ServiceError("배정된 제품이 없어요", 409)
        bid = a["bundleId"]
        cands = self._bundle_candidates(bid)
        decided = self._decided(bid)
        undecided = [c["candidateId"] for c in cands if c["candidateId"] not in decided]
        if undecided:
            raise ServiceError(f"아직 판단하지 않은 후보가 {len(undecided)}개 있어요")
        decisions = [d for d in self.store.decisions() if d["bundleId"] == bid]
        if not any(d["kind"] in ("human", "no_missed") for d in decisions):
            raise ServiceError("놓친 질문이 있는지 한 번 답해주세요 (없으면 '없어요')")
        self.store.upsert_assignment({**a, "status": "done", "_update": True})
        summary = {k: sum(1 for d in decisions if d["kind"] == k) for k in ("accept", "edit", "reject", "human")}
        nxt = self.assign_next(lid, a["labelerName"])
        return {"bundleId": bid, "summary": summary, "next": ({"bundleId": nxt["bundleId"], "displayName": self.bundles[nxt["bundleId"]]["displayName"],
                                                                "reviewCount": len(self.bundles[nxt["bundleId"]]["reviews"])} if nxt else None)}

    def progress(self, lid: str) -> dict:
        done = [a for a in self.store.assignments() if a["labelerId"] == lid and a["status"] == "done"]
        return {"doneBundles": len(done)}

    @staticmethod
    def _friendly(msg: str) -> str:
        msg = re.sub(r"^\[labelId=[^\]]*\]\s*", "", msg)
        if "부분문자열" in msg:
            return "원문과 한 글자라도 다르면 저장할 수 없어요. 문장을 다시 선택해 주세요."
        if "evidence 가 비었다" in msg:
            return "근거 문장을 하나 이상 골라주세요."
        if "question" in msg and "비었" in msg:
            return "질문을 적어주세요."
        if "answer" in msg and "비었" in msg:
            return "답을 적어주세요."
        if "세그먼트" in msg or "미기재" in msg:
            return "이 제품 묶음의 조건과 맞지 않는 리뷰가 근거에 있어요. 해당 근거를 빼주세요."
        return msg
