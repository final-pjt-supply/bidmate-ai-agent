"""[0] Router — 자연어 질의 → QueryIntent. LLM은 자연어 이해만, 병합은 merge.py."""
import logging
from pathlib import Path

from agents import llm, merge
from agents.llm import ModelTier
from agents.logging_util import node_logger
from agents.schemas import AgentRequest, QueryIntent

logger = logging.getLogger(__name__)

_PROMPT = (Path(__file__).parent.parent / "prompts" / "router.md").read_text(
    encoding="utf-8")
_DEFAULT_CLARIFY = "찾으시는 공고의 지역이나 분야를 알려주시겠어요?"


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


@node_logger("router")
def router_node(state: dict) -> dict:
    prompt = _PROMPT.format(context_block=_context_block(state),
                            query=state["query"])
    raw = llm.invoke(
        ModelTier.ROUTER,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=512,
        output_schema=QueryIntent.model_json_schema(),
    )
    intent = QueryIntent.model_validate(raw)

    if intent.action == "clarify" and not intent.clarify_message:
        logger.warning("router fallback: clarify_message 누락 → 기본 문구")
        intent = intent.model_copy(update={"clarify_message": _DEFAULT_CLARIFY})

    req = AgentRequest(query=state["query"], company_id=state["company_id"],
                       entry_context=state["entry_context"],
                       session_context=state["session_context"])
    resolved = merge.resolve_filters(req, intent)

    logger.info("router intent type=%s action=%s scope=%s entry_bid_scope=%s "
                "filter_keys=%s", intent.type, intent.action, intent.scope,
                intent.entry_bid_scope, sorted(resolved.keys()))
    return {"intent": intent, "resolved_filters": resolved}
