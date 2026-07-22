"""공고 메타 정보 조회 도구 (bid_table).

OpenSearch 검색은 bid_id와 청크 원문만 돌려준다. 사용자에게 보여줄
공고명·기관·마감일·금액 등은 여기서 PostgreSQL bid_table을 조회해 채운다.

설계 원칙
- 검색(search.py)과 분리한다. RAG 검색은 이 조회가 필요 없고,
  추천 목록만 필요하므로 불필요한 DB 왕복을 만들지 않는다.
- bid_id는 GENERATED 파생 컬럼이므로 문자열을 split하지 않는다.
  차수 비교가 필요하면 bid_ntce_no / bid_ntce_ord 컬럼을 직접 쓴다.
- SQL은 전부 파라미터화한다. 문자열 포매팅으로 값을 끼워넣지 않는다.
"""
from __future__ import annotations

from agents.clients.postgres import get_cursor
from agents.schemas import BidInfo

# 조회할 컬럼. 추천 목록 표시에 필요한 것만 추린다.
_COLUMNS = """
    bid_id, bid_ntce_no, bid_ntce_ord,
    bid_ntce_nm, bid_category,
    ntce_instt_nm, dminstt_nm,
    bid_ntce_dt, bid_clse_dt, openg_dt,
    presmpt_prce, bdgt_amt,
    cntrct_cncls_mthd_nm, sucsfbid_mthd_nm,
    re_ntce_yn, bid_ntce_dtl_url
"""


def fetch_bid_info(bid_ids: list[str]) -> dict[str, BidInfo]:
    """bid_id 목록에 대한 공고 메타 정보를 한 번의 쿼리로 가져온다.

    Args:
        bid_ids: 조회할 bid_id 목록 (예: ["R26BK01483740_001", ...]).

    Returns:
        {bid_id: BidInfo} 매핑. 테이블에 없는 bid_id는 결과에서 빠진다.
    """
    if not bid_ids:
        return {}

    sql = f"SELECT {_COLUMNS} FROM bid_table WHERE bid_id = ANY(%s)"
    with get_cursor() as cur:
        cur.execute(sql, (bid_ids,))
        rows = cur.fetchall()

    return {row["bid_id"]: BidInfo(**row) for row in rows}


def latest_ord_map(bid_ids: list[str]) -> dict[str, str]:
    """각 공고번호의 최신 차수 bid_id를 알아낸다.

    재공고(정정)가 있으면 차수가 올라가므로, 같은 bid_ntce_no 중
    가장 큰 bid_ntce_ord 하나만 유효한 공고로 본다.

    Args:
        bid_ids: 검색 결과로 나온 bid_id 목록.

    Returns:
        {bid_ntce_no: 최신 차수의 bid_id} 매핑.
    """
    if not bid_ids:
        return {}

    # 입력 bid_id들이 속한 공고번호를 먼저 구하고,
    # 그 공고번호들의 전체 차수 중 최신을 테이블에서 직접 찾는다.
    # (검색 결과에 최신 차수가 안 걸렸을 수도 있으므로 테이블 기준으로 판단)
    sql = """
        SELECT DISTINCT ON (bid_ntce_no) bid_ntce_no, bid_id
        FROM bid_table
        WHERE bid_ntce_no IN (
            SELECT bid_ntce_no FROM bid_table WHERE bid_id = ANY(%s)
        )
        ORDER BY bid_ntce_no, bid_ntce_ord DESC
    """
    with get_cursor() as cur:
        cur.execute(sql, (bid_ids,))
        rows = cur.fetchall()

    return {row["bid_ntce_no"]: row["bid_id"] for row in rows}


def filter_open_bids(bid_ids: list[str]) -> set[str]:
    """아직 마감되지 않은 공고만 골라낸다.

    bid_clse_dt(투찰마감일시)가 현재보다 미래인 것. bid_clse_dt는 KST naive
    값이므로 NOW()를 그대로 쓰지 않고 AT TIME ZONE으로 한국 시간 naive를 만들어
    비교한다(세션 시간대에 따라 결과가 달라지는 것을 막기 위함). 마감일시가 NULL인 경우
    (경쟁입찰이 아니면 공란일 수 있음 — 스키마 주석)는 판단 불가이므로 포함시킨다.

    Args:
        bid_ids: 확인할 bid_id 목록.

    Returns:
        마감 전(또는 마감일시 미상)인 bid_id 집합.
    """
    if not bid_ids:
        return set()

    sql = """
        SELECT bid_id FROM bid_table
        WHERE bid_id = ANY(%s)
          AND (bid_clse_dt IS NULL OR bid_clse_dt > (NOW() AT TIME ZONE 'Asia/Seoul'))
    """
    with get_cursor() as cur:
        cur.execute(sql, (bid_ids,))
        return {row["bid_id"] for row in cur.fetchall()}


def open_bid_ids(
    *,
    category: str | None = None,
    limit: int = 20000,
) -> list[str]:
    """현재 유효한 공고(마감 전 + 최신 차수)의 bid_id 목록을 가져온다.

    검색 전에 이 목록을 구해 OpenSearch의 bid_id 필터로 넘기면,
    "검색 후 걸러내기"에서 대부분이 탈락하는 낭비를 없앨 수 있다.

    마감 판정은 KST naive 기준(AT TIME ZONE)으로 하며,
    같은 공고번호 중에서는 가장 큰 차수 1건만 남긴다.

    Args:
        category: 업무구분 필터 (cnstwk/servc/thng/frgcpt).
        limit: 최대 개수. OpenSearch terms 필터 크기를 제한하기 위한 안전장치.

    Returns:
        bid_id 문자열 리스트.
    """
    where = ["(bid_clse_dt IS NULL OR bid_clse_dt > (NOW() AT TIME ZONE 'Asia/Seoul'))"]
    params: list[object] = []
    if category:
        where.append("bid_category = %s")
        params.append(category)

    sql = f"""
        SELECT DISTINCT ON (bid_ntce_no) bid_id
        FROM bid_table
        WHERE {' AND '.join(where)}
        ORDER BY bid_ntce_no, bid_ntce_ord DESC
        LIMIT %s
    """
    params.append(limit)

    with get_cursor() as cur:
        cur.execute(sql, params)
        return [row["bid_id"] for row in cur.fetchall()]