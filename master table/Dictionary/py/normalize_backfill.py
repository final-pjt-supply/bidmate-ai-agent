"""P2 Stage 2 — bid_table 일괄 정규화 러너.

원본 jsonb는 수정하지 않고 *_norm 병렬 컬럼에만 기록한다 (008 DDL 선행 필요).

사용법:
    # 1) DRY_RUN (기본) — 변경 예정 통계만 출력, DB 쓰기 없음
    DB_DSN='host=... dbname=bidmate user=... password=...' python normalize_backfill.py

    # 2) 실제 실행
    DB_DSN='...' DRY_RUN=false python normalize_backfill.py

멱등: normalizer_version이 현재 버전과 같은 행은 스킵. 재실행 안전.
쓰기 규약: jsonb 바인딩은 psycopg2.extras.Json, None은 SQL NULL (JSON null 금지 — 7/21 인시던트).
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter

import psycopg2
import psycopg2.extras

from normalizer import (LookupContext, normalize_license, normalize_region,
                        normalize_personnel_grade, normalize_item_code)

NORMALIZER_VERSION = "v1.5"
BATCH = 500          # UPDATE 배치 크기
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() != "false"


# ────────────────────────────────────────────────
def load_context(cur) -> LookupContext:
    cur.execute("SELECT entity_type, alias_text, canonical_code FROM master_alias")
    alias = {(e, t): c for e, t, c in cur.fetchall()}
    cur.execute("SELECT license_code FROM license_master WHERE is_active")
    license_codes = {r[0] for r in cur.fetchall()}
    cur.execute("SELECT item_code FROM item_code_master WHERE is_active")
    item_codes = {r[0] for r in cur.fetchall()}
    cur.execute("SELECT item_name, item_code FROM item_code_master WHERE is_active")
    item_names = {}
    for name, code in cur.fetchall():
        item_names.setdefault(name, code)   # 동명이면 첫 값 유지
    print(f"[ctx] alias {len(alias):,} / license {len(license_codes):,} "
          f"/ item {len(item_codes):,} / item_names {len(item_names):,}")
    return LookupContext(alias=alias, license_codes=license_codes,
                         item_codes=item_codes, item_names=item_names)


# ────────────────────────────────────────────────
def norm_licenses(arr, ctx, stats) -> list | None:
    if not isinstance(arr, list):
        return None
    out = []
    for i, el in enumerate(arr):
        raw = (el or {}).get("name_raw") or ""
        r = normalize_license(raw, ctx)
        stats[f"lic:{r.method}"] += 1
        out.append({
            "or_group": (el or {}).get("or_group") or f"g{i}",
            "codes": r.codes, "method": r.method,
            "qualifier": r.qualifier, "name_raw": raw,
        })
    return out


def norm_regions(arr, ctx, stats) -> dict | None:
    if not isinstance(arr, list):
        return None
    codes, flags, unmatched = [], [], []
    for name in arr:
        r = normalize_region(str(name), ctx)
        stats[f"rgn:{r.method}"] += 1
        if r.codes:
            codes.extend(r.codes)
        elif r.method.startswith("special:"):
            flags.append(r.method.split(":", 1)[1])
        elif r.method == "none":
            unmatched.append(str(name))
    return {"codes": sorted(set(codes)), "flags": sorted(set(flags)), "unmatched": unmatched}


def norm_personnel(arr, ctx, stats) -> list | None:
    if not isinstance(arr, list):
        return None
    out = []
    for el in arr:
        el = el or {}
        grade_raw = el.get("grade")
        r = normalize_personnel_grade(grade_raw, ctx)
        stats[f"per:{r.method}"] += 1
        out.append({
            "field": el.get("field"), "grade_raw": grade_raw,
            "qual_codes": r.codes, "count": el.get("count"), "method": r.method,
        })
    return out


def norm_items(arr, ctx, stats) -> dict | None:
    if not isinstance(arr, list):
        return None
    codes, routed, ignored = [], [], 0
    for el in arr:
        raw = str((el or {}).get("code") or "")
        r = normalize_item_code(raw, ctx, type_hint=(el or {}).get("type"))
        stats[f"itm:{r.method}"] += 1
        if r.method == "route:license":
            routed.extend(r.codes)
        elif r.codes:
            codes.extend(r.codes)
        elif r.method == "ignored":
            ignored += 1
    return {"codes": sorted(set(codes)), "license_routed": sorted(set(routed)), "ignored": ignored}


# ────────────────────────────────────────────────
def main():
    dsn = os.environ.get("DB_DSN")
    if not dsn:
        sys.exit("DB_DSN 환경변수를 설정하세요 (예: 'host=... dbname=bidmate user=... password=...')")

    conn = psycopg2.connect(dsn)
    conn.autocommit = False
    cur = conn.cursor()
    ctx = load_context(cur)

    cur.execute("""
        SELECT bid_ntce_no, bid_ntce_ord, required_licenses, region_limit_names,
               personnel_reqs, item_codes
        FROM bid_table
        WHERE qual_status IN ('partial', 'merged')
          AND (normalizer_version IS DISTINCT FROM %s)
    """, (NORMALIZER_VERSION,))
    rows = cur.fetchall()
    print(f"대상: {len(rows):,}건 (version != {NORMALIZER_VERSION})  DRY_RUN={DRY_RUN}")

    stats = Counter()
    updates = []
    for no, ord_, lic, rgn, per, itm in rows:
        updates.append((
            psycopg2.extras.Json(norm_licenses(lic, ctx, stats)) if lic is not None else None,
            psycopg2.extras.Json(norm_regions(rgn, ctx, stats)) if rgn is not None else None,
            psycopg2.extras.Json(norm_personnel(per, ctx, stats)) if per is not None else None,
            psycopg2.extras.Json(norm_items(itm, ctx, stats)) if itm is not None else None,
            NORMALIZER_VERSION, no, ord_,
        ))

    # ── 통계 리포트 ──
    print("\n── method 분포 ──")
    for k in sorted(stats):
        print(f"  {k:24s} {stats[k]:,}")
    for ent, label in [("lic", "면허"), ("rgn", "지역"), ("per", "인력"), ("itm", "물품")]:
        total = sum(v for k, v in stats.items() if k.startswith(ent + ":"))
        bad = stats.get(f"{ent}:none", 0)
        if total:
            print(f"  ▶ {label}: 처리 {total:,} / 미매칭(none) {bad:,} ({100.0*bad/total:.1f}%)")

    if DRY_RUN:
        print("\nDRY_RUN — DB 쓰기 생략. 실제 실행: DRY_RUN=false")
        conn.rollback(); conn.close()
        return

    print("\nUPDATE 실행 중...")
    sql = """UPDATE bid_table SET
               required_licenses_norm = %s, region_limit_codes = %s,
               personnel_reqs_norm = %s, item_codes_norm = %s,
               normalizer_version = %s, normalized_at = NOW()
             WHERE bid_ntce_no = %s AND bid_ntce_ord = %s"""
    done = 0
    for i in range(0, len(updates), BATCH):
        psycopg2.extras.execute_batch(cur, sql, updates[i:i + BATCH], page_size=BATCH)
        conn.commit()
        done += len(updates[i:i + BATCH])
        print(f"  {done:,}/{len(updates):,} 커밋")
    conn.close()
    print("완료.")


if __name__ == "__main__":
    main()