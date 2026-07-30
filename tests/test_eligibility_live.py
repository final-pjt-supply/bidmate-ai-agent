"""실 DB 대상 자격요건 매칭 검증 (live).

기존 tests/test_eligibility.py 는 전부 가짜 행(fake row)으로 매핑 로직만 본다.
그래서 "compute_match_results 가 실제로 도는가", "계약대로 값이 오는가",
"몇 ms 걸리는가"는 지금까지 한 번도 확인된 적이 없다. 이 파일이 그 자리다.

실행:
    pytest -m live -s -q tests/test_eligibility_live.py

기본 제외다(pytest.ini 의 addopts = -m "not live"). CI 는 건드리지 않는다.
-s 를 붙여야 성능 수치가 화면에 찍힌다.

수치를 코드에 박지 않는 이유
    공고는 매일 마감되고 유입된다. `assert len(rows) == 1431` 같은 단정은
    내일 깨진다. 대신 **같은 실행 안에서** SQL 로 다시 세어 비교한다.
    이러면 "데이터가 변했다"가 아니라 "코드와 SQL이 어긋났다"만 잡힌다.

환경
    .env 가 필요하다. 없으면 skip 한다(실패가 아니다).
    이 프로젝트에 PG_DSN 이라는 환경변수는 없다 — agents.config.Settings 가
    PG_HOST/PG_USER/... 를 각각 받아 pg_dsn 프로퍼티로 조립한다. 그래서
    단일 변수 유무가 아니라 Settings() 생성 성공 여부로 판단한다.
"""
from __future__ import annotations

import os
import time

import pytest

def _env_error() -> str | None:
    """.env 가 갖춰졌는지 본다. 못 갖춰졌으면 사유 한 줄을 돌려준다.

    Settings 는 필수 필드가 9개라, 하나만 비어도 import 시점에
    ValidationError 로 터진다. 가드가 없으면 skip 이 아니라 error 12건이
    되고, 그건 "테스트가 깨졌다"로 오해된다(실제 2026-07-28에 그랬다).
    """
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

# 회사 9001 = 검증용 계정. 다른 회사로 보려면 환경변수로 덮어쓴다.
COMPANY_ID = os.getenv("LIVE_COMPANY_ID", "9001")

VERDICTS = {"가능", "불가", "보완가능", "확인필요"}
STATUSES = {"충족", "미충족", "확인필요"}
AXES = {
    "license", "region", "size", "direct_prod",       # gate
    "item", "personnel", "performance", "capacity",   # supp
    "cert", "credit",                                 # supp (Phase3 격상)
}
# [Phase3 2026-07-29] cert·credit 이 supp 로 격상되고 info class 는 폐지됐다.
#   3차의 info 강등은 인증 해석률 20% 시절의 잠정 조치였고, v1.9 재정규화로
#   라이브 해석률 83%가 되면서 판정에 편입했다. credit 은 min_grade 파싱행만
#   축이 생긴다(결정 3 — 배점표 혼입 차단). 아래 두 테스트가 이 상태를 지킨다:
#   test_info_class는_폐지됐다 / test_credit축은_등급요구가_있는_행만_생긴다.

# 7/24 측정 기준선. §4-3(on-read vs 사전계산) 판단의 기준점이다.
# 2배 이상이면 아키텍처 선택이 바뀐다 — 그래서 실패시키지 않고 경고만 찍는다.
BASELINE_MS = 105.6


@pytest.fixture(scope="module")
def rows():
    """도구를 실제로 한 번 돌린 결과. 모듈 내 테스트가 공유한다."""
    from agents.tools.eligibility import evaluate_eligibility

    result = evaluate_eligibility(COMPANY_ID)
    if not result:
        pytest.skip(f"company_id={COMPANY_ID} 결과 0건 — 데이터/DSN 확인 필요")
    return result


@pytest.fixture(scope="module")
def 기대게이트_결측_ids():
    """[D-23] 유형별 기대 게이트가 결측인 라이브 공고의 bid_id 집합.

    물품(thng)인데 item 축 없음 / 용역·공사(servc·cnstwk)인데 license 축 없음.
    유도 테스트와 D-23 핀 테스트가 공유한다. rows 와 같은 실행 시점의 스냅샷이
    아니라 수 초 차이가 있을 수 있으나, 축 존재는 공고측 데이터라 안정적이다.
    """
    from agents.clients.postgres import get_cursor

    sql = """
        SELECT bt.bid_id
        FROM compute_match_results(%s::bigint) n
        JOIN bid_table bt
          ON bt.bid_ntce_no = n.bid_ntce_no AND bt.bid_ntce_ord = n.bid_ntce_ord
        LEFT JOIN LATERAL jsonb_array_elements(n.axes) ax ON TRUE
        GROUP BY bt.bid_id, bt.bid_category
        HAVING (bt.bid_category = 'thng'
                AND NOT COALESCE(BOOL_OR(ax->>'axis' = 'item'), false))
            OR (bt.bid_category IN ('servc', 'cnstwk')
                AND NOT COALESCE(BOOL_OR(ax->>'axis' = 'license'), false))
    """
    with get_cursor() as cur:
        cur.execute(sql, [COMPANY_ID])
        return {row["bid_id"] for row in cur.fetchall()}


# ─────────────────────────── 계약 ───────────────────────────

def test_계약대로_값이_온다(rows):
    """verdict 4-state 원문이 살아 있고, passed 와 모순되지 않는다."""
    assert all(r.verdict in VERDICTS for r in rows), \
        sorted({r.verdict for r in rows} - VERDICTS)

    # passed 는 verdict=='가능' 의 축약이다. 둘이 어긋나면 매핑이 깨진 것.
    모순 = [r.bid_id for r in rows if r.passed != (r.verdict == "가능")]
    assert not 모순, f"passed/verdict 모순 {len(모순)}건: {모순[:5]}"

    assert all(r.bid_id for r in rows), "bid_id 가 빈 행이 있다"


def test_불가에는_사유가_붙는다(rows):
    """'불가'인데 실패 사유가 비어 있으면 화면에 이유를 못 쓴다."""
    빈사유 = [r.bid_id for r in rows if r.verdict == "불가" and not r.failed_reasons]
    assert not 빈사유, f"불가인데 사유 0건: {len(빈사유)}건 {빈사유[:5]}"


def test_가능에는_사유가_없다(rows):
    더러운행 = [r.bid_id for r in rows if r.verdict == "가능" and r.failed_reasons]
    assert not 더러운행, f"가능인데 사유가 붙음: {더러운행[:5]}"


def test_가능에는_판정축이_있다(rows):
    """[축0개] 근거 0줄짜리 '가능' 이 없어야 한다.

    required_count 는 class in (gate,supp) 축의 개수다. 이게 0이면
    공고에서 요구조건을 하나도 못 뽑아냈다는 뜻이지 "조건이 없다"가 아니다.
    2026-07-28 이전 SQL 은 이걸 '가능' 으로 흘려보냈다(라이브 270건 중 92건).

    이 단정은 절대 수치를 박지 않는다 — 0건이라는 불변식만 본다.
    """
    근거없는가능 = [
        r.bid_id for r in rows
        if r.verdict == "가능" and r.required_count == 0
    ]
    assert not 근거없는가능, (
        f"판정축 0개인데 '가능': {len(근거없는가능)}건 {근거없는가능[:5]} "
        "— compute_match_results 의 required=0 분기가 빠졌다"
    )


def test_카운트가_축목록과_일치한다(rows):
    """*_count 는 axes 를 세어 만든 값이다. 어긋나면 매핑이 깨진 것."""
    어긋남 = []
    for r in rows:
        판정축 = [a for a in r.axes if a.axis_class in ("gate", "supp")]
        if r.required_count != len(판정축):
            어긋남.append((r.bid_id, r.required_count, len(판정축)))
        elif r.satisfied_count != sum(1 for a in 판정축 if a.status == "충족"):
            어긋남.append((r.bid_id, "satisfied", r.satisfied_count))
    assert not 어긋남, f"카운트/축 불일치 {len(어긋남)}건: {어긋남[:5]}"


def test_D3_판정값이_보유칸으로_새지_않는다(rows):
    """[D3] actual 은 회원의 '보유 값'이어야 한다.

    '충족'/'미충족' 같은 판정 문자열이 actual 에 들어오면 D3 재발이다.
    (SQL 이 act_value 를 안 내려보내 status 로 폴백했다는 뜻)
    """
    샌행 = [
        (r.bid_id, f.field, f.actual)
        for r in rows for f in r.failed_reasons
        if f.actual in STATUSES
    ]
    assert not 샌행, f"D3 재발 {len(샌행)}건: {샌행[:5]}"


def test_사유의_축이름이_알려진_축이다(rows):
    미지축 = {f.field for r in rows for f in r.failed_reasons} - AXES
    assert not 미지축, f"모르는 축: {미지축}"


def test_info_class는_폐지됐다(rows):
    """[Phase3] class='info' 는 더 존재하지 않는다 — cert·credit 은 supp 로 판정 참여.

    3차(info 강등)를 지키던 test_info축은_사유에서_빠진다 의 후속 방어선이다.
    함수 쪽에서 축이 info 로 되돌아가면(원복 사고) 여기서 잡힌다.
    """
    잔존 = [(r.bid_id, a.axis) for r in rows for a in r.axes
            if a.axis_class == "info"]
    assert not 잔존, f"info class 잔존 {len(잔존)}건: {잔존[:5]}"


def test_credit축은_등급요구가_있는_행만_생긴다(rows):
    """[Phase3/결정3] credit 축은 min_grade 가 파싱된 요구에만 선다.

    등급 미상(boolean-only) 신용 요구는 적격심사 배점표 혼입이 다수라(3차 실측:
    임계 요건 0건) 축을 만들지 않는다. 축이 있는데 required 가 'X 이상' 꼴이
    아니면 grade_scale JOIN 필터가 빠진 회귀다.
    """
    회귀 = [(r.bid_id, a.required) for r in rows for a in r.axes
            if a.axis == "credit" and not a.required.endswith("이상")]
    assert not 회귀, f"등급 없는 credit 축 {len(회귀)}건: {회귀[:5]}"


def test_verdict가_축목록에서_다시_유도된다(rows, 기대게이트_결측_ids):
    """compute 의 CASE 를 파이썬에서 재현해 대조한다. [최대 구멍이었던 자리]

    분기 '순서'가 곧 우선순위다(required=0 → gate_failed → need_review →
    satisfied). 지금까지 이 순서가 뒤바뀌거나 카운터 정의가 틀어져도 깨지는
    테스트가 하나도 없었다. 수치가 아니라 규칙을 박기 때문에 데이터가 변해도
    안전하다.

    axes 로부터 유도하는 이유: 집계 컬럼(required/satisfied/...)과 axes 가
    같은 CTE 에서 나오므로, 둘을 대조하면 SQL 내부 불일치까지 같이 잡힌다.
    """
    def 유도(r):
        판정축 = [a for a in r.axes if a.axis_class in ("gate", "supp")]
        if not 판정축:
            return "확인필요"
        if any(a.axis_class == "gate" and a.status == "미충족" for a in 판정축):
            return "불가"
        if any(a.status == "확인필요" for a in 판정축):
            return "확인필요"
        # [D-22] 게이트 축이 하나도 없는 공고의 supp 미충족은 보완가능이 아니라
        #   확인필요다 — 게이트를 통과한 게 아니라 검증된 적이 없다(2026-07-30).
        if (not any(a.axis_class == "gate" for a in 판정축)
                and sum(1 for a in 판정축 if a.status == "충족") < len(판정축)):
            return "확인필요"
        # [D-23] 유형별 기대 게이트 결측 — 남은 낙관 경로(보완가능/가능)를 캡.
        #   유형은 axes 로 유도 불가라 SQL 로 뽑은 집합(기대게이트_결측_ids)을 쓴다.
        if r.bid_id in 기대게이트_결측_ids:
            return "확인필요"
        if sum(1 for a in 판정축 if a.status == "충족") < len(판정축):
            return "보완가능"
        return "가능"

    어긋남 = [(r.bid_id, r.verdict, 유도(r)) for r in rows if r.verdict != 유도(r)]
    assert not 어긋남, (
        f"verdict/축목록 불일치 {len(어긋남)}건 (bid, SQL, 유도): {어긋남[:5]}"
    )


def test_보완가능과_확인필요에도_사유가_붙는다(rows, 기대게이트_결측_ids):
    """사유가 비면 화면이 회원에게 시킬 행동을 못 쓴다.

    '확인필요' 는 이제 네 갈래다(2026-07-30 v2.3 기준) —
      ① 판정축 0개(required=0)          : 사유 없음이 정상 → required_count>0 만 본다
      ② need_review>0                   : 확인필요 축이 곧 사유
      ③ 게이트0 + supp 미충족(D-22 캡)  : 미충족 축이 곧 사유
      ④ 기대 게이트 결측(D-23 캡)       : 사유가 '미충족인 축'이 아니라 **없는 축**이라
                                          failed_reasons 로 표현되지 않는다(축이 전부
                                          충족일 수 있음) → 결측 집합으로 면제.
    ④의 화면 문구는 프론트 몫 — "공고 유형상 확인돼야 할 요건이 공고에서
    추출되지 않았습니다, 원문을 확인해 주세요" 계열(인수인계 참조).
    """
    빈사유 = [
        (r.bid_id, r.verdict) for r in rows
        if not r.failed_reasons
        and r.bid_id not in 기대게이트_결측_ids
        and (r.verdict == "보완가능"
             or (r.verdict == "확인필요" and r.required_count > 0))
    ]
    assert not 빈사유, f"사유 없는 {len(빈사유)}건: {빈사유[:5]}"


def test_직생축은_supp이고_미등록은_미충족이다(rows):
    """[Phase1 정책 전가] 회원측 데이터 부재는 '미충족', 직생확인은 보완축이다.

    4차 가드(미등록 → 확인필요)는 2026-07-29 결정 1로 뒤집혔다 — 회원이 채울 수
    있는 정보의 공백은 회원 책임으로 확정 판정한다. 대신 direct_prod 가 supp 로
    내려가, 미충족이어도 '불가'가 아니라 '보완가능' 쪽이다(공고가 목록에서
    사라지지 않는다 — 4차 가드가 지키려던 실익은 축 격하가 이어받았다).
    여기서 지키는 불변식 둘:
      ① direct_prod 는 gate 로 회귀하지 않는다(회귀 시 미등록만으로 공고가 숨는다).
      ② '(회사 품목 미등록)' 인데 '확인필요' 로 남으면 전가 정책 회귀다.
    """
    게이트회귀 = [r.bid_id for r in rows for a in r.axes
                  if a.axis == "direct_prod" and a.axis_class != "supp"]
    assert not 게이트회귀, f"direct_prod 가 supp 가 아님: {게이트회귀[:5]}"

    정책회귀 = [(r.bid_id, a.status) for r in rows for a in r.axes
                if a.axis == "direct_prod" and a.actual == "(회사 품목 미등록)"
                and a.status != "미충족"]
    assert not 정책회귀, f"미등록인데 미충족이 아님 {len(정책회귀)}건: {정책회귀[:5]}"



def test_인력_총원을_넘는_요구는_충족이_아니다():
    """[D-19/v2.0] 헤드카운트 보존 불변식 — 초안 결함의 재발 방지.

    분야 매칭(v2.0)의 관대 규칙(field_family NULL = 모든 계열 풀에 계상)이
    총원 상한을 넘어 중복 계상되면 안 된다. 자격코드의 총 요구 인원이 회원의
    총 보유 인원보다 큰 공고는 어떤 분야 배정으로도 충족이 불가능하다 —
    그런데 인력 축이 '충족'이면 합산 보존 행(pers_qual_agg)이 빠진 회귀다.
    v2.0 초안이 실제로 이 결함을 갖고 있었다(2026-07-29 발견·수정): NULL 인력이
    계열 풀마다 중복 계상돼 1단계(v1.5)가 막은 공짜 통과가 되살아나는 구조였다.
    등급형 자격은 상위 등급이 하위 요구를 채워 풀이 더 넓으므로 제외한다.
    v1.5(합산)·v2.0(계열+합산) 모두에서 참이어야 하는 불변식이라 배포 전후 무관하다.
    """
    from agents.clients.postgres import get_cursor

    sql = """
        WITH req AS (            -- 요구측: (공고, 자격코드) 합산 — 분야 무시한 하한
          SELECT bid_ntce_no, bid_ntce_ord, qual_code, SUM(headcount) AS need
          FROM (SELECT DISTINCT bid_ntce_no, bid_ntce_ord, qual_code, qual_name,
                                role_field, grade_raw, headcount, method
                  FROM bid_require_personnel
                 WHERE COALESCE(method,'') <> 'ignored') d
          WHERE qual_code IS NOT NULL
            AND qual_code NOT IN (SELECT qual_code FROM personnel_grade_master
                                   WHERE qual_type = 'grade' AND grade_rank IS NOT NULL)
          GROUP BY 1, 2, 3
        ),
        viol AS (                -- 총 보유 < 총 요구 = 어떤 분야 배정으로도 불가능
          SELECT r.bid_ntce_no, r.bid_ntce_ord
          FROM req r
          LEFT JOIN (SELECT qual_code, SUM(headcount) AS have
                       FROM company_personnel
                      WHERE company_id = %s::bigint GROUP BY 1) h USING (qual_code)
          GROUP BY 1, 2
          HAVING BOOL_OR(COALESCE(h.have, 0) < r.need)
        )
        SELECT COUNT(*) AS n
        FROM compute_match_results(%s::bigint) m
        JOIN viol v ON v.bid_ntce_no = m.bid_ntce_no
                   AND v.bid_ntce_ord = m.bid_ntce_ord,
             LATERAL jsonb_array_elements(m.axes) ax
        WHERE ax->>'axis' = 'personnel' AND ax->>'status' = '충족'
    """
    with get_cursor() as cur:
        cur.execute(sql, [COMPANY_ID, COMPANY_ID])
        n = cur.fetchone()["n"]
    assert n == 0, (
        f"총원 초과 요구인데 인력 축 '충족' {n}건 — 합산 보존(pers_qual_agg) 회귀"
    )



def test_게이트없는_공고는_보완가능이_되지_않는다(rows):
    """[D-22] '보완가능' 서사는 게이트 통과가 전제다.

    실측(2026-07-30): 보완가능 25건 중 21건(84%)이 게이트 축 전무 — 물품 공고인데
    품목 축조차 추출되지 않은, 게이트가 '검증된 적 없는' 공고였다. SW 회사에
    아스콘 공고가 '보완하면 가능'으로 뜬 실사용 발견이 발단. 게이트 0개 + supp
    미충족은 확인필요여야 한다. 이 가드가 빠지면 여기서 잡힌다.
    """
    위반 = [r.bid_id for r in rows
            if r.verdict == "보완가능"
            and not any(a.axis_class == "gate" for a in r.axes)]
    assert not 위반, (
        f"게이트 축 없는 '보완가능' {len(위반)}건: {위반[:5]} — D-22 가드 회귀"
    )



def test_기대게이트_결측이면_낙관판정이_없다(rows, 기대게이트_결측_ids):
    """[D-23] 물품인데 품목 축 없음 / 용역·공사인데 면허 축 없음 → 가능·보완가능 금지.

    발단(2026-07-30 실사용): SW 회사에 코팅기·크레인 구매 공고가 "규모 1/1 충족,
    가능"으로 떴다 — 품목 검증 없이. 모집단 실측: 물품의 93.5%는 item 축이,
    용역 92.7%·공사 79%는 license 축이 있다 — 결측은 요구가 없는 게 아니라
    추출이 놓친 것이다. '가능' 목록의 결측 비율은 선택 효과로 부풀므로(게이트가
    추출된 공고는 미보유 회사에겐 '불가'로 빠짐) 이 가드가 필요하다.
    """
    위반 = [(r.bid_id, r.verdict) for r in rows
            if r.verdict in ("가능", "보완가능")
            and r.bid_id in 기대게이트_결측_ids]
    assert not 위반, (
        f"기대 게이트 결측인데 낙관 판정 {len(위반)}건: {위반[:5]} — D-23 가드 회귀"
    )


# ─────────────────────── 코드 vs SQL 대조 ───────────────────────

def test_파이썬_건수가_SQL과_같다(rows):
    """같은 실행 안에서 SQL 로 다시 세어 비교한다.

    어긋나면 대개 세션 시간대다 — psycopg 세션이 UTC 로 붙으면
    마감 판정이 9시간 밀려 live 공고 수가 달라진다(7/23에 110건 어긋난 적 있다).
    JOIN bid_table 로 bid_id 를 못 찾는 행이 있어도 여기서 드러난다.
    """
    from agents.clients.postgres import get_cursor

    with get_cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM compute_match_results(%s::bigint)",
            [COMPANY_ID],
        )
        sql_n = cur.fetchone()["n"]

    diff = abs(len(rows) - sql_n)
    print(f"\n[대조] python={len(rows)}  sql={sql_n}  diff={diff}")
    assert diff <= 2, (
        f"파이썬 {len(rows)} vs SQL {sql_n} — 세션 시간대 또는 JOIN 유실 의심"
    )


def test_bid_ids로_범위를_좁힐_수_있다(rows):
    """백엔드 상세 화면이 쓸 경로. 지정한 공고만 돌아와야 한다."""
    from agents.tools.eligibility import evaluate_eligibility

    표본 = [r.bid_id for r in rows[:3]]
    좁힌결과 = evaluate_eligibility(COMPANY_ID, bid_ids=표본)

    assert {r.bid_id for r in 좁힌결과} == set(표본)


def test_노드가_필터없이도_돈다():
    """resolved_filters 가 없는 상태로 그래프가 들어와도 터지지 않아야 한다."""
    from agents.nodes.eligibility import eligibility_node

    out = eligibility_node({"company_id": COMPANY_ID, "resolved_filters": None})
    assert isinstance(out.get("eligibility"), list)


# ───────────────────────── 성능 (§1-2) ─────────────────────────

def test_성능_측정():
    """§4-3(on-read 유지 vs match_results 사전계산) 판단 근거를 만든다.

    count(*) 로 재면 안 된다 — Postgres 가 쓰지 않는 CTE 를 통째로 잘라내서
    실제 계산의 몇 분의 일만 측정된다(7/24의 11ms 수치가 그래서 무의미했다).
    axes 를 실제로 펼치는 sum(jsonb_array_length(axes)) 로 강제한다.
    """
    from agents.clients.postgres import get_cursor

    sql = "SELECT sum(jsonb_array_length(axes)) AS n FROM compute_match_results(%s::bigint)"

    측정 = []
    with get_cursor() as cur:
        for _ in range(3):
            t0 = time.perf_counter()
            cur.execute(sql, [COMPANY_ID])
            n_axes = cur.fetchone()["n"]
            측정.append((time.perf_counter() - t0) * 1000)

    중앙값 = sorted(측정)[1]
    print(
        f"\n[성능] company={COMPANY_ID}  축_총개수={n_axes}\n"
        f"       3회={[round(m, 1) for m in 측정]} ms  중앙값={중앙값:.1f} ms\n"
        f"       기준선(7/24)={BASELINE_MS} ms  →  {중앙값 / BASELINE_MS:.2f}배"
    )
    if 중앙값 > BASELINE_MS * 2:
        print("       ⚠ 기준선 2배 초과 — §4-3 사전계산 검토 대상")

    # 성능은 기록이 목적이지 게이트가 아니다. 명백한 붕괴만 잡는다.
    assert 중앙값 < 5000, f"{중앙값:.0f} ms — 동기 호출로는 못 쓴다"


def test_도구_왕복_시간(rows):
    """SQL 이 아니라 파이썬 도구 전체(직렬화·pydantic 포함) 왕복."""
    from agents.tools.eligibility import evaluate_eligibility

    t0 = time.perf_counter()
    r = evaluate_eligibility(COMPANY_ID)
    ms = (time.perf_counter() - t0) * 1000
    print(f"\n[도구] {len(r)}건 {ms:.1f} ms  ({ms / max(len(r), 1):.3f} ms/건)")


def test_verdict_분포_출력(rows):
    """수치를 박지 않고 찍기만 한다. 사람이 §2-3 표와 눈으로 대조한다."""
    from collections import Counter

    분포 = Counter(r.verdict for r in rows)
    축별 = Counter(f.field for r in rows for f in r.failed_reasons)
    축0개 = sum(1 for r in rows if r.required_count == 0)
    print(f"\n[분포] 총 {len(rows)}건  {dict(분포)}")
    print(f"[축0개] {축0개}건 — 전부 '확인필요' 여야 한다")
    print(f"[축별 사유] {dict(축별.most_common())}")
