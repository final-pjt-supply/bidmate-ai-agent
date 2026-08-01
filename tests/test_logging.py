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
