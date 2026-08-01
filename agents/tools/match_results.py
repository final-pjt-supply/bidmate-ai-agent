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

우선순위 기준 (P1-lite, 2026-07-30 B 결정):
    ① 게이트 축이 많이 추출된 공고        (판정 분모가 큰 공고)
    ② 그 안에서 supp 충족 축이 많은 공고
    ③ 그 안에서 추정가격이 높은 공고
    (동점 최후순: bid_id — 같은 입력이면 항상 같은 목록이 나오게 하는 결정성 보장)

  '가능'은 정의상 판정축 전부 충족이므로(satisfied < required면 '보완가능'),
  ①+②는 곧 "근거가 두꺼운 가능"을 앞세우는 기준이다. 축 1~2개만 검사하고
  통과한 얇은 '가능'보다, 면허·지역·규모·품목을 다 확인하고 실적·인력까지
  충족한 공고가 위로 온다 — v2.2~2.3 캡(근거 없는 낙관 차단)과 같은 철학.

  종전 기준(마감 임박 순)은 **기본값에서 제외**했다. 마감이 곧 유리함이 아니고,
  이틀 뒤 마감(준비 불가)이 최상단을 독점하는 문제가 있었다. 정렬은 질의 의도의
  함수다 — 의도가 없으면 위 기준(rank), 사용자가 마감을 물으면 order="deadline".
  다만 그 의도를 뽑아 전달하는 통로(조건 추출)는 A의 M1 과제라 아직 없다 —
  order 파라미터는 그때 꽂을 소켓으로 미리 파둔 것이다(질문 카탈로그 Q11).
  ※ 이 변경으로 자격 목록의 순서·구성이 달라지므로 A에 공지할 것.
"""
from __future__ import annotations

from datetime import datetime

# 자격이 된다고 볼 verdict. '보완가능'은 서류를 더 갖추면 되는 상태라 지금은
# 제외한다 — "낼 수 있는 공고"로 안내했다가 못 내면 그게 더 나쁘다.
_POSSIBLE = "가능"

# 다수공급자계약(MAS) 제외 조건. 조달청이 미리 계약을 맺어두는 방식이라 개별
# 입찰 건이 아니고(C 확인, 2026-07-23), 마감이 수년 뒤(2031~37년)·금액이 커서
# 기준 ③(예산 높은 순)을 독점 왜곡한다(C 실측: "추정가격 높은 용역" 1·3위).
# 실무 취급 방식은 멘토 확인 대기 중 — 정책이 바뀌면 exclude_mas=False 한 줄.
_MAS_CONDITION = ("(bt.sucsfbid_mthd_nm IS NULL "
                  "OR bt.sucsfbid_mthd_nm NOT LIKE '%%다수공급자%%')")


def _rank_key(row: dict) -> tuple:
    """우선순위 기준 ①②③의 정렬 키. 오름차순 정렬 기준이므로 부호를 뒤집는다.

    파이썬에서 정렬하는 이유는 N-1과 같다 — 순서는 호출 계약의 속성이고,
    이쪽이 DB 없이 테스트된다. 후보가 전건이어야 하므로(정렬 후 절단) SQL은
    LIMIT 없이 '가능' 전건(~165행)을 가져온다. 행 수가 작아 비용은 무시 가능.
    """
    price = row.get("presmpt_prce")
    return (
        -(row.get("gate_cnt") or 0),          # ① 게이트 축 수, 많은 순
        -(row.get("supp_met_cnt") or 0),      # ② supp 충족 수, 많은 순
        -price if price is not None else float("inf"),   # ③ 예산 높은 순, 미상은 뒤
        row["bid_id"],                        # 동점 결정성 (백엔드 정렬 관례와 동일)
    )


def _deadline_key(row: dict) -> tuple:
    """order="deadline"의 정렬 키 — 마감 이른 순, 미상(NULL)은 맨 뒤, 동점 bid_id.

    (구 기본 동작의 보존판. 센티널 datetime.min은 마감 미상끼리 만났을 때만
    실제 비교되므로 타입이 섞일 일이 없다.)"""
    close = row.get("bid_clse_dt")
    return (close is None, close or datetime.min, row["bid_id"])


_ORDERS = {"rank": _rank_key, "deadline": _deadline_key}


def possible_bids(
    company_id: str,
    *,
    limit: int,
    order: str = "rank",
    exclude_mas: bool = True,
) -> tuple[list[dict], int]:
    """자격이 '가능'한 마감 전 공고를 우선순위 기준으로 돌려준다.

    공고명뿐 아니라 수요기관·마감일시·추정가격·계약방식을 함께 가져온다.
    `bid_table`을 이미 JOIN하고 있어 컬럼을 더 SELECT하는 비용이 0이고, 이것이
    없으면 자격 답변이 공고를 `R26BK01606492_000`처럼 공고번호로 부르거나
    "세부 조건은 원문에서 확인하라"는 빈 말만 하게 된다.

    축 카운트(gate_cnt·supp_met_cnt)는 axes(jsonb)에서 센다 — detail 같은
    표시용 문자열을 파싱하는 것이 아니라 구조화 필드(class·status)를 세는
    것이므로 파싱 금지 규약 위반이 아니다.

    Args:
        company_id: 회원 식별자(문자열). DB에선 BIGINT라 캐스팅한다.
        limit: 대화에 실을 최대 건수. 호출부가 정한다.
        order: "rank"(기본 — 우선순위 기준 ①②③) | "deadline"(마감 임박 순).
               사용자가 마감을 명시적으로 물은 턴에서 쓰라고 파둔 소켓이다.
        exclude_mas: 다수공급자계약 제외 여부(기본 True — _MAS_CONDITION 참조).

    Returns:
        (행 리스트, 전체 건수). 행은 우선순위 기준 순. 전체 건수는 절단 전
        후보 수(MAS를 제외했다면 제외 후 기준). 없으면 ([], 0).
    """
    mas_where = f"AND {_MAS_CONDITION}" if exclude_mas else ""
    sql = f"""
        SELECT bt.bid_id, bt.bid_ntce_nm, bt.dminstt_nm, bt.ntce_instt_nm,
               bt.bid_clse_dt, bt.presmpt_prce, bt.cntrct_cncls_mthd_nm,
               (SELECT count(*)
                  FROM jsonb_array_elements(COALESCE(m.axes, '[]'::jsonb)) a
                 WHERE a->>'class' = 'gate') AS gate_cnt,
               (SELECT count(*)
                  FROM jsonb_array_elements(COALESCE(m.axes, '[]'::jsonb)) a
                 WHERE a->>'class' = 'supp'
                   AND a->>'status' = '충족') AS supp_met_cnt
        FROM match_results m
        JOIN bid_table bt USING (bid_ntce_no, bid_ntce_ord)
        WHERE m.company_id = %s::bigint
          AND m.verdict = %s
          AND (bt.bid_clse_dt IS NULL
               OR bt.bid_clse_dt > (NOW() AT TIME ZONE 'Asia/Seoul'))
          {mas_where}
    """
    from agents.clients.postgres import get_cursor

    with get_cursor() as cur:
        cur.execute(sql, (company_id, _POSSIBLE))
        rows = cur.fetchall()

    try:
        key = _ORDERS[order]
    except KeyError:
        # 도달 불가 상태에서 조용히 흐르지 않는다(graph.py와 같은 원칙) —
        # 오타가 기본 정렬로 숨으면 배선 버그를 못 찾는다.
        raise ValueError(f"지원하지 않는 order: {order!r} (rank | deadline)")
    rows.sort(key=key)
    return rows[:limit], len(rows)
