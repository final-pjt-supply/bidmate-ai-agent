import agents.nodes.router as router_mod
from agents.nodes.router import router_node
from agents.schemas import (EntryContext, Filters, PendingClarify, Region,
                            SessionContext)


def _state(query, entry_bid=None, ctx=None):
    return {"query": query, "company_id": "c1",
            "entry_context": EntryContext(bid_id=entry_bid),
            "session_context": ctx, "route": None, "resolved_filters": None,
            "bid_briefs": [], "eligibility": [], "chunks": [], "scores": [],
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


def test_router_returns_route_only(monkeypatch):
    """라우터는 갈래만 정한다 — 공고 범위도, 검색어도 손대지 않는다.

    "경기", "3억"이 질의에 있어도 필터로 새지 않아야 한다. 조건 추출은 라우터
    일이 아니고, 새면 검색 범위가 조용히 좁혀진다.
    """
    _mock_llm(monkeypatch, {"route": "검색"})
    out = router_node(_state("경기 지역에서 예산 3억 이하 공고 있어?"))
    assert out == {"route": "검색"}


def test_entry_bid_does_not_change_router_output(monkeypatch):
    """공고 범위는 scope 노드가 정한다 — 라우터는 entry_bid를 소비하지 않는다."""
    _mock_llm(monkeypatch, {"route": "상세"})
    out = router_node(_state("공사 현장이 어디야?", entry_bid="R999"))
    assert out == {"route": "상세"}


def test_each_route_passes_through(monkeypatch):
    for route in ("검색", "상세", "자격", "기타"):
        _mock_llm(monkeypatch, {"route": route})
        assert router_node(_state("q"))["route"] == route


def test_prompt_includes_entry_bid_and_last_summary(monkeypatch):
    ctx = SessionContext(last_bid_ids=["R001"], last_summary="대전 5건 안내",
                         last_filters=Filters(region=Region.DAEJEON))
    cap = _mock_llm(monkeypatch, {"route": "상세"})
    router_node(_state("그중 마감 임박한 것", entry_bid="R777", ctx=ctx))
    prompt = cap["messages"][0]["content"]
    assert "R777" in prompt                  # 진입 공고는 분류 단서다
    assert "대전 5건 안내" in prompt
    assert "R001" not in prompt              # 목록 ID 원문은 넣지 않는다


def test_prompt_includes_original_query_when_pending(monkeypatch):
    ctx = SessionContext(
        last_bid_ids=[], last_summary="지역을 되물음", last_filters=Filters(),
        pending=PendingClarify(original_query="전기공사 공고 찾아줘",
                               partial_filters=Filters(category="전기공사")))
    cap = _mock_llm(monkeypatch, {"route": "상세"})
    router_node(_state("대전이요", ctx=ctx))
    assert "전기공사 공고 찾아줘" in cap["messages"][0]["content"]
