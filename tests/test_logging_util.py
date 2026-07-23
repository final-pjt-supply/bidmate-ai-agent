import logging

from agents.logging_util import node_logger


def test_decorator_logs_entry_and_summary(caplog):
    @node_logger("dummy")
    def dummy_node(state: dict) -> dict:
        return {"chunks": [1, 2, 3], "answer": "비밀 전문"}

    with caplog.at_level(logging.INFO):
        out = dummy_node({})

    assert out["answer"] == "비밀 전문"          # 반환값은 손대지 않는다
    joined = " ".join(r.message for r in caplog.records)
    assert "dummy" in joined
    assert "chunks=3" in joined                  # 건수 요약
    assert "비밀 전문" not in joined             # 전문은 로그 금지
