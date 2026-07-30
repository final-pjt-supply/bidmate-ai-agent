"""LangGraph 상태 — A 단독 소유. 각 노드는 자기 슬롯만 쓴다(스펙 §AgentState)."""
from typing import Literal, TypedDict

from pydantic import BaseModel

from agents.schemas import (Chunk, Citation, EligibilityResult, EntryContext,
                            MatchScore, SessionContext)

# Router가 정하는 갈래. graph.py가 이 값으로 분기한다.
Route = Literal["검색", "상세", "자격", "기타"]


class BidBrief(BaseModel):
    """공고 한 건의 요약 — bid_search가 채우고 respond가 신호로 읽는다.

    상태 내부 전용이다(팀 계약 schemas.py 아님). `검색` 갈래는 공고 문서 발췌가
    아니라 "이런 공고들이 있습니다"로 답하므로, 청크 대신 bid_table에서 읽은
    이 요약이 답변의 근거가 된다. 값은 전부 bid_table 원문이며 노드가 계산하지
    않는다 — 없으면 빈 문자열로 두고 respond가 그 항목을 빼고 렌더한다.
    """
    bid_id: str
    name: str = ""              # bid_ntce_nm
    institution: str = ""       # 수요기관(dminstt_nm) 없으면 공고기관
    close_at: str = ""          # bid_clse_dt — 표시용 문자열
    price: str = ""             # presmpt_prce — 표시용 문자열
    method: str = ""            # 계약 체결 방법(cntrct_cncls_mthd_nm)


class AgentState(TypedDict):
    # 입력 (백엔드가 주입)
    query: str
    company_id: str
    entry_context: EntryContext
    session_context: SessionContext | None

    # [0] Router 산출 — 갈래 한 단어뿐이다.
    #
    # 계약(schemas.QueryIntent)을 상태에 싣지 않는 이유: Router가 내는 것은
    # 한 단어라, 7필드 모델을 채우면 나머지 필드에 판단하지 않은 값이 실려
    # "LLM이 정한 값"처럼 보인다. QueryIntent는 백엔드로 나가지 않는 내부
    # 객체이므로 상태에서 뺀다(ADR 0007).
    route: Route | None

    # [1] scope / [2] bid_search 산출 — 어느 공고를 볼지.
    #   resolved_filters["bid_ids"]가 뒤 노드의 작업 범위다.
    resolved_filters: dict | None
    # bid_search가 찾은 공고 요약. `검색` 갈래에서 답변의 근거가 된다.
    bid_briefs: list[BidBrief]

    # [3] B / [4] C 산출 (현재 스텁)
    eligibility: list[EligibilityResult]
    chunks: list[Chunk]
    # bid_id → 공고명. 답변에서 공고를 사용자 친화적으로 부르기 위한 맵.
    # C(retrieval)가 채우고 respond가 읽는다. 조회 실패 시 빈 dict — respond는
    # 이름이 없으면 bid_id로 폴백하므로 없어도 동작한다.
    bid_names: dict[str, str]

    # [3a] B 산출 — 아직 배선되지 않았다(graph.py 참조). 항상 빈 리스트다.
    scores: list[MatchScore]

    # [5] 산출
    answer: str | None
    citations: list[Citation]
