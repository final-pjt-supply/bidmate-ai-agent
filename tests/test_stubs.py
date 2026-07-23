import logging

from agents.nodes.stubs import eligibility_node, retrieval_node, scoring_node


def test_eligibility_stub_has_pass_and_fail(caplog):
    with caplog.at_level(logging.WARNING):
        out = eligibility_node({})
    results = out["eligibility"]
    assert any(r.passed for r in results)
    assert any(not r.passed and r.failed_reasons for r in results)
    assert any("stub" in r.message for r in caplog.records)   # 스텁 호출 경고


def test_retrieval_stub_returns_chunks():
    out = retrieval_node({})
    assert 3 <= len(out["chunks"]) <= 5
    assert all(c.text for c in out["chunks"])


def test_scoring_stub_returns_breakdown():
    out = scoring_node({})
    score = out["scores"][0]
    assert score.total > 0
    assert score.breakdown                  # 파생값 서술(note) 포함
    assert all(s.note for s in score.breakdown)
