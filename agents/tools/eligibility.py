"""자격요건 매칭 도구 (compute_match_results).

세션에서 만든 DB 함수 compute_match_results(company_id)를 호출해, 현재
살아있는 공고에 대한 회원의 자격 충족 판정을 가져온다. 순수 파이썬이며
search.py·bid_info.py와 같은 규약을 따른다:
- get_cursor()로 접속 세부를 숨긴다.
- SQL은 전부 파라미터화한다(문자열 포매팅으로 값 주입 금지).
- 노드는 이 도구를 호출만 한다(내부 구조를 모른다).

DB 함수는 on-read(호출 시점 계산)이고 살아있는 공고만 대상으로 한다
(내부에서 bid_clse_dt로 필터). verdict는 게이트 3-state:
  '가능' / '불가' / '보완가능' / '확인필요'
axes 항목은 [{axis, class(gate|supp|info), status(충족|미충족|확인필요),
  detail, required, actual}].
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

from agents.schemas import AxisResult, EligibilityResult, FailedReason

# get_cursor는 _fetch 안에서 늦게 import한다(모듈 최상단 아님).
# 이 모듈에는 DB를 전혀 안 타는 순수 매핑(_to_result·_to_axes)이 같이 있는데,
# 최상단에서 psycopg를 끌어오면 그 매핑을 테스트하거나 재사용하려는 쪽까지
# DB 드라이버와 접속 설정을 갖추도록 강요하게 된다.
# 백엔드가 이 모듈을 import하는 시점(프로세스 기동)과 실제로 DB를 쓰는 시점을
# 분리하는 효과도 있다.

# '통과'로 볼 verdict. passed(bool)는 '가능'만 True. 4-state 원문은
#   EligibilityResult.verdict 필드로 그대로 보존한다(D2(B) 결정).
_PASS_VERDICTS = {"가능"}

# failed_reasons에 담을 축 상태. '충족'은 제외.
_FAIL_STATUSES = {"미충족", "확인필요"}
# 표시축(인증=info)은 N/M 미참여 → 실패 사유에서 제외. 게이트·보완 다 포함(D3 결정).
_REASON_CLASSES = {"gate", "supp"}
_AXIS_CLASSES = {"gate", "supp", "info"}
_STATUSES = {"충족", "미충족", "확인필요"}


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
        EligibilityResult 리스트. 결과가 없으면 빈 리스트.
    """
    rows = _fetch(company_id, bid_ids)
    return [_to_result(r) for r in rows]


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
        verdict=row["verdict"],        # 게이트 3-state 원문 보존 (D2(B))
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
