"""노드 진입/종료 공통 로깅 — 전문은 남기지 않는다(로그 정책 합의 전)."""
import functools
import logging
import time

logger = logging.getLogger(__name__)


def _summarize(output: dict) -> str:
    parts = []
    for k, v in output.items():
        if isinstance(v, list):
            parts.append(f"{k}={len(v)}")
        elif isinstance(v, str):
            parts.append(f"{k}=str({len(v)})")
        elif v is None:
            parts.append(f"{k}=None")
        else:
            parts.append(f"{k}={type(v).__name__}")
    return " ".join(parts)


def node_logger(node_name: str):
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(state):
            start = time.monotonic()
            logger.info("node=%s enter", node_name)
            out = fn(state)
            ms = (time.monotonic() - start) * 1000
            logger.info("node=%s exit duration_ms=%.0f %s",
                        node_name, ms, _summarize(out))
            return out
        return wrapper
    return deco
