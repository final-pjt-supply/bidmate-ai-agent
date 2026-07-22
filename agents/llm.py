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
from botocore.exceptions import ClientError, ReadTimeoutError
from dotenv import load_dotenv

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
        config=Config(read_timeout=120, retries={"max_attempts": 0}),
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
            return json.loads(text) if output_schema else text
        except (ClientError, ReadTimeoutError) as e:
            ms = (time.monotonic() - start) * 1000
            if isinstance(e, ClientError):
                code = e.response["Error"]["Code"]
                if code not in _RETRYABLE:
                    raise
            else:
                code = "ReadTimeoutError"
            if attempt == _MAX_ATTEMPTS:
                logger.warning("llm retry exhausted attempts=%d code=%s",
                               attempt, code)
                raise
            delay = min(2 ** attempt + random.random(), 20)
            logger.warning("llm retry attempt=%d code=%s latency_ms=%.0f "
                           "next_delay_s=%.1f", attempt, code, ms, delay)
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
