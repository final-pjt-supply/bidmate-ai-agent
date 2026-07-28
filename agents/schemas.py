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


class AxisResult(BaseModel):
    """9축 체크리스트 한 줄. 미달 축뿐 아니라 충족 축도 그대로 싣는다.

    failed_reasons는 "왜 안 되는가"만 담아서, 화면이 "무엇을 확인했는가"를
    보여줄 수 없다. 충족 축이 안 내려가면 '가능' 판정은 근거 없는 결론으로
    보이고, 회원은 자기가 이미 채운 조건을 확인할 수 없다.

    class(gate/supp/info)는 파이썬 예약어라 axis_class로 받는다.
    """
    axis: str
    axis_class: Literal["gate", "supp", "info"]
    status: Literal["충족", "미충족", "확인필요"]
    required: str = ""   # 공고가 요구하는 값
    actual: str = ""     # 회원이 보유한 값
    detail: str = ""     # 사람이 읽는 한 줄 (하위호환)


class EligibilityResult(BaseModel):
    bid_id: str
    passed: bool
    # 게이트 판정 원문(4-state). passed(bool)로는 '보완가능'과 '불가'가
    # 똑같이 False로 뭉개져 화면에서 구분이 안 된다 — 원문을 같이 싣는다.
    # 기본값 None이라 기존 stub·호출부는 무변경으로 그대로 동작한다.
    verdict: Literal["가능", "불가", "보완가능", "확인필요"] | None = None
    failed_reasons: list[FailedReason] = []

    # ↓ 9축 체크리스트용. 전부 기본값이 있어 기존 호출부는 무변경으로 동작한다.
    #   failed_reasons는 respond.py(A 소유)가 쓰므로 그대로 둔다 — 대체가 아니라 추가다.
    axes: list[AxisResult] = []
    required_count: int = 0    # 판정에 참여한 축 수 (info 제외)
    satisfied_count: int = 0
    need_review_count: int = 0


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
