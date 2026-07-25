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
axes 항목은 [{axis, class(gate|supp|info), status(충족|미충족|확인필요), detail}].

결정 반영(프로젝트 문서 ai_agent_eligibility_integration.md §4):
  [D1] 공고 범위 = 라이브 전체 계산 후 bid_table.bid_id로 바깥 필터. (유지)
  [D2] (B) passed(bool)는 '가능'만 True + verdict 필드로 4-state 원문 보존.
  [D3] failed_reasons = 게이트·보완(gate+supp) 축 모두 포함.
  [D4] scoring 노드(배점)는 별도 — 이 도구 범위 밖.
잔여 TODO: required/actual을 사람이 읽을 값으로 분리하려면 compute의 axes
  페이로드 확장 필요(현재는 detail→required, status→actual 잠정).
"""
from __future__ import annotations

from agents.clients.postgres import get_cursor
from agents.schemas import EligibilityResult, FailedReason

# '통과'로 볼 verdict. passed(bool)는 '가능'만 True. 4-state 원문은
#   EligibilityResult.verdict 필드로 그대로 보존한다(D2(B) 결정).
_PASS_VERDICTS = {"가능"}

# failed_reasons에 담을 축 상태. '충족'은 제외.
_FAIL_STATUSES = {"미충족", "확인필요"}
# 표시축(인증=info)은 N/M 미참여 → 실패 사유에서 제외. 게이트·보완 다 포함(D3 결정).
_REASON_CLASSES = {"gate", "supp"}


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
    with get_cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _to_result(row: dict) -> EligibilityResult:
    """DB 행 → 계약 EligibilityResult. [D2][D3] 매핑이 여기 모여 있다."""
    passed = row["verdict"] in _PASS_VERDICTS

    # [D3 잠정] axes(JSON)에서 실패 축을 뽑아 FailedReason으로 만든다.
    #   현재 compute의 axes 항목은 {axis, class, status, detail}이고
    #   required/actual을 분리 제공하지 않는다. 잠정으로 detail→required,
    #   status→actual. 사람이 읽을 required/actual 분리는 axes 페이로드를
    #   넓힌 뒤 확정한다(TODO).
    reasons: list[FailedReason] = []
    if not passed:
        for ax in (row.get("axes") or []):
            if (ax.get("class") in _REASON_CLASSES
                    and ax.get("status") in _FAIL_STATUSES):
                reasons.append(FailedReason(
                    field=str(ax.get("axis", "")),
                    required=str(ax.get("detail", "") or ax.get("status", "")),
                    actual=str(ax.get("status", "")),
                ))

    return EligibilityResult(
        bid_id=row["bid_id"],
        passed=passed,
        verdict=row["verdict"],        # 게이트 3-state 원문 보존 (D2(B))
        failed_reasons=reasons,
    )
