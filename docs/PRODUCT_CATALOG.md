# 제품 카탈로그 레이어 (PER-171)

작성: 2026-09-03 · PRD §3-3 · 이슈 [PER-171](https://linear.app/banjax/issue/PER-171)
정본 파일: `data/input/product_catalog.json` (생성물 — 손으로 고치지 않는다)
생성기: [`pipeline/build_product_catalog.py`](../pipeline/build_product_catalog.py) · 로더: [`pipeline/catalog.py`](../pipeline/catalog.py)
계약 테스트: [`pipeline/test_catalog.py`](../pipeline/test_catalog.py) · 커버리지 근거: [`eval/reports/product_catalog_coverage.json`](../eval/reports/product_catalog_coverage.json)

---

## 0. 이 레이어가 있는 이유

제품 동일성은 분석의 판단 대상이 아니라 **입력 계약의 일부**다. 파이프라인이 매번 "이 리뷰가 어느 제품 것인가"를 풀면 단계마다 다르게 풀 수 있고, 그 불일치는 집계 수치가 틀어진 뒤에야 드러난다.

cursor API는 요청한 상품뿐 아니라 **변형 SKU의 리뷰를 합산해 반환한다**(`docs/SCRAPLING_MIGRATION_POC.md` §5-2c). 그래서 25K 스냅샷은 `productKey` 50개인데 `goodsNo`는 153개다. 한 제품 안에 `productName`이 최대 17종까지 있다(피지오겔 DMT 페이셜크림 — 기획·증정·용량 변형).

### 이름을 키로 쓰면 실제로 깨진다 — 측정된 사고 2건

v4는 `data/input/product_canonical_map.json`에서 **한글 제품명을 키**로 매핑을 관리했다. 그 결과:

| 사고 | 실측 |
|---|---|
| 파일 간 이름 드리프트 | v4 맵과 `crawler/products_50.json` 사이에 표시명 불일치 **10쌍** (`피지오겔 DMT 페이셜 크림` vs `피지오겔 DMT 페이셜크림`, `롬앤 쥬시…` vs `롬앤 더 쥬시…` 등) |
| **브랜드 오기** | `A000000166641`을 v4 맵은 `닥터자르트 레드 블레미쉬 클리어 수딩 크림`으로 적었다. 이 제품은 **닥터지(Dr.G)** 상품이고, 리뷰의 `productName`도 닥터지 것이다 |

그리고 v4 맵은 현재 데이터와도 어긋나 있었다 — 25K의 `goodsNo` 153개 중 **37개(리뷰 2,003건) 미등록**, 반대로 맵에만 있는 `goodsNo` 14개, 크롤 요청 50개 중 6개 미등록.

→ 그래서 카탈로그의 키는 **표시명이 아니라 고정 ID(`productId`)** 다. 표시명 오타를 고쳐도 집계 키가 바뀌지 않는다.

---

## 1. 스키마

```json
{
  "_meta": { "schemaVersion": "v5-1", "rules": [...], "sources": [{"path", "sha256", "role"}] },
  "products": [
    {
      "productId": "p041",
      "displayName": "닥터지 레드 블레미쉬 클리어 수딩크림",
      "category": "크림",
      "requestedGoodsNo": "A000000164615",
      "renewalPolicy": null,
      "notes": ["v4 맵 표시명 '닥터자르트 …' 정정: …"],
      "goodsNos": [{"goodsNo": "A000000164615", "source": "crawl_request"}, ...]
    }
  ]
}
```

| 필드 | 의미 |
|---|---|
| `productId` | 집계 단위. `p001`~ 형식, **한 번 부여하면 고정**(생성기가 append-only로 관리) |
| `displayName` | 사람이 읽는 이름. 프롬프트·UI·레거시 조회용. 바뀌어도 `productId`는 유지 |
| `requestedGoodsNo` | 이 제품을 대표하는 SKU(크롤 요청에 쓴 값) |
| `goodsNos[].source` | `crawl_request`(요청 목록) / `observed_variant`(스냅샷에서 관측) / `legacy_v4`(v4 시절 SKU, 레거시 재현용) |
| `renewalPolicy` | **PER-172 결정 슬롯.** 현재 전 제품 `null` — 리뉴얼을 별개 제품으로 볼지 시점 컷으로 볼지는 여기서 정하지 않는다 |
| `notes` | 정정 이력. 위 브랜드 오기처럼 사람이 판단한 근거를 남긴다 |

현재 상태: **제품 50개 / `goodsNo` 167개** (`crawl_request` 50 + `observed_variant` 103 + `legacy_v4` 14).

---

## 2. 파이프라인이 지켜야 하는 규칙

```python
from catalog import load_catalog

catalog = load_catalog()
product = catalog.product_of_goods_no(row["goodsNo"])   # 미등록이면 UnknownGoodsNoError
```

1. **리뷰 행의 `productKey` 문자열을 읽지 않는다.** 제품은 `goodsNo` → 카탈로그로만 확정한다. `step0_preprocess.py`가 그 경계다.
2. **미등록 `goodsNo`는 조용히 폴백하지 않고 에러다.** 폴백은 같은 제품을 두 개로 쪼개고, 충분성 게이트에서 근거 수가 조용히 줄어든다.
3. **매핑을 코드 상수로 들지 않는다.** 유일한 정본은 카탈로그 파일이다.
4. **집계 단위는 `productId`.** `goodsNo`로 그룹핑하면 리뷰 1~7건 상품이 139개 생겨 충분성 게이트가 대량 실패한다(`docs/V5_INPUTS_AND_LEGACY_AUDIT.md` §3-1).
5. 카탈로그가 계약을 위반한 상태면 **로드 시점에** 에러다 — 한 `goodsNo`가 두 제품에 걸침, 표시명 중복, `schemaVersion` 불일치, ID·`goodsNo` 형식 위반, 미지의 `source`.

---

## 3. 운영 절차 — 새 SKU가 등장하면

파이프라인이 `UnknownGoodsNoError`로 멈춘다. 그때 하는 일:

1. 그 `goodsNo`가 **어느 제품인지 사람이 정한다.** 리뷰의 `productName`과 `requestedGoodsNo`가 단서다.
2. 새 수집분이면 `crawler/products_50.json`(요청 목록) 또는 스냅샷에 이미 들어 있을 것이다 → 생성기를 다시 돌리면 `observed_variant`로 붙는다.

```bash
.venv/bin/python pipeline/build_product_catalog.py          # 카탈로그·커버리지 리포트 갱신
.venv/bin/python pipeline/build_product_catalog.py --check  # 커밋본이 소스와 일치하는지
python3 -m unittest discover -s pipeline -p 'test_*.py'     # 계약 테스트
```

3. 생성기가 **추측이 필요한 지점에서 멈춘다**: 한 `goodsNo`가 두 제품에 걸치거나, v4 맵 엔트리의 브랜드가 정본과 다르면 에러다. 브랜드 불일치는 확인 후 `RESOLVED_BRAND_CONFLICTS`에 **근거와 함께** 등록해야 통과한다(현재 1건).
4. 카탈로그 파일을 직접 편집하지 않는다. 생성기가 다시 덮어쓴다.

`--check`는 같은 입력에서 같은 카탈로그가 나오는지 확인한다(PRD §5-2 재현성). 생성물에 타임스탬프를 넣지 않고 소스 파일의 sha256만 기록하는 이유다.

---

## 4. 확인된 상태 (2026-09-03)

| 항목 | 결과 |
|---|---|
| 25K 스냅샷 25,000건 | `goodsNo` 153개 전부 해석 → **미해결 0** |
| v4 입력 2파일(각 500건) | `goodsNo` 12개 전부 해석 → **미해결 0**, 그룹 키·카테고리가 v4와 동일 |
| v4 맵 엔트리 50개 | 이름 드리프트 10건 재조정, **고아 0건** |
| 계약 테스트 | 15 케이스 통과 (미등록 폴백 금지 · 로드 검증 7종 · 레거시 표시명 조회) |

v4 베이스라인은 그대로 재현된다 — step0 재실행 결과가 리뷰 499건 / 5제품이고 그룹 키와 카테고리가 이전과 같다. 추가된 것은 `productId`·`displayName` 필드뿐이다.

---

## 5. 남은 것

- **`productId`를 파이프라인 전 구간의 그룹 키로 승격** — 현재 step0의 중간 산출물은 v4 호환을 위해 표시명으로 키를 잡고 `productId`를 필드로 싣는다. v4의 step1~4는 이 문자열을 프롬프트·`concernId` 접두사·출력 스키마에 그대로 쓰기 때문에, 키를 바꾸면 v4 베이스라인 산출물이 달라진다. v5 생성 단계(#5)를 새로 쓸 때 `productId`로 승격한다.
- **`renewalPolicy`** — PER-172.
- **입력 계약 문서화** — PER-176이 이 문서를 참조해 계약으로 고정한다.
