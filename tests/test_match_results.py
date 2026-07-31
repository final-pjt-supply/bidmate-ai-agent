"""possible_bids 우선순위 기준(P1-lite) 테스트 — DB 없이 검증한다.

우선순위 기준(2026-07-30 B 결정): ① 게이트 축 수 ↓ ② supp 충족 수 ↓
③ 추정가격 ↓ (동점 최후순 bid_id). '가능'은 전 축 충족이므로 ①+②는
"근거가 두꺼운 가능"을 앞세우는 기준이다.

정렬은 파이썬(_rank_key)에서 하므로 여기서 직접 검증하고, DB 왕복은
sys.modules 주입으로 대체한다(clients.postgres가 psycopg를 최상단에서
끌어오기 때문 — N-2a 테스트와 같은 수법).
"""
import sys
import types
from contextlib import contextmanager

import pytest

import agents.tools.match_results as mr
from agents.tools.match_results import _rank_key, possible_bids


def _row(bid_id, gate=0, supp=0, price=None):
    return {"bid_id": bid_id, "gate_cnt": gate, "supp_met_cnt": supp,
            "presmpt_prce": price, "bid_ntce_nm": f"공고 {bid_id}"}


def _fake_db(monkeypatch, rows):
    """get_cursor를 흉내 내고, 실행된 SQL을 돌려받을 통로를 연다."""
    captured = {}

    class _Cursor:
        def execute(self, sql, params):
            captured["sql"], captured["params"] = sql, params

        def fetchall(self):
            return [dict(r) for r in rows]     # 호출부의 in-place sort 방어

    @contextmanager
    def get_cursor():
        yield _Cursor()

    monkeypatch.setitem(sys.modules, "agents.clients.postgres",
                        types.SimpleNamespace(get_cursor=get_cursor))
    return captured


# ── 기준 ①②③ 각각 ────────────────────────────────────────────────

def test_기준1_게이트_축이_많은_공고가_먼저다():
    rows = [_row("A", gate=1, supp=5, price=999), _row("B", gate=4, supp=0)]
    assert [r["bid_id"] for r in sorted(rows, key=_rank_key)] == ["B", "A"]


def test_기준2_게이트가_같으면_supp_충족이_많은_공고가_먼저다():
    rows = [_row("A", gate=4, supp=2), _row("B", gate=4, supp=5)]
    assert [r["bid_id"] for r in sorted(rows, key=_rank_key)] == ["B", "A"]


def test_기준3_게이트와_supp가_같으면_예산이_높은_공고가_먼저다():
    rows = [_row("A", gate=4, supp=3, price=100),
            _row("B", gate=4, supp=3, price=900)]
    assert [r["bid_id"] for r in sorted(rows, key=_rank_key)] == ["B", "A"]


def test_예산_미상은_같은_등급_안에서_맨_뒤로_간다():
    rows = [_row("A", gate=4, supp=3, price=None),
            _row("B", gate=4, supp=3, price=1)]
    assert [r["bid_id"] for r in sorted(rows, key=_rank_key)] == ["B", "A"]


def test_전부_동점이면_bid_id로_결정적이다():
    """같은 입력이면 항상 같은 목록 — 재현성은 N-1과 같은 계약이다."""
    rows = [_row("B", gate=4, supp=3, price=100),
            _row("A", gate=4, supp=3, price=100)]
    assert [r["bid_id"] for r in sorted(rows, key=_rank_key)] == ["A", "B"]


# ── possible_bids 통합 동작 ───────────────────────────────────────

def test_정렬_후_절단하고_총계는_절단_전_건수다(monkeypatch):
    _fake_db(monkeypatch, [
        _row("얇은가능", gate=1, supp=0, price=999),
        _row("두꺼운가능", gate=4, supp=5, price=10),
        _row("중간가능", gate=4, supp=2, price=500),
    ])
    rows, total = possible_bids("9001", limit=2)
    assert [r["bid_id"] for r in rows] == ["두꺼운가능", "중간가능"]
    assert total == 3                     # 총계는 자르기 전 — "N건 중 M건" 신호용


def test_MAS_제외가_기본이고_끌_수_있다(monkeypatch):
    cap = _fake_db(monkeypatch, [])
    possible_bids("9001", limit=5)
    assert "다수공급자" in cap["sql"]      # 기본: 후보·총계에서 제외

    cap = _fake_db(monkeypatch, [])
    possible_bids("9001", limit=5, exclude_mas=False)
    assert "다수공급자" not in cap["sql"]  # 정책 반전은 한 줄


def test_빈_결과는_빈_리스트와_0이다(monkeypatch):
    _fake_db(monkeypatch, [])
    assert possible_bids("9001", limit=5) == ([], 0)


# ── order 소켓 (마감 임박 순은 요구가 있을 때만) ──────────────────

def _row_d(bid_id, close):
    r = _row(bid_id, gate=4, supp=3, price=100)
    r["bid_clse_dt"] = close
    return r


def test_order_deadline은_마감_이른_순이고_미상은_뒤다(monkeypatch):
    from datetime import datetime as dt
    _fake_db(monkeypatch, [
        _row_d("늦은", dt(2026, 8, 20)),
        _row_d("미상", None),
        _row_d("빠른", dt(2026, 8, 1)),
    ])
    rows, _ = possible_bids("9001", limit=5, order="deadline")
    assert [r["bid_id"] for r in rows] == ["빠른", "늦은", "미상"]


def test_order_기본값은_rank다(monkeypatch):
    _fake_db(monkeypatch, [
        _row("얇은가능", gate=1, supp=0, price=999),
        _row("두꺼운가능", gate=4, supp=5, price=10),
    ])
    rows, _ = possible_bids("9001", limit=5)
    assert rows[0]["bid_id"] == "두꺼운가능"


def test_모르는_order는_조용히_넘어가지_않는다(monkeypatch):
    _fake_db(monkeypatch, [])
    with pytest.raises(ValueError):
        possible_bids("9001", limit=5, order="deadlnie")   # 오타 시나리오
