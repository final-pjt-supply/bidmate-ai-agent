"""[1] 자격요건 매칭 — B 실구현. stubs.eligibility_node를 대체한다.

배선: run.py에서 build_graph(..., eligibility_node=eligibility_node)로 넘긴다.
stubs.py는 손대지 않는다 — C의 retrieval 실구현과 같은 규약(스텁 파일 무수정,
test_stubs.py 통과 유지).

노드는 도구를 호출하기만 한다. 판정 내부(compute_match_results 호출, verdict·
축 매핑)는 agents/tools/eligibility.py가 담당하며 노드는 알 필요가 없다.

여기서 유일하게 노드가 지는 책임은 **개수 상한**이다. 아래 참조.
"""
import logging

from agents.logging_util import node_logger
from agents.tools.eligibility import evaluate_eligibility

logger = logging.getLogger(__name__)

# 대화 경로에 실을 판정 개수 상한.
#
# 필터 없이 물으면 라이브 공고 전건(2026-07-27 기준 약 1,400건)이 그대로
# respond의 프롬프트로 들어간다. 한 건당 판정 한 줄 + 미달 사유 몇 개니까
# 수만 토큰이다. 비용도 비용이지만, 그 길이에서 LLM이 특정 공고를 정확히
# 짚어낼 거라 기대하기 어렵다. 답이 길어지는 게 아니라 나빠진다.
#
# 20건은 "사람이 한 화면에서 훑을 분량"에서 잡은 값이다. 목록 전체가 필요한
# 화면(마일스톤 (1))은 이 노드를 거치지 않고 백엔드가 도구를 직접 부르므로
# 상한의 영향을 받지 않는다.
_MAX_ELIGIBILITY_ROWS = 20

# 잘라야 한다면 무엇을 남길 것인가. 회원에게 쓸모 있는 순서다:
# 지금 되는 것 → 채우면 되는 것 → 우리가 모르는 것 → 가망 없는 것.
# '불가'가 전체의 절반 이상(회사 9001 기준 830/1,427)이라, 정렬 없이 앞에서
# 20건을 자르면 화면이 전부 불가로 덮인다.
_VERDICT_ORDER = {"가능": 0, "보완가능": 1, "확인필요": 2, "불가": 3}


@node_logger("eligibility")
def eligibility_node(state: dict) -> dict:
    """회원의 자격 충족 판정을 계약(EligibilityResult) 리스트로 반환한다.

    state에서 읽는 것
        company_id        회원 식별자(백엔드 주입)
        resolved_filters  merge.py가 병합한 필터. bid_ids가 있으면
                          그 공고들로 범위를 한정(특정 공고 문맥).

    반환은 계약의 eligibility 슬롯뿐이다.
    """
    filters = state.get("resolved_filters") or {}
    bid_ids = filters.get("bid_ids")

    results = evaluate_eligibility(state["company_id"], bid_ids=bid_ids)
    return {"eligibility": _cap(results)}


def _cap(results: list) -> list:
    """상한을 넘으면 유용한 순서로 정렬해 앞에서 자른다.

    상한 이내면 정렬도 하지 않고 원본 순서(DB가 준 순서)를 그대로 둔다 —
    특정 공고를 물어본 경우(bid_ids 지정)에 순서가 뒤바뀌면 오히려 헷갈린다.

    잘린 건수는 로그에만 남는다. 답변에 "외 N건"을 붙이려면 상태에 슬롯이
    하나 필요한데 state.py는 A 단독 소유라 여기서 정하지 않는다.
    """
    if len(results) <= _MAX_ELIGIBILITY_ROWS:
        return results

    ordered = sorted(results,
                     key=lambda r: _VERDICT_ORDER.get(r.verdict, 99))
    logger.warning(
        "node=eligibility 판정 %d건 중 %d건만 대화 경로에 싣는다 (%d건 생략)",
        len(results), _MAX_ELIGIBILITY_ROWS,
        len(results) - _MAX_ELIGIBILITY_ROWS,
    )
    return ordered[:_MAX_ELIGIBILITY_ROWS]
