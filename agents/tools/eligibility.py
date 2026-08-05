"""자격요건 매칭 도구 (compute_match_results).

세션에서 만든 DB 함수 compute_match_results(company_id)를 호출해, 현재
살아있는 공고에 대한 회원의 자격 충족 판정을 가져온다. 순수 파이썬이며
search.py·bid_info.py와 같은 규약을 따른다:
- get_cursor()로 접속 세부를 숨긴다.
- SQL은 전부 파라미터화한다(문자열 포매팅으로 값 주입 금지).
- 노드는 이 도구를 호출만 한다(내부 구조를 모른다).

DB 함수는 on-read(호출 시점 계산)이고 살아있는 공고만 대상으로 한다
(내부에서 bid_clse_dt로 필터). verdict는 4-state:
  '가능' / '불가' / '보완가능' / '확인필요'
axes 항목은 [{axis, class(gate|supp), status(충족|미충족|확인필요),
  detail, required, actual}].
  (Phase3 2026-07-29: info class 폐지 — cert·credit supp 격상. 파서는 과거
   데이터 호환을 위해 info 를 여전히 수용하되, 신규 데이터에는 나타나지 않는다.)
  (v2.2~v2.3 2026-07-30: verdict 층에 '확인필요 캡' 2종 추가 — 게이트 축 0개의
   supp 미충족(D-22), 유형별 기대 게이트 결측(D-23: 물품인데 item 축 없음 등).
   ★ 이로써 verdict='확인필요'인데 미충족·확인필요 status의 축이 하나도 없는
   행이 정상적으로 존재한다 — 사유가 '미충족인 축'이 아니라 '없는 축'이므로.
   그 경우 failed_reasons는 비는 게 맞다. 소비처(respond 등)는 "사유 없음 =
   문제 없음"으로 읽지 말 것. 시그니처: need_review_count == 0 인 확인필요.)
  required/actual은 compute 2026-07-27 배포부터 실린다. detail은 사람이 읽는
  한 줄 요약으로 유지되며(하위호환), 기계가 쪼개는 용도가 아니다.

결정 반영(프로젝트 문서 ai_agent_eligibility_integration.md §4):
  [D1] 공고 범위 = 라이브 전체 계산 후 bid_table.bid_id로 바깥 필터. (유지)
  [D2] (B) passed(bool)는 '가능'만 True + verdict 필드로 4-state 원문 보존.
  [D3] failed_reasons = 게이트·보완(gate+supp) 축 모두 포함.
       required/actual 분리 완료 — 아래 _to_result 주석 참조.
  [D4] scoring 노드(배점)는 별도 — 이 도구 범위 밖.
"""
from __future__ import annotations

import logging

from agents.schemas import AxisResult, EligibilityResult, FailedReason

# get_cursor는 _fetch 안에서 늦게 import한다(모듈 최상단 아님).
# 이 모듈에는 DB를 전혀 안 타는 순수 매핑(_to_result·_to_axes)이 같이 있는데,
# 최상단에서 psycopg를 끌어오면 그 매핑을 테스트하거나 재사용하려는 쪽까지
# DB 드라이버와 접속 설정을 갖추도록 강요하게 된다.
# 백엔드가 이 모듈을 import하는 시점(프로세스 기동)과 실제로 DB를 쓰는 시점을
# 분리하는 효과도 있다.

logger = logging.getLogger(__name__)

# '통과'로 볼 verdict. passed(bool)는 '가능'만 True. 4-state 원문은
#   EligibilityResult.verdict 필드로 그대로 보존한다(D2(B) 결정).
_PASS_VERDICTS = {"가능"}

# failed_reasons에 담을 축 상태. '충족'은 제외.
_FAIL_STATUSES = {"미충족", "확인필요"}
# 실패 사유 = gate·supp 전 축(D3). Phase3부터 cert·credit 도 supp 라 사유에 오른다.
#   info 는 폐지됐지만, 과거 캐시·원복 사고 방어용으로 제외 필터 자체는 유지한다.
_REASON_CLASSES = {"gate", "supp"}
_AXIS_CLASSES = {"gate", "supp", "info"}
_STATUSES = {"충족", "미충족", "확인필요"}

# 판정 행이 나오지 않은 공고의 사유(N-2a). 로그와 후속 소비(N-2b)용 상수다.
#   마감       라이브가 아니라 계산 대상에서 빠짐 (compute는 라이브만 판정)
#   공고없음   bid_table에 그 bid_id가 없음
#   판정미산출 라이브인데 행이 없음 — 상류 요구조건 데이터 미비
#              (2026-07-30 실측: 라이브 1,470건 중 151건이 이 갈래)
MISSING_CLOSED = "마감"
MISSING_NOT_FOUND = "공고없음"
MISSING_NO_DATA = "판정미산출"

# ── 보완 경로 힌트 (N-5, 2026-07-31) ──────────────────────────────
# supp 축이 미달일 때 "그래서 뭘 하면 되는가"를 신호에 실어주기 위한 정적
# 도메인 지식. 판정 데이터가 아니라 지식이라 코드 상수로 둔다 — 신호에 없으면
# respond의 LLM이 일반론을 지어내는데, 서술형 환각은 수치 grounding 검증에
# 잡히지 않는다(검증기는 숫자만 본다). 지식을 신호에 넣어 지어낼 이유 자체를
# 없애는 것이 목적이다.
#
# 게이트 축에는 힌트를 주지 않는다 — "지역 제한을 보완하세요"는 성립하지 않는
# 행동 유도이고, 불가 공고에 헛된 기대를 만든다. 예외는 하나: 보유값이
# 미등록 계열(회사가 프로필만 채우면 되는 공백)이면 축과 무관하게 입력 유도.
#
# ※ 문구는 도메인 검증 대상 — 팀 리뷰 후 확정 (2026-07-31 초안).
_REMEDY_BY_AXIS = {
    "performance": "실적 보완은 공동수급(컨소시엄) 구성으로 가능한 경우가 있음",
    "capacity": "시공능력평가액 보완도 공동수급 구성 검토 대상",
    "personnel": "기술인력 채용 또는 마이페이지 인력의 분야 지정으로 해소 가능",
    "cert": "해당 인증 취득 시 해소 (취득 소요 기간 확인 필요)",
    "credit": "요구 등급 이상의 신용평가 등급 취득 시 해소",
    "direct_prod": "직접생산확인증명서 취득 시 해소 — 대상 품목은 공고 원문 확인",
}

# 회사측 미입력을 뜻하는 보유값. SQL(ax_* CTE)이 내는 **고정 리터럴과의 전체
# 동등 비교**다 — detail 등을 쪼개는 구조 파싱이 아니므로 파싱 금지 규약과
# 무관하지만, SQL이 리터럴을 바꾸면 힌트가 조용히 꺼진다는 결합은 있다.
# 힌트는 판정이 아니라 보조 정보라 그 정도 결합은 수용한다(변경 시 여기도 갱신).
_UNREGISTERED_ACTUALS = {"(미등록)", "(없음)"}

_PROFILE_HINT = "회사 프로필에 해당 정보를 입력하면 판정이 정확해짐 (미입력은 미충족으로 계산)"


def remedy_hint(axis: str, actual: str = "") -> str | None:
    """미달 축 하나에 대한 보완 경로 한 줄. 해당 없으면 None.

    우선순위: ① 보유값이 미등록 계열이면 축과 무관하게 프로필 입력 유도
    (게이트 축이라도 — 입력만 하면 되는 공백이므로) ② supp 축이면 축별 경로
    ③ 게이트 축·모르는 축은 None — 억지 행동 유도를 만들지 않는다.

    소비처: respond의 자격 신호(_eligibility_block)가 미달 사유 뒤에 붙이는
    용도(A 제안 diff — docs/proposal_respond_remedy.md). '보완가능' 공고에서
    특히 의미가 있고, '불가'에서도 미등록 계열 입력 유도는 유효하다.
    """
    if (actual or "").strip() in _UNREGISTERED_ACTUALS:
        return _PROFILE_HINT
    return _REMEDY_BY_AXIS.get(axis)


def evaluate_eligibility(
    company_id: str,
    *,
    bid_ids: list[str] | None = None,
) -> list[EligibilityResult]:
    """회원(company_id)의 라이브 공고 자격 판정을 계약 형식으로 돌려준다.

    Args:
        company_id: 회원 식별자(문자열). DB에선 BIGINT라 캐스팅한다.
        bid_ids: 지정 시 그 공고들로 범위를 좁힌다(대화 문맥상 특정 공고).
                 None이면 회원의 라이브 공고 전체. [D1 잠정]

    Returns:
        EligibilityResult 리스트. bid_ids를 지정했으면 **그 순서대로** 돌려준다.
        결과가 없으면 빈 리스트.
    """
    rows = _fetch(company_id, bid_ids)
    results = [_to_result(r) for r in rows]

    if bid_ids:
        # 입력 순서 복원. _fetch의 SQL에는 ORDER BY가 없어 행 순서가 플랜
        # (해시 조인 등)에 좌우된다. 호출자(scope 노드)는 우선순위 순으로
        # 고른 bid_ids를 넘기는데, 여기서 순서가 섞이면 respond 신호에서
        # 공고 목록 블록(scope 순서)과 자격 판정 블록(DB 순서)이 서로
        # 어긋난다 — "상위 5건"이라 말하며 순서는 아닌 답이 나간다.
        #
        # SQL(array_position) 대신 파이썬에서 정렬하는 이유: 순서는 조회
        # 결과가 아니라 호출 계약의 속성이고, 이쪽이 DB 없이 테스트된다.
        # 요청에 없는 bid_id는 정상 경로에선 나오지 않지만(_fetch가
        # ANY(bid_ids)로 거른다) 방어적으로 맨 뒤에 둔다.
        pos = {b: i for i, b in enumerate(bid_ids)}
        results.sort(key=lambda r: pos.get(r.bid_id, len(pos)))

        # 판정이 안 나온 공고의 사유를 로그로 남긴다(N-2a). 지금은 관측이
        # 목적이라 반환 계약은 바꾸지 않는다 — 사용자 문구 분기(N-2b)는
        # run.py(A 소유)와 협의 후 이 진단을 소비하는 형태로 붙인다.
        found = {r.bid_id for r in results}
        missing = [b for b in bid_ids if b not in found]
        if missing:
            try:
                grouped: dict[str, list[str]] = {}
                for b, why in missing_verdict_reasons(missing).items():
                    grouped.setdefault(why, []).append(b)
                logger.warning(
                    "eligibility 판정 미제공 %d/%d건 (company_id=%s): %s",
                    len(missing), len(bid_ids), company_id,
                    "; ".join(f"{why} {len(bs)}건 {bs}"
                              for why, bs in grouped.items()),
                    extra={"event": "no_verdict",
                           "n_closed": len(grouped.get(MISSING_CLOSED, [])),
                           "n_not_found": len(grouped.get(MISSING_NOT_FOUND, [])),
                           "n_no_data": len(grouped.get(MISSING_NO_DATA, []))})
            except Exception:  # noqa: BLE001
                # 진단은 관측 보조일 뿐이다 — 진단 쿼리 실패가 이미 확보한
                # 판정 반환까지 막으면 주객이 바뀐다.
                logger.warning("eligibility 판정 미제공 사유 진단 실패 "
                               "(missing=%s)", missing, exc_info=True)

    # bid_ids 미지정(전건) 경로는 정렬 계약이 없다 — DB가 준 순서 그대로.
    # 대화 상한을 넘는 경우의 유용순 정렬은 노드(_cap)의 책임이다.
    return results


def missing_verdict_reasons(bid_ids: list[str]) -> dict[str, str]:
    """판정 행이 나오지 않은 공고들의 사유를 가른다 (N-2a).

    같은 "판정 없음"이라도 사용자에게 할 말이 다르다 — 마감이면 "마감돼서
    판정 대상이 아님", 공고없음이면 "그런 공고 없음", 판정미산출이면 라이브라
    검색·목록엔 멀쩡히 뜨는데 판정만 없는 상태라 "요건 데이터 준비 중"이
    정직하다. run.py의 현행 _NO_VERDICT는 마감 문구 하나로 뭉뚱그린다(N-2b).

    라이브 판정을 여기서 다시 쓰지 않고 C의 공개 도구(fetch_bid_info·
    filter_open_bids)를 재사용한다 — 라이브 정의 사본이 이미 3곳(compute의
    live_bids / bid_info._OPEN_CONDITION / match_results.py)이라 더 늘리면
    안 된다. 쿼리 2회는 누락이 있을 때만, 대화 스코프(≤수십 건)에서만 돈다.

    Returns:
        {bid_id: MISSING_* 상수}. 입력이 비면 빈 dict.
    """
    if not bid_ids:
        return {}
    # bid_info는 최상단에서 psycopg를 끌어오므로 늦게 import한다 — 이 모듈의
    # "순수 매핑은 DB 드라이버 없이 import 가능" 속성을 지키기 위함(상단 주석).
    from agents.tools.bid_info import fetch_bid_info, filter_open_bids

    exists = fetch_bid_info(bid_ids)
    live = filter_open_bids(list(exists)) if exists else set()
    reasons: dict[str, str] = {}
    for b in bid_ids:
        if b not in exists:
            reasons[b] = MISSING_NOT_FOUND
        elif b not in live:
            reasons[b] = MISSING_CLOSED
        else:
            reasons[b] = MISSING_NO_DATA
    return reasons


def _fetch(company_id: str, bid_ids: list[str] | None) -> list[dict]:
    """compute_match_results를 호출하고 agent의 bid_id를 붙여 행을 가져온다.

    DB 함수는 (bid_ntce_no, bid_ntce_ord)로 결과를 낸다. 계약의 bid_id
    (파생 컬럼)를 얻기 위해 bid_table과 조인한다. bid_id로 스코프를 좁힌다.
    """
    where = ""
    params: list[object] = [company_id]
    if bid_ids:
        where = "WHERE bt.bid_id = ANY(%s)"
        params.append(bid_ids)

    sql = f"""
        SELECT bt.bid_id,
               m.verdict, m.gate_failed,
               m.required, m.satisfied, m.need_review,
               m.axes
        FROM compute_match_results(%s::bigint) AS m
        JOIN bid_table bt USING (bid_ntce_no, bid_ntce_ord)
        {where}
    """
    from agents.clients.postgres import get_cursor

    with get_cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _to_result(row: dict) -> EligibilityResult:
    """DB 행 → 계약 EligibilityResult. [D2][D3] 매핑이 여기 모여 있다."""
    passed = row["verdict"] in _PASS_VERDICTS

    # [D3] axes(JSON)에서 실패 축을 뽑아 FailedReason으로 만든다.
    #   compute가 required(요구값)·actual(보유값)을 분리해서 싣는다. 폴백은
    #   DB 배포 순서 독립성을 위한 것 — 함수가 아직 구버전이면 키가 없고,
    #   그때는 이전과 똑같이 동작한다(detail→required, status→actual).
    #
    #   ※ detail을 문자열 파싱해서 두 값을 복원하려는 시도는 하지 말 것.
    #     결합 방식이 축마다 다르다: size는 ' vs ', personnel은 '/',
    #     credit은 서술문. 단일 split 규칙이 존재하지 않는다. 값 분리는
    #     반드시 SQL(compute_match_results의 ax_* CTE) 쪽에서 해야 한다.
    #
    #   ※ actual에 status(충족/미충족/확인필요)가 들어가면 회귀다.
    #     '보유 미충족'은 문장으로 성립하지 않고, respond가 이 문자열을
    #     LLM 프롬프트에 그대로 실어서 환각을 유발한다.
    reasons: list[FailedReason] = []
    if not passed:
        for ax in (row.get("axes") or []):
            if not isinstance(ax, dict):
                continue
            if (ax.get("class") in _REASON_CLASSES
                    and ax.get("status") in _FAIL_STATUSES):
                reasons.append(FailedReason(
                    field=str(ax.get("axis", "")),
                    required=str(ax.get("required")
                                 or ax.get("detail")
                                 or ax.get("status")
                                 or ""),
                    actual=str(ax.get("actual")
                               or ax.get("status")
                               or ""),
                ))

    return EligibilityResult(
        bid_id=row["bid_id"],
        passed=passed,
        verdict=row["verdict"],        # 4-state 원문 보존 (D2(B))
        failed_reasons=reasons,
        axes=_to_axes(row.get("axes")),
        required_count=row.get("required") or 0,
        satisfied_count=row.get("satisfied") or 0,
        need_review_count=row.get("need_review") or 0,
    )


def _to_axes(raw: object) -> list[AxisResult]:
    """axes jsonb 전체를 그대로 내려보낸다 (9축 체크리스트용).

    failed_reasons는 미달 축만 담는다. 화면이 "무엇을 확인했는가"까지
    보여주려면 충족 축도 있어야 하는데, 지금까지 _fetch가 SELECT해온
    m.axes를 여기서 통째로 버리고 있었다.

    상류가 이상한 값을 주면 조용히 건너뛴다 — 축 하나 때문에 공고
    전체가 사라지는 것보다, 그 축만 빠지고 나머지가 보이는 편이 낫다.
    (Literal 검증에 걸리는 경우가 이에 해당한다.)
    """
    out: list[AxisResult] = []
    for ax in (raw or []):
        if not isinstance(ax, dict):
            continue
        cls, status = ax.get("class"), ax.get("status")
        if cls not in _AXIS_CLASSES or status not in _STATUSES:
            continue
        out.append(AxisResult(
            axis=str(ax.get("axis") or ""),
            axis_class=cls,
            status=status,
            required=str(ax.get("required") or ""),
            actual=str(ax.get("actual") or ""),
            detail=str(ax.get("detail") or ""),
        ))
    return out
