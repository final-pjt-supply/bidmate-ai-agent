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
        items=[{"bid_id": "R26BK_STUB01", "label": "", "text": "자격 통과, 여유율 2.5배"}]))
    out = respond_node(_state())
    assert out["answer"].splitlines()[0] == "여유율 2.5배로 자격을 충족합니다."
    # 렌더러 양식 — 공고 이름이 한 줄, 항목은 그 아래 들여쓴 줄
    assert "- R26BK_STUB01:\n  · 자격 통과" in out["answer"]
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
    out = RespondOutput(headline="조건에 맞는 공고가 1건 있습니다.",
                        items=[BidItem(bid_id="R001", label="", text="설명 문장.")],
                        caveat="지역 제한이 있습니다.")
    assert render_answer(out) == (
        "조건에 맞는 공고가 1건 있습니다.\n- R001:\n  · 설명 문장.\n참고: 지역 제한이 있습니다.")
    assert render_answer(out) == render_answer(out)


def test_render_answer_puts_item_label_before_value():
    """항목 이름은 LLM이 정하고 구분자는 코드가 붙인다."""
    out = RespondOutput(
        headline="공고 1건입니다.",
        items=[BidItem(bid_id="R001", label="마감", text="2026-07-31 10:00"),
               BidItem(bid_id="R001", label="추정가격", text="250,873,091원"),
               BidItem(bid_id="R001", label="", text="3개 축을 충족합니다.")],
        caveat=None)
    assert render_answer(out, {"R001": "레미콘 관급자재"}) == (
        "공고 1건입니다.\n"
        "- 레미콘 관급자재:\n"
        "  · 마감 : 2026-07-31 10:00\n"
        "  · 추정가격 : 250,873,091원\n"
        "  · 3개 축을 충족합니다.")


def test_render_answer_drops_items_that_repeat_the_bid_name():
    """공고 이름은 머리줄에 이미 있다 — 되풀이하는 항목은 코드가 지운다.

    프롬프트로 금지해도 label을 "공고명"으로 바꿔 우회하는 경우가 관측됐다.
    """
    out = RespondOutput(
        headline="공고 1건입니다.",
        items=[BidItem(bid_id="R001", label="공고명", text="레미콘 관급자재"),
               BidItem(bid_id="R001", label="사업 명", text="아무 값"),
               BidItem(bid_id="R001", label="", text="레미콘 관급자재"),
               BidItem(bid_id="R001", label="마감", text="2026-07-31 10:00")],
        caveat=None)
    assert render_answer(out, {"R001": "레미콘 관급자재"}) == (
        "공고 1건입니다.\n- 레미콘 관급자재:\n  · 마감 : 2026-07-31 10:00")


def test_render_answer_omits_header_when_all_items_dropped():
    """전부 버려진 공고는 빈 껍데기 머리줄만 남기지 않는다."""
    out = RespondOutput(
        headline="공고 1건입니다.",
        items=[BidItem(bid_id="R001", label="공고명", text="레미콘 관급자재"),
               BidItem(bid_id="R002", label="마감", text="2026-08-01 10:00")],
        caveat=None)
    assert render_answer(out, {"R001": "레미콘 관급자재", "R002": "다른 공고"}) == (
        "공고 1건입니다.\n- 다른 공고:\n  · 마감 : 2026-08-01 10:00")


def test_render_answer_labels_with_bid_name_when_available():
    """공고명을 알면 라벨로 쓴다 — 사용자에게 공고번호는 읽을 이유가 없다."""
    out = RespondOutput(headline="조건에 맞는 공고가 1건 있습니다.",
                        items=[BidItem(bid_id="R001", label="", text="설명 문장.")],
                        caveat=None)
    assert render_answer(out, {"R001": "○○청사  청소용역"}) == (
        "조건에 맞는 공고가 1건 있습니다.\n- ○○청사 청소용역:\n  · 설명 문장.")   # 이중 공백도 정리
    # 맵에 없는 공고는 기존대로 bid_id (조회 실패·스텁 환경 폴백)
    assert render_answer(out, {"R999": "다른 공고"}) == (
        "조건에 맞는 공고가 1건 있습니다.\n- R001:\n  · 설명 문장.")


def test_render_answer_does_not_repeat_same_label():
    """같은 공고를 여러 줄로 설명해도 이름을 매 줄 반복하지 않는다."""
    out = RespondOutput(
        headline="조건에 맞는 공고가 1건 있습니다.",
        items=[BidItem(bid_id="R001", label="", text="첫째 근거."),
               BidItem(bid_id="R001", label="", text="둘째 근거."),
               BidItem(bid_id="R002", label="", text="다른 공고 근거.")],
        caveat=None)
    assert render_answer(out, {"R001": "가공고", "R002": "나공고"}) == (
        "조건에 맞는 공고가 1건 있습니다.\n- 가공고:\n  · 첫째 근거.\n  · 둘째 근거."
        "\n- 나공고:\n  · 다른 공고 근거.")


def test_respond_uses_bid_names_from_state(monkeypatch):
    monkeypatch.setattr(respond_mod.llm, "invoke", lambda *a, **k: _out(
        items=[{"bid_id": "R26BK_STUB01", "label": "", "text": "자격 통과, 여유율 2.5배"}]))
    state = _state()
    state["bid_names"] = {"R26BK_STUB01": "전기공사 통합 발주"}
    out = respond_node(state)
    assert "- 전기공사 통합 발주:" in out["answer"]
    assert "R26BK_STUB01" not in out["answer"]      # 번호는 citations로만 나간다


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
        items=[{"bid_id": "R26BK_STUB01", "label": "",
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


# ---- build_summary 골든 ----
#
# 출력 전문을 고정한다. rewrite·router 프롬프트가 이 형식에 의존하는데, 지금까지
# 어떤 테스트도 형식을 지키지 않았다 — 통째로 바꿔도 전부 통과했다(2026-07-31).
# 형식을 바꿀 일이 생기면 여기가 먼저 깨져야 한다. 깨지면 rewrite.md 예시와
# router.py의 접두사도 함께 봐야 한다는 신호다.

def test_build_summary_golden_no_route_no_names():
    """route도 공고명도 없을 때 — label은 '공고'로 폴백한다."""
    assert build_summary(_state()) == "공고 2건 안내 (조건: region)"


def test_build_summary_golden_detail_with_names():
    """공고명이 있으면 최대 _SUMMARY_NAMES(3)건까지 싣고 나머지는 '외 N건'."""
    s = _state()
    s["route"] = "상세"
    s["bid_names"] = {"R26BK_STUB01": "국가기상슈퍼컴퓨터 교체(6호기 구축)"}
    assert build_summary(s) == (
        "상세 2건 안내 — 국가기상슈퍼컴퓨터 교체(6호기 구축) 외 1건 (조건: region)")


def test_build_summary_golden_search_from_briefs():
    """`검색` 갈래는 청크도 판정도 없다 — bid_briefs에서 공고가 와야 한다."""
    from agents.state import BidBrief
    s = {"eligibility": [], "chunks": [], "route": "검색",
         "bid_briefs": [BidBrief(bid_id="R001_000", name="대전 하수관로 정비공사"),
                        BidBrief(bid_id="R002_000",
                                 name="대전 학교 전기설비 개선공사")],
         "bid_names": {"R001_000": "대전 하수관로 정비공사",
                       "R002_000": "대전 학교 전기설비 개선공사"},
         "resolved_filters": {"bid_ids": ["R001_000", "R002_000"]}}
    assert build_summary(s) == (
        "검색 2건 안내 — 대전 하수관로 정비공사, 대전 학교 전기설비 개선공사")


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


def test_bids_block_carries_count_and_ordinals():
    """건수·항목 번호가 신호에 실려야 답변의 "N건" 표현이 그라운딩된다.

    _TASK_LIST가 건수를 적으라고 지시하는데 그 숫자가 신호에 없으면
    check_grounding이 위반으로 잡아 재생성·폴백까지 떠밀린다(실측).
    """
    from agents.state import BidBrief
    state = {"bid_briefs": [BidBrief(bid_id="R001_000", name="가 용역"),
                            BidBrief(bid_id="R002_000", name="나 공사"),
                            BidBrief(bid_id="R003_000", name="다 물품")],
             "bid_names": {}}
    signals = respond_mod._bids_block(state)
    assert signals.startswith("공고 3건")
    assert "1. R001_000" in signals and "3. R003_000" in signals
    # 3건 중 1건만 골라 안내하는 답변도 위반이 아니어야 한다
    assert check_grounding("조건에 맞는 공고는 3건 중 1건입니다", signals) == []


def test_bids_block_falls_back_to_names_with_count():
    state = {"bid_briefs": [], "bid_names": {"R001_000": "가 용역"}}
    signals = respond_mod._bids_block(state)
    assert signals.startswith("공고 1건")
    assert "1. R001_000: 가 용역" in signals


def test_bids_block_without_any_bid():
    assert respond_mod._bids_block({"bid_briefs": [], "bid_names": {}}) \
        == "(공고명 정보 없음)"


def test_grounding_allows_zero_stripped_form():
    """bid_table 마감일시는 "2026-07-30 10:00"로 실린다 — 신호 토큰이 "07"인데
    답변은 "7월 30일"로 쓰는 것이 자연스럽다. 이걸 위반으로 잡으면 정상 답변이
    재생성·폴백까지 떠밀린다(bid_search가 채우는 공고 요약이 이 형식이다)."""
    signals = "- R001_000: 스쿨넷서비스 제공 용역 | 마감 2026-07-30 10:00"
    assert check_grounding("마감은 2026년 7월 30일 10시입니다", signals) == []


def test_grounding_zero_strip_does_not_widen_to_other_numbers():
    """앞자리 0 제거가 무관한 수치까지 허용하면 안 된다."""
    signals = "마감 2026-07-30 10:00"
    violations = check_grounding("마감은 8월 3일입니다", signals)
    assert "8" in violations and "3" in violations
