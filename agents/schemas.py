"""팀 공용 계약 — 변경 시 3인 승인 (roles.md)."""
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel


class Region(StrEnum):
    SEOUL = "서울"; BUSAN = "부산"; DAEGU = "대구"; INCHEON = "인천"
    GWANGJU = "광주"; DAEJEON = "대전"; ULSAN = "울산"; SEJONG = "세종"
    GYEONGGI = "경기"; GANGWON = "강원"; CHUNGBUK = "충북"; CHUNGNAM = "충남"
    JEONBUK = "전북"; JEONNAM = "전남"; GYEONGBUK = "경북"; GYEONGNAM = "경남"
    JEJU = "제주"



class Filters(BaseModel):
    """전 필드 optional — Router는 '언급된 필터만' 채운다(스펙 §병합 규칙)."""
    region: Region | None = None
    category: str | None = None            # 업종 — 코드 체계는 B와 합의 후 enum화
    deadline_within_days: int | None = None
    budget_min: int | None = None
    budget_max: int | None = None
    bid_ids: list[str] | None = None


class EntryContext(BaseModel):
    bid_id: str | None = None              # 존재하면 Case 2(특정 공고 문맥)


class PendingClarify(BaseModel):
    original_query: str
    partial_filters: Filters


class SessionContext(BaseModel):
    last_bid_ids: list[str]                # 상한 20건 — run.py에서 절단
    last_summary: str
    last_filters: Filters
    pending: PendingClarify | None = None


class QueryIntent(BaseModel):
    type: Literal["eligibility_only", "content_only", "full"]
    action: Literal["answer", "redirect", "clarify"]
    scope: Literal["inherit", "new"]
    entry_bid_scope: Literal["keep", "leave"]
    new_filters: Filters
    normalized_query: str
    clarify_message: str | None = None


class FailedReason(BaseModel):
    field: str
    required: str
    actual: str


class EligibilityResult(BaseModel):
    bid_id: str
    passed: bool
    failed_reasons: list[FailedReason] = []


class Chunk(BaseModel):
    """OpenSearch bid_chunks 실측 필드와 1:1."""
    bid_id: str
    document_id: str
    file_id: str
    chunk_idx: int
    text: str
    type: str


class ScoreItem(BaseModel):
    axis: str                              # 예: "실적 여유율"
    points: float
    note: str                              # 파생값 서술 — 응답 노드는 이걸 렌더만 한다


class MatchScore(BaseModel):
    bid_id: str
    total: float
    breakdown: list[ScoreItem]


class Citation(BaseModel):
    bid_id: str
    file_id: str
    chunk_idx: int
    text: str                              # 청크 원문 재사용 — 생성 금지


class AgentRequest(BaseModel):
    query: str
    company_id: str
    entry_context: EntryContext
    session_context: SessionContext | None = None


class AgentResponse(BaseModel):
    action: Literal["answer", "redirect", "clarify"]
    answer: str | None = None
    clarify_message: str | None = None
    redirect_filters: Filters | None = None
    citations: list[Citation] = []
    session_context: SessionContext
