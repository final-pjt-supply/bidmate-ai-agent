from agents.graph import build_graph
from agents.schemas import EntryContext
from agents.state import BidBrief


def _fake_router(route):
    def node(state):
        return {"route": route}
    return node


def _fake_scope(bid_ids=None):
    """scope는 공고 범위만 정한다. bid_ids=None이면 못 구한 상황."""
    def node(state):
        return {"resolved_filters": {"bid_ids": bid_ids} if bid_ids else {}}
    return node


def _fake_bid_search(bid_ids=("R_FOUND",)):
    def node(state):
        return {"resolved_filters": {"bid_ids": list(bid_ids)},
                "bid_briefs": [BidBrief(bid_id=b, name=f"{b} 공고")
                               for b in bid_ids]}
    return node


def _fake_respond(state):
    return {"answer": "답변", "citations": []}


def _initial():
    return {"query": "q", "company_id": "c1",
            "entry_context": EntryContext(), "session_context": None,
            "route": None, "resolved_filters": None, "bid_briefs": [],
            "eligibility": [], "chunks": [], "bid_names": {}, "scores": [],
            "answer": None, "citations": []}


def _run(route, scope_bid_ids=None):
    graph = build_graph(_fake_router(route), _fake_respond,
                        scope_node=_fake_scope(scope_bid_ids),
                        bid_search_node=_fake_bid_search())
    return graph.invoke(_initial())


def test_search_answers_from_bid_search_without_retrieval():
    """검색 — 찾은 목록 자체가 답이다. 본문 발췌까지 가지 않는다."""
    out = _run("검색")
    assert out["bid_briefs"]
    assert not out["chunks"]
    assert not out["eligibility"]
    assert out["answer"] == "답변"


def test_detail_with_scope_skips_bid_search():
    out = _run("상세", scope_bid_ids=["R_ENTRY"])
    assert out["resolved_filters"]["bid_ids"] == ["R_ENTRY"]
    assert not out["bid_briefs"]              # bid_search를 안 탔다
    assert out["chunks"]


def test_detail_without_scope_falls_through_bid_search():
    out = _run("상세")
    assert out["bid_briefs"]                  # bid_search가 공고를 찾았다
    assert out["resolved_filters"]["bid_ids"] == ["R_FOUND"]
    assert out["chunks"]                      # 그 범위에서 발췌까지 했다


def test_eligibility_runs_with_scope():
    out = _run("자격", scope_bid_ids=["R_POSSIBLE"])
    assert out["eligibility"]
    assert not out["chunks"]


def test_eligibility_without_candidates_bypasses_node():
    """'가능' 공고가 0건이면 판정 노드를 태우지 않는다.

    빈 bid_ids를 넘기면 eligibility가 라이브 전건 판정으로 떨어진다.
    """
    out = _run("자격")
    assert not out["eligibility"]
    assert out["answer"] is None              # run.py가 결정적 문구로 답한다


def test_other_bypasses_everything():
    out = _run("기타")
    assert out["answer"] is None
    assert not out["eligibility"] and not out["chunks"]
    assert not out["bid_briefs"]


def test_scoring_is_never_wired():
    """[3a] 스텁은 가짜 점수를 만든다 — 어느 경로도 이 노드를 타지 않는다."""
    for route in ("검색", "상세", "자격", "기타"):
        assert not _run(route, scope_bid_ids=["R1"])["scores"]
