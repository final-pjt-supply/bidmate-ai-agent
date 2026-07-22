from agents.merge import resolve_filters
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
