"""노드 배선 — route 한 단어로 갈래를 정하고, 공고 범위를 확정한 뒤 일한다.

    rewrite → router ─┬─ 검색 ─────→ bid_search ─────────────→ respond
            ├─ 상세 → scope ─구했음──────────→ retrieval ───→ respond
            │            └─못구함─→ bid_search → retrieval ─→ respond
            ├─ 자격 → scope ────────────────→ eligibility ─→ respond
            └─ 기타 ────────────────────────────────────────────→ END

`검색`은 어떤 공고가 있는지 묻는 질의라 공고를 특정할 필요가 없다 — scope를
건너뛰고 바로 찾아서 목록으로 답한다. `상세`에서 scope와 bid_search는 나란한
갈래가 아니라 순차 조건부다(scope가 먼저 시도, 못 구했을 때만 bid_search).
"""
from langgraph.graph import END, StateGraph

from agents.nodes import bid_search as bid_search_mod
from agents.nodes import rewrite as rewrite_mod
from agents.nodes import scope as scope_mod
from agents.nodes import stubs
from agents.state import AgentState

# scope를 거친 뒤 공고를 구했을 때 갈 작업 노드. scope를 거치는 route만 있다.
_ROUTE_TO_WORKER: dict[str, str] = {"상세": "retrieval", "자격": "eligibility"}


def _found_bids(state: AgentState) -> bool:
    """공고를 구했는가.

    분기가 보는 값은 이 불리언 하나다. scope와 bid_search는
    resolved_filters["bid_ids"]를 **항상 리스트로** 채우므로(키 없음·None·빈
    리스트가 같은 뜻을 세 가지로 표현하는 것을 막는다) 여기서는 비었는지만 본다.
    """
    return bool((state.get("resolved_filters") or {}).get("bid_ids"))


def _route_after_router(state: AgentState) -> str:
    route = state["route"]
    if route == "기타":
        # 답이 우리 데이터에 없다. run.py가 안내 문구로 답한다.
        return "exit"
    if route == "검색":
        # 어떤 공고가 있는지 묻는 질의 — 특정할 공고가 없으니 바로 찾는다.
        return "bid_search"
    return "scope"               # 상세 · 자격


def _route_after_scope(state: AgentState) -> str:
    route = state["route"]
    if not _found_bids(state):
        if route == "자격":
            # 자격이 '가능'한 공고가 하나도 없다. 빈 bid_ids를 eligibility에
            # 넘기면 falsy라 라이브 전건 판정으로 떨어진다(eligibility.py) —
            # 노드를 태우지 않고 빠지고, run.py가 결정적 문구로 답한다.
            return "exit"
        return "bid_search"      # 상세 — 검색으로 공고를 찾는다

    worker = _ROUTE_TO_WORKER.get(route)
    if worker is None:
        # 도달할 수 없어야 하는 상태(검색·기타는 scope를 거치지 않는다). 조용히
        # 한쪽으로 흘리면 잘못된 노드가 돌므로 여기서 끊는다.
        raise ValueError(f"scope를 거칠 수 없는 route: {route!r}")
    return worker


def _route_after_bid_search(state: AgentState) -> str:
    if not _found_bids(state):
        # 어느 공고인지 끝내 모른다. bid_search가 이미 마감 전 전체를 훑었으므로
        # 범위 없이 retrieval을 또 돌려도 같은 인덱스에서 같은 결과가 나오고,
        # 빈 신호로 respond를 태우면 LLM이 없는 공고를 지어낸다.
        # run.py가 결정적 문구로 답한다.
        return "exit"
    # `검색`은 찾은 목록 자체가 답이다 — 본문 발췌까지 갈 필요가 없다.
    return "respond" if state["route"] == "검색" else "retrieval"


def build_graph(router_node, respond_node,
                eligibility_node=stubs.eligibility_node,
                retrieval_node=stubs.retrieval_node,
                scoring_node=stubs.scoring_node,
                scope_node=scope_mod.scope_node,
                bid_search_node=bid_search_mod.bid_search_node,
                rewrite_node=rewrite_mod.rewrite_node):
    """그래프를 조립한다.

    scoring_node는 **배선하지 않는다.** [3a] 자격 매칭도 점수화는 B 실구현이
    없고, 스텁(stubs.scoring_node)은 "실적 여유율 2.5배 / 72점" 같은 가짜
    점수를 만들어낸다. 그것이 respond의 신호로 들어가면 없는 근거가 답변에
    실린다. 어느 경로도 이 노드를 타지 않으므로 state["scores"]는 항상 빈
    리스트이고, respond는 "(매칭도 없음)"으로 렌더한다.

    파라미터는 호출부 호환을 위해 남긴다(for_test/eval_agent.py 등). 배점
    라우트가 생기면 그때 배선한다.
    """
    g = StateGraph(AgentState)
    g.add_node("rewrite", rewrite_node)
    g.add_node("router", router_node)
    g.add_node("scope", scope_node)
    g.add_node("bid_search", bid_search_node)
    g.add_node("eligibility", eligibility_node)
    g.add_node("retrieval", retrieval_node)
    g.add_node("respond", respond_node)

    # rewrite가 진입점이다. 라우터보다 앞에 둬서 자기완결 질의를 넘긴다 —
    # 라우터 라벨을 늘리지도, 루프를 만들지도 않는다(rewrite.py 참조).
    # 조건에 안 맞으면 노드가 LLM을 부르지 않고 그대로 통과시킨다.
    g.set_entry_point("rewrite")
    g.add_edge("rewrite", "router")
    g.add_conditional_edges("router", _route_after_router, {
        "exit": END,
        "scope": "scope",
        "bid_search": "bid_search",
    })
    g.add_conditional_edges("scope", _route_after_scope, {
        "exit": END,
        "bid_search": "bid_search",
        "retrieval": "retrieval",
        "eligibility": "eligibility",
    })
    g.add_conditional_edges("bid_search", _route_after_bid_search, {
        "exit": END,
        "retrieval": "retrieval",
        "respond": "respond",
    })
    g.add_edge("eligibility", "respond")
    g.add_edge("retrieval", "respond")
    g.add_edge("respond", END)
    return g.compile()
