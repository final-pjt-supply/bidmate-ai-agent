"""
normalize_output_adapter.py — 정규화 출력 어댑터 (v2.4)
=======================================================

역할: bid_table(원본, LLM 추출 v0.2 + API 정형 v0.1)을 읽어, **실제 normalizer.py**로
      해석한 결과를 v2.4 스키마의 bid_require_* 11개 테이블에 **행으로 적재**한다.

normalizer.py(v1 "P2 Stage 1", 순수함수)는 원본 그대로 사용한다. 이 어댑터가 그 계약에
맞춰 호출·매핑한다(= 어댑터의 본분). 실제 인터페이스:

  · canon_key(text) -> str
  · LookupContext(alias, license_codes, item_codes, item_names)   # DB에서 1회 구성해 주입
  · normalize_license(raw, ctx)              -> Result
  · normalize_region(raw, ctx)               -> Result
  · normalize_personnel_grade(grade, ctx)    -> Result            # 등급만(인원·분야는 어댑터 몫)
  · normalize_item_code(raw, ctx, type_hint) -> Result
  Result(codes:list, method:str, qualifier:str, raw:str)
    method ∈ rule0/alias/rule+alias/name_map/route:item/route:license/ignored/special:*/none
    codes  = OR 그룹(하나라도 보유 시 충족). 한 요구 문자열 = 한 Result = 한 or_group.

매핑 원칙(어댑터 소관):
  · route:item(면허칸의 물품)·route:license(물품칸의 업종) → 올바른 축으로 **교차 이동**.
    나라장터 물품 공고는 면허칸에 물품코드를 넣는 관행이라 흔함(normalizer도 대량 관측 전제).
    품목은 코드 기준 dedup으로 중복(면허칸 + 물품칸 동시 기재) 방지.
  · ignored → 해당 축 요건 아님 → 드롭. codes 없고 none → NULL 행(미해석 = 확인필요 표면화).
  · 인력은 등급이 ignored/no_grade여도 인원 요구가 남으므로 항상 행 유지(qual_code NULL).
  · v2.4: 규모→bid_require_size / 신용→bid_require_credit / 직생→bid_require_items,
          지명경쟁·공동수급은 summary 표시전용. 실적·시공능력 파싱·인증 라우팅은 어댑터 소관.

DB: psycopg(v3). 실행: python normalize_output_adapter.py  (기본 DRY_RUN=True)
"""
from __future__ import annotations
import os, re, logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("normalize_adapter")

NORMALIZER_VERSION = "v1.9"   # summary.normalizer_version — 재정규화 레버
#   v1.9 (2026-07-29): [D-04] route_cert 되먹임. 종전에는 라우팅 4갈래가 None 만 반환하고
#     목적지 축(직생/규모/신용)은 LLM 이 별도 필드를 채웠기를 기대할 뿐이라, 필드가 비면
#     요구가 통째로 유실됐다(실증 R26BK01639584_000: 인증칸 직생증명서 2건 + 직생 '요구 없음').
#     cert_feedback() 프리패스가 required_certs 를 훑어 공백 필드를 보강한다:
#       · 직생 문자열 → dp_req 승격 + 세부품명번호(10자리) 품목코드 회수
#       · 규모 문자열 → company_size_limit 공백 시 size 행 보강
#           ('중소기업' 포함 → sme_only / 그 외 '소기업'·'소상공인' → small_only)
#       · 신용 문자열 → credit 행 보강 + min_grade 파싱(D-05, 'B등급 이상' 등)
#     LLM 필드가 이미 차 있으면 절대 덮어쓰지 않는다(공백 보강 전용).
#   v1.8 (2026-07-27): normalizer 면허 규칙 보강 + 아래 두 가지.
#     · 인증칸에서 신용평가등급 확인서 분리 (신용 축 소관, 실측 72건)
#     · 물품칸→면허 교차이동분의 or_group 을 단일 'x' 로 통합
#   ※ 이 상수를 올리면 select_targets 가 전건을 재정규화 대상으로 잡는다.
#   ★ 배포 절차(Phase 2 runbook): cert_master 보강과 묶어 1회만 재정규화.
#     realtime-normalize-dev 람다 동시성 0 예약 → DRY_RUN 확인 → 본실행 → 동시성 복원.

# ─────────────────────────────────────────────────────────────
# 상수 (어댑터 소관 규칙)
# ─────────────────────────────────────────────────────────────
PERF_UNIT_WHITELIST = {"원", "건"}                       # 실적 unit 화이트리스트 (그 외 → 확인필요)
SIZE_ENUM = {"sme_only", "small_only", "no_large", "no_conglomerate"}  # 'none'은 요구 아님

# 인증 라우팅/무시: required_certs에 혼입된 비(非)인증 서류를 걸러낸다 (cert 매칭 전).
CERT_ROUTE_DIRECT = ("직접생산", "직생")                 # → 품목 축 직생으로 (이미 direct_production_req로 처리)
CERT_ROUTE_SIZE   = ("중소기업확인", "중소기업제품", "소기업확인", "소상공인",
                     "중소기업 간주")  # → 규모 축
#   '소상공인' 추가: 최다값 '중소기업ㆍ소상공인확인서'(907공고)를 잡되, '성능인증(중소기업
#   기술개발제품)' 같은 실인증은 '소상공인'이 없어 안전(바 '중소기업'은 과제외라 미채택).
#   v1.9: '중소기업 간주' 추가 — '특별법인 중소기업 간주 확인서'(실측 미해석 22건) → sme_only.
CERT_IGNORE       = ("여성기업", "장애인기업", "사회적기업",      # 우대(가점) — v1 제외
                     "공장등록", "사업자등록", "납세증명", "국세완납", "지방세완납",
                     # v1.9: 미해석 전수 트리아지(5,023행)에서 확인된 비(非)인증 증빙·서류.
                     #   참가자격 인증이 아니라 계약·납품·행정 단계 서류라 축 대상이 아니다.
                     #   ★ 가족 규칙(_CERT_FAMILY)이 먼저 평가되므로 '안전확인신고증명서' 같은
                     #     실인증 증빙은 여기 토큰('증명서')에 도달하기 전에 매핑된다.
                     #   한계(문서화): 자격증·면허류 혼입은 인력·면허 축 되먹임 없이 드롭된다(v2).
                     "공제증서", "보험증서", "손해배상책임", "시험성적서",
                     "검사확인증", "합격증서", "출하증명서", "참가자격등록증",
                     "서약서", "확약서", "동의서", "신청서", "위임장", "각서", "명부",
                     "등기부등본", "인감증명", "세금계산서", "4대 보험", "4대보험",
                     "완납증명", "원산지", "수입신고", "재직증명", "경력증명", "이력서",
                     "규격서", "제원표", "제원관리", "목록표", "계약서", "매뉴얼",
                     "계획서", "보고서", "결과서", "성적서", "증권", "정품",
                     "라이선스", "라이센스", "License", "license",
                     "A/S", "AS 보증", "AS 관련", "보증서",
                     "청렴", "공정거래", "공정경쟁", "불공정", "심사위원", "설계공모",
                     "제안서", "견적서", "수료증", "이수증", "교육필증", "배치현황",
                     "현황", "서류", "증빙", "증명서", "필증", "확인증", "통보서",
                     "통지서", "지정서", "허가증", "등록증", "신고서", "등록", "허가",
                     "인가", "면허", "자격증", "자격", "기술사", "기사", "건축사",
                     "기술자", "기술인", "지도사", "영양사", "조리사", "감리원",
                     "배치", "선임", "조정자")
# v1.9: 단독으로만 무시할 원문(부분문자열로 걸면 'CSAP 보안인증서' 등 실인증까지 죽는다).
CERT_IGNORE_EXACT = {"인증서", "인증서 등", "인증", "특허", "실용신안", "특허증", "공인인증서",
                     "ISO", "EN", "DIN", "EC", "CE", "UL", "cUL", "ETL", "PSE", "RoHS",
                     "IP65", "IP66", "IP67", "인증‧인허가"}

# ── [v1.9/D-09] 인증 가족(family) 규칙 — 롱테일 정면 돌파 ──────────────────
#   미해석 5,023행 전수 트리아지(2026-07-29) 결과, 별칭으로는 못 잡는 규격번호·수식어
#   변형이 대부분이라 '정규식 → 마스터 코드' 계열 매핑을 별칭 조회 앞단에 둔다.
#   순서 = 우선순위(구체적 인증 먼저, 총칭 나중). IGNORE 보다 먼저 평가되므로
#   '안전확인신고증명서' 같은 실인증 증빙이 '증명서' 토큰에 죽지 않는다.
#   시뮬레이션 실측: 가족 2,118행 매핑 + IGNORE 1,405행 제거 → 라이브 미해석 1,043→165.
#   ★ 코드가 cert_master 에 없으면 _emit_cert 의 _fk 가드가 NULL 강등(미해석 적재) —
#     신규 10종은 cert_master_seed_p2.sql ③ 선적재 필수.
_CERT_FAMILY: list[tuple[str, "re.Pattern"]] = [(c, re.compile(p)) for c, p in [
    ("CSAP",           r"CSAP|클라우드.{0,10}보안\s*인증"),
    ("CSA_STAR",       r"CSA\s*STAR"),
    ("ISMS_P",         r"ISMS-P"),
    ("ISMS",           r"ISMS|정보보호\s*관리체계|정보보호\s*및\s*개인정보보호|개인.?정보보호\s*관리체계"),
    ("CC",             r"CC\s*인증|CC인증|\bCC\s*\(|공통평가기준"),
    ("GS",             r"GS\s*인증|GS인증|GS\s*1등급|Good\s*Software"),
    ("TTA_VERIFIED",   r"TTA"),
    ("SPS",            r"단체표준|SPS[- ]"),
    ("RRA_CONFORM",    r"적합인증|적합등록|적합성\s*평가|전자파\s*적합|전자파적합|방송통신\s*기자재|방송통신기자재|전파법|전파\s*인증|전파인증"),
    ("SAFETY_KCS",     r"KCs"),
    ("KC",             r"KC(?![A-Z])|국가통합인증|전기용품|전기안전|안전확인|자율안전확인|공급자\s*적합성|공급자적합성|어린이제품"),
    ("KS",             r"KS(?![A-Z가-힣]*스)|한국산업규격|한국산업표준"),
    ("HACCP",          r"HACCP|안전관리인증기준"),
    ("ISO_9001",       r"ISO\s*9001|ISO\s*품질"),
    ("ISO_14001",      r"ISO\s*14001"),
    ("ISO_45001",      r"ISO\s*45001"),
    ("ISO_27001",      r"ISO.{0,5}27001"),
    ("ECO_LABEL",      r"환경표지|환경마크"),
    ("GR",             r"GR\s*인증|GR인증|우수재활용|\bGR\b|GR마크"),
    ("GREEN_TECH",     r"녹색기술"),
    ("GREEN_BUILDING", r"녹색건축|G-SEED"),
    ("ZEB",            r"제로에너지"),
    ("EFF_MGMT",       r"에너지\s*소비\s*효율|에너지소비효율|효율관리기자재|에너지효율"),
    ("HIGH_EFF",       r"고효율"),
    ("WATER_FIT",      r"위생안전"),
    ("EPC",            r"성능인증"),
    ("NEP",            r"NEP|신제품\s*인증|신제품인증"),
    ("NET",            r"NET|신기술\s*인증|신기술인증|건설신기술"),
    ("EXCELLENT_PROC", r"우수조달|조달우수"),
    ("QA_PROC",        r"품질보증조달|자기품질보증"),
    ("K_MARK",         r"K\s*마크|K-마크|K마크"),
    ("Q_MARK",         r"Q\s*마크|Q-마크|Q마크|Q-Mark"),
    ("VENTURE",        r"벤처기업"),
    ("INNOBIZ",        r"이노비즈|Inno-?Biz|기술혁신형"),
    ("MAINBIZ",        r"메인비즈|경영혁신형"),
    ("RND_LAB",        r"기업부설연구소"),
    ("SEC_FUNC",       r"보안기능확인서"),
    ("CE",             r"CE\s*인증|CE인증|CE\s*마킹"),
    ("UL",             r"UL\s*인증|UL인증"),
    # ── 신규 10종 (cert_master_seed_p2.sql ③) ──
    ("STANDBY_POWER",  r"대기전력"),
    ("BF",             r"장애물\s*없는|장애물없는|BF\s*인증|BF인증|무장애|Barrier\s*Free|\bBF\b"),
    ("MIN_GREEN",      r"최소\s*녹색|최소녹색"),
    ("INNO_PROD",      r"혁신제품"),
    ("NRE_CERT",       r"신재생에너지"),
    ("SP_CERT",        r"SP인증|소프트웨어\s*프로세스|소프트웨어프로세스"),
    ("SEC_SVC",        r"정보보호\s*전문\s*서비스|정보보호전문서비스"),
    ("EDU_SAFE",       r"교육시설\s*안전|교육시설안전"),
    ("GD_MARK",        r"^GD$|우수디자인|Good\s*Design"),
    ("TYPE_APPROVAL",  r"형식승인|형식인증|형식증명|KFI"),
]]
# v1.8: 인증칸에 섞인 신용 서류 → 신용 축 소관이라 인증에서 뺀다.
#   실측 '신용평가등급 확인서' 42 + '신용평가등급확인서' 30 = 72건이 cert 미해석으로 쌓여 있었다.
#   (신용 요구 자체는 bid_table.credit_rating_req → bid_require_credit 로 별도 적재됨)
CERT_ROUTE_CREDIT = ("신용평가등급", "신용등급")

# 실적 basis에서 집계 방식 판정
_AGG_SUM   = ("누계", "합산", "합계", "총")
_AGG_COUNT = ("건 이상", "건이상", "회 이상", "횟수")

# 면허 코드로 확정된 method (그 외는 코드로 못 씀 — 시공능력 업종 해석 등에 사용)
_LICENSE_RESOLVED = ("rule0", "alias", "rule+alias")


# ─────────────────────────────────────────────────────────────
# 조회 컨텍스트 구성 — normalizer.LookupContext를 DB에서 1회 로드
# ─────────────────────────────────────────────────────────────
def build_lookup_context(conn, norm: Any):
    """전 엔티티 별칭 + license/item 코드 set + item 이름역맵을 로드해 LookupContext 생성.
    (canon_key 정규화는 LookupContext.__post_init__가 적용하므로 원문 그대로 전달.)"""
    def rows(sql):
        with conn.cursor() as c:
            c.execute(sql); return c.fetchall()
    alias = {(et, txt): code
             for et, txt, code in rows("SELECT entity_type, alias_text, canonical_code FROM master_alias")}
    license_codes = {r[0] for r in rows("SELECT license_code FROM license_master")}
    item_codes    = {r[0] for r in rows("SELECT item_code FROM item_code_master")}
    item_names    = {name: code for name, code in rows("SELECT item_name, item_code FROM item_code_master")}
    return norm.LookupContext(alias=alias, license_codes=license_codes,
                              item_codes=item_codes, item_names=item_names)


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
    def load(cls, conn, norm: Any) -> "MasterNames":
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
# 어댑터 소관 파서 (normalizer 밖)
# ─────────────────────────────────────────────────────────────
def _num(v) -> float | None:
    if v is None: return None
    if isinstance(v, (int, float)): return float(v)
    s = re.sub(r"[^\d.]", "", str(v))
    return float(s) if s else None

def parse_performance(req: dict) -> dict:
    """실적 요구 1건 → bid_require_performances 행. 미해석은 통과가 아니라 확인필요."""
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

def parse_capacity(req: dict, norm: Any, ctx: Any) -> dict:
    """능력 요구 1건 → bid_require_capacity 행 (시공능력평가액)."""
    min_value = _num(req.get("value"))
    name = req.get("name") or ""
    lic = None
    if name:                    # 업종명이 붙어 있으면 면허 정규화로 코드 시도
        res = norm.normalize_license(name, ctx)
        if res.codes and res.method in _LICENSE_RESOLVED and res.codes[0] in ctx.license_codes:
            lic = res.codes[0]  # 마스터에 실재하는 코드만 (FK 위반 방지)
    parsed = min_value is not None
    return {
        "capacity_type": "시공능력평가액",
        "license_code": lic,
        "min_value": int(min_value) if parsed else None,
        "parse_status": "parsed" if parsed else "unparsed",
        "name_raw": name or (req.get("unit") or "능력요건"),
        "method": "rule0" if lic else "none",
    }

def route_cert(name_raw: str, norm: Any, mn: MasterNames) -> dict | None:
    """
    required_certs 1건 처리.
    반환 None = 인증 아님(라우팅/무시). dict = bid_require_certs 행(코드 or 미해석).
    v1.9: 라우팅 3갈래(직생/규모/신용)의 목적지 반영은 cert_feedback() 프리패스가 담당.
          여기의 None 반환은 '인증 축에서 뺀다'는 뜻일 뿐 요구 유실이 아니게 됐다.
    """
    s = str(name_raw)
    if any(k in s for k in CERT_ROUTE_DIRECT):  return None  # 직생 → cert_feedback 이 반영
    if any(k in s for k in CERT_ROUTE_SIZE):    return None  # 규모 → cert_feedback 이 반영
    if any(k in s for k in CERT_ROUTE_CREDIT):  return None  # 신용 → cert_feedback 이 반영
    # [v1.9/D-09] 가족 규칙 — IGNORE 보다 먼저. 규격번호·수식어 변형을 계열로 접는다.
    for fam_code, rx in _CERT_FAMILY:
        if rx.search(s):
            return {"cert_code": fam_code,
                    "cert_name": mn.cert.get(fam_code),
                    "method": "family", "name_raw": s}
    if any(k in s for k in CERT_IGNORE):        return None  # 우대·증빙·서류 → 무시(의도된 드롭)
    if s.strip() in CERT_IGNORE_EXACT:          return None  # v1.9: 무의미 단독 원문 무시
    code = mn.cert_alias.get(norm.canon_key(s))
    return {
        "cert_code": code,
        "cert_name": mn.cert.get(code) if code else None,
        "method": "alias" if code else "none",
        "name_raw": s,
    }


# [v1.9/D-04] 인증칸 → 타 축 되먹임 프리패스 ------------------------------------
#   신용 등급 토큰: 긴 것 우선(AAA 가 A 로 잡히지 않게). 회사채/기업신용 계열만 v1.
#   CP(A1~A3) 체계는 실측 후 v2. '등급' 앞의 토큰만 신뢰한다('B등급 이상' → B).
_GRADE_RE  = re.compile(r"(AAA|AA|BBB|BB|CCC|CC|A|B|C|D)\s*([+\-])?\s*등급")
_ITEM10_RE = re.compile(r"\d{10}")   # 세부품명번호 10자리 (예: 8111179901)

def cert_feedback(bid: dict) -> dict:
    """required_certs 문자열을 훑어 직생/규모/신용 필드의 '공백 보강' 재료를 만든다.
    LLM 필드가 이미 차 있으면 build_rows 쪽에서 이 값을 쓰지 않는다(덮어쓰기 금지).
    한계(v2): 면허칸(required_licenses)에 섞인 직생/규모/신용 문자열은 스캔하지 않는다."""
    fb = {"dp_req": False, "dp_items": [],       # [(세부품명번호, 원문)]
          "size": None, "size_raw": None,
          "credit": False, "credit_min_grade": None, "credit_raw": None}
    for c in (bid.get("required_certs") or []):
        s = str(c.get("name") if isinstance(c, dict) else c)
        if any(k in s for k in CERT_ROUTE_DIRECT):        # route_cert 와 동일한 판정 순서
            fb["dp_req"] = True
            fb["dp_items"] += [(code, s) for code in _ITEM10_RE.findall(s)]
        elif any(k in s for k in CERT_ROUTE_SIZE):
            if fb["size"] is None:
                # '중소기업ㆍ소상공인확인서'(가운뎃점 변형 포함) → sme_only
                # '소기업ㆍ소상공인확인서' 등 중소기업 미포함 → small_only (보수적: 더 좁은 제한)
                fb["size"] = "sme_only" if "중소기업" in s else "small_only"
                fb["size_raw"] = s
        elif any(k in s for k in CERT_ROUTE_CREDIT):
            fb["credit"] = True
            fb["credit_raw"] = fb["credit_raw"] or s
            if fb["credit_min_grade"] is None:
                m = _GRADE_RE.search(s)
                if m:
                    fb["credit_min_grade"] = m.group(1) + (m.group(2) or "")
    return fb


# ─────────────────────────────────────────────────────────────
# 행 빌더 (순수 — DB 안 건드림). bid_row(dict) → {table: [row dict, ...]}
# ─────────────────────────────────────────────────────────────
def build_rows(bid: dict, norm: Any, mn: MasterNames, ctx: Any) -> dict[str, list[dict]]:
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

    # 마스터에 없는 코드는 NULL로 강등(확인필요) — FK 위반 방지.
    #   normalizer 일부 경로(면허칸 route:item 등)가 존재 확인 없이 코드를 뽑을 수 있어,
    #   적재 직전 마스터 멤버십(=FK 대상 집합)으로 최종 가드한다. 원문은 name_raw로 보존.
    def _fk(code, names):
        return code if (code is not None and code in names) else None

    # 품목 적재 헬퍼 (코드 dedup — 면허칸 교차이동 + 물품칸 중복 방지)
    # [v1.9/D-04] 인증칸 되먹임: 직생 문자열이 있으면 dp_req 승격 (LLM 필드 공백 보강).
    fb = cert_feedback(bid)
    dp_req = bool(bid.get("direct_production_req")) or fb["dp_req"]
    _item_seen: set = set()
    def _emit_item(code, method, source, name_raw):
        code = _fk(code, mn.item)
        if code is not None:
            if code in _item_seen:
                return
            _item_seen.add(code)
        out["items"].append({
            "bid_ntce_no": no, "bid_ntce_ord": ord_, "item_code": code,
            "item_name": mn.item.get(code) if code else None,
            "direct_production_req": dp_req, "method": method, "source": source,
            "name_raw": name_raw,
        })

    # 인증 적재 헬퍼 (코드 dedup — 면허칸 구제분 + required_certs 중복 방지)
    _cert_seen: set = set()
    def _emit_cert(row):
        code = _fk(row.get("cert_code"), mn.cert)
        row["cert_code"], row["cert_name"] = code, (mn.cert.get(code) if code else None)
        if code is not None:
            if code in _cert_seen:
                return
            _cert_seen.add(code)
        row.update(bid_ntce_no=no, bid_ntce_ord=ord_)
        out["certs"].append(row)

    # ── ① 면허 (한 요구 = 한 Result = 한 or_group; codes = OR, 요구끼리 = AND) ──
    #   or_group은 요구 항목 인덱스로 네임스페이스 → 서로 다른 요구가 AND로 유지된다.
    for req_idx, req in enumerate(bid.get("required_licenses") or [], start=1):
        raw = (req.get("name_raw") if isinstance(req, dict) else req) or ""
        res = norm.normalize_license(raw, ctx)
        if res.method == "route:item":       # 면허칸에 기재된 물품 → 품목 축으로 교차 이동
            for code in (res.codes or [None]):
                _emit_item(code, "route:item", "license_field", raw)
            continue
        if res.method == "ignored":          # 면허 아님 → 인증만 구제 후 드롭
            cr = route_cert(raw, norm, mn)   # 면허칸에 섞인 인증(예: 품질인증(GS)) → 인증축(dedup)
            if cr and cr.get("cert_code"):   # 알려진 인증으로 풀릴 때만. 무역업고유번호·총칭 등은 드롭
                _emit_cert(cr)
            continue
        grp = str(req_idx)
        for code in (res.codes or [None]):   # 미해석·마스터부재 → NULL 1행 (확인필요 표면화)
            lc = _fk(code, mn.license)
            out["licenses"].append({
                "bid_ntce_no": no, "bid_ntce_ord": ord_, "or_group": grp,
                "license_code": lc, "license_name": mn.license.get(lc) if lc else None,
                "method": res.method, "source": "license_field",
                "qualifier": res.qualifier or None, "name_raw": raw,
            })

    # ── ② 지역 ──
    for req in (bid.get("region_limit_names") or []):
        raw = (req.get("name_raw") if isinstance(req, dict) else req) or ""
        res = norm.normalize_region(raw, ctx)
        if res.method == "ignored":
            continue
        flag = ("nationwide" if res.method == "special:nationwide"
                else "site_ref" if res.method == "special:site_ref" else None)
        code = _fk(res.codes[0] if res.codes else None, mn.region)
        out["regions"].append({
            "bid_ntce_no": no, "bid_ntce_ord": ord_, "region_code": code,
            "region_name": mn.region.get(code) if code else None,
            "method": res.method, "flag": flag, "name_raw": raw,
        })

    # ── ③ 인력 (등급만 정규화. 등급 무관/미해석이어도 인원 요구는 남으므로 행 유지) ──
    for req in (bid.get("personnel_reqs") or []):
        grade = req.get("grade"); rfield = req.get("field"); cnt = req.get("count")
        res = norm.normalize_personnel_grade(grade, ctx)
        code = _fk(res.codes[0] if res.codes else None, mn.personnel)
        out["personnel"].append({
            "bid_ntce_no": no, "bid_ntce_ord": ord_, "qual_code": code,
            "qual_name": mn.personnel.get(code) if code else None,
            "role_field": rfield, "headcount": int(cnt) if cnt else 1,
            "method": res.method, "grade_raw": grade,
        })

    # ── ④ 품목 + 직생 흡수 (route:license면 물품칸의 업종 → 면허 축으로 교차 이동) ──
    #   v1.8: 교차이동분은 or_group 을 단일 'x' 로 묶는다.
    #     종전에는 x1, x2, x3… 로 흩어져 매칭에서 각각 독립 AND 그룹으로 세어졌고,
    #     그 결과 "이 품목을 하려면 이 면허 중 하나" 라는 유추가 "전부 보유" 요구로 바뀌었다.
    #     (실측: live 31건이 과조임. R26BK01426807 은 9개 AND 그룹이 되어 폐기물 4종을 모두 요구)
    #     품목 경유는 유추이므로 OR 가 맞다. compute_match_results 도 같은 판단으로 병합 중이며,
    #     여기서 고치면 SQL 쪽 보정은 방어용으로만 남는다.
    for req in (bid.get("item_codes") or []):
        if isinstance(req, dict):
            raw = req.get("code") or req.get("name") or ""; type_hint = req.get("type")
        else:
            raw = req; type_hint = None
        res = norm.normalize_item_code(str(raw), ctx, type_hint)
        if res.method == "route:license":    # 물품칸에 기재된 업종 → 면허 축으로 교차 이동
            for code in (res.codes or [None]):
                lc = _fk(code, mn.license)
                out["licenses"].append({
                    "bid_ntce_no": no, "bid_ntce_ord": ord_, "or_group": "x",
                    "license_code": lc, "license_name": mn.license.get(lc) if lc else None,
                    "method": res.method, "source": "item_field",
                    "qualifier": None, "name_raw": str(raw),
                })
            continue
        if res.method == "ignored":          # 품목 아님 → 드롭
            continue
        code = res.codes[0] if res.codes else None   # none → NULL 1행 (확인필요)
        _emit_item(code, res.method, "item_field", str(raw))
    # [v1.9/D-04] 직생 인증 문자열에 명시된 세부품명번호 회수 (예: 직접생산확인증명서[…8111179901])
    #   _emit_item 의 _fk 가드가 마스터 부재 코드를 NULL 로 강등하므로 FK 안전. 코드 dedup 공유.
    #   source 는 CHECK (item_field|license_field) 제약상 'item_field' 로 두고,
    #   출처 추적은 method='route:cert' + name_raw(인증 원문)가 담당한다.
    for code10, raw_s in fb["dp_items"]:
        _emit_item(code10, "route:cert", "item_field", raw_s[:300])
    # 직생 요구인데 품목이 하나도 없으면 요구 유실 방지용 플레이스홀더 행
    if dp_req and not out["items"]:
        _emit_item(None, "none", "item_field", "직접생산확인 요구(품목 미상)")

    # ── ⑤ 실적 (파싱) ──
    for req in (bid.get("performance_reqs") or []):
        row = parse_performance(req); row.update(bid_ntce_no=no, bid_ntce_ord=ord_)
        out["performances"].append(row)

    # ── ⑥ 인증 (required_certs 라우팅 후 적재; 면허칸 구제분과 코드 dedup) ──
    for c in (bid.get("required_certs") or []):
        raw = c.get("name") if isinstance(c, dict) else c
        row = route_cert(raw, norm, mn)
        if row: _emit_cert(row)

    # ── ⑦ 시공능력 (파싱) ──
    for req in (bid.get("capacity_reqs") or []):
        row = parse_capacity(req, norm, ctx); row.update(bid_ntce_no=no, bid_ntce_ord=ord_)
        out["capacity"].append(row)

    # ── ⑧ 규모 (요구 있을 때만 1행. 'none'/미상 → 행 없음) ──
    size = bid.get("company_size_limit")
    if size in SIZE_ENUM:
        out["size"].append({"bid_ntce_no": no, "bid_ntce_ord": ord_, "size_limit": size,
                             "name_raw": size, "method": "rule0"})
    elif fb["size"]:
        # [v1.9/D-04] LLM 필드 공백을 인증칸 문자열로 보강. 원문을 name_raw 에 보존.
        out["size"].append({"bid_ntce_no": no, "bid_ntce_ord": ord_, "size_limit": fb["size"],
                             "name_raw": (fb["size_raw"] or fb["size"])[:300],
                             "method": "route:cert"})

    # ── ⑨ 신용 (요구 TRUE 또는 인증칸 신용 문자열 존재 시 1행) ──
    if bid.get("credit_rating_req") or fb["credit"]:
        # [v1.9/D-05] min_grade 파싱 — 'B등급 이상' → 'B'. 등급 미상은 종전대로 NULL.
        #   (매칭 SQL Phase 3 는 min_grade 有 행만 supp 축 생성 — 등급 미상은 축 미생성 결정)
        out["credit"].append({"bid_ntce_no": no, "bid_ntce_ord": ord_, "required": True,
                              "min_grade": fb["credit_min_grade"],
                              "name_raw": (fb["credit_raw"] or "신용등급 요구")[:300],
                              "method": "route:cert" if (fb["credit"] and not bid.get("credit_rating_req"))
                                        else "rule0"})

    # ── 지역의무공동도급 의무지역 (신호, 다중값) ──
    for req in (bid.get("jntcontrct_duty_rgns") or []):
        raw = (req.get("name_raw") if isinstance(req, dict) else req) or ""
        res = norm.normalize_region(raw, ctx)
        code = _fk(res.codes[0] if res.codes else None, mn.region)
        out["region_duty"].append({
            "bid_ntce_no": no, "bid_ntce_ord": ord_, "region_code": code,
            "region_name": mn.region.get(code) if code else None,
            "method": res.method, "name_raw": raw,
        })
    return out


# ─────────────────────────────────────────────────────────────
# 쓰기 (멱등 트랜잭션) — 공고 단위 DELETE(summary CASCADE)→INSERT
# ─────────────────────────────────────────────────────────────
_CHILD_TABLES = {
    "licenses": "bid_require_licenses", "regions": "bid_require_regions",
    "personnel": "bid_require_personnel", "items": "bid_require_items",
    "performances": "bid_require_performances", "certs": "bid_require_certs",
    "capacity": "bid_require_capacity", "size": "bid_require_size",
    "credit": "bid_require_credit", "region_duty": "bid_require_region_duty",
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
            cur.execute("DELETE FROM bid_require_summary WHERE bid_ntce_no=%s AND bid_ntce_ord=%s", (no, ord_))
            for r in rows["summary"]:
                _insert(cur, "bid_require_summary", r)
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
            LEFT JOIN bid_require_summary s USING (bid_ntce_no, bid_ntce_ord)
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

def run(conn, norm: Any, current_version: str = NORMALIZER_VERSION, dry_run: bool = True) -> dict:
    # autocommit: write_bid의 `with conn.transaction()`이 공고별 독립 트랜잭션으로 커밋되게 한다.
    #   (비autocommit이면 초기 SELECT가 연 트랜잭션 안에 전 공고가 savepoint로 묶여 = 한 방에 커밋/롤백,
    #    락도 끝까지 유지. autocommit이 공고 단위 커밋·재개가능·짧은 락을 보장.)
    if not dry_run:
        conn.autocommit = True
    mn  = MasterNames.load(conn, norm)
    ctx = build_lookup_context(conn, norm)         # normalizer.LookupContext (1회 구성)
    targets = select_targets(conn, current_version)
    log.info("대상 공고 %d건 (dry_run=%s)", len(targets), dry_run)
    n_bids = n_rows = 0
    failures: list = []
    for bid in targets:
        no, ord_ = bid["bid_ntce_no"], bid["bid_ntce_ord"]
        try:
            rows = build_rows(bid, norm, mn, ctx)
            n_rows += write_bid(conn, no, ord_, rows, dry_run)
            n_bids += 1
        except Exception as e:                     # 한 공고 실패가 전체를 멈추지 않게 (재실행 시 자동 재시도)
            failures.append((no, ord_, str(e).splitlines()[0]))
            log.warning("SKIP %s_%s: %s", no, ord_, str(e).splitlines()[0])
    log.info("완료: 공고 %d건, 행 %d개, 실패 %d건%s", n_bids, n_rows, len(failures),
             " (DRY_RUN — 미기록)" if dry_run else "")
    if failures:
        head = ", ".join(f"{a}_{b}" for a, b, _ in failures[:20])
        log.warning("실패 공고 %d건 (미커밋 → 재실행 시 자동 재시도): %s%s",
                    len(failures), head, " …" if len(failures) > 20 else "")
    return {"bids": n_bids, "rows": n_rows, "failed": len(failures), "dry_run": dry_run}


if __name__ == "__main__":
    import psycopg
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    import normalizer  # 실제 normalizer.py 주입
    dsn = os.environ["DB_DSN"]                     # key=value 전체 문자열
    dry = os.environ.get("DRY_RUN", "1") != "0"    # 기본 DRY_RUN
    with psycopg.connect(dsn) as conn:
        run(conn, normalizer, dry_run=dry)