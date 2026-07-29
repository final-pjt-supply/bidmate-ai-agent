"""[2] 검색 — C 실구현. stubs.retrieval_node를 대체한다.

배선: run.py에서 build_graph(..., retrieval_node=retrieval_node)로 넘긴다.
stubs.py는 손대지 않는다 — B의 노드가 같은 파일에 있어 충돌을 피하고,
기존 test_stubs.py도 그대로 통과시키기 위함이다.

노드는 도구를 호출하기만 한다. 검색 내부 구조(점수, 필터 해석, 집계)는
agents/tools/search.py가 담당하며 노드는 알 필요가 없다.
"""
import logging

from agents.logging_util import node_logger
from agents.tools.search import retrieve_chunks

logger = logging.getLogger(__name__)


def _bid_names(chunks) -> dict[str, str]:
    """검색된 공고의 bid_id → 공고명 맵. respond가 답변에서 공고를 공고명으로
    부르기 위한 보조 신호다. 조회 실패는 답변을 막을 이유가 아니므로 빈 맵 폴백."""
    if not chunks:
        return {}
    try:
        from agents.tools.bid_info import fetch_bid_info
        infos = fetch_bid_info(sorted({c.bid_id for c in chunks}))
        return {b: i.bid_ntce_nm for b, i in infos.items() if i.bid_ntce_nm}
    except Exception:                                      # noqa: BLE001
        logger.warning("공고명 조회 실패 — 답변은 bid_id 표기로 폴백")
        return {}


@node_logger("retrieval")
def retrieval_node(state: dict) -> dict:
    """질의와 필터로 근거 청크를 찾아 반환한다.

    state에서 읽는 것
        intent.normalized_query  Router가 정규화한 검색용 질의
        resolved_filters         merge.py가 병합한 필터(dict)
                                 bid_ids가 있으면 그 공고들로 범위 한정

    반환은 계약의 Chunk 리스트와 bid_names(공고명 맵)다. 검색 점수는 넘기지
    않는다(Chunk에 score 필드가 없음 — 팀 계약 준수).
    """
    intent = state.get("intent")
    if intent is None:
        # Router를 거치지 않은 호출 — 원 질의로 대체
        query = state.get("query", "")
    else:
        query = intent.normalized_query

    chunks = retrieve_chunks(query, filters=state.get("resolved_filters"))
    # 앞 노드(B 등)가 채운 이름 맵이 있으면 보존하며 합친다
    names = {**(state.get("bid_names") or {}), **_bid_names(chunks)}
    return {"chunks": chunks, "bid_names": names}