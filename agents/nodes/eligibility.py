"""[1] 자격요건 매칭 — B 실구현. stubs.eligibility_node를 대체한다.

배선: run.py에서 build_graph(..., eligibility_node=eligibility_node)로 넘긴다.
stubs.py는 손대지 않는다 — C의 retrieval 실구현과 같은 규약(스텁 파일 무수정,
test_stubs.py 통과 유지).

노드는 도구를 호출하기만 한다. 판정 내부(compute_match_results 호출, verdict·
축 매핑)는 agents/tools/eligibility.py가 담당하며 노드는 알 필요가 없다.
"""
from agents.logging_util import node_logger
from agents.tools.eligibility import evaluate_eligibility


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
    return {"eligibility": results}
