import logging

import agents.nodes.respond as respond_mod
from agents.nodes.respond import (BidItem, RespondOutput, build_summary,
                                  check_grounding, render_answer,
                                  respond_node, sanitize)
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


def _out(headline="여유율 2.5배로 자격을 충족합니다.", items=None, caveat=None):
    """구조화 출력 모킹 페이로드 — llm.invoke가 돌려주는 dict 형태."""
    return {"headline": headline,
            "items": items if items is not None else [],
            "caveat": caveat}


# ---- respond_node (구조화 출력 경로) ----

def test_respond_builds_answer_and_citations(monkeypatch):
    monkeypatch.setattr(respond_mod.llm, "invoke", lambda *a, **k: _out(
        items=[{"bid_id": "R26BK_STUB01", "text": "자격 통과, 여유율 2.5배"}]))
    out = respond_node(_state())
    assert out["answer"].splitlines()[0] == "여유율 2.5배로 자격을 충족합니다."
    assert "- R26BK_STUB01: " in out["answer"]     # 렌더러 양식
    assert out["citations"]
    # 인용은 청크 원문 그대로
    assert all("입찰" in c.text or "낙찰" in c.text or "추정가격" in c.text
               for c in out["citations"])


def test_respond_forces_output_schema(monkeypatch):
    seen = {}

    def fake_invoke(*a, **k):
        seen.update(k)
        return _out()

    monkeypatch.setattr(respond_mod.llm, "invoke", fake_invoke)
    respond_node(_state())
    assert seen["output_schema"] == RespondOutput.model_json_schema()


def test_grounding_violation_retries_once_then_falls_back(monkeypatch, caplog):
    calls = []

    def fake_invoke(*a, **k):
        calls.append(k)
        return _out(headline="통과 확률 93%로 예상됩니다.")

    monkeypatch.setattr(respond_mod.llm, "invoke", fake_invoke)
    with caplog.at_level(logging.WARNING):
        out = respond_node(_state())
    assert len(calls) == 2                          # 원호출 + 재생성 1회뿐
    assert "93" not in out["answer"]                # 위반 답변은 안 나감
    assert "원문 발췌" in out["answer"]             # 폴백으로 대체
    assert "입찰참가자격" in out["answer"]          # 폴백은 청크 원문 인용
    assert any("grounding" in r.message for r in caplog.records)


def test_grounding_violation_retry_succeeds(monkeypatch):
    payloads = [_out(headline="통과 확률 93%로 예상됩니다."),
                _out(headline="여유율 2.5배로 자격을 충족합니다.")]
    sent = []

    def fake_invoke(tier, messages, **k):
        sent.append(messages)
        return payloads[len(sent) - 1]

    monkeypatch.setattr(respond_mod.llm, "invoke", fake_invoke)
    out = respond_node(_state())
    assert out["answer"] == "여유율 2.5배로 자격을 충족합니다."
    # 재생성 요청에는 위반 수치가 피드백으로 실린다
    assert "93" in sent[1][-1]["content"]


# ---- 렌더러·sanitizer ----

def test_render_answer_is_deterministic_format():
    out = RespondOutput(headline="결론입니다.",
                        items=[BidItem(bid_id="R001", text="설명 문장.")],
                        caveat="지역 제한이 있습니다.")
    assert render_answer(out) == (
        "결론입니다.\n- R001: 설명 문장.\n참고: 지역 제한이 있습니다.")
    assert render_answer(out) == render_answer(out)


def test_sanitize_strips_tags_links_urls():
    dirty = '<script>알림</script> [클릭](https://evil.com) https://evil.com/x 정상'
    cleaned = sanitize(dirty)
    assert "<" not in cleaned and ">" not in cleaned
    assert "http" not in cleaned
    assert "클릭" in cleaned            # 링크 텍스트는 보존
    assert "정상" in cleaned


def test_answer_never_contains_html_or_links(monkeypatch):
    monkeypatch.setattr(respond_mod.llm, "invoke", lambda *a, **k: _out(
        headline='자격 충족 <img src=x onerror=alert(1)>',
        items=[{"bid_id": "R26BK_STUB01",
                "text": "상세는 [여기](https://evil.com) 참고, 여유율 2.5배"}]))
    out = respond_node(_state())
    assert "<img" not in out["answer"]
    assert "http" not in out["answer"]
    assert "여기" in out["answer"]      # 텍스트 내용은 유지


# ---- check_grounding (순수 함수 — 기존 회귀 유지) ----

def test_grounding_allows_derived_values():
    signals = "요구 실적 10억 대비 보유 25억, 여유율 2.5배 / 낙찰하한율 87.745%"
    assert check_grounding("여유율은 2.5배입니다", signals) == []


def test_grounding_flags_invented_numbers():
    signals = "여유율 2.5배"
    violations = check_grounding("통과 확률은 93%입니다", signals)
    assert "93" in " ".join(violations)


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


def test_grounding_flags_decimal_segments():
    assert check_grounding("통과 확률은 5%입니다", "여유율 2.5배") == ["5"]


def test_grounding_flags_partial_threshold():
    signals = "요구 실적 10억 대비 보유 25억, 여유율 2.5배 / 낙찰하한율 87.745%"
    violations = check_grounding("통과 확률 87%로 예상됩니다", signals)
    assert "87" in violations


def test_grounding_flags_comma_inner_group():
    violations = check_grounding("낙찰 건수는 234건입니다", "추정가격 1,234,567원")
    assert "234" in violations
