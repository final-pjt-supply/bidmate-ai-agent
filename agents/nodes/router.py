"""[0] Router — 질의를 어느 갈래로 보낼지만 정한다(ADR 0007).

LLM이 내는 것은 경로 한 단어뿐이고, 상태에 싣는 것도 route 하나다. 검색 문장을
다듬거나 조건을 뽑거나 어느 공고인지 정하는 일은 하지 않는다.

    검색 → bid_search                      (어떤 공고가 있는지 — 목록 안내)
    상세 → scope → (필요하면 bid_search) → retrieval   (정해진 공고의 내용)
    자격 → scope → eligibility             (우리 회사가 되는지)
    기타 → 노드 없음 (graph.py가 END로 빼고 run.py가 안내 문구로 답한다)
"""
import logging
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from agents import llm
from agents.llm import ModelTier
from agents.logging_util import node_logger

logger = logging.getLogger(__name__)

_PROMPT = (Path(__file__).parent.parent / "prompts" / "router.md").read_text(
    encoding="utf-8")


class RouteDecision(BaseModel):
    """라우터가 LLM에서 받는 것 — 경로 한 단어뿐. 팀 계약(schemas.py) 아님."""
    route: Literal["검색", "상세", "자격", "기타"]


def _context_block(state: dict) -> str:
    ctx = state["session_context"]
    lines = []
    if state["entry_context"].bid_id:
        lines.append(f"entry_bid: {state['entry_context'].bid_id} "
                     "(특정 공고 화면에서 진입)")
    if ctx:
        lines.append(f"직전 턴 요약: {ctx.last_summary}")
        if ctx.pending:
            lines.append(f"직전 턴에 되물었음. 원 질의: "
                         f"\"{ctx.pending.original_query}\"")
    return "\n".join(lines) or "(첫 턴 — 직전 맥락 없음)"


def classify(state: dict) -> RouteDecision:
    """질의를 경로 한 단어로 분류한다 (LLM 1회, DB 미접근).

    노드 배선과 분리해 둔 이유는 분기 정확도만 따로 측정할 수 있게 하기
    위함이다 — for_test/eval_router.py가 이 함수만 부른다.
    """
    prompt = _PROMPT.format(context_block=_context_block(state),
                            query=state["query"])
    raw = llm.invoke(
        ModelTier.ROUTER,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=64,
        output_schema=RouteDecision.model_json_schema(),
    )
    return RouteDecision.model_validate(raw)


@node_logger("router")
def router_node(state: dict) -> dict:
    """갈래만 정한다. 어느 공고인지는 scope 노드가, 검색 문장은 작업 노드가 정한다."""
    decision = classify(state)
    logger.info("router route=%s", decision.route)
    return {"route": decision.route}
