"""진입점 — 프레임워크 비의존 순수 함수. 백엔드 호출 방식 확정 전까지 유지."""
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

_MAX_BID_IDS = 20

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
    return {"query": req.query, "company_id": req.company_id,
            "entry_context": req.entry_context,
            "session_context": req.session_context,
            "route": None, "resolved_filters": None, "bid_briefs": [],
            "eligible_total": 0,
            "eligibility": [], "chunks": [], "bid_names": {}, "scores": [],
            "answer": None, "citations": []}


def run_agent(req: AgentRequest) -> AgentResponse:
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
            last_summary=prev.last_summary if prev else "직전 턴: 업무 밖 질의",
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
            last_summary=prev.last_summary if prev else "직전 턴: 추천 화면 이동",
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

    ctx = SessionContext(last_bid_ids=bids,
                         last_summary=build_summary(result),
                         last_filters=storable, pending=None)
    log_turn_end(route=result["route"], action="answer", result=result_code,
                 total_ms=round((time.monotonic() - t0) * 1000),
                 bids_shown=len(bids))
    return AgentResponse(action="answer", answer=answer,
                         citations=result["citations"], session_context=ctx)
