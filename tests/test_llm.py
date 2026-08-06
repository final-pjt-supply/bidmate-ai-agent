import json
from unittest.mock import MagicMock

import pytest

import agents.llm as llm
from agents.llm import ModelTier
from agents.schemas import QueryIntent


def _fake_response(text: str) -> dict:
    body = MagicMock()
    body.read.return_value = json.dumps(
        {"content": [{"type": "text", "text": text}]}).encode()
    return {"body": body}


def test_invoke_text(monkeypatch):
    client = MagicMock()
    client.invoke_model.return_value = _fake_response("안녕하세요")
    monkeypatch.setattr(llm, "_client", lambda: client)

    out = llm.invoke(ModelTier.SYNTHESIS, [{"role": "user", "content": "hi"}],
                     max_tokens=100)
    assert out == "안녕하세요"
    body = json.loads(client.invoke_model.call_args.kwargs["body"])
    assert body["max_tokens"] == 100
    assert "thinking" not in body            # Sonnet 4.6: 생략=off
    assert "output_config" not in body


def test_invoke_structured(monkeypatch):
    client = MagicMock()
    client.invoke_model.return_value = _fake_response('{"a": 1}')
    monkeypatch.setattr(llm, "_client", lambda: client)

    out = llm.invoke(ModelTier.ROUTER, [{"role": "user", "content": "hi"}],
                     output_schema={"type": "object"})
    assert out == {"a": 1}
    body = json.loads(client.invoke_model.call_args.kwargs["body"])
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert "effort" not in body              # Haiku 4.5: effort 금지


def test_retry_on_throttle(monkeypatch, caplog):
    from botocore.exceptions import ClientError
    err = ClientError({"Error": {"Code": "ThrottlingException"}}, "InvokeModel")
    client = MagicMock()
    client.invoke_model.side_effect = [err, _fake_response("ok")]
    monkeypatch.setattr(llm, "_client", lambda: client)
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)

    out = llm.invoke(ModelTier.ROUTER, [{"role": "user", "content": "hi"}])
    assert out == "ok"
    assert any("retry" in r.message for r in caplog.records
               if r.levelname == "WARNING")


def test_retry_exhausted_raises(monkeypatch):
    from botocore.exceptions import ClientError
    err = ClientError({"Error": {"Code": "ThrottlingException"}}, "InvokeModel")
    client = MagicMock()
    client.invoke_model.side_effect = err
    monkeypatch.setattr(llm, "_client", lambda: client)
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)

    with pytest.raises(ClientError):
        llm.invoke(ModelTier.ROUTER, [{"role": "user", "content": "hi"}])


def _assert_bedrock_compatible(node):
    """모든 object 노드(root, $defs 내부 포함)에 additionalProperties: False가
    있고, 어디에도 "default" 키가 남아있지 않은지 재귀적으로 검사."""
    if isinstance(node, dict):
        assert "default" not in node
        if node.get("type") == "object" or "properties" in node:
            assert node.get("additionalProperties") is False
        for v in node.values():
            _assert_bedrock_compatible(v)
    elif isinstance(node, list):
        for v in node:
            _assert_bedrock_compatible(v)


def test_invoke_prepares_query_intent_schema_for_bedrock(monkeypatch):
    client = MagicMock()
    client.invoke_model.return_value = _fake_response(
        '{"type": "full", "action": "answer", "scope": "inherit", '
        '"entry_bid_scope": "keep", "new_filters": {}, '
        '"normalized_query": "q", "clarify_message": null}')
    monkeypatch.setattr(llm, "_client", lambda: client)

    out = llm.invoke(ModelTier.ROUTER, [{"role": "user", "content": "hi"}],
                     output_schema=QueryIntent.model_json_schema())
    assert out["action"] == "answer"
    body = json.loads(client.invoke_model.call_args.kwargs["body"])
    schema = body["output_config"]["format"]["schema"]
    _assert_bedrock_compatible(schema)


def test_retry_on_read_timeout(monkeypatch, caplog):
    from botocore.exceptions import ReadTimeoutError
    err = ReadTimeoutError(endpoint_url="https://example.com")
    client = MagicMock()
    client.invoke_model.side_effect = [err, _fake_response("ok")]
    monkeypatch.setattr(llm, "_client", lambda: client)
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)

    out = llm.invoke(ModelTier.ROUTER, [{"role": "user", "content": "hi"}])
    assert out == "ok"
    assert any("retry" in r.message and "ReadTimeoutError" in r.message
               for r in caplog.records if r.levelname == "WARNING")


def test_retry_on_connection_closed(monkeypatch, caplog):
    """2026-08-06 502 장애 회귀 테스트 — Bedrock이 응답 전에 연결을 끊는
    ConnectionClosedError는 재시도 대상이어야 한다(과거엔 안 잡혀 500 유출)."""
    from botocore.exceptions import ConnectionClosedError
    err = ConnectionClosedError(endpoint_url="https://bedrock.example.com")
    client = MagicMock()
    client.invoke_model.side_effect = [err, _fake_response("ok")]
    monkeypatch.setattr(llm, "_client", lambda: client)
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)

    out = llm.invoke(ModelTier.ROUTER, [{"role": "user", "content": "hi"}])
    assert out == "ok"
    assert any("retry" in r.message and "ConnectionClosedError" in r.message
               for r in caplog.records if r.levelname == "WARNING")


def test_connection_closed_exhausted_raises(monkeypatch):
    """연결 계열도 _MAX_ATTEMPTS 소진 시에는 그대로 올라간다(무한 재시도 금지)."""
    from botocore.exceptions import ConnectionClosedError
    err = ConnectionClosedError(endpoint_url="https://bedrock.example.com")
    client = MagicMock()
    client.invoke_model.side_effect = err
    monkeypatch.setattr(llm, "_client", lambda: client)
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)

    with pytest.raises(ConnectionClosedError):
        llm.invoke(ModelTier.ROUTER, [{"role": "user", "content": "hi"}])
    assert client.invoke_model.call_count == 4  # _MAX_ATTEMPTS


def test_invoke_raises_when_no_text_block(monkeypatch):
    client = MagicMock()
    body = MagicMock()
    body.read.return_value = json.dumps(
        {"content": [{"type": "tool_use", "input": {}}]}).encode()
    client.invoke_model.return_value = {"body": body}
    monkeypatch.setattr(llm, "_client", lambda: client)

    with pytest.raises(ValueError, match="text 블록이 없습니다"):
        llm.invoke(ModelTier.ROUTER, [{"role": "user", "content": "hi"}])
