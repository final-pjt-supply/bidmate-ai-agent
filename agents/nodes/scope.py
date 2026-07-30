"""[1] 공고 범위 확정 — 어느 공고에 대한 질문인지 정한다.

라우터는 갈래만 정하므로 "어느 공고인가"는 이 노드가 정한다. 결과는
resolved_filters["bid_ids"]로 넘어가고, 뒤 노드는 그 범위 안에서만 일한다.

    entry_bid 있음   → 그 공고 (화면 문맥이 가장 강한 신호)
    자격 + 없음      → match_results에서 verdict='가능' 마감 임박 순 상위 N건
    상세 + 없음      → 빈 리스트 — bid_search 노드가 검색으로 찾는다

bid_ids는 못 구했을 때도 **빈 리스트로 채운다.** 뒤 분기(graph._found_bids)가
"비었나"만 보게 하려면 키 없음·None·빈 리스트가 갈리지 않아야 한다.

멀티턴 승계(직전 턴이 다룬 공고를 잇는 것)는 아직 하지 않는다. merge에 그
분기가 있으나 scope="new"로만 부르므로 도달하지 않는다 — 승계를 넣을 자리는
여기다.
"""
import logging

from agents import merge
from agents.logging_util import node_logger
from agents.schemas import AgentRequest
from agents.state import BidBrief
from agents.tools.match_results import possible_bids

logger = logging.getLogger(__name__)

# 자격 목록을 한 번에 몇 건까지 대화에 실을지.
# 회사 9001 기준 '가능'이 165건(2026-07-30 실측)이라 전부 실으면 respond
# 프롬프트가 넘친다. 5건은 "한 번에 훑고 더 볼지 정하는" 분량이다.
_MAX_POSSIBLE = 5


@node_logger("scope")
def scope_node(state: dict) -> dict:
    """공고 범위(bid_ids)를 정해 resolved_filters에 싣는다.

    state에서 읽는 것
        route            상세 | 자격 (기타는 이 노드를 타지 않는다)
        entry_context    화면 문맥 — 특정 공고 페이지에서 진입했나
        company_id       자격 목록을 뽑을 회원 식별자

    반환은 공고 범위(resolved_filters)와, 자격 갈래에서 답변의 근거가 될
    공고 요약(bid_briefs·bid_names·eligible_total)이다. bid_ids가 비어 있으면
    뒤에서 검색으로 찾거나(상세), 판정을 생략한다(자격 — graph.py가 가른다).
    """
    req = AgentRequest(query=state["query"], company_id=state["company_id"],
                       entry_context=state["entry_context"],
                       session_context=state["session_context"])
    # entry_bid → bid_ids 변환은 merge가 소유한다(승계 규칙이 들어올 자리).
    filters = merge.resolve_filters(req)
    bid_ids = filters.get("bid_ids") or []
    briefs: list[BidBrief] = []
    total = 0

    if not bid_ids and state["route"] == "자격":
        rows, total = possible_bids(state["company_id"], limit=_MAX_POSSIBLE)
        bid_ids = [r["bid_id"] for r in rows]
        # 공고 요약을 여기서 채워야 답변이 공고를 이름으로 부르고 마감일·금액을
        # 말할 수 있다. 자격 갈래는 retrieval도 bid_search도 타지 않아, 이 맵을
        # 채울 기회가 있는 노드가 여기뿐이다.
        briefs = [BidBrief.of(bid_id=r["bid_id"], name=r["bid_ntce_nm"],
                              institution=r["dminstt_nm"],
                              fallback_institution=r["ntce_instt_nm"],
                              close_at=r["bid_clse_dt"],
                              price=r["presmpt_prce"],
                              method=r["cntrct_cncls_mthd_nm"])
                  for r in rows]
        if not bid_ids:
            logger.warning("node=scope 자격 '가능' 공고 0건 — company_id=%s",
                           state["company_id"])

    # 항상 리스트로 채운다. 뒤 분기(graph._found_bids)는 "비었나"만 보므로,
    # 키 없음 / None / 빈 리스트가 섞이면 같은 뜻을 세 가지로 표현하는 셈이 된다.
    filters["bid_ids"] = bid_ids
    logger.info("node=scope route=%s bid_ids=%d건 (가능 전체 %d건)",
                state["route"], len(bid_ids), total)
    names = {**(state.get("bid_names") or {}),
             **{b.bid_id: b.name for b in briefs if b.name}}
    return {"resolved_filters": filters, "bid_names": names,
            "bid_briefs": briefs, "eligible_total": total}
