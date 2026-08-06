"""Bedrock 호출 래퍼 — 티어 스위칭·재시도. 모델 ID는 여기에만 둔다."""
import copy
import json
import logging
import os
import random
import time
from enum import StrEnum
from functools import lru_cache

import boto3
from botocore.config import Config
# ConnectionError는 파이썬 내장과 이름이 겹쳐 별칭으로 임포트한다.
# 2026-08-06 트러블슈팅: ConnectionClosedError(Bedrock이 응답 전 연결을 끊음)가
# 어느 예외 계열에도 안 잡혀 재시도 없이 500으로 새어 나갔다(→ API 502).
# botocore 버전에 따라 ConnectionClosedError의 부모가 ConnectionError였다가
# HTTPClientError로 바뀌었으므로(1.43 기준 HTTPClientError) 두 계열을 모두 잡는다.
# ReadTimeoutError·ConnectTimeoutError·EndpointConnectionError도 이 둘로 커버된다.
from botocore.exceptions import ClientError, HTTPClientError
from botocore.exceptions import ConnectionError as BotoConnectionError
from dotenv import load_dotenv

from agents.logging_util import add_turn_metric

logger = logging.getLogger(__name__)
load_dotenv()

BEDROCK_REGION = "ap-northeast-2"   # Step 1 결과로 확정. 쿼터가 리전별이므로 변경 금지.

class ModelTier(StrEnum):
    ROUTER = "router"          # Haiku 4.5 — effort/thinking 금지
    SYNTHESIS = "synthesis"    # Sonnet 4.6 — thinking 생략=off, max_tokens 명시

_MODEL_IDS = {
    ModelTier.ROUTER: "global.anthropic.claude-haiku-4-5-20251001-v1:0",
    ModelTier.SYNTHESIS: "global.anthropic.claude-sonnet-4-6",
}

_MAX_ATTEMPTS = 4
_RETRYABLE = {"ThrottlingException", "ServiceUnavailableException",
              "ModelTimeoutException", "InternalServerException"}


def _prepare_schema(schema: dict) -> dict:
    """Bedrock 구조화 출력 호환 후처리 — deep copy 후:
    - object 스키마(모든 "type": "object" 또는 "properties" 보유 노드)에
      "additionalProperties": False 강제 (Anthropic structured outputs 요구사항).
    - "default" 키는 어디에 있든 제거(모델 스키마의 "default": null 등은
      Bedrock structured output 검증에서 거부됨).
    properties/$defs/items/anyOf/allOf/oneOf를 재귀적으로 처리한다.
    """
    def _walk(node):
        if isinstance(node, dict):
            node = {k: _walk(v) for k, v in node.items() if k != "default"}
            if node.get("type") == "object" or "properties" in node:
                node["additionalProperties"] = False
            return node
        if isinstance(node, list):
            return [_walk(v) for v in node]
        return node

    return _walk(copy.deepcopy(schema))


@lru_cache(maxsize=1)
def _client():
    # .env의 비표준 키 이름을 boto3 표준 인자로 명시 재매핑 (CLAUDE.md 규칙)
    return boto3.client(
        "bedrock-runtime",
        region_name=BEDROCK_REGION,
        aws_access_key_id=os.environ["AWS_ACCESS_KEY"],
        aws_secret_access_key=os.environ["AWS_SECRET_KEY"],
        # retries=0: 재시도는 아래 invoke() 루프가 전담(중복 재시도 방지).
        # tcp_keepalive: 풀에 쉬고 있는 커넥션이 중간 장비(NAT/LB)에서 조용히
        # 끊기는 것을 줄인다 — ConnectionClosedError 완화(근본 해결은 재시도).
        config=Config(read_timeout=120, retries={"max_attempts": 0},
                      tcp_keepalive=True),
    )


def invoke(tier: ModelTier, messages: list[dict], system: str | None = None,
           max_tokens: int = 1024, output_schema: dict | None = None):
    body: dict = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if system:
        body["system"] = system
    if output_schema:
        body["output_config"] = {
            "format": {"type": "json_schema",
                       "schema": _prepare_schema(output_schema)}}

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            start = time.monotonic()
            resp = _client().invoke_model(
                modelId=_MODEL_IDS[tier], body=json.dumps(body))
            payload = json.loads(resp["body"].read())
            text = next((b["text"] for b in payload["content"]
                        if b["type"] == "text"), None)
            if text is None:
                raise ValueError(
                    "Bedrock 응답에 text 블록이 없습니다 (content types=%s)"
                    % [b.get("type") for b in payload["content"]])

            # LLM 사용량 계측 (L1, 2026-07-31). Bedrock 응답의 usage를 그동안
            # 버리고 있었다. latency는 성공한 이 호출의 순수 지연이다(재시도
            # 대기 제외 — start가 attempt마다 갱신되므로). 없어도 죽지 않는다.
            usage = payload.get("usage") or {}
            ms = (time.monotonic() - start) * 1000
            logger.info(
                "llm_call tier=%s latency_ms=%.0f tokens_in=%s tokens_out=%s "
                "attempt=%d", tier, ms, usage.get("input_tokens"),
                usage.get("output_tokens"), attempt,
                extra={"event": "llm_call", "tier": str(tier),
                       "latency_ms": round(ms),
                       "tokens_in": usage.get("input_tokens"),
                       "tokens_out": usage.get("output_tokens"),
                       "attempt": attempt})
            add_turn_metric("llm_calls", 1)
            add_turn_metric("tokens_in", int(usage.get("input_tokens") or 0))
            add_turn_metric("tokens_out", int(usage.get("output_tokens") or 0))
            return json.loads(text) if output_schema else text
        except (ClientError, BotoConnectionError, HTTPClientError) as e:
            ms = (time.monotonic() - start) * 1000
            if isinstance(e, ClientError):
                code = e.response["Error"]["Code"]
                if code not in _RETRYABLE:
                    raise
            else:
                # 연결 계열(ConnectionClosedError·ReadTimeoutError 등)은 전부
                # 일시 장애로 보고 재시도한다. 클래스명이 곧 로그의 code.
                code = type(e).__name__
            if attempt == _MAX_ATTEMPTS:
                logger.warning("llm retry exhausted attempts=%d code=%s",
                               attempt, code,
                               extra={"event": "llm_retry_exhausted",
                                      "tier": str(tier), "code": code,
                                      "attempt": attempt})
                raise
            delay = min(2 ** attempt + random.random(), 20)
            logger.warning("llm retry attempt=%d code=%s latency_ms=%.0f "
                           "next_delay_s=%.1f", attempt, code, ms, delay,
                           extra={"event": "llm_retry", "tier": str(tier),
                                  "code": code, "attempt": attempt,
                                  "latency_ms": round(ms)})
            time.sleep(delay)


if __name__ == "__main__":               # python -m agents.llm — 실호출 스모크
    logging.basicConfig(level=logging.INFO)
    for tier in ModelTier:
        t0 = time.monotonic()
        out = invoke(tier, [{"role": "user", "content": "한 단어로 인사"}],
                     max_tokens=50)
        logger.info("smoke tier=%s latency_ms=%.0f ok=%s",
                    tier, (time.monotonic() - t0) * 1000, bool(out))
    from agents.schemas import QueryIntent
    out = invoke(ModelTier.ROUTER, [{"role": "user", "content": "인사해"}],
                 output_schema=QueryIntent.model_json_schema(), max_tokens=100)
    logger.info("smoke structured ok=%s", isinstance(out, dict))
