from agents.graph import build_graph
from agents.schemas import Filters, QueryIntent


def _fake_router(intent_kw):
    def node(state):
        base = dict(type="full", action="answer", scope="new",
                    entry_bid_scope="keep", new_filters=Filters(),
                    normalized_query="q", clarify_message=None)
        base.update(intent_kw)
        return {"intent": QueryIntent(**base), "resolved_filters": {}}
    return node


def _fake_respond(state):
    return {"answer": "답변", "citations": []}


def _initial():
    from agents.schemas import EntryContext
    return {"query": "q", "company_id": "c1",
            "entry_context": EntryContext(), "session_context": None,
            "intent": None, "resolved_filters": None,
            "eligibility": [], "chunks": [], "scores": [],
            "answer": None, "citations": []}


def _run(intent_kw):
    graph = build_graph(_fake_router(intent_kw), _fake_respond)
    return graph.invoke(_initial())


def test_full_path_runs_all_nodes():
    out = _run({"type": "full"})
    assert out["eligibility"] and out["chunks"] and out["scores"]
    assert out["answer"] == "답변"


def test_eligibility_only_skips_retrieval_and_scoring():
    out = _run({"type": "eligibility_only"})
    assert out["eligibility"]
    assert not out["chunks"] and not out["scores"]
    assert out["answer"] == "답변"


def test_content_only_skips_eligibility():
    out = _run({"type": "content_only"})
    assert out["chunks"]
    assert not out["eligibility"] and not out["scores"]


def test_redirect_bypasses_everything():
    out = _run({"action": "redirect"})
    assert out["answer"] is None
    assert not out["eligibility"] and not out["chunks"]


def test_clarify_bypasses_everything():
    out = _run({"action": "clarify", "clarify_message": "어느 지역인가요?"})
    assert out["answer"] is None
    assert not out["eligibility"] and not out["chunks"]
