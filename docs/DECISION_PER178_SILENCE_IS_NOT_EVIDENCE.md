# 결정: 침묵은 근거가 아니다 — 언급 수를 분모로 보존한다 (PER-178)

작성: 2026-09-09 · 이슈 [PER-178](https://linear.app/banjax/issue/PER-178) · 적용 범위: 골든셋(PER-178~180) → 게이트(PER-186) → 생성(PER-189) → 표기(PER-190·7-4)
강제 지점: [`pipeline/golden_contract.py`](../pipeline/golden_contract.py) `support_counts` · [`eval/concern_candidates.py`](../eval/concern_candidates.py) `auto_checks.generalizes_from_silence`
관련: [`DECISION_PER177_FAILURE_TAXONOMY.md`](DECISION_PER177_FAILURE_TAXONOMY.md) §2 (U/D/S) · [`DECISION_PER178_GOLDEN_LABELING_SPEC.md`](DECISION_PER178_GOLDEN_LABELING_SPEC.md) §9

---

## 1. 문제

라벨러 질문(B01 첫 라벨, 2026-09-09): "구순염이 생겼다는 리뷰가 있고 생기지 않았다는 리뷰도 있다"라는 답은
**구순염을 언급하지 않은 리뷰 37개를 '생기지 않았다'로 센 것인가?**

번들 B01은 리뷰 40건(고유 작성자 40명)이고 구순염을 말한 사람은 3명이다. 나머지 37명은 이 주제를 말하지 않았다.
같은 함정은 향("향을 말하지 않음 ≠ 향이 없음"), 셀 번들("민감성인데 자극을 말하지 않음 ≠ 자극 없음"),
옵션("그 호수를 말하지 않음 ≠ 문제 없음") 어디서나 생긴다.

## 2. 결정

**언급하지 않은 리뷰는 긍정도 부정도 아니다. 어느 쪽 근거로도 세지 않고, 그 수를 따로 보존한다.**

| 기호 | 정의 | 골든셋 필드 | B01-c1 |
|---|---|---|---|
| U+ | 주제에 대해 긍정을 **명시**한 고유 작성자 | `positiveAuthors` | 1 |
| U− | 부정을 **명시**한 고유 작성자 | `negativeAuthors` | 2 |
| D | 주제를 말한 작성자 = U+ ∪ U− | `spokeAuthors` | 3 |
| S | 셀(번들)의 유효 고유 작성자 전체 | `bundleAuthors` | 40 |
| S − D | **언급 없음** | `silentAuthors` | 37 |

- 안 생겼다는 긍정 근거는 "다행히 구순염 피해갔네요"처럼 **명시 문장**만이다.
- 답 문장이 언급 없음을 부정 증거로 일반화하면("대부분 트러블이 없다") **`unsupported_claim`** 이다 — 인용이 전부 원문과
  일치해도 명제가 근거 밖이다 (PER-177 §2 "정확한 인용이라도 명제를 뒷받침하는지").
- 방향(`direction`)은 U+와 U−만으로 계산한다. S−D 는 방향에 영향을 주지 않는다.
- 비율은 골든셋 답에 쓰지 않는다. 번들은 1~2★ 을 과표집했으므로 "40명 중 3명"도 코퍼스 비율이 아니다.
  비율은 파이프라인이 전수·층별 가중치로 낸다.

리스크 질문의 정직한 답 형태:
> 언급한 사람 중에서는 생겼다는 쪽이 많고, 안 생겼다는 사람도 있다. 대부분의 리뷰는 이 주제를 말하지 않는다.

## 3. 파이프라인에서 유지하는 방법

| 단계 | 유지 규칙 | 담당 |
|---|---|---|
| 태깅 (PER-175) | 태그는 `(리뷰 × aspect)` 단위. 리뷰가 그 aspect 를 말하지 않으면 태그가 **없다** — 중립 태그를 만들어 채우지 않는다 | 확정 |
| 게이트3 방향 (PER-185) | 방향은 태그가 있는 리뷰의 U+/U− 로만. 태그 없는 리뷰를 한쪽에 넣지 않는다. 혼재는 양쪽 수 보존 | 구현 시 |
| 게이트4 충분성 (PER-186) | `U ≥ N_min AND U/D ≥ R_min AND S ≥ S_min`. **분모 D 는 언급한 작성자**, S 는 셀 크기. 미기재 리뷰는 특정 조건 셀의 D·S 에 넣지 않는다. D 가 작을 때(예: 40 중 3) 노출 여부와 표기는 여기서 정한다 | 구현 시 |
| 생성 (PER-189) | 프롬프트에 "언급하지 않은 리뷰를 어느 쪽 증거로도 쓰지 말 것"을 명시. `support` 수치는 코드가 U/D/S 에서 계산해 넣고 LLM 이 쓰지 않는다 | 구현 시 |
| 검증 (PER-189/196) | 답에 일반화 어휘(대부분·거의·누구나·문제 없…)가 있고 D 가 작으면 judge 가 `unsupported_claim` 후보로 본다 — 골든셋 도구의 `generalizes_from_silence` 와 같은 규칙 | 구현 시 |
| 표기 (PER-190·7-4) | 아마존 방식으로 세 수를 따로: "리뷰 N개 중 D명이 언급 · 긍정 U+ · 부정 U−". "만족 95%" 같은 전체 분모 비율은 쓰지 않는다 | 구현 시 |
| 평가 (PER-197·201) | judge 일치율은 U+/U−/D 를 라벨과 대조. v4 대비 비교에서 "부정 은폐"를 이 수치로 잰다 | 구현 시 |

## 4. 외부 사례 (2026-09-09 조사)

같은 결론에 도달한 사례가 이미 있다. 요약만 적고 출처는 아래에 둔다.

- **아마존 "Customers say"**: 속성별로 언급 고객 수 · 긍정 수 · 부정 수 세 값을 따로 보여준다 (예: "264 customers mention
  'Comfort' — 247 positive, 17 negative"). 속성을 누르면 그 속성을 언급한 리뷰 원문 발췌와 링크. 하이라이트는 여러 고객이
  같은 의견을 말했을 때만 생성. **언급하지 않은 고객을 만족으로 세지 않는다.** 구글 맵도 스니펫 아래 "이 표현을 언급한
  다른 사용자 수"를 붙인다.
- **NN/G, AI 리뷰 요약 사용성 연구**: 사용자는 요약이 긍정을 과장하고 부정을 축소한다고 의심하며, 일관되게 좋은 요약은
  체리피킹으로 읽힌다. 권고: 부정 테마를 숨기지 말 것, 모든 주장에서 원문 인용·링크로 되돌아갈 수 있게 할 것.
- **Hu·Pavlou·Zhang (MIS Quarterly 2017)**: 리뷰의 J자 분포는 획득 편향(호의적인 사람이 사고 쓴다)과 과소보고 편향(극단적
  경험만 쓴다)에서 온다. 리뷰를 쓰지 않은 다수의 의견은 알 수 없다. 우리 "언급 없음"은 그 한 단계 아래 — 리뷰는 썼지만
  그 주제는 말하지 않은 사람.
- **설문 무응답 편향**: 채팅 상담 만족도에서 무응답자 대다수가 실제로는 불만족이었다는 결과. "말하지 않음 = 문제 없음"은
  방향까지 틀릴 수 있다.
- **Opinion Prevalence (2023)**: 요약 문장이 원문 리뷰 몇 개에 의해 지지되는지를 지표로. 사람이 쓴 요약도 무작위 발췌보다
  겨우 조금 나았다 — 사람도 근거 수를 과장하므로 도구가 고유 작성자 수를 대신 센다.
- **Bias-Aware Design (2022)**: 리뷰어 경험·평점 극단성·언급된 측면을 UI 에 드러내면 편향 인식과 결정 만족도가 오른다.
- **국내 AI 리뷰 요약(크리마 등)**: "긍정 리뷰 95%, 대부분 만족" — 분모가 전체 리뷰라 주제별 언급 수가 드러나지 않는다.
  우리가 피하려는 표기의 예.

## 5. 기각한 대안

- **언급 없음을 긍정(문제 없음)으로 간주**: 무응답 편향 연구가 반증. 방향까지 틀릴 수 있다.
- **언급 없음을 중립 태그로 채움**: 태그 단위 `(리뷰 × aspect)` 계약과 충돌하고, D 가 S 로 부풀어 U/D 가 무의미해진다.
- **골든셋 답에 비율 기재**: 층화 표본이라 코퍼스 비율이 아니다. 비율은 파이프라인 몫.
- **반대 1명이면 다수 방향으로 뭉개기**: 소수 의견을 골든셋에서 지우면 게이트 정책(PER-186)을 평가할 수 없다 (규격 §9).

## 6. 출처

- https://www.aboutamazon.com/news/amazon-ai/amazon-improves-customer-reviews-with-generative-ai
- https://www.aboutamazon.com/news/retail/amazon-ai-generated-review-highlights
- https://www.sellerlabs.com/knowledge-base/ai-generated-review-highlights-what-amazon-shows-and-why-it-matters/
- https://www.nngroup.com/articles/ai-reviews/
- https://gatherup.com/blog/google-review-summaries/
- https://misq.umn.edu/on-self-selection-biases-in-online-product-reviews.html
- https://archive.nyu.edu/bitstream/2451/14951/2/usedbook1.pdf
- https://arxiv.org/pdf/2209.08751
- https://arxiv.org/pdf/2307.14305
- https://arxiv.org/pdf/1803.03346
- https://www.surveymonkey.com/learn/survey-best-practices/nonresponse-bias-what-it-is-and-how-to-avoid-its-errors/
- https://arxiv.org/pdf/2206.01543
- https://www.cre.ma/blog/ai-reviewsummary
