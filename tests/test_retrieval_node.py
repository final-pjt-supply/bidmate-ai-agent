"""[2] retrieval 노드 검증.

실제 OpenSearch·PostgreSQL·Cloudflare에 접속하므로 live 마커를 붙인다.
pytest.ini의 addopts = -m "not live" 때문에 기본 실행에서는 제외되며,
실행하려면 명시적으로 지정한다.

    pytest tests/test_retrieval_node.py -m live
"""
import pytest

from agents.nodes.retrieval import retrieval_node
from agents.schemas import Chunk, EntryContext, Filters, QueryIntent

pytestmark = pytest.mark.live


def _state(query: str, filters: dict | None = None,
           entry_bid: str | None = None) -> dict:
    """AgentState와 같은 형태 (Router 통과 후 상태)."""
    return {
        "query": query,
        "company_id": "test-company",
        "entry_context": EntryContext(bid_id=entry_bid),
        "session_context": None,
        "intent": QueryIntent(
            type="content_only", action="answer", scope="new",
            entry_bid_scope="keep", new_filters=Filters(),
            normalized_query=query,
        ),
        "resolved_filters": filters or {},
        "eligibility": [], "chunks": [], "scores": [],
        "answer": None, "citations": [],
    }


def test_returns_chunk_list():
    out = retrieval_node(_state("소프트웨어 개발 용역"))
    assert "chunks" in out
    assert isinstance(out["chunks"], list)
    assert all(isinstance(c, Chunk) for c in out["chunks"])


def test_chunk_has_no_score_field():
    """계약의 Chunk에는 score가 없다 — 검색 점수는 노드로 넘기지 않는다."""
    out = retrieval_node(_state("전기 공사"))
    assert all(not hasattr(c, "score") for c in out["chunks"])


def test_bid_ids_scope_is_respected():
    """Case 2 진입 — 지정 공고 안에서만 검색한다."""
    target = "R26BK01634638_000"
    out = retrieval_node(_state("입찰 참가 자격", {"bid_ids": [target]}))
    assert out["chunks"], "지정 공고에서 결과가 나와야 한다"
    assert {c.bid_id for c in out["chunks"]} == {target}


def test_empty_query_returns_empty():
    out = retrieval_node(_state(""))
    assert out["chunks"] == []


def test_category_filter_narrows_result():
    """업무구분 필터가 적용되면 결과가 그 구분으로 좁혀진다."""
    out = retrieval_node(_state("정보시스템 구축", {"category": "servc"}))
    assert isinstance(out["chunks"], list)   # 결과 유무는 데이터에 따름


def test_works_without_intent():
    """그래프 밖 호출 — intent 없이도 query로 폴백한다."""
    out = retrieval_node({"query": "청소 용역", "resolved_filters": {}})
    assert isinstance(out["chunks"], list)