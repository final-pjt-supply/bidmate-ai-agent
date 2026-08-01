"""[0] 질의 재구성 — 앞 대화를 가리키는 질의를 자기완결 문장으로 바꾼다.

그래프의 진입점이다. 라우터보다 **앞에** 두는 이유:

- 라우터가 자기완결 질의를 보게 되므로 갈래 판단이 쉬워진다. 라우터에
  "이 질의를 지금 분류할 수 있나"라는 성격이 다른 판단을 얹지 않아도 된다
- 재구성된 질의에 사업명이 들어가니 bid_search가 그 공고를 그냥 찾아낸다.
  직전 턴의 bid_ids를 스코프로 물려주는 배관이 필요 없다
- 실패해도 원문 그대로 라우터가 도는 것뿐이라, 최악이 재구성 이전 상태다

재구성 근거는 session_context.recent_turns(최근 10턴 요약)다. respond.build_summary가
공고명과 갈래를 실어 보내고, run.py가 answer 턴마다 쌓는다. 대화 원문을 쌓지 않는
이유는 ADR 0006 — 결정적 템플릿이라 공고 문서의 악성 문구가 다음 턴 프롬프트로
전파되지 않는다. 다만 그 성질은 모든 줄을 에이전트가 만들었을 때만 참이므로,
읽을 때도 sanitize와 길이·턴수 상한을 다시 건다(_history_block 참조).
"""
import logging
from pathlib import Path

from pydantic import BaseModel

from agents import llm
from agents.llm import ModelTier
from agents.logging_util import node_logger
from agents.nodes.respond import sanitize

logger = logging.getLogger(__name__)

_PROMPT = (Path(__file__).parent.parent / "prompts" / "rewrite.md").read_text(
    encoding="utf-8")

# 앞 대화를 가리키는 말. 이 중 하나도 없으면 재구성 노드를 건너뛴다.
#
# 이 규칙은 **호출을 아끼는 필터**일 뿐 정확성을 지지 않는다. 놓치면 재구성을
# 안 할 뿐이라 재구성 이전 상태와 같고, 과잉 발동하면 프롬프트가 원문을 그대로
# 돌려준다. 그래서 애매하면 넣어두는 편이 낫다.
#
# 기억이 10턴이 되면서 거슬러 가리키는 말("처음", "먼저", "전에")을 더했다.
# "전에"는 "안전에" 같은 말에도 걸리지만, 과잉 발동의 대가는 프롬프트가 원문을
# 그대로 돌려주는 것뿐이라 감수한다.
_REFERENTIAL = (
    "그 ", "그거", "그건", "그중", "그 중", "그것", "거기", "저거",
    "방금", "아까", "아깐", "위에", "앞에", "직전", "이전",
    "처음", "먼저", "전에",
    "더 ", "또 ", "다음", "나머지", "번째",
)

# 읽을 때 다시 적용하는 상한. run.py가 쓸 때도 자르지만, 들어온 값이 우리가 쓴
# 값이라는 보장이 없어 여기서 한 번 더 건다(_history_block 참조). run.py의
# 동명 상수와 값이 같은 것은 같은 정책이기 때문이지만, 신뢰 경계가 다르므로
# 한쪽을 import하지 않는다.
_MAX_RECENT_TURNS = 10
_MAX_TURN_CHARS = 200


def _history_block(ctx) -> str:
    """최근 턴 기록을 프롬프트 블록으로 렌더한다.

    가장 최신이 "직전 턴", 그 앞이 "2턴 전"이다. 라벨이 있어야 "아까"가 어디를
    가리키는지 모델이 판단할 근거가 생긴다.

    **유입 방어(ADR 0008).** recent_turns는 백엔드를 거쳐 돌아오는 값이라
    "우리가 쓴 그 값"이라는 보장이 없다. build_summary가 결정적 템플릿이라 2차
    인젝션이 막힌다는 성질은 *모든 줄을 에이전트가 만들었을 때만* 참이다. 그래서
    쓸 때 건 sanitize를 읽을 때 한 번 더 걸고, 줄당 길이와 턴 수도 여기서 다시
    자른다. 줄당 상한은 보안과 무관하게도 필요하다 — 공고명 길이에 상한이 없어
    기록이 무한정 자랄 수 있다.

    recent_turns가 비면 last_summary 한 줄로 폴백한다(구버전 백엔드 대응).
    """
    turns = [t for t in (sanitize(line)[:_MAX_TURN_CHARS]
                         for line in (ctx.recent_turns or [])[-_MAX_RECENT_TURNS:])
             if t]
    if not turns:
        fallback = sanitize(ctx.last_summary or "")[:_MAX_TURN_CHARS]
        turns = [fallback] if fallback else []
    if not turns:
        return ""
    n = len(turns)
    return "\n".join(f"직전 턴: {t}" if i == n - 1 else f"{n - i}턴 전: {t}"
                     for i, t in enumerate(turns))


class _Rewritten(BaseModel):
    """재구성기가 내는 것 — 질의 한 문장뿐. 팀 계약 아님."""
    query: str


def _needs_rewrite(state: dict) -> bool:
    """재구성이 필요한 상태인가.

    entry_bid가 있으면 화면 문맥이 대상을 이미 정했으므로 풀 것이 없다 —
    "이 공고 마감일 언제야?"를 직전 턴 공고로 바꿔버리면 오히려 망가진다.
    """
    if state["entry_context"].bid_id:
        return False
    ctx = state["session_context"]
    if ctx is None or not (ctx.recent_turns or ctx.last_summary):
        return False                      # 첫 턴 — 풀 근거가 없다
    return any(k in state["query"] for k in _REFERENTIAL)


@node_logger("rewrite")
def rewrite_node(state: dict) -> dict:
    """지시어 질의를 자기완결 문장으로 바꾼다.

    반환
        query           뒤 노드(router·bid_search·retrieval)가 쓸 질의
        original_query  사용자가 실제로 한 말. respond가 이걸 보고 답한다 —
                        재구성본으로 답하면 사용자가 하지 않은 말에 답하는
                        것처럼 읽힌다

    건너뛰는 경우에도 original_query는 채운다. 뒤 노드가 "있을 수도 없을 수도"를
    따지지 않게 하기 위함이다.
    """
    query = state["query"]
    if not _needs_rewrite(state):
        return {"original_query": query}

    raw = llm.invoke(
        ModelTier.ROUTER,
        messages=[{"role": "user", "content": _PROMPT.format(
            history=_history_block(state["session_context"]), query=query)}],
        max_tokens=300,
        output_schema=_Rewritten.model_json_schema(),
    )
    rewritten = _Rewritten.model_validate(raw).query.strip()

    if not rewritten:
        logger.warning("node=rewrite 빈 결과 — 원문으로 진행한다")
        return {"original_query": query}
    if rewritten != query:
        logger.info("node=rewrite %r → %r", query, rewritten)
    return {"query": rewritten, "original_query": query}
