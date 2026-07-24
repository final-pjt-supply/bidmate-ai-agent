"""C 파트 내부 타입 — 팀 공용 계약(schemas.py)이 아닌 검색 도구 내부용.

공용 schemas.py의 Chunk에는 score가 없다(순수 데이터). 검색이 만들어내는
점수·집계 정보는 여기서 Chunk를 감싸 전달한다. 공용 계약을 오염시키지 않기
위한 분리이며, 팀 합의로 승격이 필요해지면 schemas.py로 옮긴다.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from agents.schemas import Chunk, Citation


class SearchHit(BaseModel):
    """검색으로 반환된 청크 1건 + 점수."""

    chunk: Chunk
    score: float = Field(..., description="하이브리드 검색 점수")

    @property
    def bid_id(self) -> str:
        return self.chunk.bid_id

    def to_citation(self) -> Citation:
        """응답 생성(A)에 넘길 인용으로 변환. 원문을 그대로 재사용한다."""
        return Citation(
            bid_id=self.chunk.bid_id,
            file_id=self.chunk.file_id,
            chunk_idx=self.chunk.chunk_idx,
            text=self.chunk.text,
        )


class SearchResult(BaseModel):
    """검색 1회의 결과 묶음 (청크 단위, RAG용)."""

    query: str
    hits: list[SearchHit] = Field(default_factory=list, description="점수 내림차순")
    total_hits: int = 0

    def citations(self) -> list[Citation]:
        return [h.to_citation() for h in self.hits]


class BidHit(BaseModel):
    """공고 1건 (청크들을 공고 단위로 집계). 추천 목록용.

    score는 집계 방식(aggregate)에 따라 의미가 다르다.
      "max"    최고 청크 점수 하나 (단일 청크만 봄)
      "sum_topn" 상위 N개 청크 점수 합 (여러 청크에 걸친 관련도 반영)
    """

    bid_id: str
    score: float = Field(..., description="집계된 공고 점수")
    top_hit: SearchHit = Field(..., description="가장 점수 높은 대표 청크")
    matched_chunks: int = Field(1, description="이 공고에서 검색에 걸린 청크 수")
    max_score: float = Field(0.0, description="최고 청크 점수 (집계 방식과 무관)")
    summed_chunks: int = Field(0, description="합산에 실제 사용된 청크 수")
    document_ids: list[str] = Field(
        default_factory=list, description="걸린 청크가 속한 문서 종류")


class BidSearchResult(BaseModel):
    """공고 단위 검색 결과 묶음."""

    query: str
    bids: list[BidHit] = Field(default_factory=list)
    total_hits: int = 0


class BidInfo(BaseModel):
    """bid_table(PostgreSQL)에서 조회한 공고 메타 정보."""

    bid_id: str
    bid_ntce_no: str
    bid_ntce_ord: str

    bid_ntce_nm: str | None = None
    bid_category: str | None = None
    ntce_instt_nm: str | None = None
    dminstt_nm: str | None = None

    bid_ntce_dt: datetime | None = None
    bid_clse_dt: datetime | None = None
    openg_dt: datetime | None = None

    presmpt_prce: int | None = None
    bdgt_amt: int | None = None

    cntrct_cncls_mthd_nm: str | None = None
    sucsfbid_mthd_nm: str | None = None
    re_ntce_yn: bool | None = None
    bid_ntce_dtl_url: str | None = None


class RecommendedBid(BaseModel):
    """검색 결과 + 공고 정보를 합친 추천 목록 1건."""

    hit: BidHit
    info: BidInfo | None = None