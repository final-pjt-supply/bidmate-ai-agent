"""Bedrock 호출 래퍼 — 티어 스위칭·재시도. 모델 ID는 여기에만 둔다."""
import json
import logging
import os
import random
import time
from enum import StrEnum
from functools import lru_cache

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
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
            "format": {"type": "json_schema", "schema": output_schema}}

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            start = time.monotonic()
            resp = _client().invoke_model(
                modelId=_MODEL_IDS[tier], body=json.dumps(body))
            payload = json.loads(resp["body"].read())
            text = next(b["text"] for b in payload["content"]
                        if b["type"] == "text")
            return json.loads(text) if output_schema else text
        except ClientError as e:
            code = e.response["Error"]["Code"]
            ms = (time.monotonic() - start) * 1000
            if code not in _RETRYABLE or attempt == _MAX_ATTEMPTS:
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
    schema = {"type": "object", "additionalProperties": False,
              "required": ["greeting"],
              "properties": {"greeting": {"type": "string"}}}
    out = invoke(ModelTier.ROUTER, [{"role": "user", "content": "인사해"}],
                 output_schema=schema, max_tokens=100)
    logger.info("smoke structured ok=%s", isinstance(out, dict))
