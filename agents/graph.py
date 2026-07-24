"""노드 배선 — redirect·clarify는 respond를 거치지 않는다(스펙 §그래프 배선)."""
from langgraph.graph import END, StateGraph

from agents.nodes import stubs
from agents.state import AgentState


def _route_after_router(state: AgentState) -> str:
    intent = state["intent"]
    if intent.action in ("redirect", "clarify"):
        return "exit"
    return intent.type            # eligibility_only | content_only | full


def _route_after_eligibility(state: AgentState) -> str:
    return "retrieval" if state["intent"].type == "full" else "respond"


def _route_after_retrieval(state: AgentState) -> str:
    return "scoring" if state["intent"].type == "full" else "respond"


def build_graph(router_node, respond_node,
                eligibility_node=stubs.eligibility_node,
                retrieval_node=stubs.retrieval_node,
                scoring_node=stubs.scoring_node):
    g = StateGraph(AgentState)
    g.add_node("router", router_node)
    g.add_node("eligibility", eligibility_node)
    g.add_node("retrieval", retrieval_node)
    g.add_node("scoring", scoring_node)
    g.add_node("respond", respond_node)

    g.set_entry_point("router")
    g.add_conditional_edges("router", _route_after_router, {
        "exit": END,                      # redirect / clarify — 노드 안 탐
        "eligibility_only": "eligibility",
        "content_only": "retrieval",
        "full": "eligibility",
    })
    g.add_conditional_edges("eligibility", _route_after_eligibility,
                            {"retrieval": "retrieval", "respond": "respond"})
    g.add_conditional_edges("retrieval", _route_after_retrieval,
                            {"scoring": "scoring", "respond": "respond"})
    g.add_edge("scoring", "respond")
    g.add_edge("respond", END)
    return g.compile()
