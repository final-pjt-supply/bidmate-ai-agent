"""환경변수 로딩. 비밀값은 .env에서 읽고, 코드에는 하드코딩하지 않는다."""
from __future__ import annotations

import os
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- OpenSearch ---
    opensearch_endpoint: str = Field(..., alias="OPENSEARCH_ENDPOINT")
    opensearch_user: str = Field(..., alias="OPENSEARCH_USER")
    opensearch_password: str = Field(..., alias="OPENSEARCH_PASSWORD")
    opensearch_index: str = Field("bid_chunks", alias="OPENSEARCH_INDEX")
    # AWS VPC 도메인이 자체 인증서를 쓰면 검증을 끌 수 있다. 운영에선 True 권장.
    opensearch_verify_certs: bool = Field(True, alias="OPENSEARCH_VERIFY_CERTS")

    # --- PostgreSQL (bid_table) ---
    pg_host: str = Field(..., alias="PG_HOST")
    pg_port: int = Field(5432, alias="PG_PORT")
    pg_database: str = Field(..., alias="PG_DATABASE")
    pg_user: str = Field(..., alias="PG_USER")
    pg_password: str = Field(..., alias="PG_PASSWORD")

    # --- Cloudflare Workers AI (임베딩) ---
    cf_account_id: str = Field(..., alias="CF_ACCOUNT_ID")
    cf_api_token: str = Field(..., alias="CF_API_TOKEN")
    # 색인과 반드시 동일해야 하는 값. bid_chunks가 이 모델로 색인됨.
    cf_embedding_model: str = Field("@cf/baai/bge-m3", alias="CF_EMBEDDING_MODEL")

    # --- 검색 파라미터 기본값 ---
    default_top_k: int = Field(10, alias="DEFAULT_TOP_K")
    knn_weight: float = Field(0.5, alias="KNN_WEIGHT")      # 벡터 가중치
    bm25_weight: float = Field(0.5, alias="BM25_WEIGHT")    # 키워드 가중치
    # True면 정규화 하이브리드(임시 search pipeline), False면 bool 병합
    use_normalization: bool = Field(True, alias="USE_NORMALIZATION")
    # knn이 뽑을 후보 수 = top_k * 이 배수.
    # 값이 작으면 knn/BM25 두 결과의 겹침이 적어 점수가 0.5에 몰린다
    # (한쪽 목록에만 있는 문서는 다른 쪽 기여가 0이 되기 때문).
    knn_candidate_multiplier: int = Field(20, alias="KNN_CANDIDATE_MULTIPLIER")

    @property
    def pg_dsn(self) -> str:
        return (
            f"postgresql://{self.pg_user}:{self.pg_password}"
            f"@{self.pg_host}:{self.pg_port}/{self.pg_database}"
        )

    @property
    def cf_embed_url(self) -> str:
        return (
            f"https://api.cloudflare.com/client/v4/accounts/"
            f"{self.cf_account_id}/ai/run/{self.cf_embedding_model}"
        )


@lru_cache
def get_settings() -> Settings:
    """설정을 한 번만 읽어 캐시한다."""
    return Settings()  # type: ignore[call-arg]