"""구조화 로깅(L1) 규약 테스트 — DB·네트워크 없음.

지키는 것: ① JSON 한 줄에 extra 필드가 최상위 키로 승격된다 ② request_id가
자동으로 붙는다 ③ 턴 메트릭이 누산·초기화된다(턴 밖에선 무해) ④ node_logger가
event/duration을 싣는다 ⑤ turn_end가 메트릭 합계를 싣는다 ⑥ llm 래퍼가
Bedrock usage를 계측한다. 이 키들은 CloudWatch 메트릭 필터가 읽는 계약이다.
"""
import json
import logging

import agents.logging_util as lu
from agents.logging_util import (JsonFormatter, add_turn_metric,
                                 get_turn_metrics, log_turn_end,
                                 new_request_id, node_logger)


def _fmt(record) -> dict:
    return json.loads(JsonFormatter().format(record))


def _record(msg="hello", extra=None, level=logging.INFO):
    rec = logging.LogRecord("t", level, __file__, 1, msg, (), None)
    for k, v in (extra or {}).items():
        setattr(rec, k, v)
    return rec


def test_JSON_한_줄에_extra가_최상위_키로_승격된다():
    doc = _fmt(_record(extra={"event": "llm_call", "tokens_out": 5}))
    assert doc["event"] == "llm_call"
    assert doc["tokens_out"] == 5
    assert doc["msg"] == "hello"
    assert doc["level"] == "INFO"


def test_한글이_이스케이프되지_않는다():
    text = JsonFormatter().format(_record("판정 미제공"))
    assert "판정 미제공" in text                 # \\uXXXX로 깨지지 않는다


def test_직렬화_불가_값은_str로_강등되고_죽지_않는다():
    doc = _fmt(_record(extra={"weird": object()}))
    assert isinstance(doc["weird"], str)         # 로깅이 앱을 죽이면 안 된다


def test_request_id는_발급_후_모든_레코드에_자동으로_붙는다():
    rid = new_request_id()
    assert _fmt(_record())["request_id"] == rid
    # 발급된 ID는 매 턴 달라야 상관관계가 성립한다
    assert new_request_id() != rid


def test_턴_메트릭은_누산되고_새_턴에서_초기화된다():
    new_request_id()
    add_turn_metric("llm_calls", 1)
    add_turn_metric("llm_calls", 1)
    add_turn_metric("tokens_out", 7)
    assert get_turn_metrics() == {"llm_calls": 2, "tokens_out": 7}
    new_request_id()
    assert get_turn_metrics() == {}


def test_턴_밖의_메트릭_추가는_조용히_무시된다():
    lu._turn_metrics.set(None)                   # 배치·단독 호출 상황 재현
    add_turn_metric("llm_calls", 1)              # 죽지 않아야 한다 (fail-safe)
    assert get_turn_metrics() == {}


def test_node_logger가_event와_duration을_싣는다(caplog):
    @node_logger("검증노드")
    def fake_node(state):
        return {"eligibility": [1, 2]}

    with caplog.at_level(logging.INFO):
        fake_node({})
    exits = [r for r in caplog.records if getattr(r, "event", "") == "node_exit"]
    assert exits and exits[0].node == "검증노드"
    assert isinstance(exits[0].duration_ms, int)


def test_turn_end는_턴_메트릭_합계를_싣는다(caplog):
    new_request_id()
    add_turn_metric("llm_calls", 2)
    add_turn_metric("tokens_out", 30)
    with caplog.at_level(logging.INFO):
        log_turn_end(route="자격", action="answer", result="answer",
                     total_ms=1234, bids_shown=5)
    rec = [r for r in caplog.records if getattr(r, "event", "") == "turn_end"][0]
    assert (rec.route, rec.result, rec.total_ms, rec.bids_shown) == \
        ("자격", "answer", 1234, 5)
    assert rec.llm_calls == 2 and rec.tokens_out == 30


# ── llm 래퍼의 usage 계측 ────────────────────────────────────────

def test_llm_call이_Bedrock_usage를_계측하고_누산한다(monkeypatch, caplog):
    import agents.llm as llm_mod

    class _Body:
        def read(self):
            return json.dumps({
                "content": [{"type": "text", "text": "안녕"}],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            }).encode()

    class _Client:
        def invoke_model(self, modelId, body):
            return {"body": _Body()}

    monkeypatch.setattr(llm_mod, "_client", lambda: _Client())
    new_request_id()
    with caplog.at_level(logging.INFO):
        out = llm_mod.invoke(llm_mod.ModelTier.ROUTER,
                             [{"role": "user", "content": "hi"}])
    assert out == "안녕"
    rec = [r for r in caplog.records if getattr(r, "event", "") == "llm_call"][0]
    assert (rec.tokens_in, rec.tokens_out, rec.attempt) == (10, 5, 1)
    assert rec.tier == "router"
    m = get_turn_metrics()
    assert (m["llm_calls"], m["tokens_in"], m["tokens_out"]) == (1, 10, 5)


def test_llm_usage가_없어도_계측이_죽지_않는다(monkeypatch, caplog):
    import agents.llm as llm_mod

    class _Body:
        def read(self):
            return json.dumps(
                {"content": [{"type": "text", "text": "ok"}]}).encode()

    class _Client:
        def invoke_model(self, modelId, body):
            return {"body": _Body()}

    monkeypatch.setattr(llm_mod, "_client", lambda: _Client())
    new_request_id()
    with caplog.at_level(logging.INFO):
        assert llm_mod.invoke(llm_mod.ModelTier.ROUTER,
                              [{"role": "user", "content": "hi"}]) == "ok"
    rec = [r for r in caplog.records if getattr(r, "event", "") == "llm_call"][0]
    assert rec.tokens_in is None and rec.tokens_out is None
    assert get_turn_metrics()["llm_calls"] == 1   # 호출 수는 그래도 센다


# ── ④ 제3자 로거 소음 정리 ───────────────────────────────────────
# 루트 핸들러를 갈아끼우는 함수라 다른 테스트의 caplog를 밟지 않도록 복원한다.

def _restore_logging(saved_handlers, saved_level, names):
    root = logging.getLogger()
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)
    for name, lvl in names.items():
        logging.getLogger(name).setLevel(lvl)


def test_제3자_로거는_WARNING으로_올라간다():
    names = {n: logging.getLogger(n).level for n in lu._QUIET}
    try:
        logging.getLogger("uvicorn.access").setLevel(logging.INFO)
        logging.getLogger("botocore").setLevel(logging.DEBUG)
        lu.quiet_third_party()
        assert logging.getLogger("uvicorn.access").level == logging.WARNING
        assert logging.getLogger("botocore").level == logging.WARNING
        # 하루 2,880줄짜리 헬스체크 접근 로그가 실제로 막히는지
        assert not logging.getLogger("uvicorn.access").isEnabledFor(logging.INFO)
    finally:
        _restore_logging(logging.getLogger().handlers[:],
                         logging.getLogger().level, names)


def test_setup_json_logging이_소음_정리를_함께_한다():
    root = logging.getLogger()
    saved, saved_level = root.handlers[:], root.level
    names = {n: logging.getLogger(n).level for n in lu._QUIET}
    try:
        logging.getLogger("httpx").setLevel(logging.INFO)
        lu.setup_json_logging()
        assert logging.getLogger("httpx").level == logging.WARNING
    finally:
        _restore_logging(saved, saved_level, names)


def test_우리_로거의_INFO는_그대로_남는다():
    """소음 정리가 우리 이벤트까지 잡으면 대시보드가 통째로 빈다."""
    root = logging.getLogger()
    saved, saved_level = root.handlers[:], root.level
    names = {n: logging.getLogger(n).level for n in lu._QUIET}
    try:
        lu.setup_json_logging()
        assert logging.getLogger("agents.run").isEnabledFor(logging.INFO)
        assert logging.getLogger("agents.logging_util").isEnabledFor(logging.INFO)
    finally:
        _restore_logging(saved, saved_level, names)


def test_quiet를_끄면_제3자_레벨을_건드리지_않는다():
    root = logging.getLogger()
    saved, saved_level = root.handlers[:], root.level
    names = {n: logging.getLogger(n).level for n in lu._QUIET}
    try:
        logging.getLogger("httpx").setLevel(logging.INFO)
        lu.setup_json_logging(quiet=False)
        assert logging.getLogger("httpx").level == logging.INFO
    finally:
        _restore_logging(saved, saved_level, names)


# ── route 차원용 ASCII 사본 ──────────────────────────────────────
# CloudWatch가 한글 차원 값을 버리는 것을 우회한다. 이 키가 없어지면
# D1의 route 패널 5개가 조용히 빈다 — 계약이므로 테스트로 못 박는다.

def test_turn_end가_ASCII_route_code를_함께_싣는다(caplog):
    with caplog.at_level(logging.INFO):
        log_turn_end(route="검색", action="answer", result="answer", total_ms=1)
    rec = [r for r in caplog.records if getattr(r, "event", "") == "turn_end"][0]
    assert rec.route == "검색"
    assert rec.route_code == "search"
    assert rec.route_code.isascii()


def test_네_갈래가_모두_ASCII로_매핑된다():
    codes = [lu.route_code(r) for r in ("검색", "상세", "자격", "기타")]
    assert codes == ["search", "detail", "eligibility", "other"]
    assert all(c.isascii() for c in codes)


def test_모르는_route는_unknown으로_떨어지고_죽지_않는다():
    assert lu.route_code("새갈래") == "unknown"
    assert lu.route_code(None) == "unknown"
