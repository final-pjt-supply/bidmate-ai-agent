from datetime import datetime

import agents.nodes.bid_search as bid_search_mod
from agents.nodes.bid_search import bid_search_node
from agents.schemas import Chunk
from agents.tools.search_types import BidHit, BidInfo, RecommendedBid, SearchHit


def _rec(bid_id, name="스쿨넷서비스  제공 용역", *, info=True):
    hit = BidHit(bid_id=bid_id, score=1.0, top_hit=SearchHit(
        chunk=Chunk(bid_id=bid_id, document_id="d", file_id="f",
                    chunk_idx=0, text="t", type="text"), score=1.0))
    if not info:
        return RecommendedBid(hit=hit)
    return RecommendedBid(hit=hit, info=BidInfo(
        bid_id=bid_id, bid_ntce_no=bid_id[:-4], bid_ntce_ord="000",
        bid_ntce_nm=name, dminstt_nm="광주광역시교육청",
        bid_clse_dt=datetime(2026, 7, 30, 10, 0),
        presmpt_prce=900_000_000, cntrct_cncls_mthd_nm="제한(총액)"))


def _stub(monkeypatch, recs):
    calls = []
    def fake(query, **kw):
        calls.append((query, kw))
        return list(recs)
    monkeypatch.setattr(bid_search_mod, "recommend_bids", fake)
    return calls


def test_found_bids_become_scope_and_briefs(monkeypatch):
    calls = _stub(monkeypatch, [_rec("R001_000"), _rec("R002_000")])
    out = bid_search_node({"query": "스쿨넷 공고 있어?", "resolved_filters": None,
                           "bid_names": {}})
    assert out["resolved_filters"]["bid_ids"] == ["R001_000", "R002_000"]
    assert calls[0][0] == "스쿨넷 공고 있어?"
    assert calls[0][1]["top_k"] == bid_search_mod._TOP_K
    # 절대 점수 하한은 대화 경로에서 끈다 — 조건형 질의가 무의미한 입력보다
    # 낮은 점수를 받아 전부 잘리기 때문이다(bid_search.py의 _MIN_SCORE 주석).
    assert calls[0][1]["min_score"] == 0.0


def test_brief_carries_postgres_meta(monkeypatch):
    _stub(monkeypatch, [_rec("R001_000")])
    brief = bid_search_node({"query": "q", "resolved_filters": None,
                             "bid_names": {}})["bid_briefs"][0]
    assert brief.institution == "광주광역시교육청"
    assert brief.close_at == "2026-07-30 10:00"
    assert brief.price == "900,000,000원"       # 천단위 구분, 계산 아님
    assert brief.method == "제한(총액)"


def test_missing_info_degrades_to_bid_id_only(monkeypatch):
    """bid_table에 없는 공고 — 요약은 비고 범위에는 남는다."""
    _stub(monkeypatch, [_rec("R001_000", info=False)])
    out = bid_search_node({"query": "q", "resolved_filters": None,
                           "bid_names": {}})
    assert out["bid_briefs"][0].name == ""
    assert out["resolved_filters"]["bid_ids"] == ["R001_000"]
    assert out["bid_names"] == {}


def test_nothing_found_leaves_scope_open(monkeypatch, caplog):
    """공고를 못 찾으면 범위를 비워 둔다 — 전체 검색으로 떨어지는 편이 낫다.

    키를 지우지 않고 빈 리스트로 채운다(graph._found_bids가 한 가지 모양만
    보게 하려고). 기존 필터는 보존한다.
    """
    import logging
    _stub(monkeypatch, [])
    with caplog.at_level(logging.WARNING):
        out = bid_search_node({"query": "q", "resolved_filters": {"region": "경기"},
                               "bid_names": {}})
    assert out["resolved_filters"] == {"region": "경기", "bid_ids": []}
    assert any("특정하지 못했다" in r.message for r in caplog.records)


def test_existing_bid_names_are_preserved(monkeypatch):
    _stub(monkeypatch, [_rec("R001_000")])
    out = bid_search_node({"query": "q", "resolved_filters": None,
                           "bid_names": {"R999_000": "앞 노드가 채운 이름"}})
    assert out["bid_names"]["R999_000"] == "앞 노드가 채운 이름"
    assert out["bid_names"]["R001_000"] == "스쿨넷서비스  제공 용역"
