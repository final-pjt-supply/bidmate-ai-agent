"""LangGraph 상태 — A 단독 소유. 각 노드는 자기 슬롯만 쓴다(스펙 §AgentState)."""
from typing import TypedDict

from agents.schemas import (Chunk, Citation, EligibilityResult, EntryContext,
                            MatchScore, QueryIntent, SessionContext)


class AgentState(TypedDict):
    # 입력 (백엔드가 주입)
    query: str
    company_id: str
    entry_context: EntryContext
    session_context: SessionContext | None

    # [0] Router 산출 (resolved_filters는 merge.py 결과 — 스펙 보완 슬롯)
    intent: QueryIntent | None
    resolved_filters: dict | None

    # [1] B / [2] C 산출 (현재 스텁)
    eligibility: list[EligibilityResult]
    chunks: list[Chunk]

    # [3a] B 산출 (현재 스텁)
    scores: list[MatchScore]

    # [4] 산출
    answer: str | None
    citations: list[Citation]
