"""하이브리드 검색 도구 (C 파트 핵심).

bid_chunks 인덱스에 대해 벡터(knn) + 키워드(BM25)를 병합 검색한다.
- 벡터: Cloudflare bge-m3로 질의를 임베딩 → knn_vector 검색
- 키워드: 같은 질의 텍스트로 text 필드 BM25 검색 (한국어 분석기)
- 둘을 bool.should 로 묶어 점수를 합산 (search pipeline 없이 동작하는 방식)
- bid_ids / types 로 사전 필터링 가능

두 가지 진입점:
- search_bids()          : 청크 단위 결과. 챗봇 RAG용 (같은 공고 여러 청크 유지).
- search_bids_grouped()  : 공고 단위 결과. 추천 목록용 (bid_id 중복 제거).

이 함수들은 프레임워크 독립적인 순수 파이썬이다.
A의 LangGraph가 tool로 감싸든, 배치가 직접 부르든 그대로 재사용된다.
"""
from __future__ import annotations

from agents.clients.embedding import embed_query
from agents.clients.opensearch import get_client
from agents.config import get_settings
from agents.schemas import Chunk, Filters
from agents.tools.search_types import (
    BidHit,
    BidSearchResult,
    RecommendedBid,
    SearchHit,
    SearchResult,
)

# knn 후보 폭의 기본 배수는 config(KNN_CANDIDATE_MULTIPLIER)에서 읽는다.
# 정규화 하이브리드에서는 이 값이 중요하다: 후보가 적으면 knn 결과와 BM25 결과의
# 겹침이 적어져, 한쪽에만 걸린 문서가 많아지고 점수가 0.5 부근에 몰린다.

# 공고 단위 검색 시, 중복 제거로 개수가 줄어드는 것을 감안해
# 목표 공고 수의 몇 배만큼 청크를 미리 뽑을지.
_DEDUPE_OVERFETCH = 3

# 추천 시 마감·차수 필터로 걸러질 것을 감안한 추가 여유분.
_RECOMMEND_OVERFETCH = 3

# 공고 단위 합산 시 청크 후보 폭.
# 공고당 여러 청크가 걸려야 합산이 의미를 가지므로 넉넉히 가져온다.
_AGGREGATE_FETCH_MULTIPLIER = 40

# OpenSearch knn 제약: k는 (0, 10000] 범위여야 한다.
_KNN_K_MAX = 10000

# 정규화 하이브리드용 임시 search pipeline.
# 클러스터에 등록하지 않고 요청 본문에 실어 보내므로 쓰기 권한이 필요 없다.
# min_max로 BM25/knn 점수를 각각 0~1로 정규화한 뒤 가중 산술평균으로 합친다.
# (bool 방식은 두 점수를 그대로 더해서 스케일이 큰 BM25가 결과를 지배한다)
def _normalization_pipeline(knn_weight: float, bm25_weight: float) -> dict:
    return {
        "phase_results_processors": [
            {
                "normalization-processor": {
                    "normalization": {"technique": "min_max"},
                    "combination": {
                        "technique": "arithmetic_mean",
                        # 가중치 순서는 hybrid.queries 순서와 일치해야 한다
                        "parameters": {"weights": [knn_weight, bm25_weight]},
                    },
                }
            }
        ]
    }


def _build_query(
    *,
    text: str,
    vector: list[float],
    bid_ids: list[str] | None,
    types: list[str] | None,
    size: int,
    knn_weight: float,
    bm25_weight: float,
    knn_k: int,
) -> dict:
    """벡터 + 키워드를 bool.should 로 병합한 OpenSearch 쿼리 본문을 만든다."""
    filters: list[dict] = []
    if bid_ids:
        filters.append({"terms": {"bid_id": bid_ids}})
    if types:
        filters.append({"terms": {"type": types}})

    knn_clause = {
        "knn": {
            "vector": {
                "vector": vector,
                "k": knn_k,
                "boost": knn_weight,
                **({"filter": {"bool": {"filter": filters}}} if filters else {}),
            }
        }
    }

    bm25_clause = {
        "match": {
            "text": {
                "query": text,
                "boost": bm25_weight,
            }
        }
    }

    bool_query: dict = {
        "should": [knn_clause, bm25_clause],
        "minimum_should_match": 1,
    }
    if filters:
        bool_query["filter"] = filters

    return {
        "size": size,
        "query": {"bool": bool_query},
        "_source": {"excludes": ["vector"]},
    }


def _build_hybrid_query(
    *,
    text: str,
    vector: list[float],
    bid_ids: list[str] | None,
    types: list[str] | None,
    size: int,
    knn_weight: float,
    bm25_weight: float,
    knn_k: int,
) -> dict:
    """정규화 하이브리드 쿼리 본문을 만든다.

    bool 방식과 달리 두 점수를 각각 min-max 정규화한 뒤 합치므로,
    BM25(0~수십)와 knn 코사인(0~2)의 스케일 차이가 사라진다.
    """
    filters: list[dict] = []
    if bid_ids:
        filters.append({"terms": {"bid_id": bid_ids}})
    if types:
        filters.append({"terms": {"type": types}})

    knn_inner: dict = {
        "vector": vector,
        "k": knn_k,
    }
    if filters:
        knn_inner["filter"] = {"bool": {"filter": filters}}
    knn_clause = {"knn": {"vector": knn_inner}}

    match_clause: dict = {"match": {"text": {"query": text}}}
    if filters:
        bm25_clause: dict = {"bool": {"must": [match_clause], "filter": filters}}
    else:
        bm25_clause = match_clause

    return {
        "size": size,
        # queries 순서가 pipeline weights 순서와 대응한다 (knn, bm25)
        "query": {"hybrid": {"queries": [knn_clause, bm25_clause]}},
        "_source": {"excludes": ["vector"]},
        "search_pipeline": _normalization_pipeline(knn_weight, bm25_weight),
    }


def _to_hit(hit: dict) -> SearchHit:
    """OpenSearch 응답 1건을 공용 Chunk + 점수로 변환한다.

    공용 계약의 Chunk에는 score가 없으므로 SearchHit으로 감싼다.
    """
    src = hit["_source"]
    chunk = Chunk(
        bid_id=src["bid_id"],
        document_id=src.get("document_id", ""),
        file_id=src.get("file_id", ""),
        chunk_idx=src.get("chunk_idx", 0),
        text=src.get("text", ""),
        type=src.get("type", ""),
    )
    return SearchHit(chunk=chunk, score=hit["_score"])


def _run_search(
    query: str,
    *,
    size: int,
    bid_ids: list[str] | None,
    types: list[str] | None,
    knn_weight: float | None,
    bm25_weight: float | None,
    normalize: bool | None = None,
    knn_multiplier: int | None = None,
) -> tuple[list[SearchHit], int]:
    """공통 검색 실행부. (검색 결과 리스트, 전체 매칭 수)를 반환한다.

    normalize=True면 정규화 하이브리드(임시 pipeline), False면 bool 병합.
    None이면 설정 기본값(USE_NORMALIZATION)을 따른다.
    """
    s = get_settings()
    knn_weight = s.knn_weight if knn_weight is None else knn_weight
    bm25_weight = s.bm25_weight if bm25_weight is None else bm25_weight
    normalize = s.use_normalization if normalize is None else normalize
    mult = s.knn_candidate_multiplier if knn_multiplier is None else knn_multiplier

    vector = embed_query(query)
    builder = _build_hybrid_query if normalize else _build_query
    body = builder(
        text=query,
        vector=vector,
        bid_ids=bid_ids,
        types=types,
        size=size,
        knn_weight=knn_weight,
        bm25_weight=bm25_weight,
        knn_k=min(size * mult, _KNN_K_MAX),
    )
    resp = get_client().search(index=s.opensearch_index, body=body)

    hits = resp.get("hits", {})
    results = [_to_hit(h) for h in hits.get("hits", [])]
    total = hits.get("total", {})
    total_hits = total.get("value", 0) if isinstance(total, dict) else int(total or 0)
    return results, total_hits


def search_bids(
    query: str,
    *,
    bid_ids: list[str] | None = None,
    types: list[str] | None = None,
    top_k: int | None = None,
    knn_weight: float | None = None,
    bm25_weight: float | None = None,
    normalize: bool | None = None,
    knn_multiplier: int | None = None,
) -> SearchResult:
    """청크 단위 하이브리드 검색 (챗봇 RAG용).

    같은 공고의 여러 청크가 그대로 유지된다. 특정 공고 Q&A 시 근거 청크를
    빠짐없이 모으기 위한 용도.

    Args:
        query: 사용자 질의 텍스트.
        bid_ids: 지정 시 해당 공고들로만 검색 범위 제한 (Case 2 문맥).
        types: 청크 종류 필터. None이면 text/table/box 전부 포함.
        top_k: 반환 청크 수. None이면 설정 기본값.
        knn_weight / bm25_weight: 벡터·키워드 가중치. None이면 설정 기본값.

    Returns:
        SearchResult (점수 내림차순 청크 목록).
    """
    query = (query or "").strip()
    if not query:
        return SearchResult(query=query, hits=[], total_hits=0)

    top_k = top_k or get_settings().default_top_k
    hits, total_hits = _run_search(
        query,
        size=top_k,
        bid_ids=bid_ids,
        types=types,
        knn_weight=knn_weight,
        bm25_weight=bm25_weight,
        normalize=normalize,
        knn_multiplier=knn_multiplier,
    )
    return SearchResult(query=query, hits=hits, total_hits=total_hits)


def _aggregate_by_bid(
    hits: list[SearchHit],
    *,
    aggregate: str,
    sum_top_n: int,
) -> list[BidHit]:
    """점수 내림차순 결과를 bid_id 기준으로 묶어 공고 단위 점수를 만든다.

    집계 방식
      "max"      각 공고의 최고 청크 점수만 사용 (기존 방식).
                 "한 번 스친 언급"과 "문서 전체가 그 주제"를 구별하지 못한다.
      "sum_topn" 각 공고의 상위 N개 청크 점수를 합산.
                 관련 공고는 여러 청크에서 반복 득점하므로 총점이 높아진다.

    sum 대신 상위 N개로 제한하는 이유: 단순 전체 합산은 청크가 많은 긴 공고가
    관련도와 무관하게 유리해진다(길이 편향). 상위 N개만 더하면 "여러 곳에서
    관련 내용이 나온다"는 신호는 살리면서 길이 편향은 막을 수 있다.

    실측 근거: "소프트웨어" 질의 기준 관련 공고 34개 청크 매칭 vs 무관 공고 0개,
    "청소" 질의 기준 관련 공고 38/26개 vs 무관 공고 0개. 관련 공고끼리는
    비슷하게 나오므로 합산이 관련 공고 간 순위를 뒤흔들지는 않는다.

    입력이 점수 내림차순이므로 각 공고에서 처음 만난 청크가 곧 최고점이다.
    """
    grouped: dict[str, list[SearchHit]] = {}
    for h in hits:
        grouped.setdefault(h.bid_id, []).append(h)

    results: list[BidHit] = []
    for bid_id, group in grouped.items():
        top = group[0]                       # 입력이 내림차순이므로 첫 항목이 최고점
        used = group[:sum_top_n] if aggregate == "sum_topn" else group[:1]
        score = sum(h.score for h in used) if aggregate == "sum_topn" else top.score
        results.append(BidHit(
            bid_id=bid_id,
            score=score,
            top_hit=top,
            matched_chunks=len(group),
            max_score=top.score,
            summed_chunks=len(used),
            document_ids=sorted({h.chunk.document_id for h in group}),
        ))

    results.sort(key=lambda b: b.score, reverse=True)
    return results


def search_bids_grouped(
    query: str,
    *,
    bid_ids: list[str] | None = None,
    types: list[str] | None = None,
    top_k: int | None = None,
    knn_weight: float | None = None,
    bm25_weight: float | None = None,
    normalize: bool | None = None,
    knn_multiplier: int | None = None,
    aggregate: str | None = None,
    sum_top_n: int | None = None,
    min_score: float | None = None,
    min_chunks: int | None = None,
    fetch_size: int | None = None,
) -> BidSearchResult:
    """공고 단위 하이브리드 검색 (추천 목록용).

    청크 검색 결과를 bid_id로 묶어 공고 점수를 만든다. 집계 방식은
    설정(AGGREGATE)으로 정하며 기본은 상위 N개 합산이다.

    Args:
        query: 사용자 질의.
        bid_ids: 검색 범위 제한.
        types: 청크 종류 필터.
        top_k: 반환할 공고 수.
        knn_weight / bm25_weight / normalize / knn_multiplier: 검색 파라미터.
        aggregate: "max" | "sum_topn". None이면 설정 기본값.
        sum_top_n: 합산에 쓸 청크 수. None이면 설정 기본값.
        min_score: 이 점수 미만 공고 제외. None이면 설정 기본값(0=미적용).
        min_chunks: 걸린 청크가 이 수 미만이면 제외. None이면 설정 기본값.
        fetch_size: 가져올 청크 수. None이면 top_k에 비례해 자동 결정.

    Returns:
        BidSearchResult (집계 점수 내림차순).
    """
    query = (query or "").strip()
    if not query:
        return BidSearchResult(query=query, bids=[], total_hits=0)

    s = get_settings()
    top_k = top_k or s.default_top_k
    aggregate = aggregate or s.aggregate
    sum_top_n = sum_top_n if sum_top_n is not None else s.sum_top_n
    min_score = min_score if min_score is not None else s.min_score
    min_chunks = min_chunks if min_chunks is not None else s.min_chunks

    # 합산 방식은 공고당 여러 청크가 필요하므로 후보를 크게 잡는다.
    if fetch_size is None:
        multiplier = (_AGGREGATE_FETCH_MULTIPLIER
                      if aggregate == "sum_topn" else _DEDUPE_OVERFETCH)
        fetch_size = top_k * multiplier

    hits, total_hits = _run_search(
        query,
        size=fetch_size,
        bid_ids=bid_ids,
        types=types,
        knn_weight=knn_weight,
        bm25_weight=bm25_weight,
        normalize=normalize,
        knn_multiplier=knn_multiplier,
    )

    bids = _aggregate_by_bid(hits, aggregate=aggregate, sum_top_n=sum_top_n)

    # 하한선 — 관련도가 낮은 공고를 top_k 채우기용으로 끌어오지 않는다
    if min_chunks > 1:
        bids = [b for b in bids if b.matched_chunks >= min_chunks]
    if min_score > 0:
        bids = [b for b in bids if b.score >= min_score]

    return BidSearchResult(query=query, bids=bids[:top_k], total_hits=total_hits)


def recommend_bids(
    query: str,
    *,
    filters: Filters | None = None,
    top_k: int | None = None,
    types: list[str] | None = None,
    only_open: bool = True,
    latest_ord_only: bool = True,
    aggregate: str | None = None,
    sum_top_n: int | None = None,
    min_score: float | None = None,
    min_chunks: int | None = None,
) -> list[RecommendedBid]:
    """추천 공고 목록을 만든다: 검색(OpenSearch) + 공고 정보(PostgreSQL).

    흐름
      1) Filters를 bid_table 조회로 해석해 대상 bid_id 목록을 먼저 구한다
      2) 그 목록을 필터로 넘겨 하이브리드 검색 → 버려지는 결과가 없다
      3) bid_id 기준 중복 제거 (공고당 1건)
      4) bid_table에서 공고명·기관·마감일 등을 채운다

    1번을 먼저 하는 이유: 색인 공고 상당수가 이미 마감된 상태라,
    "검색 후 걸러내기" 방식은 후보 대부분이 탈락해 결과가 빈약해진다.
    (실측 생존율 약 10%)

    Args:
        query: 사용자 질의 (Router의 normalized_query).
        filters: 팀 계약의 Filters. 마감·예산·업종·bid_ids를 검색 범위에 반영.
        top_k: 반환할 공고 수. None이면 설정 기본값.
        types: 청크 종류 필터 (text/table/box).
        only_open: True면 마감 지난 공고를 제외한다.
        latest_ord_only: True면 같은 공고번호 중 최신 차수만 남긴다.

    Returns:
        RecommendedBid 목록 (검색 점수 내림차순).
    """
    from agents.tools.bid_info import fetch_bid_info, filters_to_bid_ids

    query = (query or "").strip()
    if not query:
        return []

    top_k = top_k or get_settings().default_top_k

    # 1) 검색 대상을 조건에 맞는 공고로 미리 좁힌다
    allowed = filters_to_bid_ids(
        filters,
        only_open=only_open,
        latest_ord_only=latest_ord_only,
    )
    if allowed is not None and not allowed:
        return []

    # 2) 좁혀진 범위에서 검색 (집계·하한선은 search_bids_grouped가 처리)
    search_result = search_bids_grouped(
        query,
        bid_ids=allowed,
        types=types,
        top_k=top_k,
        aggregate=aggregate,
        sum_top_n=sum_top_n,
        min_score=min_score,
        min_chunks=min_chunks,
    )
    hits = search_result.bids[:top_k]
    if not hits:
        return []

    # 3) 공고 메타 채우기 (한 번의 쿼리)
    info_map = fetch_bid_info([h.bid_id for h in hits])
    return [RecommendedBid(hit=h, info=info_map.get(h.bid_id)) for h in hits]


def retrieve_chunks(
    query: str,
    *,
    filters: dict | Filters | None = None,
    top_k: int | None = None,
    types: list[str] | None = None,
) -> list[Chunk]:
    """그래프 retrieval 노드용 진입점 (도구 호출 계약).

    노드는 이 함수만 부르면 되고, 검색 내부 구조(SearchHit, 점수, 필터 해석)를
    알 필요가 없다. 반환은 팀 공용 계약의 Chunk 리스트뿐이다.

    필터 처리
      - bid_ids가 있으면(Case 2 진입 / 멀티턴 스코프 승계) 그 공고들로만 한정
      - 없으면 나머지 조건(마감·예산·업종)을 bid_table 조회로 해석해 범위 지정
      - 조건이 전혀 없으면 마감 전 공고 전체를 대상으로 검색

    Args:
        query: 검색 질의. 보통 QueryIntent.normalized_query.
        filters: AgentState의 resolved_filters(dict) 또는 Filters 인스턴스.
        top_k: 반환 청크 수. None이면 설정 기본값.
        types: 청크 종류 필터 (text/table/box). None이면 전부.

    Returns:
        점수 내림차순 Chunk 리스트. 결과가 없으면 빈 리스트.
    """
    from agents.tools.bid_info import filters_to_bid_ids

    query = (query or "").strip()
    if not query:
        return []

    # dict(resolved_filters) → Filters 정규화
    if isinstance(filters, dict):
        filters_obj = Filters(**{k: v for k, v in filters.items()
                                 if k in Filters.model_fields})
    else:
        filters_obj = filters

    # 검색 대상 bid_id 범위를 먼저 확정한다.
    # 색인 공고 상당수가 이미 마감 상태라, 검색 후 걸러내면 후보 대부분이 탈락한다.
    allowed = filters_to_bid_ids(filters_obj)
    if allowed is not None and not allowed:
        return []

    result = search_bids(
        query,
        bid_ids=allowed,
        types=types,
        top_k=top_k,
    )
    return [h.chunk for h in result.hits]