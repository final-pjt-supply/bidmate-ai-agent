"""멀티턴 필터 병합 — 결정적, LLM 없음 (스펙 §병합 규칙)."""
from agents.schemas import AgentRequest, Filters


def _set_fields(f) -> dict:
    return {k: v for k, v in f.model_dump().items() if v is not None}


def resolve_filters(req: AgentRequest, *,
                    new_filters: Filters | None = None,
                    scope: str = "new",
                    entry_bid_scope: str = "keep") -> dict:
    """이번 턴에 쓸 필터와 공고 스코프(bid_ids)를 확정한다.

    세 인자는 원래 Router가 LLM으로 판단해 QueryIntent에 실어 보냈던 값이다.
    지금 Router는 경로만 정하고 승계를 판단하지 않으므로(ADR 0007) 기본값
    — 새 화제, 진입 공고 유지 — 으로 호출된다. 그래서 실제로 도달하는 것은
    1·2번 분기뿐이고, 3·4번은 승계 판단이 돌아오면 다시 살아난다.
    """
    new = _set_fields(new_filters or Filters())
    ctx = req.session_context

    # 1. entry_context가 기본 주체 — 명시적으로 이탈(leave)하면 예외.
    #    entry_bid는 백엔드가 주입하는 외부 입력이므로 공백을 걷어낸다. " "는
    #    파이썬에서 truthy라, 그대로 두면 "공고를 구했다"로 판정되어 뒤 노드가
    #    빈 스코프로 검색·판정하게 된다(결과 0건).
    entry_bid = (req.entry_context.bid_id or "").strip()
    if entry_bid and entry_bid_scope == "keep":
        return {**new, "bid_ids": [entry_bid]}

    # 2. 화제 전환 — 이전 맥락(pending 포함)을 통째로 버린다
    if scope == "new" or ctx is None:
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
