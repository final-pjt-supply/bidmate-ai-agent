import agents.nodes.respond as respond_mod
from agents.nodes.respond import build_summary, check_grounding, respond_node
from agents.nodes.stubs import (eligibility_node, retrieval_node,
                                scoring_node)
from agents.schemas import EntryContext, Filters, QueryIntent


def _state():
    s = {"query": "이 공고 우리 자격 돼?", "company_id": "c1",
         "entry_context": EntryContext(), "session_context": None,
         "intent": QueryIntent(type="full", action="answer", scope="new",
                               entry_bid_scope="keep", new_filters=Filters(),
                               normalized_query="q"),
         "resolved_filters": {"region": "대전"},
         "answer": None, "citations": []}
    s.update(eligibility_node({}))
    s.update(retrieval_node({}))
    s.update(scoring_node({}))
    return s


def test_respond_builds_answer_and_citations(monkeypatch):
    monkeypatch.setattr(respond_mod.llm, "invoke",
                        lambda *a, **k: "여유율 2.5배로 자격을 충족합니다.")
    out = respond_node(_state())
    assert out["answer"]
    assert out["citations"]
    # 인용은 청크 원문 그대로
    assert all("입찰" in c.text or "낙찰" in c.text or "추정가격" in c.text
               for c in out["citations"])


def test_grounding_allows_derived_values():
    signals = "요구 실적 10억 대비 보유 25억, 여유율 2.5배 / 낙찰하한율 87.745%"
    assert check_grounding("여유율은 2.5배입니다", signals) == []


def test_grounding_flags_invented_numbers():
    signals = "여유율 2.5배"
    violations = check_grounding("통과 확률은 93%입니다", signals)
    assert "93" in " ".join(violations)


def test_grounding_violation_logged(monkeypatch, caplog):
    import logging
    monkeypatch.setattr(respond_mod.llm, "invoke",
                        lambda *a, **k: "통과 확률 93%로 예상됩니다.")
    with caplog.at_level(logging.WARNING):
        respond_node(_state())
    assert any("grounding" in r.message for r in caplog.records)


def test_build_summary_is_deterministic():
    s = _state()
    assert build_summary(s) == build_summary(s)
    assert "1건" in build_summary(s) or "건" in build_summary(s)


def test_grounding_allows_date_and_comma_number_parts():
    signals = "마감일 2026.08.01, 추정가격 1,200,000,000원"
    answer = "08월 01일 마감이며 추정가격은 1200000000원입니다"
    assert check_grounding(answer, signals) == []


def test_grounding_still_flags_unrelated_numbers():
    signals = "마감일 2026.08.01"
    violations = check_grounding("통과 확률은 93%입니다", signals)
    assert "93" in " ".join(violations)
