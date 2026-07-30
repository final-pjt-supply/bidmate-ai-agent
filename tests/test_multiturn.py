import pytest

import agents.nodes.bid_search as bid_search_mod
import agents.nodes.eligibility as eligibility_mod
import agents.nodes.retrieval as retrieval_mod
import agents.nodes.router as router_mod
import agents.nodes.scope as scope_mod
import agents.run as run_mod
from agents.llm import ModelTier
from agents.merge import resolve_filters
from agents.nodes import stubs
from agents.run import run_agent
from agents.schemas import (
    AgentRequest, EntryContext, Filters, PendingClarify, Region, SessionContext,
)


@pytest.fixture(autouse=True)
def _stub_retrieval(monkeypatch):
    """run.py가 C 실구현(retrieval_node)을 배선하므로, E2E가 실제
    OpenSearch·PG·Cloudflare에 붙지 않도록 검색 도구만 스텁 데이터로 대체한다.
    (LLM을 monkeypatch하는 것과 같은 원칙 — 외부 의존만 끊고 배선은 실물)"""
    monkeypatch.setattr(
        retrieval_mod, "retrieve_chunks",
        lambda query, filters=None, **kw: stubs.retrieval_node({})["chunks"])


@pytest.fixture(autouse=True)
def _stub_eligibility(monkeypatch):
    """run.py가 B 실구현(eligibility_node)도 배선하므로, 위 검색과 같은
    이유로 판정 도구만 대체한다. 도구는 DB 함수(compute_match_results)를
    호출하니 스텁이 없으면 E2E가 실 PG에 붙는다."""
    monkeypatch.setattr(
        eligibility_mod, "evaluate_eligibility",
        lambda company_id, bid_ids=None, **kw:
            stubs.eligibility_node({})["eligibility"])


@pytest.fixture(autouse=True)
def _stub_bid_names(monkeypatch):
    """retrieval_node의 공고명 조회(fetch_bid_info)가 실 PostgreSQL에 붙는 것을 막는다.

    조회 실패는 빈 맵으로 폴백되므로 스텁이 없어도 테스트는 통과하지만, 턴마다
    접속 타임아웃을 기다려 스위트가 몇 분씩 걸린다. 위 두 픽스처와 같은 원칙 —
    외부 의존만 끊고 배선은 실물.
    """
    import agents.tools.bid_info as bid_info_mod
    monkeypatch.setattr(bid_info_mod, "fetch_bid_info", lambda bid_ids: {})


@pytest.fixture(autouse=True)
def _stub_scope_db(monkeypatch):
    """scope의 match_results 조회(자격 갈래)가 실 PostgreSQL에 붙는 것을 막는다."""
    monkeypatch.setattr(scope_mod, "possible_bids",
                        lambda company_id, *, limit: (
                            [{"bid_id": "R26BK_POSSIBLE01",
                              "bid_ntce_nm": "가능 공고",
                              "dminstt_nm": "광주교육청", "ntce_instt_nm": None,
                              "bid_clse_dt": None, "presmpt_prce": None,
                              "cntrct_cncls_mthd_nm": None}], 165))


def _rec(bid_id="R26BK_FOUND01", name="스쿨넷서비스 제공 용역"):
    """recommend_bids가 돌려주는 형태 1건."""
    from datetime import datetime

    from agents.schemas import Chunk
    from agents.tools.search_types import (BidHit, BidInfo, RecommendedBid,
                                           SearchHit)
    return RecommendedBid(
        hit=BidHit(bid_id=bid_id, score=1.0, top_hit=SearchHit(
            chunk=Chunk(bid_id=bid_id, document_id="d", file_id="f",
                        chunk_idx=0, text="t", type="text"), score=1.0)),
        info=BidInfo(bid_id=bid_id, bid_ntce_no=bid_id, bid_ntce_ord="000",
                     bid_ntce_nm=name, dminstt_nm="광주광역시교육청",
                     bid_clse_dt=datetime(2026, 7, 30, 10, 0),
                     presmpt_prce=900_000_000))


@pytest.fixture(autouse=True)
def _stub_bid_search(monkeypatch):
    """bid_search의 공고 검색이 실 OpenSearch·PG에 붙는 것을 막는다.

    기본은 '1건 찾음'. 못 찾으면 그래프가 노드를 더 태우지 않고 빠지므로
    (graph._route_after_bid_search) 그 경로를 기본값으로 두면 대부분의 E2E가
    답변 없이 끝난다. 못 찾는 경우가 필요한 테스트는 이 스텁을 다시 덮는다.
    """
    monkeypatch.setattr(bid_search_mod, "recommend_bids",
                        lambda query, **kw: [_rec()])


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


# ---- merge.resolve_filters 단위 ----
# scope/entry_bid_scope/new_filters는 원래 Router가 LLM으로 판단했던 값이다.
# 지금 Router는 기본값(scope=new, entry_bid_scope=keep, 필터 없음)으로만
# 호출하지만, 승계 판단이 돌아올 자리이므로 네 분기 전부를 여기서 지킨다.

def test_no_context_returns_new_only():
    out = resolve_filters(_req(), new_filters=Filters(region=Region.DAEJEON))
    assert out == {"region": "대전"}


def test_add_condition_keeps_bid_ids():
    # "그중 마감 임박한 것만" — 신규 키 → bid_ids 고정
    out = resolve_filters(_req(ctx=_CTX), scope="inherit",
                          new_filters=Filters(deadline_within_days=7))
    assert out["region"] == "대전"
    assert out["bid_ids"] == _CTX.last_bid_ids


def test_same_value_restated_keeps_bid_ids():
    # "대전 공고 중 마감 임박한 것" — 동일값 재언급은 이탈 아님
    out = resolve_filters(_req(ctx=_CTX), scope="inherit", new_filters=Filters(
        region=Region.DAEJEON, deadline_within_days=7))
    assert out["bid_ids"] == _CTX.last_bid_ids


def test_different_value_releases_bid_ids():
    # "서울은 어때?" — 같은 키 다른 값 → 재검색
    out = resolve_filters(_req(ctx=_CTX), scope="inherit",
                          new_filters=Filters(region=Region.SEOUL))
    assert out["region"] == "서울"
    assert out["category"] == "전기공사"      # 필터는 승계
    assert "bid_ids" not in out               # 스코프는 해제


def test_scope_new_drops_everything():
    out = resolve_filters(_req(ctx=_CTX), scope="new",
                          new_filters=Filters(category="토목"))
    assert out == {"category": "토목"}


def test_pending_merges_partial_filters():
    # clarify("어느 지역?") → "대전이요"
    ctx = _CTX.model_copy(update={"pending": PendingClarify(
        original_query="전기공사 공고 찾아줘",
        partial_filters=Filters(category="전기공사"))})
    out = resolve_filters(_req(ctx=ctx), scope="inherit",
                          new_filters=Filters(region=Region.DAEJEON))
    assert out == {"category": "전기공사", "region": "대전"}


def test_scope_new_discards_pending():
    # 되묻기 무시하고 화제 전환 — pending째 버려진다
    ctx = _CTX.model_copy(update={"pending": PendingClarify(
        original_query="전기공사 공고 찾아줘",
        partial_filters=Filters(category="전기공사"))})
    out = resolve_filters(_req(ctx=ctx), scope="new",
                          new_filters=Filters(category="토목"))
    assert out == {"category": "토목"}


def test_entry_bid_keep_pins_scope():
    out = resolve_filters(_req(entry_bid="R999", ctx=_CTX))
    assert out["bid_ids"] == ["R999"]


def test_entry_bid_leave_escapes():
    # "이거 말고 비슷한 다른 공고는?"
    out = resolve_filters(_req(entry_bid="R999", ctx=_CTX),
                          entry_bid_scope="leave", scope="new",
                          new_filters=Filters(category="전기공사"))
    assert out == {"category": "전기공사"}


def test_null_fields_do_not_clobber_previous():
    # structured outputs가 미언급 필터를 None으로 채워도 기존 값이 깨지지 않는다
    out = resolve_filters(_req(ctx=_CTX), scope="inherit", new_filters=Filters(
        region=None, deadline_within_days=7))
    assert out["region"] == "대전"


def test_polluted_prev_bid_ids_cannot_survive_scope_release():
    # 3턴 연쇄: T2 병합 결과(bid_ids 포함)가 last_filters로 저장된 컨텍스트에서
    # T3 "서울은 어때?" — 값 변경으로 스코프를 풀었는데 prev의 bid_ids가
    # {**prev, **new} 경유로 살아남으면 서울 검색이 옛 대전 5건에 갇힌다
    polluted = _CTX.model_copy(update={"last_filters": Filters(
        region=Region.DAEJEON, category="전기공사",
        deadline_within_days=7, bid_ids=_CTX.last_bid_ids)})
    out = resolve_filters(_req(ctx=polluted), scope="inherit",
                          new_filters=Filters(region=Region.SEOUL))
    assert out["region"] == "서울"
    assert "bid_ids" not in out


# ---- run_agent E2E (LLM은 monkeypatch) ----

def _mock_llm(monkeypatch, route="상세", respond_payload=None):
    """router·respond 둘 다 같은 agents.llm 모듈을 쓰므로 patch는 하나만 —
    두 번 걸면 마지막 것이 이겨서 분기가 깨진다. 티어로 분기해 ROUTER/SYNTHESIS
    각각의 구조화 출력 dict를 돌려준다."""
    if respond_payload is None:
        respond_payload = {"headline": "여유율 2.5배로 자격을 충족합니다.",
                           "items": [], "caveat": None}

    def fake_invoke(tier, messages, system=None, max_tokens=1024,
                    output_schema=None):
        return {"route": route} if tier == ModelTier.ROUTER else respond_payload
    monkeypatch.setattr(router_mod.llm, "invoke", fake_invoke)


def _force_filters(monkeypatch, resolved: dict):
    """라우터도 scope도 조건을 뽑지 않으므로, 필터가 필요한 E2E는 merge 결과를
    고정한다. 여기서 검증하려는 것은 run.py의 컨텍스트 조립이다 — 승계 규칙
    자체는 위 resolve_filters 단위 테스트가 지킨다."""
    monkeypatch.setattr(scope_mod.merge, "resolve_filters",
                        lambda req, **kw: resolved)


def test_answer_returns_updated_context(monkeypatch):
    _mock_llm(monkeypatch)
    _force_filters(monkeypatch, {"region": "대전"})
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


def test_out_of_scope_exits_without_touching_nodes(monkeypatch):
    """route=기타 — 노드를 타지 않고 코드가 소유한 고정 문구로 빠진다.

    문구를 LLM이 쓰지 않는 것이 요점이다(질의 경유 인젝션 차단). 되물은 것이
    아니므로 pending을 만들지 않고, 직전 맥락은 그대로 통과시킨다.
    """
    _mock_llm(monkeypatch, route="기타")
    prev = SessionContext(last_bid_ids=["R001"], last_summary="대전 5건",
                          last_filters=Filters(region=Region.DAEJEON))
    resp = run_agent(AgentRequest(query="점심 뭐 먹을까?", company_id="c1",
                                  entry_context=EntryContext(),
                                  session_context=prev))
    assert resp.action == "clarify"
    assert resp.clarify_message == run_mod.OUT_OF_SCOPE
    assert resp.answer is None                     # 노드를 안 탔으므로 답변 없음
    assert resp.session_context.pending is None
    assert resp.session_context.last_bid_ids == ["R001"]   # 맥락 유지


def test_pending_reset_after_consumption(monkeypatch):
    # 되묻기 뒤 "대전이요"(answer) → 반환 컨텍스트의 pending은 None
    _mock_llm(monkeypatch)
    _force_filters(monkeypatch, {"category": "전기공사", "region": "대전"})
    ctx = SessionContext(
        last_bid_ids=[], last_summary="지역을 되물음", last_filters=Filters(),
        pending=PendingClarify(original_query="전기공사 공고 찾아줘",
                               partial_filters=Filters(category="전기공사")))
    resp = run_agent(AgentRequest(query="대전이요", company_id="c1",
                                  entry_context=EntryContext(),
                                  session_context=ctx))
    assert resp.session_context.pending is None   # 소비 후 리셋
    assert resp.session_context.last_filters.category == "전기공사"


def test_redirect_passes_context_through_with_pending_cleared(monkeypatch):
    """run.py의 redirect 분기 — 지금 어느 route도 이 action을 내지 않는다.

    '추천'(조건만 있는 질의 → 추천 화면 이동) 라우트를 추가하면 이 분기가
    그대로 재사용되므로 커버리지를 남긴다. route→action 매핑을 갈아
    도달시킨다.
    """
    _mock_llm(monkeypatch)
    _force_filters(monkeypatch, {"region": "서울"})
    monkeypatch.setitem(run_mod._ROUTE_ACTION, "상세", "redirect")
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


def test_two_turn_context_roundtrip(monkeypatch):
    """T1이 반환한 session_context를 손대지 않고 T2에 먹인다.

    지금 라우터는 승계를 판단하지 않으므로(scope 항상 new) 그래프 경로만으로는
    스코프 고정을 관찰할 수 없다. 컨텍스트가 실제로 왕복하는지 E2E로 보고,
    승계가 돌아왔을 때 T1의 공고에 갇히는지는 resolve_filters로 직접 확인한다.
    """
    _mock_llm(monkeypatch)
    req1 = AgentRequest(query="대전 전기공사 공고 찾아줘", company_id="c1",
                        entry_context=EntryContext())
    resp1 = run_agent(req1)
    assert resp1.action == "answer"
    ctx1 = resp1.session_context
    assert ctx1.last_bid_ids                      # 스텁 청크의 공고

    req2 = AgentRequest(query="그중 마감 임박한 것만", company_id="c1",
                        entry_context=EntryContext(), session_context=ctx1)
    resp2 = run_agent(req2)
    assert resp2.action == "answer"

    # 승계 판단이 돌아오면 T2는 T1이 다룬 공고 안에서만 검색돼야 한다
    out = resolve_filters(req2, scope="inherit",
                          new_filters=Filters(deadline_within_days=7))
    assert out["bid_ids"] == ctx1.last_bid_ids


# ---- 갈래별 E2E ----

def test_search_route_answers_from_briefs(monkeypatch):
    """검색 — 공고 목록 요약만으로 답한다. 발췌가 없으니 인용도 없다."""
    monkeypatch.setattr(bid_search_mod, "recommend_bids",
                        lambda query, **kw: [_rec("R001_000")])
    _mock_llm(monkeypatch, route="검색",
              respond_payload={"headline": "스쿨넷 공고가 있습니다.",
                               "items": [], "caveat": None})

    resp = run_agent(AgentRequest(query="스쿨넷 공고 있어?", company_id="c1",
                                  entry_context=EntryContext()))
    assert resp.action == "answer"
    # respond가 만든 답변이 그대로 나와야 한다. stale 분기에 걸려 "이전에 보신
    # 공고 중 …"으로 갈리면 검색 답변이 매번 통째로 유실된다.
    assert resp.answer == "스쿨넷 공고가 있습니다."
    assert resp.citations == []                    # 청크를 안 만들었다
    # 검색 턴이 안내한 공고가 다음 턴으로 승계돼야 한다(청크·판정이 없으므로
    # bid_briefs에서 와야 한다)
    assert resp.session_context.last_bid_ids == ["R001_000"]


def test_nothing_found_answers_deterministically(monkeypatch):
    """공고를 못 찾으면 노드를 더 태우지 않고 코드가 문구를 정한다."""
    monkeypatch.setattr(bid_search_mod, "recommend_bids", lambda query, **kw: [])
    _mock_llm(monkeypatch, route="검색")
    resp = run_agent(AgentRequest(query="우주선 공고 있어?", company_id="c1",
                                  entry_context=EntryContext()))
    assert resp.action == "answer"
    assert resp.answer == run_mod._NOT_FOUND
    assert resp.citations == []
    assert resp.session_context.last_bid_ids == []


def test_eligibility_route_uses_possible_bids(monkeypatch):
    _mock_llm(monkeypatch, route="자격")
    resp = run_agent(AgentRequest(query="나랑 맞는 공고 보여줘", company_id="9001",
                                  entry_context=EntryContext()))
    assert resp.action == "answer"
    assert resp.answer
    assert resp.session_context.last_bid_ids       # 판정한 공고가 기록된다


def test_eligibility_route_with_no_candidates_answers_deterministically(monkeypatch):
    """'가능' 0건 — 노드를 안 타므로 LLM 답변이 없다. 코드가 문구를 정한다."""
    monkeypatch.setattr(scope_mod, "possible_bids",
                        lambda company_id, *, limit: ([], 0))
    _mock_llm(monkeypatch, route="자격")
    resp = run_agent(AgentRequest(query="나랑 맞는 공고 보여줘", company_id="9002",
                                  entry_context=EntryContext()))
    assert resp.action == "answer"
    assert resp.answer == run_mod._NO_ELIGIBLE
    assert resp.citations == []


def test_missing_verdict_for_entry_bid_says_so(monkeypatch):
    """특정 공고를 물었는데 판정이 없는 경우 — "자격 미달"로 읽히면 안 된다.

    마감된 공고는 match_results 계산 대상에서 빠지므로 판정이 없다.
    """
    monkeypatch.setattr(eligibility_mod, "evaluate_eligibility",
                        lambda company_id, bid_ids=None, **kw: [])
    _mock_llm(monkeypatch, route="자격")
    resp = run_agent(AgentRequest(query="우리 이 공고 자격 돼?", company_id="9001",
                                  entry_context=EntryContext(bid_id="R_CLOSED")))
    assert resp.answer == run_mod._NO_VERDICT
    assert resp.answer != run_mod._NO_ELIGIBLE
