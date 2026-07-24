import agents.nodes.router as router_mod
from agents.nodes.router import router_node
from agents.schemas import (EntryContext, Filters, PendingClarify, Region,
                            SessionContext)


def _state(query, entry_bid=None, ctx=None):
    return {"query": query, "company_id": "c1",
            "entry_context": EntryContext(bid_id=entry_bid),
            "session_context": ctx, "intent": None, "resolved_filters": None,
            "eligibility": [], "chunks": [], "scores": [],
            "answer": None, "citations": []}


def _mock_llm(monkeypatch, payload: dict):
    captured = {}
    def fake_invoke(tier, messages, system=None, max_tokens=1024,
                    output_schema=None):
        captured["system"] = system
        captured["messages"] = messages
        return payload
    monkeypatch.setattr(router_mod.llm, "invoke", fake_invoke)
    return captured


_FULL = dict(type="full", action="answer", scope="new", entry_bid_scope="keep",
             new_filters={"region": "대전"}, normalized_query="대전 공고",
             clarify_message=None)


def test_router_returns_intent_and_resolved_filters(monkeypatch):
    _mock_llm(monkeypatch, _FULL)
    out = router_node(_state("대전 공고 알려줘"))
    assert out["intent"].type == "full"
    assert out["resolved_filters"] == {"region": "대전"}


def test_prompt_includes_last_summary(monkeypatch):
    ctx = SessionContext(last_bid_ids=["R001"], last_summary="대전 5건 안내",
                         last_filters=Filters(region=Region.DAEJEON))
    cap = _mock_llm(monkeypatch, _FULL)
    router_node(_state("그중 마감 임박한 것", ctx=ctx))
    prompt = cap["messages"][0]["content"]
    assert "대전 5건 안내" in prompt
    assert "R001" not in prompt              # ID 원문은 프롬프트에 넣지 않는다


def test_prompt_includes_original_query_when_pending(monkeypatch):
    ctx = SessionContext(
        last_bid_ids=[], last_summary="지역을 되물음", last_filters=Filters(),
        pending=PendingClarify(original_query="전기공사 공고 찾아줘",
                               partial_filters=Filters(category="전기공사")))
    cap = _mock_llm(monkeypatch, _FULL)
    router_node(_state("대전이요", ctx=ctx))
    assert "전기공사 공고 찾아줘" in cap["messages"][0]["content"]


def test_clarify_without_message_gets_default(monkeypatch, caplog):
    import logging
    _mock_llm(monkeypatch, dict(_FULL, action="clarify", clarify_message=None))
    with caplog.at_level(logging.WARNING):
        out = router_node(_state("공고"))
    assert out["intent"].clarify_message      # 기본 문구 폴백
    assert any("fallback" in r.message for r in caplog.records)
