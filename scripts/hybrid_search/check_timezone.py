"""시간대 진단 — 세션 설정과 실제 데이터를 모두 확인한다.

목적
  1) 파이썬(psycopg) 세션의 시간대가 DBeaver와 다른지 확인
  2) bid_clse_dt에 저장된 값이 실제 마감 시각과 맞는지 확인
     (표시 문제인지, 적재 시점부터 어긋난 데이터 문제인지 구분)

실행: python check_timezone.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from datetime import datetime, timedelta, timezone

from agents.clients.postgres import get_cursor

KST = timezone(timedelta(hours=9))


def section(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def main() -> None:
    now_local = datetime.now()
    now_kst = datetime.now(KST)
    now_utc = datetime.now(timezone.utc)

    section("1. 클라이언트(이 PC) 시각")
    print(f"  로컬 시각      : {now_local:%Y-%m-%d %H:%M:%S}")
    print(f"  KST 기준       : {now_kst:%Y-%m-%d %H:%M:%S}")
    print(f"  UTC 기준       : {now_utc:%Y-%m-%d %H:%M:%S}")

    with get_cursor() as cur:
        section("2. 파이썬 세션의 DB 시간대")
        cur.execute("""
            SELECT
              current_setting('TimeZone')     AS session_tz,
              NOW()                           AS now_raw,
              NOW() AT TIME ZONE 'Asia/Seoul' AS now_kst,
              NOW() AT TIME ZONE 'UTC'        AS now_utc
        """)
        row = cur.fetchone()
        print(f"  세션 TimeZone  : {row['session_tz']}")
        print(f"  NOW()          : {row['now_raw']}")
        print(f"  NOW() → KST    : {row['now_kst']}")
        print(f"  NOW() → UTC    : {row['now_utc']}")

        # NOW()가 실제 한국 시각과 얼마나 차이나는지
        now_raw = row["now_raw"]
        if now_raw.tzinfo is not None:
            diff = (now_raw.astimezone(KST).replace(tzinfo=None)
                    - now_kst.replace(tzinfo=None))
        else:
            diff = now_raw - now_kst.replace(tzinfo=None)
        hours = diff.total_seconds() / 3600
        verdict = "일치" if abs(hours) < 0.1 else f"{hours:+.1f}시간 차이"
        print(f"  → 한국 시각 대비: {verdict}")

        section("3. 저장된 컬럼 타입 확인")
        cur.execute("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = 'bid_table'
              AND column_name IN ('bid_ntce_dt','bid_clse_dt','openg_dt')
            ORDER BY column_name
        """)
        for r in cur.fetchall():
            print(f"  {r['column_name']:14s} {r['data_type']}")
        print("  → 'timestamp without time zone'이면 naive(시간대 정보 없음)")

        section("4. 마감 임박 공고 — 실제 값 확인")
        print("  아래 공고를 나라장터에서 조회해 마감 시각이 일치하는지 대조하세요.")
        print("  (bid_ntce_dtl_url 참고. 9시간 어긋나면 적재 단계 문제)")
        cur.execute("""
            SELECT bid_id, bid_ntce_nm, bid_ntce_dt, bid_clse_dt, openg_dt,
                   bid_ntce_dtl_url
            FROM bid_table
            WHERE bid_clse_dt IS NOT NULL
            ORDER BY bid_clse_dt DESC
            LIMIT 3
        """)
        for r in cur.fetchall():
            print(f"\n  [{r['bid_id']}] {(r['bid_ntce_nm'] or '')[:40]}")
            print(f"    공고일시 {r['bid_ntce_dt']}")
            print(f"    마감일시 {r['bid_clse_dt']}")
            print(f"    개찰일시 {r['openg_dt']}")
            if r["bid_ntce_dtl_url"]:
                print(f"    {r['bid_ntce_dtl_url']}")

        section("5. 시각 분포 — 적재 오류 탐지")
        print("  입찰 마감은 관행상 10:00, 11:00, 17:00 등 정시가 대부분이다.")
        print("  01:00, 08:00 같은 시각이 몰려 있으면 UTC로 저장됐을 가능성.")
        cur.execute("""
            SELECT EXTRACT(HOUR FROM bid_clse_dt)::int AS hh, COUNT(*) AS n
            FROM bid_table
            WHERE bid_clse_dt IS NOT NULL
            GROUP BY 1 ORDER BY 2 DESC LIMIT 8
        """)
        rows = cur.fetchall()
        total = sum(r["n"] for r in rows)
        for r in rows:
            bar = "#" * max(1, int(r["n"] / max(total, 1) * 40))
            print(f"    {r['hh']:02d}시  {r['n']:6,}건  {bar}")

        section("6. 마감 판정 비교 — 세션 의존성 확인")
        cur.execute("""
            SELECT
              COUNT(*) FILTER (WHERE bid_clse_dt > NOW())
                AS naive_now,
              COUNT(*) FILTER (WHERE bid_clse_dt > (NOW() AT TIME ZONE 'Asia/Seoul'))
                AS kst_now
            FROM bid_table
        """)
        r = cur.fetchone()
        print(f"  NOW() 직접 비교          : {r['naive_now']:,}건")
        print(f"  NOW() AT TIME ZONE KST   : {r['kst_now']:,}건")
        gap = r["naive_now"] - r["kst_now"]
        if gap == 0:
            print("  → 차이 없음. 세션이 KST로 고정되어 두 방식이 일치한다")
        else:
            print(f"  → {abs(gap):,}건 차이. 세션 시간대에 따라 결과가 달라진다")
            print("     (세션 고정이 적용되지 않은 상태)")

        section("7. 판정 결과")
        tz = row["session_tz"]
        if tz in ("Asia/Seoul", "KST"):
            print("  세션이 KST로 고정되어 있다. NOW() 직접 비교도 안전하다.")
        else:
            print(f"  세션이 {tz}다. KST naive 컬럼과 NOW()를 직접 비교하면")
            print("  결과가 어긋난다. 다음 중 하나가 필요하다:")
            print("    - 커넥션에 timezone=Asia/Seoul 설정 (postgres.py)")
            print("    - 쿼리에 AT TIME ZONE 'Asia/Seoul' 명시")
        print("\n  참고: C 코드는 두 가지를 모두 적용해 이중으로 방어한다.")


if __name__ == "__main__":
    main()