import agents.nodes.scope as scope_mod
from agents.nodes.scope import scope_node
from agents.schemas import EntryContext


def _state(route, entry_bid=None, company_id="9001"):
    return {"query": "q", "company_id": company_id, "route": route,
            "entry_context": EntryContext(bid_id=entry_bid),
            "session_context": None, "resolved_filters": None}


def _row(bid_id, name="가 용역", *, close_at=None, price=None):
    """possible_bids가 돌려주는 행 한 개(bid_table 컬럼 그대로)."""
    return {"bid_id": bid_id, "bid_ntce_nm": name, "dminstt_nm": "광주교육청",
            "ntce_instt_nm": "조달청", "bid_clse_dt": close_at,
            "presmpt_prce": price, "cntrct_cncls_mthd_nm": "제한(총액)"}


def _stub_possible(monkeypatch, rows, total=None):
    """rows: possible_bids의 행 리스트. total 생략 시 len(rows)."""
    calls = []
    def fake(company_id, *, limit):
        calls.append((company_id, limit))
        return list(rows), (len(rows) if total is None else total)
    monkeypatch.setattr(scope_mod, "possible_bids", fake)
    return calls


def test_entry_bid_becomes_scope(monkeypatch):
    """화면 문맥이 가장 강한 신호 — DB를 보지 않는다."""
    calls = _stub_possible(monkeypatch, [_row("R_SHOULD_NOT_BE_USED")])
    out = scope_node(_state("상세", entry_bid="R999"))
    assert out["resolved_filters"] == {"bid_ids": ["R999"]}
    assert not calls                            # match_results 조회 없음


def test_entry_bid_wins_for_eligibility_too(monkeypatch):
    calls = _stub_possible(monkeypatch, [_row("R_OTHER")])
    out = scope_node(_state("자격", entry_bid="R999"))
    assert out["resolved_filters"] == {"bid_ids": ["R999"]}
    assert not calls
    assert out["eligible_total"] == 0           # 특정 공고 질의엔 총계가 없다


def test_eligibility_without_entry_bid_uses_match_results(monkeypatch):
    calls = _stub_possible(monkeypatch,
                           [_row("R1", "가"), _row("R2", "나"), _row("R3", "")],
                           total=165)
    out = scope_node(_state("자격"))
    assert out["resolved_filters"] == {"bid_ids": ["R1", "R2", "R3"]}
    assert calls == [("9001", scope_mod._MAX_POSSIBLE)]
    # 전체 건수를 실어야 답변이 "이 3건이 전부"라고 말하지 않는다
    assert out["eligible_total"] == 165


def test_eligibility_fills_briefs_with_meta(monkeypatch):
    """자격 갈래는 retrieval도 bid_search도 안 타므로 공고 요약을 여기서 채운다.

    안 채우면 답변이 공고를 `R26BK…` 공고번호로 부르고, 마감일·금액을 말할
    재료가 없어 "세부 조건은 원문에서 확인하라"는 빈 말만 하게 된다.
    """
    from datetime import datetime
    _stub_possible(monkeypatch, [
        _row("R1", "스쿨넷 용역", close_at=datetime(2026, 8, 5, 10, 0),
             price=346_363_636),
        _row("R2", ""),
    ])
    out = scope_node(_state("자격"))
    first = out["bid_briefs"][0]
    assert first.name == "스쿨넷 용역"
    assert first.close_at == "2026-08-05 10:00"
    assert first.price == "346,363,636원"        # 천단위 구분만, 단위 변환 없음
    assert first.institution == "광주교육청"      # 수요기관 우선
    assert out["bid_names"] == {"R1": "스쿨넷 용역"}   # 빈 이름은 넣지 않는다


def test_existing_bid_names_are_preserved(monkeypatch):
    _stub_possible(monkeypatch, [_row("R1", "가")])
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
    calls = _stub_possible(monkeypatch, [_row("R1")])
    out = scope_node(_state("상세"))
    assert out["resolved_filters"] == {"bid_ids": []}
    assert not calls


def test_blank_entry_bid_is_not_a_scope(monkeypatch):
    """백엔드가 공백을 보내도 "구했다"로 잡히면 안 된다 — " "는 truthy다."""
    _stub_possible(monkeypatch, [])
    out = scope_node(_state("상세", entry_bid="   "))
    assert out["resolved_filters"] == {"bid_ids": []}
