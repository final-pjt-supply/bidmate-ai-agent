"""공통 스키마 (Pydantic v2).

계획상 이 파일은 3인 공통 소유이며 변경 시 팀 승인이 필요하다.
여기에는 C 파트의 검색 관련 타입만 정의한다.
B의 EligibilityResult / MatchScore, A의 QueryIntent 등은 각 담당이 추가한다.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class Chunk(BaseModel):
    """검색으로 반환되는 청크 1건.

    OpenSearch bid_chunks 문서에서 vector를 제외한 필드 + 검색 점수.
    """

    chunk_id: str = Field(..., description="OpenSearch _id, 예: R26BK01630591_000_doc04::0")
    bid_id: str = Field(..., description="공고 식별자, 예: R26BK01630591_000")
    document_id: str = Field(..., description="공고 내 문서 식별자, 예: doc04")
    chunk_idx: int = Field(..., description="문서 내 청크 순번")
    text: str = Field(..., description="청크 원문 (인용 시 이 값을 그대로 재사용)")
    type: str = Field(..., description="청크 종류: text / table / box")
    score: float = Field(..., description="하이브리드 검색 점수")


class SearchResult(BaseModel):
    """검색 1회의 결과 묶음 (청크 단위).

    챗봇 RAG 검색에서 사용. 같은 공고의 여러 청크가 그대로 유지된다.
    """

    query: str = Field(..., description="원본 질의")
    chunks: list[Chunk] = Field(default_factory=list, description="점수 내림차순 정렬")
    total_hits: int = Field(0, description="필터 적용 후 매칭된 전체 문서 수")


class BidHit(BaseModel):
    """공고 1건 (중복 제거 후 대표 청크).

    추천 공고 목록에서 사용. 공고당 최고점 청크 1개가 대표로 남는다.
    """

    bid_id: str = Field(..., description="공고 식별자")
    score: float = Field(..., description="이 공고의 최고 청크 점수")
    top_chunk: Chunk = Field(..., description="가장 점수 높은 대표 청크")
    matched_chunks: int = Field(1, description="이 공고에서 검색에 걸린 청크 수")


class BidSearchResult(BaseModel):
    """공고 단위 검색 결과 묶음.

    추천 공고 목록용. bid_id 기준 중복 제거된 공고 리스트.
    """

    query: str = Field(..., description="원본 질의")
    bids: list[BidHit] = Field(default_factory=list, description="점수 내림차순 공고 목록")
    total_hits: int = Field(0, description="필터 적용 후 매칭된 전체 청크 수")


class BidInfo(BaseModel):
    """bid_table(PostgreSQL)에서 조회한 공고 메타 정보.

    OpenSearch 검색은 bid_id와 청크 원문만 주므로, 사용자에게 보여줄
    공고명·기관·마감일·금액 등은 이 타입으로 채운다.
    """

    bid_id: str = Field(..., description="공고번호_차수 (조회용 파생키, split 금지)")
    bid_ntce_no: str = Field(..., description="공고번호 (PK 성분)")
    bid_ntce_ord: str = Field(..., description="차수 (PK 성분). 정정/재공고 시 증가")

    bid_ntce_nm: str | None = Field(None, description="공고명")
    bid_category: str | None = Field(None, description="cnstwk/servc/thng/frgcpt")
    ntce_instt_nm: str | None = Field(None, description="공고기관명")
    dminstt_nm: str | None = Field(None, description="수요기관명 (실제 사업 주체)")

    bid_ntce_dt: datetime | None = Field(None, description="공고일시")
    bid_clse_dt: datetime | None = Field(None, description="투찰마감일시")
    openg_dt: datetime | None = Field(None, description="개찰일시")

    presmpt_prce: int | None = Field(None, description="추정가격(원)")
    bdgt_amt: int | None = Field(None, description="배정예산(원)")

    cntrct_cncls_mthd_nm: str | None = Field(None, description="계약체결방법명")
    sucsfbid_mthd_nm: str | None = Field(None, description="낙찰자결정방법명")
    re_ntce_yn: bool | None = Field(None, description="재공고 여부")
    bid_ntce_dtl_url: str | None = Field(None, description="나라장터 상세페이지 링크")


class RecommendedBid(BaseModel):
    """검색 결과(BidHit) + 공고 정보(BidInfo)를 합친 추천 목록 1건."""

    hit: BidHit = Field(..., description="검색 점수와 대표 청크")
    info: BidInfo | None = Field(None, description="bid_table 메타. 조회 실패 시 None")