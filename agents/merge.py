"""멀티턴 필터 병합 — 결정적, LLM 없음 (스펙 §병합 규칙)."""
from agents.schemas import AgentRequest, QueryIntent


def _set_fields(f) -> dict:
    return {k: v for k, v in f.model_dump().items() if v is not None}


def resolve_filters(req: AgentRequest, intent: QueryIntent) -> dict:
    new = _set_fields(intent.new_filters)
    ctx = req.session_context

    # 1. entry_context가 기본 주체 — Router가 명시적으로 이탈(leave)하면 예외
    if req.entry_context.bid_id and intent.entry_bid_scope == "keep":
        return {**new, "bid_ids": [req.entry_context.bid_id]}

    # 2. 화제 전환 — 이전 맥락(pending 포함)을 통째로 버린다
    if intent.scope == "new" or ctx is None:
        return new

    # 3. clarify 이어받기 — 되묻기 답변(scope=inherit + pending)
    if ctx.pending is not None:
        return {**_set_fields(ctx.pending.partial_filters), **new}

    # 4. 일반 승계 — 같은 키에 '다른 값'이 들어왔을 때만 스코프 해제
    prev = _set_fields(ctx.last_filters)
    prev.pop("bid_ids", None)   # 스코프는 필터가 아니다 — 오염된 컨텍스트 방어
    merged = {**prev, **new}
    real_override = any(k in prev and prev[k] != v for k, v in new.items())
    if not real_override:
        merged["bid_ids"] = ctx.last_bid_ids
    return merged
