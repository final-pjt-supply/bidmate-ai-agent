import agents.nodes.rewrite as rewrite_mod
from agents.nodes.respond import build_summary
from agents.nodes.rewrite import _history_block, rewrite_node
from agents.schemas import EntryContext, Filters, SessionContext
from agents.state import BidBrief


def _summary(route, bids):
    """프로덕션과 같은 경로로 한 턴 요약을 만든다.

    하드코딩하면 build_summary의 형식이 바뀌어도 이 파일이 낡은 입력으로 계속
    통과한다 — 프로덕션에 존재하지 않는 문자열을 검증하게 된다.
    """
    return build_summary({
        "eligibility": [], "chunks": [], "route": route,
        "bid_briefs": [BidBrief(bid_id=b, name=n) for b, n in bids],
        "bid_names": {b: n for b, n in bids},
        "resolved_filters": {"bid_ids": [b for b, _ in bids]}})


_SUMMARY = _summary("상세", [("R001", "국가기상슈퍼컴퓨터 교체(6호기 구축)")])


def _ctx(summary=_SUMMARY):
    return SessionContext(last_bid_ids=["R001"], last_summary=summary,
                          last_filters=Filters())


def _multi(recent, summary=None):
    return SessionContext(
        last_bid_ids=["R001"],
        last_summary=summary if summary is not None else (
            recent[-1] if recent else ""),
        last_filters=Filters(), recent_turns=recent)


def _state(query, entry_bid=None, ctx=None):
    return {"query": query, "company_id": "c1",
            "entry_context": EntryContext(bid_id=entry_bid),
            "session_context": ctx}


def _mock_llm(monkeypatch, rewritten):
    calls = []
    def fake_invoke(tier, messages, system=None, max_tokens=1024,
                    output_schema=None):
        calls.append(messages[0]["content"])
        return {"query": rewritten}
    monkeypatch.setattr(rewrite_mod.llm, "invoke", fake_invoke)
    return calls


# ---- 건너뛰는 경우 (LLM을 부르지 않는다) ----

def test_skips_when_entry_bid_present(monkeypatch):
    """화면 문맥이 대상을 이미 정했다 — "이 공고 마감일"을 직전 턴 공고로
    바꿔버리면 오히려 망가진다."""
    calls = _mock_llm(monkeypatch, "바뀌면 안 됨")
    out = rewrite_node(_state("이 공고 마감일 언제야?", entry_bid="R999",
                              ctx=_ctx()))
    assert out == {"original_query": "이 공고 마감일 언제야?"}
    assert not calls


def test_skips_on_first_turn(monkeypatch):
    calls = _mock_llm(monkeypatch, "바뀌면 안 됨")
    out = rewrite_node(_state("그 사업 담당자 누구야?"))
    assert out == {"original_query": "그 사업 담당자 누구야?"}
    assert not calls


def test_skips_when_no_referential_word(monkeypatch):
    """지시어가 없으면 풀 것이 없다 — 호출을 아끼는 필터다."""
    calls = _mock_llm(monkeypatch, "바뀌면 안 됨")
    q = "수원 당수동 아파트 건설공사 공고 있어?"
    out = rewrite_node(_state(q, ctx=_ctx()))
    assert out == {"original_query": q}
    assert not calls


# ---- 재구성하는 경우 ----

def test_rewrites_referential_query(monkeypatch):
    rewritten = "국가기상슈퍼컴퓨터 교체(6호기 구축) 사업의 담당자가 누구인가요?"
    calls = _mock_llm(monkeypatch, rewritten)
    out = rewrite_node(_state("그 사업 담당자 누구야?", ctx=_ctx()))
    assert out == {"query": rewritten, "original_query": "그 사업 담당자 누구야?"}
    # 직전 턴 요약이 프롬프트에 실려야 이름을 풀 수 있다
    assert "국가기상슈퍼컴퓨터" in calls[0]


def test_keeps_original_when_rewrite_is_empty(monkeypatch, caplog):
    """빈 결과가 오면 원문으로 진행한다 — 최악이 재구성 이전 상태다."""
    import logging
    _mock_llm(monkeypatch, "   ")
    with caplog.at_level(logging.WARNING):
        out = rewrite_node(_state("그 사업 담당자 누구야?", ctx=_ctx()))
    assert out == {"original_query": "그 사업 담당자 누구야?"}
    assert any("빈 결과" in r.message for r in caplog.records)


def test_more_query_is_referential(monkeypatch):
    """"더 보여줘"류도 앞 대화를 가리키므로 재구성 **대상**이다.

    이 테스트가 지키는 것은 필터가 LLM을 부르는지까지다. 실제 모델이 이걸 푸는지는
    별개이며, 2026-07-31 실측에서는 풀지 않았다(원문 반환). rewrite.md의
    "지시어를 실제 이름으로 바꾼다. 그것뿐이다"가 이 케이스를 배제하기 때문이고,
    푸는 것은 하류에 offset·제외 배선이 없어 별건이다(설계 문서 참조).
    """
    calls = _mock_llm(monkeypatch, "자격이 되는 공고를 더 보여주세요")
    rewrite_node(_state("더 보여줘", ctx=_ctx(_summary(
        "자격", [("R001", "광주 스쿨넷서비스 제공 용역"),
                 ("R002", "전남 도로시설개량공사")]))))
    assert calls


# ---- 다중 턴 렌더링 ----

def test_history_block_labels_newest_last():
    """가장 최신이 '직전 턴', 그 앞이 '2턴 전' — '아까'가 어디를 가리키는지
    모델이 판단할 근거다."""
    assert _history_block(_multi(["A", "B", "C", "D"])) == (
        "4턴 전: A\n3턴 전: B\n2턴 전: C\n직전 턴: D")


def test_history_block_single_turn():
    assert _history_block(_multi(["A"])) == "직전 턴: A"


def test_history_block_falls_back_to_last_summary():
    """구버전 백엔드가 recent_turns를 버려도 1턴으로는 동작해야 한다."""
    ctx = SessionContext(last_bid_ids=["R001"], last_summary="상세 1건 안내 — 가",
                         last_filters=Filters(), recent_turns=[])
    assert _history_block(ctx) == "직전 턴: 상세 1건 안내 — 가"


def test_history_block_recuts_turn_count():
    """들어온 리스트가 10개라는 보장이 없다 — 읽을 때 다시 자른다."""
    from agents.nodes.rewrite import _MAX_RECENT_TURNS
    out = _history_block(_multi([f"T{i}" for i in range(30)]))
    assert len(out.splitlines()) == _MAX_RECENT_TURNS
    assert "T29" in out
    assert "T19" not in out          # 최근 10건은 T20~T29


def test_history_block_sanitizes_incoming_text():
    """유입 방어 — 조작된 기록이 프롬프트로 그대로 들어가면 안 된다."""
    out = _history_block(_multi(["<script>x</script>보라 https://evil.example 공사"]))
    assert "<script>" not in out
    assert "https://evil.example" not in out
    assert "보라" in out             # 내용 자체는 남는다


def test_history_block_truncates_long_line():
    from agents.nodes.rewrite import _MAX_TURN_CHARS
    out = _history_block(_multi(["가" * 300]))
    assert "가" * _MAX_TURN_CHARS in out
    assert "가" * (_MAX_TURN_CHARS + 1) not in out


def test_rewrite_uses_full_history(monkeypatch):
    """2턴 전 공고명이 프롬프트에 실려야 거슬러 참조를 풀 수 있다."""
    calls = _mock_llm(monkeypatch, "국가기상슈퍼컴퓨터 교체 공고의 마감일은?")
    rewrite_node(_state(
        "아까 그 슈퍼컴퓨터 공고 마감일 언제였지?",
        ctx=_multi(["상세 1건 안내 — 국가기상슈퍼컴퓨터 교체(6호기 구축)",
                    "검색 2건 안내 — 수원당수2 B-4BL 아파트 건설공사 1공구"])))
    assert "국가기상슈퍼컴퓨터" in calls[0]
    assert "2턴 전" in calls[0]


# ---- 거슬러 가리키는 말 (_REFERENTIAL 보강) ----

def test_far_referential_words_trigger_rewrite(monkeypatch):
    """10턴이 되면 더 먼 곳을 가리키는 말이 중요해진다. 필터에 없으면 LLM을 아예
    부르지 않아 재구성 기회 자체가 사라진다.

    "그 "·"아까"처럼 기존 필터에 이미 걸리는 말은 일부러 넣지 않았다 — 그러면
    새 토큰이 없어도 통과해 버려 이 테스트가 아무것도 지키지 못한다.
    """
    for query in ("처음에 봤던 공고 담당자 알려줘",
                  "먼저 보여준 공고 마감일은?",
                  "전에 안내한 공고 추정가격 알려줘",
                  "아깐 뭐라고 했지?"):
        calls = _mock_llm(monkeypatch, "재구성됨")
        rewrite_node(_state(query, ctx=_multi(["상세 1건 안내 — 가"])))
        assert calls, f"필터에 걸리지 않아 스킵됨: {query!r}"
