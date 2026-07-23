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

    # --- 공고 단위 집계 ---
    # "max": 최고 청크 점수만 / "sum_topn": 상위 N개 청크 합산
    aggregate: str = Field("sum_topn", alias="AGGREGATE")
    sum_top_n: int = Field(5, alias="SUM_TOP_N")
    # --- 하한선 (0이면 미적용) ---
    # 절대 점수 하한 — 검색이 완전히 실패했을 때의 최후 방어선.
    # 조건형 질의("오늘 마감인 공고" 등)는 Router가 redirect로 거르는 것이
    # 설계 의도이므로, C가 그것까지 막으려고 임계값을 올리지 않는다.
    # 실측: 무의미한 입력("안녕하세요")이 0.815 → 그 위인 0.9로 둔다.
    # 1.0 이상으로 올리면 관련 있는 결과까지 잘린다(전기공사 1.04/1.01 손실).
    min_score: float = Field(0.9, alias="MIN_SCORE")
    # 1위 점수 대비 비율 하한 (0.4 = 1위의 40% 미만 제외).
    # 절대값과 달리 질의별 스케일 차이에 영향받지 않는다.
    min_score_ratio: float = Field(0.4, alias="MIN_SCORE_RATIO")
    # 걸린 청크가 이 수 미만이면 제외. 무관 공고는 대개 1~2개만 걸린다.
    min_chunks: int = Field(2, alias="MIN_CHUNKS")
    # 하한선 적용 후에도 최소 이 개수는 남긴다. 기본 0(안전장치 없음) —
    # 관련 없는 질의에 억지로 결과를 만들어 보여주지 않기 위함이다.
    # "관련 공고를 찾지 못했습니다"가 무관한 공고를 보여주는 것보다 낫다.
    min_results: int = Field(0, alias="MIN_RESULTS")

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