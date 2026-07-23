"""
normalize_output_adapter.py — 정규화 출력 어댑터 (v2.4)
=======================================================

역할: bid_table(원본, LLM 추출 v0.2 + API 정형 v0.1)을 읽어, normalizer 순수 함수로
      해석한 결과를 v2.4 스키마의 bid_qual_* 11개 테이블에 **행으로 적재**한다.

구 어댑터(jsonb *_norm UPDATE)를 폐기하고 전면 재작성:
  · 쓰기부 = "행 INSERT + 마스터 조인으로 표준명 채움"  (구: jsonb UPDATE)
  · 공고 단위 DELETE→INSERT 멱등 트랜잭션
  · v2.4 반영: 규모→bid_qual_size / 신용→bid_qual_credit / 직생→bid_qual_items,
              지명경쟁·공동수급은 summary 표시전용(매칭 미참여)
  · 실적·시공능력 파싱, 인증 라우팅(직생·규모 서류 혼입 배제)은 이 어댑터 소관(신규)

normalizer 순수 함수(v1.5, 3개 v1.6 버그는 normalizer 쪽에서 흡수)는 그대로 재사용.
아래 Normalizer 프로토콜에 실제 normalizer.py를 주입한다(테스트는 mock 주입).

  · canon_key(s)                         -> str
  · normalize_license(name_raw)          -> list[dict{or_group, code, method, qualifier, source}]
  · normalize_region(name_raw, site_rgn) -> dict{code, method, flag}
  · normalize_personnel(field,grade,cnt) -> dict{qual_code, method, role_field, headcount, grade_raw}
  · normalize_item(raw)                  -> dict{item_code, method, source}

DB: psycopg(v3). 실행: python normalize_output_adapter.py  (기본 DRY_RUN=True)
"""
from __future__ import annotations
import os, re, json, logging
from dataclasses import dataclass, field
from typing import Protocol, Any

log = logging.getLogger("normalize_adapter")

NORMALIZER_VERSION = "v1.6"   # summary.normalizer_version — 재정규화 레버

# ─────────────────────────────────────────────────────────────
# 상수 (어댑터 소관 규칙)
# ─────────────────────────────────────────────────────────────
PERF_UNIT_WHITELIST = {"원", "건"}                       # 실적 unit 화이트리스트 (그 외 → 확인필요)
SIZE_ENUM = {"sme_only", "small_only", "no_large", "no_conglomerate"}  # 'none'은 요구 아님

# 인증 라우팅/무시: required_certs에 혼입된 비(非)인증 서류를 걸러낸다 (cert 매칭 전).
CERT_ROUTE_DIRECT = ("직접생산", "직생")                 # → 품목 축 직생으로 (이미 direct_production_req로 처리)
CERT_ROUTE_SIZE   = ("중소기업확인", "중소기업제품", "소기업확인")  # → 규모 축
CERT_IGNORE       = ("여성기업", "장애인기업", "사회적기업",      # 우대(가점) — v1 제외
                     "공장등록", "사업자등록", "납세증명", "국세완납", "지방세완납")

# 실적 basis에서 집계 방식 판정
_AGG_SUM   = ("누계", "합산", "합계", "총")
_AGG_COUNT = ("건 이상", "건이상", "회 이상", "횟수")


# ─────────────────────────────────────────────────────────────
# normalizer 인터페이스
# ─────────────────────────────────────────────────────────────
class Normalizer(Protocol):
    def canon_key(self, s: str) -> str: ...
    def normalize_license(self, name_raw: str) -> list[dict]: ...
    def normalize_region(self, name_raw: str, site_rgn: str | None = None) -> dict: ...
    def normalize_personnel(self, field: str | None, grade: str | None, count: Any) -> dict: ...
    def normalize_item(self, raw: str) -> dict: ...


@dataclass
class MasterNames:
    """표준명 병기용 — 코드→이름 캐시 (한 번 로드)."""
    license: dict[str, str] = field(default_factory=dict)
    region: dict[str, str] = field(default_factory=dict)
    personnel: dict[str, str] = field(default_factory=dict)
    item: dict[str, str] = field(default_factory=dict)
    cert: dict[str, str] = field(default_factory=dict)
    cert_alias: dict[str, str] = field(default_factory=dict)  # canon_key(별칭) → cert_code

    @classmethod
    def load(cls, conn, norm: Normalizer) -> "MasterNames":
        m = cls()
        def rows(sql):
            with conn.cursor() as c:
                c.execute(sql); return c.fetchall()
        m.license   = {k: v for k, v in rows("SELECT license_code, license_name FROM license_master")}
        m.region    = {k: v for k, v in rows("SELECT region_code, region_name FROM region_master")}
        m.personnel = {k: v for k, v in rows("SELECT qual_code, qual_name FROM personnel_grade_master")}
        m.item      = {k: v for k, v in rows("SELECT item_code, item_name FROM item_code_master")}
        m.cert      = {k: v for k, v in rows("SELECT cert_code, cert_name FROM cert_master")}
        # cert 별칭 사전 (canon_key 대칭 적용)
        for alias, code in rows("SELECT alias_text, canonical_code FROM master_alias WHERE entity_type='cert'"):
            m.cert_alias[norm.canon_key(alias)] = code
        # 인증명 자체도 canon_key로 조회 가능하게
        for code, name in m.cert.items():
            m.cert_alias.setdefault(norm.canon_key(name), code)
            m.cert_alias.setdefault(norm.canon_key(code), code)
        return m


# ─────────────────────────────────────────────────────────────
# 어댑터 소관 파서 (신규 — normalizer 밖)
# ─────────────────────────────────────────────────────────────
def _num(v) -> float | None:
    if v is None: return None
    if isinstance(v, (int, float)): return float(v)
    s = re.sub(r"[^\d.]", "", str(v))
    return float(s) if s else None

def parse_performance(req: dict) -> dict:
    """실적 요구 1건 → bid_qual_performances 행. 미해석은 통과가 아니라 확인필요."""
    unit_raw = (req.get("unit") or "")
    basis    = (req.get("basis") or "")
    unit = "원" if "원" in unit_raw else ("건" if "건" in unit_raw else None)
    min_value = _num(req.get("value"))
    if any(k in basis for k in _AGG_COUNT) or unit == "건":
        agg = "count"
    elif any(k in basis for k in _AGG_SUM):
        agg = "sum"
    else:
        agg = "single"          # 기본: 단일 실적
    yr = re.search(r"(\d+)\s*년", basis)
    period_years = int(yr.group(1)) if yr else 5
    parsed = unit in PERF_UNIT_WHITELIST and min_value is not None
    return {
        "parse_status": "parsed" if parsed else "unparsed",
        "unit": unit if unit in PERF_UNIT_WHITELIST else None,
        "min_value": min_value,
        "agg_type": agg if parsed else None,
        "period_years": period_years,
        "field_code": None,     # 분야 해석은 v2 (category_raw 보존)
        "category_raw": req.get("category"),
        "basis_raw": basis or None,
        "scope_raw": req.get("scope_raw"),
    }

def parse_capacity(req: dict, norm: Normalizer) -> dict:
    """능력 요구 1건 → bid_qual_capacity 행 (시공능력평가액)."""
    min_value = _num(req.get("value"))
    lic = None
    name = req.get("name") or ""
    if name:                    # 업종명이 붙어 있으면 면허 정규화로 코드 시도
        comps = norm.normalize_license(name)
        for cpt in comps:
            if cpt.get("code"):
                lic = cpt["code"]; break
    parsed = min_value is not None
    return {
        "capacity_type": "시공능력평가액",
        "license_code": lic,
        "min_value": int(min_value) if parsed else None,
        "parse_status": "parsed" if parsed else "unparsed",
        "name_raw": name or (req.get("unit") or "능력요건"),
        "method": "rule0" if lic else "none",
    }

def route_cert(name_raw: str, norm: Normalizer, mn: MasterNames) -> dict | None:
    """
    required_certs 1건 처리.
    반환 None = 인증 아님(라우팅/무시). dict = bid_qual_certs 행(코드 or 미해석).
    """
    s = str(name_raw)
    if any(k in s for k in CERT_ROUTE_DIRECT):  return None  # 직생 → 품목 축(이미 처리)
    if any(k in s for k in CERT_ROUTE_SIZE):    return None  # 규모 → 규모 축
    if any(k in s for k in CERT_IGNORE):        return None  # 우대·일반서류 → 무시
    code = mn.cert_alias.get(norm.canon_key(s))
    return {
        "cert_code": code,
        "cert_name": mn.cert.get(code) if code else None,
        "method": "alias" if code else "none",
        "name_raw": s,
    }


# ─────────────────────────────────────────────────────────────
# 행 빌더 (순수 — DB 안 건드림). bid_row(dict) → {table: [row dict, ...]}
# ─────────────────────────────────────────────────────────────
def build_rows(bid: dict, norm: Normalizer, mn: MasterNames) -> dict[str, list[dict]]:
    no, ord_ = bid["bid_ntce_no"], bid["bid_ntce_ord"]
    out: dict[str, list[dict]] = {t: [] for t in (
        "summary", "licenses", "regions", "personnel", "items", "performances",
        "certs", "capacity", "size", "credit", "region_duty")}

    # ── 앵커 summary (스칼라 게이트 폐기 후: 지역기준·표시전용·신호·스코어링·메타) ──
    out["summary"].append({
        "bid_ntce_no": no, "bid_ntce_ord": ord_, "bid_category": bid["bid_category"],
        "region_limit_type": bid.get("region_limit_type"),
        "designated_competition": bid.get("dsgnt_cmpt_yn"),          # 표시전용
        "joint_supply_method": bid.get("cmmn_spldmd_methd_nm"),       # 표시전용
        "region_duty_joint_contract": bid.get("rgn_duty_jntcontrct_yn"),
        "region_duty_rate": bid.get("rgn_duty_jntcontrct_rt"),
        "joint_venture_allowed": bid.get("joint_venture_allowed"),
        "subcontract_allowed": bid.get("subcontract_allowed"),
        "award_cutline_type": bid.get("award_cutline_type"),
        "award_cutline_value": bid.get("award_cutline_value"),
        "tech_weight": bid.get("tech_weight"), "price_weight": bid.get("price_weight"),
        "normalizer_version": NORMALIZER_VERSION,
    })

    # ── ① 면허 (OR 분해 → or_group별 다중 행) ──
    #   normalizer는 문자열 1건 단위 순수함수라 or_group을 "1"부터 로컬 번호로 반환한다.
    #   서로 다른 required_licenses 항목(=독립 AND 요구)이 같은 "1"로 충돌하면
    #   매칭 쿼리(그룹 간 AND)가 이를 OR로 오인 → 요구 항목 인덱스로 네임스페이스.
    #   결과: "A 또는 B"(한 항목) = 같은 그룹(OR), 항목이 다르면 다른 그룹(AND).
    for req_idx, req in enumerate(bid.get("required_licenses") or [], start=1):
        raw = req.get("name_raw") or ""
        for cpt in norm.normalize_license(raw):
            code = cpt.get("code")
            out["licenses"].append({
                "bid_ntce_no": no, "bid_ntce_ord": ord_,
                "or_group": f"{req_idx}.{cpt.get('or_group', '1')}",
                "license_code": code, "license_name": mn.license.get(code) if code else None,
                "method": cpt.get("method", "none"), "source": cpt.get("source", "license_field"),
                "qualifier": cpt.get("qualifier"), "name_raw": raw,
            })

    # ── ② 지역 ──
    site = bid.get("cnstrtsite_rgn_nm")
    for raw in (bid.get("region_limit_names") or []):
        r = norm.normalize_region(raw, site)
        code = r.get("code")
        out["regions"].append({
            "bid_ntce_no": no, "bid_ntce_ord": ord_, "region_code": code,
            "region_name": mn.region.get(code) if code else None,
            "method": r.get("method", "none"), "flag": r.get("flag"), "name_raw": raw,
        })

    # ── ③ 인력 ──
    for req in (bid.get("personnel_reqs") or []):
        p = norm.normalize_personnel(req.get("field"), req.get("grade"), req.get("count"))
        code = p.get("qual_code")
        out["personnel"].append({
            "bid_ntce_no": no, "bid_ntce_ord": ord_, "qual_code": code,
            "qual_name": mn.personnel.get(code) if code else None,
            "role_field": p.get("role_field"), "headcount": p.get("headcount") or 1,
            "method": p.get("method", "none"), "grade_raw": p.get("grade_raw"),
        })

    # ── ④ 품목 + 직생 흡수 ──
    dp_req = bool(bid.get("direct_production_req"))
    for req in (bid.get("item_codes") or []):
        raw = req.get("code") or req.get("name") or ""
        it = norm.normalize_item(str(raw))
        code = it.get("item_code")
        out["items"].append({
            "bid_ntce_no": no, "bid_ntce_ord": ord_, "item_code": code,
            "item_name": mn.item.get(code) if code else None,
            "direct_production_req": dp_req,           # 직생 요구를 품목 행에 부여
            "method": it.get("method", "none"), "source": it.get("source", "item_field"),
            "name_raw": str(raw),
        })
    # 직생 요구인데 품목이 하나도 없으면 요구 유실 방지용 플레이스홀더 행
    if dp_req and not out["items"]:
        out["items"].append({
            "bid_ntce_no": no, "bid_ntce_ord": ord_, "item_code": None, "item_name": None,
            "direct_production_req": True, "method": "none", "source": "item_field",
            "name_raw": "직접생산확인 요구(품목 미상)",
        })

    # ── ⑤ 실적 (파싱) ──
    for req in (bid.get("performance_reqs") or []):
        row = parse_performance(req); row.update(bid_ntce_no=no, bid_ntce_ord=ord_)
        out["performances"].append(row)

    # ── ⑥ 인증 (라우팅 후 적재) ──
    for c in (bid.get("required_certs") or []):
        raw = c.get("name") if isinstance(c, dict) else c
        row = route_cert(raw, norm, mn)
        if row: row.update(bid_ntce_no=no, bid_ntce_ord=ord_); out["certs"].append(row)

    # ── ⑦ 시공능력 (파싱) ──
    for req in (bid.get("capacity_reqs") or []):
        row = parse_capacity(req, norm); row.update(bid_ntce_no=no, bid_ntce_ord=ord_)
        out["capacity"].append(row)

    # ── ⑧ 규모 (요구 있을 때만 1행. 'none'/미상 → 행 없음) ──
    size = bid.get("company_size_limit")
    if size in SIZE_ENUM:
        out["size"].append({"bid_ntce_no": no, "bid_ntce_ord": ord_, "size_limit": size,
                             "name_raw": size, "method": "rule0"})

    # ── ⑨ 신용 (요구 TRUE일 때만 1행) ──
    if bid.get("credit_rating_req"):
        out["credit"].append({"bid_ntce_no": no, "bid_ntce_ord": ord_, "required": True,
                              "min_grade": None, "name_raw": "신용등급 요구", "method": "rule0"})

    # ── 지역의무공동도급 의무지역 (신호, 다중값) ──
    for raw in (bid.get("jntcontrct_duty_rgns") or []):
        r = norm.normalize_region(raw)
        code = r.get("code")
        out["region_duty"].append({
            "bid_ntce_no": no, "bid_ntce_ord": ord_, "region_code": code,
            "region_name": mn.region.get(code) if code else None,
            "method": r.get("method", "none"), "name_raw": raw,
        })
    return out


# ─────────────────────────────────────────────────────────────
# 쓰기 (멱등 트랜잭션) — 공고 단위 DELETE(summary CASCADE)→INSERT
# ─────────────────────────────────────────────────────────────
_CHILD_TABLES = {
    "licenses": "bid_qual_licenses", "regions": "bid_qual_regions",
    "personnel": "bid_qual_personnel", "items": "bid_qual_items",
    "performances": "bid_qual_performances", "certs": "bid_qual_certs",
    "capacity": "bid_qual_capacity", "size": "bid_qual_size",
    "credit": "bid_qual_credit", "region_duty": "bid_qual_region_duty",
}

def _insert(cur, table: str, row: dict):
    cols = list(row.keys())
    cur.execute(
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(['%s']*len(cols))})",
        [row[c] for c in cols])

def write_bid(conn, no: str, ord_: str, rows: dict[str, list[dict]], dry_run: bool = True) -> int:
    """공고 1건 멱등 적재. 반환: INSERT된 총 행 수 (dry_run이면 계획 행 수)."""
    total = sum(len(v) for v in rows.values())
    if dry_run:
        log.info("[DRY_RUN] %s_%s → %d rows %s", no, ord_, total,
                 {k: len(v) for k, v in rows.items() if v})
        return total
    with conn.transaction():
        with conn.cursor() as cur:
            # summary 삭제 → 자식 CASCADE 소거 (멱등 재작성)
            cur.execute("DELETE FROM bid_qual_summary WHERE bid_ntce_no=%s AND bid_ntce_ord=%s", (no, ord_))
            for r in rows["summary"]:
                _insert(cur, "bid_qual_summary", r)
            for key, table in _CHILD_TABLES.items():
                for r in rows[key]:
                    _insert(cur, table, r)
    return total


# ─────────────────────────────────────────────────────────────
# 대상 선정 + 러너
# ─────────────────────────────────────────────────────────────
def select_targets(conn, current_version: str) -> list[dict]:
    """정규화 필요 공고 = merged/partial 중 미정규화·재병합·버전상이."""
    with conn.cursor(row_factory=_dict_row(conn)) as cur:
        cur.execute("""
            SELECT b.* FROM bid_table b
            LEFT JOIN bid_qual_summary s USING (bid_ntce_no, bid_ntce_ord)
            WHERE b.qual_status IN ('merged','partial')
              AND (s.bid_ntce_no IS NULL
                   OR b.merged_at > s.normalized_at
                   OR s.normalizer_version <> %s)
        """, (current_version,))
        return cur.fetchall()

def _dict_row(conn):
    # psycopg3 dict row factory 지연 import (테스트 주입 시 불필요)
    from psycopg.rows import dict_row
    return dict_row

def run(conn, norm: Normalizer, current_version: str = NORMALIZER_VERSION, dry_run: bool = True) -> dict:
    mn = MasterNames.load(conn, norm)
    targets = select_targets(conn, current_version)
    log.info("대상 공고 %d건 (dry_run=%s)", len(targets), dry_run)
    n_bids = n_rows = 0
    for bid in targets:
        rows = build_rows(bid, norm, mn)
        n_rows += write_bid(conn, bid["bid_ntce_no"], bid["bid_ntce_ord"], rows, dry_run)
        n_bids += 1
    log.info("완료: 공고 %d건, 행 %d개%s", n_bids, n_rows, " (DRY_RUN — 미기록)" if dry_run else "")
    return {"bids": n_bids, "rows": n_rows, "dry_run": dry_run}


if __name__ == "__main__":
    import psycopg
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    import normalizer  # 실제 normalizer.py 주입 (없으면 ImportError — 인터페이스 참조)
    dsn = os.environ["DB_DSN"]                     # key=value 전체 문자열
    dry = os.environ.get("DRY_RUN", "1") != "0"    # 기본 DRY_RUN
    with psycopg.connect(dsn) as conn:
        run(conn, normalizer, dry_run=dry)