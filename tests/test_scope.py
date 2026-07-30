import agents.nodes.scope as scope_mod
from agents.nodes.scope import scope_node
from agents.schemas import EntryContext


def _state(route, entry_bid=None, company_id="9001"):
    return {"query": "q", "company_id": company_id, "route": route,
            "entry_context": EntryContext(bid_id=entry_bid),
            "session_context": None, "resolved_filters": None}


def _stub_possible(monkeypatch, rows):
    """rows: [(bid_id, 공고명), ...]"""
    calls = []
    def fake(company_id, *, limit):
        calls.append((company_id, limit))
        return list(rows)
    monkeypatch.setattr(scope_mod, "possible_bids", fake)
    return calls


def test_entry_bid_becomes_scope(monkeypatch):
    """화면 문맥이 가장 강한 신호 — DB를 보지 않는다."""
    calls = _stub_possible(monkeypatch, [("R_SHOULD_NOT_BE_USED", "x")])
    out = scope_node(_state("상세", entry_bid="R999"))
    assert out["resolved_filters"] == {"bid_ids": ["R999"]}
    assert not calls                            # match_results 조회 없음


def test_entry_bid_wins_for_eligibility_too(monkeypatch):
    calls = _stub_possible(monkeypatch, [("R_OTHER", "x")])
    out = scope_node(_state("자격", entry_bid="R999"))
    assert out["resolved_filters"] == {"bid_ids": ["R999"]}
    assert not calls


def test_eligibility_without_entry_bid_uses_match_results(monkeypatch):
    calls = _stub_possible(monkeypatch, [("R1", "가"), ("R2", "나"), ("R3", "")])
    out = scope_node(_state("자격"))
    assert out["resolved_filters"] == {"bid_ids": ["R1", "R2", "R3"]}
    assert calls == [("9001", scope_mod._MAX_POSSIBLE)]


def test_eligibility_fills_bid_names(monkeypatch):
    """자격 갈래는 retrieval을 안 타므로 공고명을 여기서 채워야 한다.

    안 채우면 답변이 공고를 `R26BK…` 공고번호로 부른다.
    """
    _stub_possible(monkeypatch, [("R1", "스쿨넷 용역"), ("R2", "")])
    out = scope_node(_state("자격"))
    assert out["bid_names"] == {"R1": "스쿨넷 용역"}   # 빈 이름은 넣지 않는다


def test_existing_bid_names_are_preserved(monkeypatch):
    _stub_possible(monkeypatch, [("R1", "가")])
    state = _state("자격")
    state["bid_names"] = {"R9": "앞에서 채운 이름"}
    out = scope_node(state)
    assert out["bid_names"] == {"R9": "앞에서 채운 이름", "R1": "가"}


def test_eligibility_with_no_possible_bids_returns_empty(monkeypatch, caplog):
    """'가능' 0건은 빈 리스트로 알린다 — graph가 판정 노드를 건너뛰게 한다."""
    import logging
    _stub_possible(monkeypatch, [])
    with caplog.at_level(logging.WARNING):
        out = scope_node(_state("자격"))
    assert out["resolved_filters"] == {"bid_ids": []}
    assert any("0건" in r.message for r in caplog.records)


def test_detail_without_entry_bid_stays_empty(monkeypatch):
    """상세는 DB를 보지 않는다 — bid_search가 검색으로 찾는다.

    못 구했을 때도 키를 비워두지 않고 빈 리스트로 채운다. 분기가 "비었나"만
    보게 하려면 키 없음과 빈 리스트가 갈리지 않아야 한다(graph._found_bids).
    """
    calls = _stub_possible(monkeypatch, [("R1", "가")])
    out = scope_node(_state("상세"))
    assert out["resolved_filters"] == {"bid_ids": []}
    assert not calls


def test_blank_entry_bid_is_not_a_scope(monkeypatch):
    """백엔드가 공백을 보내도 "구했다"로 잡히면 안 된다 — " "는 truthy다."""
    _stub_possible(monkeypatch, [])
    out = scope_node(_state("상세", entry_bid="   "))
    assert out["resolved_filters"] == {"bid_ids": []}
