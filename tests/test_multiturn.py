import agents.nodes.router as router_mod
from agents.llm import ModelTier
from agents.merge import resolve_filters
from agents.run import run_agent
from agents.schemas import (
    AgentRequest, EntryContext, Filters, PendingClarify, QueryIntent,
    Region, SessionContext,
)


def _intent(**kw) -> QueryIntent:
    base = dict(type="full", action="answer", scope="inherit",
                entry_bid_scope="keep", new_filters=Filters(),
                normalized_query="q", clarify_message=None)
    base.update(kw)
    return QueryIntent(**base)


def _req(entry_bid=None, ctx=None) -> AgentRequest:
    return AgentRequest(query="q", company_id="c1",
                        entry_context=EntryContext(bid_id=entry_bid),
                        session_context=ctx)


_CTX = SessionContext(
    last_bid_ids=["R001", "R002", "R003", "R004", "R005"],
    last_summary="대전 전기공사 5건 안내",
    last_filters=Filters(region=Region.DAEJEON, category="전기공사"),
    pending=None,
)


def test_no_context_returns_new_only():
    out = resolve_filters(_req(), _intent(new_filters=Filters(region=Region.DAEJEON)))
    assert out == {"region": "대전"}


def test_add_condition_keeps_bid_ids():
    # "그중 마감 임박한 것만" — 신규 키 → bid_ids 고정
    out = resolve_filters(_req(ctx=_CTX),
                          _intent(new_filters=Filters(deadline_within_days=7)))
    assert out["region"] == "대전"
    assert out["bid_ids"] == _CTX.last_bid_ids


def test_same_value_restated_keeps_bid_ids():
    # "대전 공고 중 마감 임박한 것" — 동일값 재언급은 이탈 아님
    out = resolve_filters(_req(ctx=_CTX), _intent(
        new_filters=Filters(region=Region.DAEJEON, deadline_within_days=7)))
    assert out["bid_ids"] == _CTX.last_bid_ids


def test_different_value_releases_bid_ids():
    # "서울은 어때?" — 같은 키 다른 값 → 재검색
    out = resolve_filters(_req(ctx=_CTX),
                          _intent(new_filters=Filters(region=Region.SEOUL)))
    assert out["region"] == "서울"
    assert out["category"] == "전기공사"      # 필터는 승계
    assert "bid_ids" not in out               # 스코프는 해제


def test_scope_new_drops_everything():
    out = resolve_filters(_req(ctx=_CTX),
                          _intent(scope="new", new_filters=Filters(category="토목")))
    assert out == {"category": "토목"}


def test_pending_merges_partial_filters():
    # clarify("어느 지역?") → "대전이요"
    ctx = _CTX.model_copy(update={"pending": PendingClarify(
        original_query="전기공사 공고 찾아줘",
        partial_filters=Filters(category="전기공사"))})
    out = resolve_filters(_req(ctx=ctx),
                          _intent(new_filters=Filters(region=Region.DAEJEON)))
    assert out == {"category": "전기공사", "region": "대전"}


def test_scope_new_discards_pending():
    # 되묻기 무시하고 화제 전환 — pending째 버려진다
    ctx = _CTX.model_copy(update={"pending": PendingClarify(
        original_query="전기공사 공고 찾아줘",
        partial_filters=Filters(category="전기공사"))})
    out = resolve_filters(_req(ctx=ctx),
                          _intent(scope="new", new_filters=Filters(category="토목")))
    assert out == {"category": "토목"}


def test_entry_bid_keep_pins_scope():
    out = resolve_filters(_req(entry_bid="R999", ctx=_CTX),
                          _intent(new_filters=Filters()))
    assert out["bid_ids"] == ["R999"]


def test_entry_bid_leave_escapes():
    # "이거 말고 비슷한 다른 공고는?"
    out = resolve_filters(_req(entry_bid="R999", ctx=_CTX), _intent(
        entry_bid_scope="leave", scope="new",
        new_filters=Filters(category="전기공사")))
    assert out == {"category": "전기공사"}


def test_null_fields_do_not_clobber_previous():
    # structured outputs가 미언급 필터를 None으로 채워도 기존 값이 깨지지 않는다
    out = resolve_filters(_req(ctx=_CTX), _intent(
        new_filters=Filters(region=None, deadline_within_days=7)))
    assert out["region"] == "대전"


def test_polluted_prev_bid_ids_cannot_survive_scope_release():
    # 3턴 연쇄: T2 병합 결과(bid_ids 포함)가 last_filters로 저장된 컨텍스트에서
    # T3 "서울은 어때?" — 값 변경으로 스코프를 풀었는데 prev의 bid_ids가
    # {**prev, **new} 경유로 살아남으면 서울 검색이 옛 대전 5건에 갇힌다
    polluted = _CTX.model_copy(update={"last_filters": Filters(
        region=Region.DAEJEON, category="전기공사",
        deadline_within_days=7, bid_ids=_CTX.last_bid_ids)})
    out = resolve_filters(_req(ctx=polluted),
                          _intent(new_filters=Filters(region=Region.SEOUL)))
    assert out["region"] == "서울"
    assert "bid_ids" not in out


# ---- run_agent E2E (LLM은 monkeypatch) ----

def _mock_llm(monkeypatch, router_payload,
              respond_text="여유율 2.5배로 자격을 충족합니다."):
    """router·respond 둘 다 같은 agents.llm 모듈을 쓰므로 patch는 하나만 —
    두 번 걸면 마지막 것이 이겨서 Router가 산문을 받아 ValidationError가 난다.
    티어로 분기해 ROUTER=dict, SYNTHESIS=str을 돌려준다."""
    def fake_invoke(tier, messages, system=None, max_tokens=1024,
                    output_schema=None):
        return router_payload if tier == ModelTier.ROUTER else respond_text
    monkeypatch.setattr(router_mod.llm, "invoke", fake_invoke)


def test_answer_returns_updated_context(monkeypatch):
    _mock_llm(monkeypatch, dict(
        type="full", action="answer", scope="new", entry_bid_scope="keep",
        new_filters={"region": "대전"}, normalized_query="대전 공고",
        clarify_message=None))
    resp = run_agent(AgentRequest(query="대전 공고", company_id="c1",
                                  entry_context=EntryContext()))
    assert resp.action == "answer"
    assert resp.answer
    ctx = resp.session_context
    assert ctx.pending is None
    assert ctx.last_filters.region == Region.DAEJEON
    assert ctx.last_filters.bid_ids is None       # 스코프는 last_filters에 안 실림
    assert ctx.last_bid_ids                       # 다룬 공고 기록
    assert len(ctx.last_bid_ids) <= 20            # 상한


def test_clarify_sets_pending(monkeypatch):
    _mock_llm(monkeypatch, dict(
        type="full", action="clarify", scope="new", entry_bid_scope="keep",
        new_filters={"category": "전기공사"}, normalized_query="전기공사",
        clarify_message="어느 지역의 공고를 찾으시나요?"))
    resp = run_agent(AgentRequest(query="전기공사 공고 찾아줘",
                                  company_id="c1",
                                  entry_context=EntryContext()))
    assert resp.action == "clarify"
    assert resp.clarify_message == "어느 지역의 공고를 찾으시나요?"
    p = resp.session_context.pending
    assert p.original_query == "전기공사 공고 찾아줘"
    assert p.partial_filters.category == "전기공사"


def test_pending_reset_after_consumption(monkeypatch):
    # clarify → "대전이요"(answer) → 반환 컨텍스트의 pending은 None
    _mock_llm(monkeypatch, dict(
        type="full", action="answer", scope="inherit", entry_bid_scope="keep",
        new_filters={"region": "대전"}, normalized_query="대전 전기공사",
        clarify_message=None))
    ctx = SessionContext(
        last_bid_ids=[], last_summary="지역을 되물음", last_filters=Filters(),
        pending=PendingClarify(original_query="전기공사 공고 찾아줘",
                               partial_filters=Filters(category="전기공사")))
    resp = run_agent(AgentRequest(query="대전이요", company_id="c1",
                                  entry_context=EntryContext(),
                                  session_context=ctx))
    assert resp.session_context.pending is None   # 소비 후 리셋
    assert resp.session_context.last_filters.category == "전기공사"  # 병합됨


def test_redirect_passes_context_through_with_pending_cleared(monkeypatch):
    _mock_llm(monkeypatch, dict(
        type="full", action="redirect", scope="new", entry_bid_scope="keep",
        new_filters={"region": "서울"}, normalized_query="서울 공고 추천",
        clarify_message=None))
    ctx = SessionContext(
        last_bid_ids=["R001"], last_summary="대전 5건",
        last_filters=Filters(region=Region.DAEJEON),
        pending=PendingClarify(original_query="q", partial_filters=Filters()))
    resp = run_agent(AgentRequest(query="추천해줘", company_id="c1",
                                  entry_context=EntryContext(),
                                  session_context=ctx))
    assert resp.action == "redirect"
    assert resp.redirect_filters.region == Region.SEOUL
    assert resp.session_context.last_bid_ids == ["R001"]   # 통과
    assert resp.session_context.pending is None            # 단 pending은 리셋


def test_three_turn_context_chain(monkeypatch):
    """T1(clarify) → T2(대전, inherit) → T3(마감 임박, inherit) — 각 턴은
    이전 턴이 '반환한' session_context를 그대로 다음 run_agent에 먹인다
    (손으로 만든 컨텍스트 없음)."""
    # T1 — "전기공사 공고 찾아줘" → 지역을 되물음
    _mock_llm(monkeypatch, dict(
        type="full", action="clarify", scope="new", entry_bid_scope="keep",
        new_filters={"category": "전기공사"}, normalized_query="전기공사 공고",
        clarify_message="어느 지역의 공고를 찾으시나요?"))
    req1 = AgentRequest(query="전기공사 공고 찾아줘", company_id="c1",
                        entry_context=EntryContext())
    resp1 = run_agent(req1)
    assert resp1.action == "clarify"
    ctx1 = resp1.session_context
    assert ctx1.pending is not None
    assert ctx1.pending.partial_filters.category == "전기공사"

    # T2 — "대전이요" → clarify 이어받아 답변, bid_ids 확보
    _mock_llm(monkeypatch, dict(
        type="full", action="answer", scope="inherit", entry_bid_scope="keep",
        new_filters={"region": "대전"}, normalized_query="대전 전기공사",
        clarify_message=None))
    req2 = AgentRequest(query="대전이요", company_id="c1",
                        entry_context=EntryContext(), session_context=ctx1)
    resp2 = run_agent(req2)
    assert resp2.action == "answer"
    ctx2 = resp2.session_context
    assert ctx2.pending is None
    assert ctx2.last_filters.category == "전기공사"
    assert ctx2.last_filters.region == Region.DAEJEON
    assert ctx2.last_bid_ids

    # T3 — "그중 마감 임박한 것만" → inherit, 신규 키만 추가 → 스코프(bid_ids) 고정
    _mock_llm(monkeypatch, dict(
        type="full", action="answer", scope="inherit", entry_bid_scope="keep",
        new_filters={"deadline_within_days": 7}, normalized_query="마감 임박 전기공사",
        clarify_message=None))
    req3 = AgentRequest(query="그중 마감 임박한 것만", company_id="c1",
                        entry_context=EntryContext(), session_context=ctx2)
    resp3 = run_agent(req3)
    assert resp3.action == "answer"

    # resolve_filters를 직접 호출해 T3가 실제로 T2의 bid_ids 스코프에 갇혀
    # 검색됐는지(신규 키 추가는 이탈이 아님) 확인.
    intent3 = _intent(new_filters=Filters(deadline_within_days=7))
    assert resolve_filters(req3, intent3)["bid_ids"] == ctx2.last_bid_ids
