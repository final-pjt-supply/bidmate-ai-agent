"""P2 Stage 4 — 정규화 상시 배치 Lambda 핸들러.

배치 위치: 병합 배치 후단 독립 단계 (EventBridge rate(5 minutes), A2 결정).
동작: normalizer_version이 현재 버전과 다른 partial/merged 공고를 최신순으로
      배치 처리해 *_norm 컬럼을 채운다. 멱등 — 재실행/중복 트리거 안전.

환경변수:
    DB_DSN               : PostgreSQL DSN (권장: Secrets Manager 주입)
    NORMALIZER_VERSION   : 기본 "v1.5" — 규칙 보강 시 이 값만 올리면 전량 재정규화
    BATCH_LIMIT          : 호출당 최대 처리 공고 수 (기본 1500 — Timeout 300s 대비 여유)

배포 메모 (팀 표준 패턴 준수):
    - 컨테이너 이미지에 normalizer.py 동봉 (psycopg2-binary 포함)
    - VPC 프라이빗 서브넷 배치 (RDS 접근) — 인덱싱 Lambda와 동일 패턴:
      전용 SG 생성 → RDS SG 5432 인바운드에 SG를 소스로 추가,
      IAM에 AWSLambdaVPCAccessExecutionRole
    - EventBridge rate(5 minutes), 동시성 1 (ReservedConcurrentExecutions=1 —
      같은 행 중복 처리 방지. 멱등이라 겹쳐도 데이터는 안전하지만 낭비 방지)
    - 알람: 기존 SNS 토픽에 Lambda Errors 연결. 로그의 none_rate가 급등하면
      새 표기 유형 유입 신호 (Stage 5 검토 트리거)
"""
from __future__ import annotations

import json
import os
import time
from collections import Counter

import psycopg2
import psycopg2.extras

from normalizer import (LookupContext, normalize_license, normalize_region,
                        normalize_personnel_grade, normalize_item_code)

NORMALIZER_VERSION = os.environ.get("NORMALIZER_VERSION", "v1.5")
BATCH_LIMIT = int(os.environ.get("BATCH_LIMIT", "1500"))
UPDATE_CHUNK = 200
TIME_GUARD_MS = 30_000          # 남은 실행시간이 이보다 작으면 다음 호출에 위임

_CTX_CACHE = {"ctx": None, "loaded_at": 0.0}
CTX_TTL_SEC = 600               # 별칭 사전 캐시 10분 (콜드스타트 간 재사용)


def _load_context(cur) -> LookupContext:
    now = time.time()
    if _CTX_CACHE["ctx"] is not None and now - _CTX_CACHE["loaded_at"] < CTX_TTL_SEC:
        return _CTX_CACHE["ctx"]
    cur.execute("SELECT entity_type, alias_text, canonical_code FROM master_alias")
    alias = {(e, t): c for e, t, c in cur.fetchall()}
    cur.execute("SELECT license_code FROM license_master")           # 폐지 업종 포함 (해석은 전체)
    license_codes = {r[0] for r in cur.fetchall()}
    cur.execute("SELECT item_code FROM item_code_master")
    item_codes = {r[0] for r in cur.fetchall()}
    cur.execute("SELECT item_name, item_code FROM item_code_master")
    item_names = {}
    for name, code in cur.fetchall():
        item_names.setdefault(name, code)
    ctx = LookupContext(alias=alias, license_codes=license_codes,
                        item_codes=item_codes, item_names=item_names)
    _CTX_CACHE.update(ctx=ctx, loaded_at=now)
    return ctx


# ── normalize_backfill.py와 동일한 변환 (공용 모듈로 빼도 됨) ──
def _norm_licenses(arr, ctx, stats):
    if not isinstance(arr, list):
        return None
    out = []
    for i, el in enumerate(arr):
        raw = (el or {}).get("name_raw") or ""
        r = normalize_license(raw, ctx)
        stats[f"lic:{r.method}"] += 1
        out.append({"or_group": (el or {}).get("or_group") or f"g{i}",
                    "codes": r.codes, "method": r.method,
                    "qualifier": r.qualifier, "name_raw": raw})
    return out


def _norm_regions(arr, ctx, stats):
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


def _norm_personnel(arr, ctx, stats):
    if not isinstance(arr, list):
        return None
    out = []
    for el in arr:
        el = el or {}
        r = normalize_personnel_grade(el.get("grade"), ctx)
        stats[f"per:{r.method}"] += 1
        out.append({"field": el.get("field"), "grade_raw": el.get("grade"),
                    "qual_codes": r.codes, "count": el.get("count"), "method": r.method})
    return out


def _norm_items(arr, ctx, stats):
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


UPDATE_SQL = """UPDATE bid_table SET
    required_licenses_norm = %s, region_limit_codes = %s,
    personnel_reqs_norm = %s, item_codes_norm = %s,
    normalizer_version = %s, normalized_at = NOW()
  WHERE bid_ntce_no = %s AND bid_ntce_ord = %s"""


def lambda_handler(event, context):
    conn = psycopg2.connect(os.environ["DB_DSN"])
    conn.autocommit = False
    cur = conn.cursor()
    ctx = _load_context(cur)

    cur.execute("""
        SELECT bid_ntce_no, bid_ntce_ord, required_licenses, region_limit_names,
               personnel_reqs, item_codes
        FROM bid_table
        WHERE qual_status IN ('partial', 'merged')
          AND normalizer_version IS DISTINCT FROM %s
        ORDER BY merged_at DESC NULLS LAST
        LIMIT %s
    """, (NORMALIZER_VERSION, BATCH_LIMIT))
    rows = cur.fetchall()

    stats, updates, done = Counter(), [], 0
    for no, ord_, lic, rgn, per, itm in rows:
        updates.append((
            psycopg2.extras.Json(_norm_licenses(lic, ctx, stats)) if lic is not None else None,
            psycopg2.extras.Json(_norm_regions(rgn, ctx, stats)) if rgn is not None else None,
            psycopg2.extras.Json(_norm_personnel(per, ctx, stats)) if per is not None else None,
            psycopg2.extras.Json(_norm_items(itm, ctx, stats)) if itm is not None else None,
            NORMALIZER_VERSION, no, ord_,
        ))

    for i in range(0, len(updates), UPDATE_CHUNK):
        if context and context.get_remaining_time_in_millis() < TIME_GUARD_MS:
            print(json.dumps({"event": "time_guard", "committed": done}))
            break                                  # 남은 행은 다음 5분 호출이 처리 (멱등)
        psycopg2.extras.execute_batch(cur, UPDATE_SQL, updates[i:i + UPDATE_CHUNK],
                                      page_size=UPDATE_CHUNK)
        conn.commit()
        done += len(updates[i:i + UPDATE_CHUNK])
    conn.close()

    lic_total = sum(v for k, v in stats.items() if k.startswith("lic:"))
    none_rate = round(100.0 * stats.get("lic:none", 0) / lic_total, 1) if lic_total else 0.0
    summary = {"event": "normalize_done", "version": NORMALIZER_VERSION,
               "targeted": len(rows), "committed": done,
               "lic_none_rate": none_rate, "stats": dict(stats)}
    print(json.dumps(summary, ensure_ascii=False))   # CloudWatch — none_rate 급등 감시 포인트
    return summary