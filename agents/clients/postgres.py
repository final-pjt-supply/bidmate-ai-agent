"""PostgreSQL 클라이언트 (bid_table 조회용).

접속 설정을 한곳에 모아, 조회 로직이 접속 세부사항을 몰라도 되게 한다.
읽기 전용 조회만 수행한다. 쓰기는 이 모듈의 책임이 아니다.
"""
from __future__ import annotations

import atexit
from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from agents.config import get_settings

_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    """커넥션 풀을 한 번만 만들어 재사용한다.

    매 조회마다 새 연결을 여는 비용을 피하기 위함.
    """
    global _pool
    if _pool is None:
        s = get_settings()
        _pool = ConnectionPool(
            conninfo=s.pg_dsn,
            min_size=1,
            max_size=5,
            open=True,
        )
        # 프로세스 종료 시 풀을 먼저 닫는다. 등록하지 않으면 인터프리터
        # 종료 절차 중에 풀의 백그라운드 스레드를 정리하려다
        # PythonFinalizationError 경고가 뜬다.
        atexit.register(close_pool)
    return _pool


def close_pool() -> None:
    """커넥션 풀을 닫는다. 종료 시 자동 호출되며, 수동 호출도 안전하다."""
    global _pool
    if _pool is not None:
        try:
            _pool.close()
        except Exception:
            pass
        _pool = None


@contextmanager
def get_cursor() -> Iterator[psycopg.Cursor]:
    """dict 형태로 행을 돌려주는 커서를 빌려준다.

    사용 예:
        with get_cursor() as cur:
            cur.execute("SELECT ...", params)
            rows = cur.fetchall()   # [{"컬럼명": 값, ...}, ...]
    """
    with get_pool().connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            yield cur