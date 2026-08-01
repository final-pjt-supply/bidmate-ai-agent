"""실 DB 대상 자격 목록(possible_bids) 검증 (live) — P1-lite 우선순위 기준.

단위 테스트(tests/test_match_results.py)는 가짜 행으로 정렬 로직만 본다.
여기서 지키는 것은 "SQL이 실제로 돌고, 계약(총계·범위·정렬·MAS 제외)이
실 데이터에서 성립하는가"다.

실행:
    pytest -m live -s -q tests/test_match_results_live.py

수치를 코드에 박지 않는다 — 공고는 매일 마감·유입되므로, 같은 실행 안에서
SQL로 다시 세어 비교한다(test_eligibility_live.py와 같은 불변식 방식).
"""
from __future__ import annotations

import os

import pytest


def _env_error() -> str | None:
    try:
        from agents.config import get_settings
        get_settings()
    except Exception as exc:                      # ValidationError 포함
        return type(exc).__name__ + ": " + str(exc).splitlines()[0]
    return None


_ENV_ERROR = _env_error()

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(_ENV_ERROR is not None, reason=f".env 미설정 — {_ENV_ERROR}"),
]

COMPANY_ID = os.getenv("LIVE_COMPANY_ID", "9001")

# 독립 재계산용 카운트 SQL — 도구와 같은 조건(가능·라이브·비MAS)을 별도 경로로 센다.
_COUNT_SQL = """
    SELECT count(*)
    FROM match_results m
    JOIN bid_table bt USING (bid_ntce_no, bid_ntce_ord)
    WHERE m.company_id = %s::bigint
      AND m.verdict = '가능'
      AND (bt.bid_clse_dt IS NULL
           OR bt.bid_clse_dt > (NOW() AT TIME ZONE 'Asia/Seoul'))
      AND (bt.sucsfbid_mthd_nm IS NULL
           OR bt.sucsfbid_mthd_nm NOT LIKE '%%다수공급자%%')
"""


def _fetch_all():
    from agents.tools.match_results import possible_bids
    rows, total = possible_bids(COMPANY_ID, limit=10_000)   # 절단 없이 전건
    if not rows:
        pytest.skip(f"company_id={COMPANY_ID} '가능' 공고 0건 — 데이터/DSN 확인 필요")
    return rows, total


def test_총계는_독립_카운트와_일치한다():
    """도구의 총계가 같은 조건의 별도 SQL 카운트와 어긋나면 필터가 갈라진 것이다."""
    from agents.clients.postgres import get_cursor

    _, total = _fetch_all()
    with get_cursor() as cur:
        cur.execute(_COUNT_SQL, [COMPANY_ID])
        independent = cur.fetchall()[0]["count"]
    assert total == independent


def test_rank_정렬_불변식_인접_행의_키가_단조롭다():
    """(게이트↓, supp충족↓, 예산↓)이 앞 행에서 뒷 행으로 갈수록 약해져야 한다."""
    from agents.tools.match_results import _rank_key

    rows, _ = _fetch_all()
    keys = [_rank_key(r) for r in rows]
    assert keys == sorted(keys)          # 도구가 이 키로 정렬했다는 사실 자체를 재확인


def test_축_카운트는_계약_범위_안이다():
    """게이트 축은 4종(license·region·size·item), supp는 6종이 상한이다."""
    rows, _ = _fetch_all()
    for r in rows:
        assert 0 <= r["gate_cnt"] <= 4, r["bid_id"]
        assert 0 <= r["supp_met_cnt"] <= 6, r["bid_id"]


def test_MAS는_반환에_없고_제외를_풀면_총계가_줄지_않는다():
    from agents.tools.match_results import possible_bids

    rows, total = _fetch_all()
    assert all("다수공급자" not in (r["cntrct_cncls_mthd_nm"] or "") for r in rows)
    _, total_with_mas = possible_bids(COMPANY_ID, limit=1, exclude_mas=False)
    assert total_with_mas >= total       # 제외를 풀면 후보는 같거나 늘어야 한다


def test_order_deadline은_마감_오름차순이고_미상은_뒤다():
    from agents.tools.match_results import possible_bids

    rows, _ = possible_bids(COMPANY_ID, limit=10_000, order="deadline")
    dates = [r["bid_clse_dt"] for r in rows]
    known = [d for d in dates if d is not None]
    assert known == sorted(known)                        # 이른 마감 먼저
    if None in dates:                                    # 미상이 있다면 전부 꼬리에
        assert all(d is None for d in dates[dates.index(None):])
