import json
from unittest.mock import MagicMock

import pytest

import agents.llm as llm
from agents.llm import ModelTier


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
