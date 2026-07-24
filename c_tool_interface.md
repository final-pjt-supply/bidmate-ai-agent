# C 검색 도구 인터페이스 (retrieval 노드용)

> C가 제공하는 도구 함수와 호출 방법. `agents/nodes/`의 retrieval 노드가 이 문서대로 호출하면 된다.

---

## 노드에서 쓸 함수

```python
from agents.tools.search import retrieve_chunks

chunks = retrieve_chunks(
    query,              # str  — 보통 intent.normalized_query
    filters=...,        # dict | Filters | None — state["resolved_filters"]
    top_k=10,           # int | None — 생략 시 설정 기본값(10)
    types=None,         # list[str] | None — ["text","table","box"] 중 선택
)
# → list[Chunk]  (agents.schemas.Chunk, 점수 내림차순)
```

반환은 계약의 `Chunk`뿐이다. 검색 점수·집계는 노드로 넘기지 않는다.

### 노드 구현 예시

```python
from agents.logging_util import node_logger
from agents.tools.search import retrieve_chunks


@node_logger("retrieval")
def retrieval_node(state: dict) -> dict:
    chunks = retrieve_chunks(
        state["intent"].normalized_query,
        filters=state["resolved_filters"],
    )
    return {"chunks": chunks}
```

---

## filters 처리 규칙

`resolved_filters`(merge.py 산출 dict)를 그대로 넘기면 된다. 내부 동작은 이렇다.

| 조건 | 처리 |
| --- | --- |
| `bid_ids` 있음 | **그 공고들로만 한정.** 마감·차수 필터를 덧씌우지 않는다 |
| `deadline_within_days` | `bid_clse_dt` 기준 N일 이내 |
| `budget_min` / `budget_max` | `presmpt_prce` 범위 |
| `category` | `bid_category`(업무구분 코드일 때만) — 아래 미결 참조 |
| `region` | **미구현** — 아래 미결 참조 |
| 조건 없음 | 마감 전 + 최신 차수 공고 전체 |

### bid_ids를 특별 취급하는 이유

Case 2(특정 공고 화면 진입)나 멀티턴 스코프 승계에서 `bid_ids`가 들어온다. 이때 마감 필터를 적용하면 "이 공고 자격 요건 뭐야?"라는 질의에 빈 결과가 나온다. 이미 마감된 공고도 내용은 조회 가능해야 하므로, `bid_ids`가 있으면 다른 조건 없이 그대로 스코프로 쓴다.

### 마감 필터를 기본으로 두는 이유

색인된 공고 중 마감 전은 약 1,369건뿐이다. 검색 후 걸러내는 방식은 후보의 약 90%가 탈락해 결과가 빈약해진다. 그래서 검색 **전에** PostgreSQL에서 유효 공고 범위를 확정한 뒤 OpenSearch에 넘긴다.

---

## 다른 도구 (참고)

노드에서 쓸 일은 없지만, 배치·평가·추천 화면용으로 제공된다.

```python
from agents.tools.search import search_bids, search_bids_grouped, recommend_bids

search_bids(query, ...)          # → SearchResult (Chunk + score)
search_bids_grouped(query, ...)  # → BidSearchResult (공고 단위, 중복 제거)
recommend_bids(query, ...)       # → list[RecommendedBid] (+ bid_table 메타)
```

`retrieve_chunks`는 `search_bids`를 감싼 얇은 껍질이다.

---

## 필요한 환경변수

C 도구가 동작하려면 `.env`에 아래가 필요하다. (팀 기존 `AWS_ACCESS_KEY` / `AWS_SECRET_KEY`에 **추가**)

```
# OpenSearch (AWS OpenSearch Service, VPC 도메인)
OPENSEARCH_ENDPOINT=https://vpc-....ap-northeast-2.es.amazonaws.com
OPENSEARCH_USER=...
OPENSEARCH_PASSWORD=...
OPENSEARCH_INDEX=bid_chunks
OPENSEARCH_VERIFY_CERTS=false

# PostgreSQL (bid_table)
PG_HOST=...
PG_PORT=5432
PG_DATABASE=...
PG_USER=...
PG_PASSWORD=...

# Cloudflare Workers AI (질의 임베딩 — 색인과 동일 모델이어야 함)
CF_ACCOUNT_ID=...
CF_API_TOKEN=...
CF_EMBEDDING_MODEL=@cf/baai/bge-m3

# 검색 파라미터 (선택 — 없으면 기본값)
DEFAULT_TOP_K=10
KNN_WEIGHT=0.5
BM25_WEIGHT=0.5
USE_NORMALIZATION=true
KNN_CANDIDATE_MULTIPLIER=20
```

추가 패키지: `opensearch-py`, `httpx`, `psycopg[binary]`, `psycopg-pool`

**VPC 주의**: OpenSearch가 VPC 도메인이라 VPN 등으로 해당 네트워크에 접근 가능한 환경에서만 동작한다.

---

## 미결 사항 (B와 합의 필요)

**`Filters.category`의 의미** — 계약 주석은 '업종'인데, `bid_table`에는 `bid_category`(업무구분: cnstwk/servc/thng/frgcpt)와 `item_codes`(품목·업종 코드)가 별개로 존재한다. 현재는 업무구분 코드로 들어온 경우에만 적용하고, 그 외 값은 무시한다.

**`Filters.region` 매핑 대상** — 시도명을 `region_limit_names`(입찰 자격 제한 지역)에 매핑할지 `cnstrtsite_rgn_nm`(공사현장 지역)에 매핑할지 미정. 자격 판정 영역과 겹치므로 B와 역할 경계를 함께 정리해야 한다. 현재 미구현이며, region이 들어와도 무시된다.

---

## 알려진 제약

**공고명이 검색 대상이 아니다** — OpenSearch에는 첨부 공고서 본문만 색인되어 있고, 공고명(`bid_ntce_nm`)은 PostgreSQL에만 있다. "소프트웨어 공고"처럼 제목 기준 질의는 본문 매칭에 의존한다.

**표준 문구 오염** — "소프트웨어 개발보안 준수" 같은 표준 조항이 사업 성격과 무관하게 대부분의 공고에 포함되어, 관련 없는 공고가 상위에 오를 수 있다.

**점수 하한선 없음** — 현재 top_k를 무조건 채운다. 관련도가 낮아도 반환되므로, 조건형 질의("오늘 마감인 공고")가 검색으로 넘어오면 부적절한 결과가 나온다. 정답셋 평가 후 임계값 도입 예정.

**응답 시간** — 검색 1회에 수 초 소요(임베딩 호출 + OpenSearch 검색). 캐싱·계측은 후속 작업.