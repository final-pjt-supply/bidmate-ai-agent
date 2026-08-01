"""진입점 — 프레임워크 비의존 순수 함수. 백엔드 호출 방식 확정 전까지 유지."""
import logging
import time
from functools import lru_cache

from agents.logging_util import log_turn_end, new_request_id

from agents.graph import build_graph
from agents.nodes.eligibility import eligibility_node
from agents.nodes.respond import build_summary, respond_node
from agents.nodes.retrieval import retrieval_node
from agents.nodes.router import router_node
from agents.schemas import (AgentRequest, AgentResponse, Filters,
                            SessionContext)

logger = logging.getLogger(__name__)

_MAX_BID_IDS = 20

# 기억할 턴 수. 사람이 한 세션에서 거슬러 참조하는 범위가 대체로 이 안이고,
# 프롬프트 비용이 무시할 수준(약 500토큰)에서 유지된다.
_MAX_RECENT_TURNS = 10


def _push_turn(prev: SessionContext | None, summary: str) -> list[str]:
    """이번 턴 요약을 기록에 밀어 넣고 최근 _MAX_RECENT_TURNS건만 남긴다.

    `answer` 턴에서만 부른다. 기타·redirect 턴은 기록을 남기지 않고 이전 값을
    그대로 통과시킨다 — 업무 밖 한 턴이 진행 중인 대화를 끊지 않게 하기 위함이고,
    이는 last_summary가 이미 따르던 규칙이다. 두 값이 같은 분기에서 함께
    움직이므로 recent_turns[-1] == last_summary 불변식이 유지된다.
    """
    history = list(prev.recent_turns) if prev else []
    history.append(summary)
    return history[-_MAX_RECENT_TURNS:]


def _warn_if_context_degraded(ctx: SessionContext | None) -> None:
    """백엔드가 recent_turns를 버리고 있는지 감지한다.

    백엔드는 agents.schemas를 pip 패키지로 공유한다. 구버전을 물고 있으면
    pydantic이 extra="ignore" 기본값으로 이 필드를 **조용히 버린다** — 예외도
    로그도 없이 기억만 1턴으로 되돌아간다. 배포 순서가 어긋났을 때 드러나는
    유일한 신호다.

    조건에 last_bid_ids를 함께 쓰는 이유는 오탐 때문이다. last_summary만 보면
    "첫 턴이 기타였던 정상 상황"(clarify 분기가 문구만 채우고 recent_turns는
    비운다)이 매번 걸린다. 공고를 다룬 answer 턴은 반드시 recent_turns도
    채우므로, 아래 조합은 백엔드가 버린 경우에만 성립한다.

    공고를 못 찾은 answer 턴(last_bid_ids == [])은 한 번 놓치지만 다음 정상
    턴에서 잡힌다 — 오탐이 반복되면 경고 자체를 무시하게 되므로 미탐을 택한다.
    """
    if ctx and ctx.last_bid_ids and not ctx.recent_turns:
        logger.warning(
            "recent_turns가 비어 있는데 last_bid_ids는 있다 — 백엔드가 필드를 "
            "버리고 있을 수 있다(agents.schemas 패키지 버전 확인 필요). "
            "멀티턴 기억이 1턴으로 동작한다.")

# route → 사용자에게 돌려줄 행동. 계약(AgentResponse.action)의 세 값 중 하나다.
# redirect는 아직 어느 route도 내지 않는다 — '추천'(조건만 있는 질의 → 추천
# 화면 이동) 라우트가 추가되면 그 매핑이 여기 들어온다.
_ROUTE_ACTION: dict[str, str] = {
    "검색": "answer",
    "상세": "answer",
    "자격": "answer",
    "기타": "clarify",
}

# 아래 두 문구는 그래프가 노드를 타지 않고 빠진 경우다(graph.py). 답변 문자열이
# 없는 상태이고, 신호 없이 LLM에 맡기면 없는 공고를 지어내므로 코드가 정한다.
#
# route=자격인데 판정할 대상이 없을 때. 두 상황을 갈라야 한다 — 같은 문장을
# 쓰면 "판정 데이터가 없다"가 "자격 미달"로 읽힌다.
#   목록 질의   자격이 되는 공고가 실제로 하나도 없음
#   특정 공고   그 공고의 판정이 없음(대개 마감돼 계산 대상에서 빠진 경우)
_NO_ELIGIBLE = "지금 자격 요건을 충족하는 공고가 없습니다."
_NO_VERDICT = ("이 공고의 자격 판정 정보를 찾지 못했습니다. "
               "마감된 공고는 판정 대상에서 제외됩니다.")
# 검색·상세인데 bid_search가 공고를 하나도 특정하지 못했을 때.
_NOT_FOUND = ("질문에 해당하는 공고를 찾지 못했습니다. "
              "공고명이나 조건을 조금 더 알려주시면 다시 찾아보겠습니다.")

# 업무 밖 질의(route=기타)에 돌려줄 문구. LLM이 쓰게 하지 않고 코드가 고정한다 —
# 질의를 통한 프롬프트 인젝션이 성공해도 이 경로로는 한 글자도 새 나갈 수 없다.
OUT_OF_SCOPE = ("입찰 공고 검색과 공고 내용 안내를 도와드릴 수 있습니다. "
                "찾으시는 공고나 조건을 알려주세요.")


@lru_cache(maxsize=1)
def _graph():
    # retrieval(C)·eligibility(B) 실구현 배선. scoring(B)은 배선하지 않는다
    # (graph.build_graph 참조 — 스텁이 가짜 점수를 만든다).
    return build_graph(router_node, respond_node,
                       eligibility_node=eligibility_node,
                       retrieval_node=retrieval_node)


def _initial_state(req: AgentRequest) -> dict:
    return {"query": req.query, "original_query": req.query,
            "company_id": req.company_id,
            "entry_context": req.entry_context,
            "session_context": req.session_context,
            "route": None, "resolved_filters": None, "bid_briefs": [],
            "eligible_total": 0,
            "eligibility": [], "chunks": [], "bid_names": {}, "scores": [],
            "answer": None, "citations": []}


def run_agent(req: AgentRequest) -> AgentResponse:
    _warn_if_context_degraded(req.session_context)
    # 턴 상관 ID 발급 + 메트릭 초기화 (L1). 이후 이 턴의 모든 로그(노드·LLM·
    # 도구)에 request_id가 자동으로 붙는다 — 한 턴을 로그에서 묶는 열쇠다.
    new_request_id()
    t0 = time.monotonic()
    result = _graph().invoke(_initial_state(req))
    action = _ROUTE_ACTION[result["route"]]
    resolved = Filters(**(result["resolved_filters"] or {}))
    # 스코프(bid_ids)는 필터가 아니다 — 컨텍스트로 저장 시 항상 뗀다.
    # 안 떼면 다음 턴 병합 베이스(prev)에 섞여 스코프 해제가 깨진다.
    storable = resolved.model_copy(update={"bid_ids": None})

    if action == "clarify":
        # route=기타 — 업무 밖 질의다. 되물은 것이 아니므로 pending을 만들지
        # 않는다(만들면 다음 턴 라우터 컨텍스트에 "직전 턴에 되물었음. 원 질의:
        # 점심 뭐 먹을까?"가 실려 분류를 흐린다). 직전 맥락은 그대로 통과시켜,
        # 업무 밖 한 턴이 진행 중인 대화를 끊지 않게 한다.
        prev = req.session_context
        ctx = SessionContext(
            last_bid_ids=prev.last_bid_ids if prev else [],
            last_summary=prev.last_summary if prev else "업무 밖 질의",
            recent_turns=prev.recent_turns if prev else [],
            last_filters=prev.last_filters if prev else Filters(),
            pending=None,
        )
        log_turn_end(route=result["route"], action="clarify",
                     result="out_of_scope",
                     total_ms=round((time.monotonic() - t0) * 1000))
        return AgentResponse(action="clarify", clarify_message=OUT_OF_SCOPE,
                             session_context=ctx)

    if action == "redirect":
        prev = req.session_context
        ctx = SessionContext(
            last_bid_ids=prev.last_bid_ids if prev else [],
            last_summary=prev.last_summary if prev else "추천 화면 이동",
            recent_turns=prev.recent_turns if prev else [],
            last_filters=prev.last_filters if prev else Filters(),
            pending=None,                    # pending은 항상 리셋
        )
        log_turn_end(route=result["route"], action="redirect",
                     result="redirect",
                     total_ms=round((time.monotonic() - t0) * 1000))
        return AgentResponse(action="redirect", redirect_filters=resolved,
                             session_context=ctx)

    # answer
    #
    # 이번 턴에 다룬 공고. bid_briefs를 빼면 `검색` 턴이 안내한 공고가 다음 턴에
    # 승계되지 않는다 — 그 갈래는 청크도 판정도 만들지 않기 때문이다.
    bids = list(dict.fromkeys(
        [r.bid_id for r in result["eligibility"]] +
        [c.bid_id for c in result["chunks"]] +
        [b.bid_id for b in result["bid_briefs"]]))[:_MAX_BID_IDS]

    # "승계했는데 전부 사라짐(stale)" 분기는 없앴다. 판단 근거가 될 수 있는 것은
    # "이번 턴이 직전 턴의 공고를 승계했다"인데, 지금 그런 승계가 없다(scope가
    # 항상 새로 정한다). session_context.last_bid_ids로 대신 재면 "직전 턴이
    # 공고를 안내했다"를 재는 것이라, 새 검색이 0건일 때 엉뚱하게 "이전에 보신
    # 공고 중 …"이 나간다. 승계를 넣을 때 scope가 표시를 남기며 되살릴 자리다.
    if result["route"] == "자격" and not result["eligibility"]:
        # 판정 대상이 없다. 진입 공고가 있었다면 "그 공고 판정을 못 구했다"이고,
        # 없었다면 "자격 되는 공고가 없다"이다.
        if req.entry_context.bid_id:
            answer, result_code = _NO_VERDICT, "no_verdict"
        else:
            answer, result_code = _NO_ELIGIBLE, "no_eligible"
    elif result["answer"] is None:
        answer, result_code = _NOT_FOUND, "not_found"   # 공고 특정 실패 — 노드 안 탐
    else:
        answer, result_code = result["answer"], "answer"

    summary = build_summary(result)
    ctx = SessionContext(last_bid_ids=bids,
                         last_summary=summary,
                         recent_turns=_push_turn(req.session_context, summary),
                         last_filters=storable, pending=None)
    log_turn_end(route=result["route"], action="answer", result=result_code,
                 total_ms=round((time.monotonic() - t0) * 1000),
                 bids_shown=len(bids))
    return AgentResponse(action="answer", answer=answer,
                         citations=result["citations"], session_context=ctx)
