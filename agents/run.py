"""진입점 — 프레임워크 비의존 순수 함수. 백엔드 호출 방식 확정 전까지 유지."""
from functools import lru_cache

from agents.graph import build_graph
from agents.nodes.respond import build_summary, respond_node
from agents.nodes.retrieval import retrieval_node
from agents.nodes.router import router_node
from agents.schemas import (AgentRequest, AgentResponse, Filters,
                            PendingClarify, SessionContext)

_MAX_BID_IDS = 20


@lru_cache(maxsize=1)
def _graph():
    # retrieval은 C 실구현. eligibility·scoring은 B 실구현이 오면 같은 방식으로 주입.
    return build_graph(router_node, respond_node,
                       retrieval_node=retrieval_node)


def _initial_state(req: AgentRequest) -> dict:
    return {"query": req.query, "company_id": req.company_id,
            "entry_context": req.entry_context,
            "session_context": req.session_context,
            "intent": None, "resolved_filters": None,
            "eligibility": [], "chunks": [], "scores": [],
            "answer": None, "citations": []}


def _original_query(req: AgentRequest) -> str:
    ctx = req.session_context
    if ctx and ctx.pending:                 # 연속 clarify — 원 질의 유지
        return ctx.pending.original_query
    return req.query


def run_agent(req: AgentRequest) -> AgentResponse:
    result = _graph().invoke(_initial_state(req))
    intent = result["intent"]
    resolved = Filters(**(result["resolved_filters"] or {}))
    # 스코프(bid_ids)는 필터가 아니다 — 컨텍스트로 저장 시 항상 뗀다.
    # 안 떼면 다음 턴 병합 베이스(prev)에 섞여 스코프 해제가 깨진다.
    storable = resolved.model_copy(update={"bid_ids": None})

    if intent.action == "clarify":
        prev = req.session_context
        ctx = SessionContext(
            last_bid_ids=prev.last_bid_ids if prev else [],
            last_summary="직전 턴: 조건을 되물음",
            last_filters=prev.last_filters if prev else Filters(),
            pending=PendingClarify(original_query=_original_query(req),
                                   partial_filters=storable),
        )
        return AgentResponse(action="clarify",
                             clarify_message=intent.clarify_message,
                             session_context=ctx)

    if intent.action == "redirect":
        prev = req.session_context
        ctx = SessionContext(
            last_bid_ids=prev.last_bid_ids if prev else [],
            last_summary=prev.last_summary if prev else "직전 턴: 추천 화면 이동",
            last_filters=prev.last_filters if prev else Filters(),
            pending=None,                    # pending은 항상 리셋
        )
        return AgentResponse(action="redirect", redirect_filters=resolved,
                             session_context=ctx)

    # answer
    bids = list(dict.fromkeys(
        [r.bid_id for r in result["eligibility"]] +
        [c.bid_id for c in result["chunks"]]))[:_MAX_BID_IDS]

    if resolved.bid_ids and not bids:        # 승계했는데 전부 사라짐(stale)
        answer = "이전에 보신 공고 중 조건에 맞는 것이 없습니다."
    else:
        answer = result["answer"]

    ctx = SessionContext(last_bid_ids=bids,
                         last_summary=build_summary(result),
                         last_filters=storable, pending=None)
    return AgentResponse(action="answer", answer=answer,
                         citations=result["citations"], session_context=ctx)
