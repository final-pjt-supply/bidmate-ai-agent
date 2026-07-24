from agents.schemas import (
    Filters, QueryIntent, SessionContext, PendingClarify,
    EntryContext, AgentRequest, Region,
)


def test_filters_unset_fields_are_none():
    f = Filters(region=Region.DAEJEON)
    d = f.model_dump()
    assert d["region"] == "대전"
    assert d["category"] is None          # 미언급 필드는 None


def test_query_intent_roundtrip():
    intent = QueryIntent(
        type="full", action="answer", scope="inherit", entry_bid_scope="keep",
        new_filters=Filters(region=Region.DAEJEON), normalized_query="대전 전기공사",
        clarify_message=None,
    )
    assert QueryIntent.model_validate(intent.model_dump()) == intent


def test_query_intent_json_schema_has_required_fields():
    schema = QueryIntent.model_json_schema()
    for field in ("type", "action", "scope", "entry_bid_scope", "new_filters"):
        assert field in schema["properties"]


def test_session_context_pending_optional():
    ctx = SessionContext(last_bid_ids=["R001"], last_summary="s",
                         last_filters=Filters(), pending=None)
    assert ctx.pending is None
    ctx2 = ctx.model_copy(update={"pending": PendingClarify(
        original_query="전기공사 공고", partial_filters=Filters())})
    assert ctx2.pending.original_query == "전기공사 공고"


def test_agent_request_minimal():
    req = AgentRequest(query="q", company_id="c1",
                       entry_context=EntryContext(), session_context=None)
    assert req.entry_context.bid_id is None
