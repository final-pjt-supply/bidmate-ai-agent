"""[2] 공고 특정 — 질의로 어느 공고인지 찾아 요약까지 싣는다.

두 갈래가 이 노드를 탄다.

    검색  어떤 공고가 있는지 묻는 질의 → 여기서 찾은 요약이 그대로 답의 근거다
    상세  정해진 공고를 묻는 질의인데 scope가 공고를 못 구했을 때만

청크가 아니라 **공고 단위**로 검색한다. 청크 단위 top_k만 쓰면 여러 공고의
청크가 섞여 들어와, respond가 "질의에 맞지 않는 공고는 빼라"를 프롬프트로
부탁받아야 한다(respond.py의 _TASK_LIST). 공고를 먼저 확정하면 그 부탁이 없다.

노드는 도구를 호출하기만 한다. 집계·관련도 하한선·bid_table 조회는
agents/tools/search.py의 recommend_bids가 담당한다.
"""
import logging

from agents.logging_util import node_logger
from agents.state import BidBrief
from agents.tools.search import recommend_bids

logger = logging.getLogger(__name__)

# 넘길 공고 수. recommend_bids가 min_score_ratio(1위 대비 비율)로 무관한 공고를
# 이미 잘라내므로 사업명이 명확한 질의는 1~2건으로 수렴한다. 3은 여유값.
_TOP_K = 3

# 절대 점수 하한을 대화 경로에서는 끈다.
#
# config의 기본값은 0.9이고, 근거는 "무의미한 입력('안녕하세요')이 0.815이니
# 그 위로 잡는다"였다(config.py). 그 값은 **조건형 질의를 Router가 redirect로
# 걸러낸다는 전제**에서 정해졌는데, ADR 0007에서 그 경로를 없앴다. 그래서 지금은
# 조건형 질의가 그대로 여기로 들어오고, 점수가 무의미한 입력보다 낮게 나온다.
#
#   실측(2026-07-30) "서울에서 하는 용역 공고 추천해줘"
#     청크 검색 5,335건 매칭, 1위 청크 = "서울대학교 역사기록관 … 용역"
#     공고 단위 집계 1위 점수 0.7274  →  min_score=0.9에 걸려 0건
#     min_score=0으로 내리면 3건, 1위가 그 서울대 용역 공고
#
# 무의미한 입력을 막는 일은 이제 Router의 `기타` 갈래가 한다(측정 9/9). 여기서는
# 중복 방어가 되어 정상 질의만 죽인다. min_score_ratio(0.4)와 min_chunks(2)는
# 그대로 두므로 무방비는 아니다. config 기본값은 추천 목록·배치가 쓰므로
# 건드리지 않고 이 호출에서만 낮춘다.
_MIN_SCORE = 0.0


def _brief(rec) -> BidBrief:
    """RecommendedBid → BidBrief. 값은 bid_table 원문 그대로, 계산 없음."""
    info = rec.info
    if info is None:
        return BidBrief(bid_id=rec.hit.bid_id)
    return BidBrief.of(bid_id=info.bid_id, name=info.bid_ntce_nm,
                       institution=info.dminstt_nm,
                       fallback_institution=info.ntce_instt_nm,
                       close_at=info.bid_clse_dt, price=info.presmpt_prce,
                       method=info.cntrct_cncls_mthd_nm)


@node_logger("bid_search")
def bid_search_node(state: dict) -> dict:
    """질의와 가장 관련 있는 공고 상위 _TOP_K건을 범위와 요약으로 싣는다.

    반환
        resolved_filters  bid_ids — 뒤 노드(retrieval)의 작업 범위
        bid_briefs        공고 요약 — `검색` 갈래에서 답변의 근거
        bid_names         bid_id → 공고명. respond가 공고를 이름으로 부르기 위함

    찾지 못하면 bid_ids를 **빈 리스트로** 채운다(키를 지우지 않는다 —
    graph._found_bids가 한 가지 모양만 보게 하려고). 그 경우 graph는 노드를 더
    태우지 않고 빠지고, run.py가 "공고를 찾지 못했습니다"로 답한다. 이 노드가
    이미 마감 전 전체를 훑었으므로 범위 없이 다시 검색해도 같은 결과다.
    """
    filters = dict(state.get("resolved_filters") or {})
    recs = recommend_bids(state["query"], top_k=_TOP_K, min_score=_MIN_SCORE)

    briefs = [_brief(r) for r in recs]
    # 항상 리스트로 채운다 — 분기(graph._found_bids)가 "비었나"만 보게 하려면
    # 키 없음과 빈 리스트가 갈리지 않아야 한다.
    filters["bid_ids"] = [b.bid_id for b in briefs]
    if not briefs:
        logger.warning("node=bid_search 공고를 특정하지 못했다 — 범위 없이 검색한다")

    names = {**(state.get("bid_names") or {}),
             **{b.bid_id: b.name for b in briefs if b.name}}
    return {"resolved_filters": filters, "bid_briefs": briefs,
            "bid_names": names}
