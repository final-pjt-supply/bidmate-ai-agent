"""자격 판정 조회 도구 — 사전 계산 테이블 match_results.

`compute_match_results(company_id)` 함수의 결과를 배치가 이 테이블에 적재한다.
대화 경로에서 함수를 부르지 않고 테이블을 읽는 이유는 실측 때문이다
(2026-07-30, 회사 9001):

    테이블 SELECT ('가능' 마감 전 165건)   22ms
    함수 호출 (전건 재계산)               426ms

두 결과는 완전히 일치했다 — 함수도 1,319행/'가능' 165건을 냈다. 마감 전 공고
1,470건 중 판정이 없는 151건은 함수를 불러도 안 나온다(요구조건 데이터가 아직
없는 공고). 즉 테이블을 써서 놓치는 판정은 없다.

인덱스: PK(company_id, bid_ntce_no, bid_ntce_ord), idx_mr_company(company_id, verdict).
아래 조회는 후자를 탄다.

규약은 tools/eligibility.py와 같다 — get_cursor는 함수 안에서 늦게 import하고,
SQL은 전부 파라미터화한다.
"""
from __future__ import annotations

# 자격이 된다고 볼 verdict. '보완가능'은 서류를 더 갖추면 되는 상태라 지금은
# 제외한다 — "낼 수 있는 공고"로 안내했다가 못 내면 그게 더 나쁘다.
_POSSIBLE = "가능"


def possible_bids(company_id: str, *, limit: int) -> list[tuple[str, str]]:
    """자격이 '가능'한 마감 전 공고를 마감 임박 순으로 돌려준다.

    공고명을 함께 SELECT한다. bid_table을 이미 JOIN하고 있어 비용이 0이고,
    이것이 없으면 자격 답변이 공고를 `R26BK01606492_000`처럼 공고번호로
    부르게 된다(respond.render_answer는 이름이 없으면 bid_id로 폴백한다).

    Args:
        company_id: 회원 식별자(문자열). DB에선 BIGINT라 캐스팅한다.
        limit: 최대 건수. 대화에 실을 분량이라 호출부가 정한다.

    Returns:
        (bid_id, 공고명) 튜플 리스트. 마감이 이른 것부터. 없으면 빈 리스트.
        공고명이 비어 있으면 빈 문자열.
    """
    sql = """
        SELECT bt.bid_id, bt.bid_ntce_nm
        FROM match_results m
        JOIN bid_table bt USING (bid_ntce_no, bid_ntce_ord)
        WHERE m.company_id = %s::bigint
          AND m.verdict = %s
          AND (bt.bid_clse_dt IS NULL
               OR bt.bid_clse_dt > (NOW() AT TIME ZONE 'Asia/Seoul'))
        ORDER BY bt.bid_clse_dt NULLS LAST
        LIMIT %s
    """
    from agents.clients.postgres import get_cursor

    with get_cursor() as cur:
        cur.execute(sql, (company_id, _POSSIBLE, limit))
        return [(row["bid_id"], row["bid_ntce_nm"] or "")
                for row in cur.fetchall()]
