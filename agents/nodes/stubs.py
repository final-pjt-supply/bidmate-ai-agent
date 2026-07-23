"""B([1][3a])·C([2]) 임시 스텁 — 실구현 들어오면 이 파일만 교체.

호출 시마다 warning을 남겨 실구현 누락을 조기에 드러낸다(스펙 §B·C 스텁).
"""
import logging

from agents.logging_util import node_logger
from agents.schemas import (Chunk, EligibilityResult, FailedReason,
                            MatchScore, ScoreItem)

logger = logging.getLogger(__name__)


@node_logger("eligibility(stub)")
def eligibility_node(state: dict) -> dict:
    logger.warning("stub 호출: eligibility — B 실구현으로 교체 필요")
    return {"eligibility": [
        EligibilityResult(bid_id="R26BK_STUB01", passed=True),
        EligibilityResult(bid_id="R26BK_STUB02", passed=False, failed_reasons=[
            FailedReason(field="region_limit_names",
                         required="대전", actual="서울"),
        ]),
    ]}


@node_logger("retrieval(stub)")
def retrieval_node(state: dict) -> dict:
    logger.warning("stub 호출: retrieval — C 실구현으로 교체 필요")
    return {"chunks": [
        Chunk(bid_id="R26BK_STUB01", document_id="doc01",
              file_id="R26BK_STUB01_doc01", chunk_idx=0, type="text",
              text="입찰참가자격: 전기공사업 면허를 보유한 업체로서 "
                   "대전광역시에 주된 영업소를 둔 업체"),
        Chunk(bid_id="R26BK_STUB01", document_id="doc01",
              file_id="R26BK_STUB01_doc01", chunk_idx=1, type="text",
              text="낙찰자 결정방법: 적격심사 낙찰제, 낙찰하한율 87.745%"),
        Chunk(bid_id="R26BK_STUB01", document_id="doc02",
              file_id="R26BK_STUB01_doc02", chunk_idx=0, type="text",
              text="추정가격: 금 1,200,000,000원, 입찰마감: 2026. 8. 1. 10:00"),
    ]}


@node_logger("scoring(stub)")
def scoring_node(state: dict) -> dict:
    logger.warning("stub 호출: scoring — B 실구현으로 교체 필요")
    return {"scores": [MatchScore(bid_id="R26BK_STUB01", total=72.0, breakdown=[
        ScoreItem(axis="실적 여유율", points=24.0,
                  note="요구 실적 10억 대비 보유 25억, 여유율 2.5배"),
        ScoreItem(axis="면허 여유도", points=12.0,
                  note="전기공사업 면허 보유로 or_group 1 충족"),
        ScoreItem(axis="마감 여유", points=8.0, note="마감까지 10일"),
    ])]}
