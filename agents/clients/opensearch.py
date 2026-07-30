"""OpenSearch 클라이언트.

AWS OpenSearch Service(VPC 도메인)에 basic auth로 접속한다.
접속 설정을 한곳에 모아, 검색 로직(search.py)이 접속 세부사항을 몰라도 되게 한다.
"""
from __future__ import annotations

from functools import lru_cache

from opensearchpy import OpenSearch

from agents.config import get_settings


@lru_cache
def get_client() -> OpenSearch:
    """OpenSearch 클라이언트를 한 번만 만들어 재사용한다."""
    s = get_settings()
    return OpenSearch(
        hosts=[s.opensearch_endpoint],
        http_auth=(s.opensearch_user, s.opensearch_password),
        use_ssl=True,
        verify_certs=s.opensearch_verify_certs,
        ssl_show_warn=s.opensearch_verify_certs,
        timeout=10,
        max_retries=1,
        retry_on_timeout=False,
    )
