"""질의 임베딩 클라이언트.

색인(bid_chunks)이 Cloudflare Workers AI의 @cf/baai/bge-m3로 만들어졌으므로,
질의도 반드시 동일한 모델로 임베딩해야 벡터 공간이 맞는다.

이 파일은 '갈아끼울 수 있는 껍질'이다. 나중에 자체 GPU/다른 API로 바꾸려면
embed_query 함수의 내부 구현만 교체하면 되고, 호출부(search.py)는 그대로 둔다.
"""
from __future__ import annotations

import time

import httpx

from agents.config import get_settings

# 일시적 오류로 보고 재시도할 HTTP 상태 코드
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class EmbeddingError(RuntimeError):
    """임베딩 호출 실패."""


def _extract_vector(payload: dict) -> list[float]:
    """Cloudflare 응답에서 벡터를 꺼낸다.

    bge-m3 응답 형태: {"result": {"data": [[...1024개...]], "shape": [1,1024]}, "success": true}
    """
    if not payload.get("success", False):
        raise EmbeddingError(f"Cloudflare 응답 실패: {payload.get('errors')}")
    data = payload.get("result", {}).get("data")
    if not data or not isinstance(data, list):
        raise EmbeddingError(f"예상치 못한 응답 구조: {payload}")
    vec = data[0]
    if len(vec) != 1024:
        raise EmbeddingError(f"차원 불일치: {len(vec)} (1024 기대)")
    return vec


def embed_query(
    text: str,
    *,
    timeout: float = 10.0,
    max_retries: int = 3,
    base_delay: float = 1.0,
) -> list[float]:
    """질의 텍스트 1건을 1024차원 벡터로 임베딩한다.

    일시적 오류(429, 5xx)는 지수 백오프로 재시도한다.

    Args:
        text: 임베딩할 질의 문자열.
        timeout: HTTP 타임아웃(초).
        max_retries: 최대 재시도 횟수.
        base_delay: 첫 재시도 대기 시간(초). 이후 2배씩 증가.

    Returns:
        1024개 float로 이루어진 리스트.
    """
    text = (text or "").strip()
    if not text:
        raise EmbeddingError("빈 문자열은 임베딩할 수 없다.")

    s = get_settings()
    headers = {"Authorization": f"Bearer {s.cf_api_token}"}
    body = {"text": [text]}

    last_exc: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            resp = httpx.post(s.cf_embed_url, headers=headers, json=body, timeout=timeout)

            # 재시도 가능한 상태면 대기 후 재시도
            if resp.status_code in _RETRYABLE_STATUS and attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                # Cloudflare가 Retry-After 헤더를 주면 그 값을 우선한다
                retry_after = resp.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    delay = float(retry_after)
                print(
                    f"  [임베딩] {resp.status_code} 응답, {delay:.0f}초 후 재시도 "
                    f"({attempt + 1}/{max_retries})"
                )
                time.sleep(delay)
                continue

            resp.raise_for_status()
            return _extract_vector(resp.json())

        except httpx.HTTPError as exc:
            last_exc = exc
            # 연결 오류 등도 남은 재시도가 있으면 한 번 더
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                print(f"  [임베딩] 호출 오류, {delay:.0f}초 후 재시도 ({attempt + 1}/{max_retries})")
                time.sleep(delay)
                continue
            break

    raise EmbeddingError(f"Cloudflare 호출 실패 (재시도 {max_retries}회 소진): {last_exc}") from last_exc