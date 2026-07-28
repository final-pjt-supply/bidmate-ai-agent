"""BidMate 정규화 분해기 v1 — name_raw → 표준 코드 (P2 Stage 1)

순수 함수 모듈: DB 접속 없음. 조회 자료(별칭·마스터)는 LookupContext로 주입한다.
규칙 순서와 근거: 2026-07-21 커버리지 검수 실측 (작업일지 참조)
  ⓪ 내장 코드 추출(+42.7%p 실증) → ① 구두점·공백 통일 → ①-b 법령 전치사 제거
  → ② 접미사 제거 → ③ OR 분해 → ④ 무시 목록 → ⑤ 특수/라우팅

v1.8 (2026-07-27) — 면허 미해석 실측 분석 후 보강.
  실측: 미해석 2,203행 / live 요구공고 849건 중 232건(27.3%)에 미해석 포함.
        verdict '확인필요' 163건의 사실상 단독 원인.
  · ⓪ search → finditer : "[A(9902)] 또는 [B(3230)] 또는 [C(3244)]" 에서 첫 코드만 잡고
    끝나던 문제. OR 연결이면 전부 수집한다. AND('과/와')는 Result(codes=OR)가 표현할 수
    없으므로 첫 코드만 쓰되 유실분을 qualifier 에 남긴다(추적용).
  · ②  "~로 신고한 자" 미지원 → 정보통신 엔지니어링/기술사 계열 62건이 여기서 막혔다.
  · ①-b 법령 전치사 신설 : "전력기술관리법 제2조제3호에 따른 X", "건설산업기본법에 따른 X".
  · ④  인증·증서류 무시 목록 확대 — 면허칸에 섞인 안전인증·적합성평가·보험증서 등.
  ※ 표현 변형(전력시설물 설계 4종 41건, 소방설계 3종 34건, 정보통신 8종 62건 등)은
    규칙이 아니라 master_alias 등록으로 해결한다. canon_key 가 공백·구두점을 흡수하므로
    대표형 1건 등록이면 띄어쓰기 변형은 자동으로 함께 잡힌다.

배치 러너 사용법:
    ctx = LookupContext(alias=..., license_codes=..., item_codes=..., item_names=...)
    r = normalize_license("토목(또는 토목건축)공사업 등록", ctx)
    # r.codes == ['0001','0003'] (or_group), r.method == 'rule+alias'
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


# ────────────────────────────────────────────────
# 조회 컨텍스트 — 배치 러너가 DB에서 1회 로드해 주입
# ────────────────────────────────────────────────
@dataclass
class LookupContext:
    alias: dict            # {(entity_type, alias_text): canonical_code}
    license_codes: set     # license_master.license_code 전체 (⚠️ is_active 필터 없이 로드 — 폐지 업종 해석 허용)
    item_codes: set        # item_code_master.item_code 전체
    item_names: dict       # {item_name: item_code}  (물품 이름 역매핑용)

    def __post_init__(self):
        # v1.5: 별칭 키를 canon_key로 정규화 — 조회 텍스트와 같은 변환을 양쪽에 적용
        #       (DB 공식명의 ㆍ/․/쉼표/공백이 조회 측 변환과 어긋나던 비대칭 버그 수정)
        self.alias = {(e, canon_key(t)): c for (e, t), c in self.alias.items()}
        self.item_names = {canon_key(n): c for n, c in self.item_names.items()}

    def find(self, entity: str, text: str):
        return self.alias.get((entity, canon_key(text)))


@dataclass
class Result:
    codes: list = field(default_factory=list)  # 확정 코드 (OR 관계면 복수 — 하나라도 보유 시 충족)
    method: str = "none"   # rule0 / alias / rule+alias / name_map / special:* / route:* / ignored / none
    qualifier: str = ""    # 보존 한정조건 (예: 주력분야:기계설비공사)
    raw: str = ""

    @property
    def matched(self) -> bool:
        return bool(self.codes)


# ────────────────────────────────────────────────
# 공통 유틸 — 규칙 ①
# ────────────────────────────────────────────────
_PUNCT_MAP = str.maketrans({"․": "·", "･": "·", "・": "·", "ㆍ": "·", "｡": "·", "‧": "·"})


def _clean(text: str) -> str:
    """구두점 통일 + 공백 축소."""
    t = text.translate(_PUNCT_MAP)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def canon_key(text: str) -> str:
    """v1.5: 별칭 조회용 정규화 키 — 구두점 통일 + 쉼표→· + 따옴표 제거 + 공백 전부 제거.
    별칭 키(공식명)와 조회 텍스트 양쪽에 동일 적용해야 한다.

    v1.8: 가운뎃점(·)과 마침표(.)도 제거한다. 통일만으로는 '개수' 차이를 못 넘었다.
      실측  원문 '금속·창호·지붕·건축물조립공사업'  ↔ 마스터 '금속창호ㆍ지붕건축물조립공사업'(4991)
            원문 '상하수도설비공사업'                ↔ 마스터 '상ㆍ하수도설비공사업'(4996)
            원문 '학술연구용역'                      ↔ 마스터 '학술.연구용역'(1169)
      구분자 유무만으로 갈리는 서로 다른 업종은 마스터에 없으므로 안전하다."""
    t = _clean(str(text))
    t = t.replace(",", "·").replace("‘", "").replace("’", "").replace("'", "")
    t = t.replace("·", "").replace(".", "")
    return re.sub(r"\s+", "", t)


# ────────────────────────────────────────────────
# 면허 (license)
# ────────────────────────────────────────────────
# 규칙 ⓪: 괄호 내 4자리 업종코드 — "(1468)", "(업종코드: 1169)", "(6202, 주력분야…)" (v1.3: 쉼표 종결 허용)
# v1.8: 종결자 확대 — "[업종코드 4990]", "기타자유업(행사대행업) 9901" 형식을 놓치고 있었다.
#   (산업디자인전문회사 4종 [4440][4442][4443][4444] 가 하나도 안 잡히던 원인)
_LIC_CODE_RE = re.compile(r"(?<![0-9])(\d{4})\s*(?:[,)\]]|$)")
# v1.8: 다중 코드의 연결어 판별. '또는'=OR(codes 에 전부), '과/와'=AND(Result 가 표현 불가).
_LIC_OR_CONN  = re.compile(r"또는|혹은|이거나")
_LIC_AND_CONN = re.compile(r"[가-힣)\]]\s*(?:과|와)\s+[가-힣\[(]")
# v1.3: 단독 10자리 세부품명번호 내장 ("전기히트펌프(4010180601)") → item 라우팅
_LIC_ITEM10_RE = re.compile(r"(?<![0-9])(\d{10})(?![0-9])")
# 규칙 ②: 접미사 (긴 패턴 먼저)
_LIC_SUFFIXES = [
    re.compile(r"[을를]?\s*(?:입찰참가자격|참가자격)[을]?\s*등록(?:한\s*자)?$"),
    re.compile(r"(?:으로|로)\s*입찰참가자격[을]?\s*등록(?:한\s*자)?$"),
    re.compile(r"[을를]\s*등록한\s*(?:자|업체)$"),
    re.compile(r"[을를]?\s*등록\s*(?:한\s*자|한\s*업체)?$"),          # v1.4: "~을 등록" 포함
    re.compile(r"(?:으로|로)\s*등록(?:한\s*자)?$"),                   # v1.4: "~으로 등록한 자"
    re.compile(r"(?:으로|로)\s*신고(?:를?\s*필한\s*자)?$"),           # v1.3: "~로 신고"
    re.compile(r"(?:으로|로)\s*신고한\s*자$"),                        # v1.8: "~로 신고한 자" (실측 62건)
    re.compile(r"에게\s*신고를?\s*필한\s*자$"),                       # v1.8: "국토교통부장관에게 신고를 필한 자"
    re.compile(r"(?<=[업사소자인)\]])(?:으로|로)$"),                    # v1.8: "…설계업으로", "…(1종)으로" 잔재
    re.compile(r"\s*면허증$"),                                       # v1.8: "정보통신공사업 면허증"
    re.compile(r"\s*허가$"),                                         # v1.8: "…제조·판매업 허가"
    re.compile(r"[을를]?\s*개설\s*및\s*신고를?\s*필한\s*자$"),         # v1.4: 건축사사무소 개설·신고
    re.compile(r"\s*업종$"),
    re.compile(r"\s*면허$"),
]
# v1.8 규칙 ①-b: 법령 전치사 제거 — "전력기술관리법 제2조제3호에 따른 X", "건설산업기본법에 따른 X"
#   근거법령이 앞에 붙어 별칭 조회가 통째로 실패하던 케이스.
_LIC_LAW_PREFIX = re.compile(
    r"^[「『\[]?[가-힣]{2,}법(?:령)?[」』\]]?"
    r"(?:\s*제\d+조(?:\s*제\d+항)?(?:\s*제\d+호)?)?"
    r"\s*에\s*(?:따른|의한|의하여)\s*")

# v1.4: 총칭 표기 → 공식 세부 업종들의 OR (identity 별칭 경유로 코드 해석 — 코드 하드코딩 금지)
_LIC_UMBRELLA = {
    "소프트웨어사업자": [
        "소프트웨어사업자(패키지소프트웨어개발.공급사업)",
        "소프트웨어사업자(컴퓨터관련서비스사업)",
        "소프트웨어사업자(디지털콘텐츠개발서비스사업)",
        "소프트웨어사업자(데이터베이스제작및검색서비스사업)",
    ],
}

# 규칙 ④: 무시 목록 (면허가 아닌 것) — v1.2: 등록증·허가증·조합 등재 추가 (7/22 표본 검수)
_LIC_IGNORE = re.compile(
    r"확인서|증명서|고유번호|품질인증|입찰참가자격등록을 한 자|공급인증|성적서"
    r"|등록증$|허가증$|조합공동사업|기업리스트|등재"
    # v1.8: 면허칸에 섞인 인증·증서류 (실측 — 안전인증 10, 적합성평가 7, 전자파 적합 인증서 7 …)
    r"|안전인증|적합성평가|적합\s*인증|형식승인|인증서$"
    r"|보험증서|공제증서|수료증|검사확인증|합격증|서약서|확약서")
# v1.2: 면허 필드에 혼입된 물품 참가자격 → item 도메인 라우팅
_LIC_ITEM_REG = re.compile(r"물품으로\s*(?:의\s*)?입찰참가|제조(?:\s*또는\s*공급)?\s*물품")
_LIC_ITEM_CODE = re.compile(r"(\d{10}|\d{8})")
# v1.2: 접미사 제거 후 잔여가 총칭뿐이면 무시 (예: "공사업 등록 또는 면허")
_LIC_GENERIC = {"공사업", "면허", "등록", "공사업 또는 면허"}
# qualifier 보존: 괄호/대괄호 안 주력분야·분담이행 조항 (v1.5.1: 대괄호 안 중첩 괄호 허용)
_LIC_QUALIFIER = re.compile(
    r"\([^()]*(?:주력분야|분담이행)[^()]*\)"          # 괄호형: (주력분야:…)
    r"|\[[^\[\]]*(?:주력분야|분담이행)[^\[\]]*\]")     # 대괄호형: [주력분야 : X(제1종)] — 내부 괄호 허용
# v1.5: 문장형 주력분야 조항 — "~ 중 'X'를 주력분야로 등록(한 업체)(에 한함)"
_LIC_MAINFIELD = re.compile(
    r"\s*중?\s*['‘]?[^'’()\s]+['’]?[를을]?\s*주력분야로\s*등록(?:한\s*(?:자|업체))?(?:에\s*한함)?$")
# v1.5: 소방 복합 (일반(기계 및 전기)) 또는 전문 — 보수적 근사: 전문소방시설공사업 단독 매핑
_LIC_FIRE_COMPOSITE = re.compile(
    r"일반소방시설공사업\s*\(\s*기계\s*및\s*전기[^)]*\)\s*또는\s*전문소방시설공사업")
# "전문건설업 중 X" / "종합건설업 중 X" / "종합건설업(X)"
_LIC_WRAPPER = [
    (re.compile(r"^(?:전문건설업|종합건설업)\s*중\s*"), ""),
    (re.compile(r"^종합건설업\((.+)\)$"), r"\1"),
]

def _lic_lookup(ctx: LookupContext, text: str):
    """v1.5: canon_key가 양쪽에 적용되므로 단일 조회로 충분 (쉼표·구두점·공백 변형 흡수)."""
    return ctx.find("license", text)

def _strip_suffixes(text: str) -> str:
    t = text
    changed = True
    while changed:
        changed = False
        for pat in _LIC_SUFFIXES:
            new = pat.sub("", t).strip()
            if new != t:
                t, changed = new, True
    return t


def _split_or(text: str) -> list[str]:
    """규칙 ③: OR 분해.
    "토목(또는 토목건축)공사업" → ["토목공사업", "토목건축공사업"]
    "A 또는 B" → ["A", "B"]  (괄호 밖 '또는'만 분리)
    """
    m = re.match(r"^(.*?)\(또는\s*([^)]+)\)(.*)$", text)
    if m:
        head, alt, tail = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
        return [f"{head}{tail}", f"{alt}{tail}"]
    # v1.5: 괄호 안 OR — "전기분야설계업(종합 또는 전문 1종)" → [X(종합), X(전문 1종)]
    m = re.match(r"^(.+?)\(([^()]+?)\s*또는\s*([^()]+?)\)$", text)
    if m:
        head, a, b = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
        return [f"{head}({a})", f"{head}({b})"]
    # v1.8: 괄호 안 "A 및 B" — 마스터가 분야별로 분리 보유하는 경우.
    #   실측  '일반소방시설설계업(기계 및 전기)' ↔ 마스터 1490 (기계) / (전기) 별도
    #         '일반소방공사감리업(기계 및 전기)' ↔ 마스터 1493 (기계)
    #   의미상 AND(동시보유)지만 Result(codes=OR)로는 표현할 수 없어 관대한 쪽으로 둔다.
    #   ※ '및' 양쪽 공백을 요구한다 — '데이터베이스제작및검색서비스사업'처럼 붙어 있는
    #     업종명을 쪼개지 않기 위해서.
    m = re.match(r"^(.+?)\(\s*([^()]+?)\s+및\s+([^()]+?)\s*\)$", text)
    if m:
        head, a, b = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
        return [f"{head}({a})", f"{head}({b})"]
    # 괄호 깊이 0에서의 " 또는 " 분리
    parts, depth, buf = [], 0, ""
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if depth == 0 and text.startswith(" 또는 ", i):
            parts.append(buf.strip())
            buf = ""
            i += len(" 또는 ")
            continue
        buf += ch
        i += 1
    parts.append(buf.strip())
    return [p for p in parts if p]


def normalize_license(raw: str, ctx: LookupContext) -> Result:
    r = Result(raw=raw)
    if not raw or not raw.strip():
        return r
    text = _clean(raw).strip("[]")           # v1.3: 대괄호 래핑 제거
    # ①-b (v1.8) 법령 전치사 제거 — 반복 적용 (중첩 인용 대응)
    while True:
        t2 = _LIC_LAW_PREFIX.sub("", text).strip()
        if t2 == text:
            break
        text = t2

    # ⓪ 내장 업종코드 (v1.8: search → finditer, 다중 코드 수집)
    found = [m.group(1) for m in _LIC_CODE_RE.finditer(text)]
    found = [c for c in dict.fromkeys(found) if c in ctx.license_codes]   # 순서 보존 dedup
    if found:
        if len(found) == 1:
            r.codes, r.method = found, "rule0"
            return r
        if _LIC_OR_CONN.search(text):
            r.codes, r.method = found, "rule0"        # OR: 하나라도 보유 시 충족
            return r
        if _LIC_AND_CONN.search(text):
            # AND 조합. Result(codes=OR)로는 표현할 수 없다.
            #   느슨한 쪽(첫 코드만)을 택하고 유실분을 qualifier 에 남긴다.
            #   ※ 정확히 표현하려면 or_group 을 나눠야 하는데, 그러면 이 공고의 항목들이
            #     실제로는 '대안 조합'인 경우(실측 R26BK01426807: 건물관리+폐기물A / +B / +C / +D)
            #     오히려 더 조여진다. 항목 간 OR 판별은 추출 스키마 몫이라
            #     여기서는 관대한 쪽을 유지한다.
            r.codes, r.method = [found[0]], "rule0"
            r.qualifier = "AND조합 미반영: " + "+".join(found[1:])
            return r
        r.codes, r.method = found, "rule0"            # 연결어 불명 → OR 로 관대 처리
        return r

    # ⓪-b (v1.2) 물품 참가자격 혼입 → item 도메인 라우팅
    if _LIC_ITEM_REG.search(text):
        mc = _LIC_ITEM_CODE.search(text)
        if mc:
            r.codes, r.method = [mc.group(1)], "route:item"
        else:
            r.method = "ignored"
        return r
    # ⓪-c (v1.3) 단독 10자리 세부품명번호 내장 → item 라우팅
    m10 = _LIC_ITEM10_RE.search(text)
    if m10 and m10.group(1) in ctx.item_codes:
        r.codes, r.method = [m10.group(1)], "route:item"
        return r

    # ④ 무시 목록 (코드도 없고 면허도 아닌 것)
    if _LIC_IGNORE.search(text):
        r.method = "ignored"
        return r

    # ⓪-d (v1.4) 총칭 → 공식 세부 업종 OR 확장 (예: "소프트웨어사업자" → SW사업자 4종)
    if text in _LIC_UMBRELLA:
        codes = [c for c in (_lic_lookup(ctx, n) for n in _LIC_UMBRELLA[text]) if c]
        if codes:
            r.codes, r.method = codes, "rule+alias"
            return r

    # v1.5: 소방 복합 (일반(기계및전기)) 또는 전문 → 보수적 근사 (전문 단독, false-pass 방지 방향)
    if _LIC_FIRE_COMPOSITE.search(text):
        cc = _lic_lookup(ctx, "전문소방시설공사업")
        if cc:
            r.codes, r.method = [cc], "rule+alias"
            r.qualifier = "보수매핑: 일반소방(기계+전기) 대안 미반영"
            return r

    # qualifier 분리 보존 (괄호형 + 문장형)
    q = _LIC_QUALIFIER.search(text)
    if q:
        r.qualifier = q.group(0).strip("()[]")
        text = _LIC_QUALIFIER.sub("", text).strip()
    mf = _LIC_MAINFIELD.search(text)
    if mf:
        r.qualifier = (r.qualifier + "; " if r.qualifier else "") + mf.group(0).strip()
        text = _LIC_MAINFIELD.sub("", text).strip()

    # 원형 별칭 조회 (규칙 적용 전)
    code = _lic_lookup(ctx, text)
    if code:
        r.codes, r.method = [code], "alias"
        return r

    # ③ OR 분해를 먼저 (접미사 제거가 " 또는 " 구조를 깨지 않도록, v1.2 순서 교정)
    #    → 컴포넌트별로 ② 접미사·래퍼 제거 → 별칭 조회
    base = text
    for pat, repl in _LIC_WRAPPER:
        base = pat.sub(repl, base).strip()

    def _resolve(comp: str, depth: int = 0) -> list | None:
        """v1.5: 컴포넌트 재귀 해석 — 중첩 OR("철도·궤도 또는 종합건설업(토목 또는 토목건축)") 대응."""
        c2 = _strip_suffixes(_clean(comp))
        for p, rp in _LIC_WRAPPER:
            c2 = p.sub(rp, c2).strip()
        c2 = _LIC_MAINFIELD.sub("", c2).strip()
        cc = _lic_lookup(ctx, c2)
        if cc:
            return [cc]
        if depth < 2:
            subs = _split_or(c2)
            if len(subs) > 1:
                acc = []
                for s in subs:
                    got = _resolve(s, depth + 1)
                    if got is None:
                        return None
                    acc.extend(got)
                return acc
        return None

    candidates = _split_or(base)
    # v1.8: 총칭 조각("면허"·"등록"·"공사업")은 OR 대안이 아니라 표현 잔재다.
    #   "전기공사업 등록 또는 면허" → ["전기공사업 등록", "면허"] 로 쪼개지면서
    #   len(codes) >= len(candidates) 조건을 못 넘겨 통째로 미해석이 되던 문제.
    def _is_filler(c: str) -> bool:
        x = _strip_suffixes(_clean(c))
        return (not x.strip()) or x in _LIC_GENERIC
    _real = [c for c in candidates if not _is_filler(c)]
    if _real and len(_real) < len(candidates):
        candidates = _real
    codes, comps = [], []
    for c in candidates:
        comps.append(_strip_suffixes(_clean(c)))
        got = _resolve(c)
        if got:
            codes.extend(got)
    if codes and len(codes) >= len(candidates):   # 전 컴포넌트 해석 시에만 확정 (중첩 OR은 코드가 더 많을 수 있음)
        transformed = len(candidates) > 1 or len(codes) > 1 or (comps and comps[0] != text)
        r.codes, r.method = codes, ("rule+alias" if transformed else "alias")
        return r
    # v1.2: 분해 결과가 전부 총칭이면 무시 ("공사업 등록 또는 면허" 등)
    if comps and all(c in _LIC_GENERIC or not c for c in comps):
        r.method = "ignored"
        return r
    # v1.3: 물품 품명이 면허 필드에 혼입 ("유틸리티소프트웨어") → item 라우팅
    ic = ctx.item_names.get(text)
    if ic:
        r.codes, r.method = [ic], "route:item"
        return r

    r.method = "none"
    return r


# ────────────────────────────────────────────────
# 지역 (region)
# ────────────────────────────────────────────────
_RGN_NATIONWIDE = {"전국", "제한없음", "전지역"}
_RGN_SITE_REF = re.compile(r"공사현장|현장을?\s*관할")
_RGN_IGNORE = re.compile(r"\d+-\d+|일원$|번지|^시·도$|^시도$")


def normalize_region(raw: str, ctx: LookupContext) -> Result:
    r = Result(raw=raw)
    if not raw or not raw.strip():
        return r
    text = _clean(raw)

    if text in _RGN_NATIONWIDE:
        r.method = "special:nationwide"     # 지역 제한 없음으로 처리
        return r
    if _RGN_SITE_REF.search(text):
        r.method = "special:site_ref"       # cnstrtsite_rgn_nm 참조 필요
        return r
    if _RGN_IGNORE.search(text):
        r.method = "ignored"                # 주소·불완전 추출
        return r

    code = ctx.find("region", text)
    if code:
        r.codes, r.method = [code], "alias"
        return r
    # "관할구역" 제거 후 재시도
    stripped = re.sub(r"\s*관할구역$", "", text)
    if stripped != text:
        code = ctx.find("region", stripped)
        if code:
            r.codes, r.method = [code], "rule+alias"
            return r
    r.method = "none"
    return r


# ────────────────────────────────────────────────
# 기술인력 등급 (personnel)
# ────────────────────────────────────────────────
_PERS_IGNORE = re.compile(r"상시\s*근로자|^기타$|^전문$|책임 또는 참여|유자격자")


def normalize_personnel_grade(raw: str, ctx: LookupContext) -> Result:
    r = Result(raw=raw)
    if raw is None or not str(raw).strip():
        r.method = "special:no_grade"       # 등급 미지정 → 인원수만 비교
        return r
    text = _clean(str(raw))

    if _PERS_IGNORE.search(text):
        r.method = "ignored"
        return r

    code = ctx.find("personnel", text)
    if code:
        r.codes, r.method = [code], "alias"
        return r
    # ② " 이상" 제거 + 기술인→기술자 통일 후 재시도
    stripped = re.sub(r"\s*이상$", "", text).replace("기술인", "기술자").strip()
    if stripped != text:
        code = ctx.find("personnel", stripped)
        if code:
            r.codes, r.method = [code], "rule+alias"
            return r
    # ③ OR 분해 ("소방기술사 또는 소방설비기사(기계 및 전기)")
    parts = _split_or(text)
    if len(parts) > 1:
        codes = [ctx.find("personnel", _clean(p)) for p in parts]
        codes = [c for c in codes if c]
        if codes:
            r.codes, r.method = codes, "rule+alias"
            return r
    r.method = "none"
    return r


# ────────────────────────────────────────────────
# 물품 (item)
# ────────────────────────────────────────────────
_ITEM_PURE = re.compile(r"^\d{8}$|^\d{10}$")
_ITEM_EMBED = re.compile(r"(\d{10}|\d{8})")
_ITEM_ROUTE_LICENSE = re.compile(r"^\d{4}$")


def normalize_item_code(raw: str, ctx: LookupContext, type_hint: str | None = None) -> Result:
    """type_hint: LLM이 추출한 type 필드 (세부품명번호/업종코드/재고번호 등).
    있으면 자릿수 추정보다 우선한다 (2026-07-22 실측: type 라벨링 활발 확인)."""
    r = Result(raw=raw)
    if not raw or not raw.strip():
        return r
    text = _clean(raw)

    # ⓪-a type 힌트 우선 (자릿수 추정보다 안전)
    if type_hint:
        th = type_hint.strip()
        if "업종" in th:                      # 업종코드 → license 도메인
            if text in ctx.license_codes:
                r.codes, r.method = [text], "route:license"
            else:
                r.method = "none"
            return r
        if any(k in th for k in ("재고", "식별", "KS")):   # 타 코드 체계 — 매칭 대상 아님
            r.method = "ignored"
            return r
        # 세부품명(번호)/물품분류번호 → 아래 물품 규칙으로 계속

    # ⓪ 순수 8/10자리
    if _ITEM_PURE.match(text):
        if text in ctx.item_codes:
            r.codes, r.method = [text], "rule0"
        else:
            r.method = "none"               # 형식은 맞으나 폐지/오기 — 확인 대상
        return r
    # ⑤ 4자리 → 업종코드 라우팅 (용역 공고 대량 관측)
    if _ITEM_ROUTE_LICENSE.match(text):
        if text in ctx.license_codes:
            r.codes, r.method = [text], "route:license"
        else:
            r.method = "none"
        return r
    # ① 텍스트 내장 코드 — "데이터처리서비스(8111200201)" 유형
    m = _ITEM_EMBED.search(text)
    if m and m.group(1) in ctx.item_codes:
        r.codes, r.method = [m.group(1)], "rule"
        return r
    # ② 한글 이름 → item_name 역매핑
    code = ctx.item_names.get(text)
    if code:
        r.codes, r.method = [code], "name_map"
        return r
    # ④ 그 외 텍스트(제품 스펙 등)
    r.method = "ignored"
    return r
