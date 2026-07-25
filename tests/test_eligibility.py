"""자격요건 매칭 도구의 매핑 규약 테스트 — DB 없이 _to_result만 검증한다.

DB 함수(compute_match_results) 자체의 정확성은 SQL 쪽 책임이고, 여기서
지키려는 건 'DB 행 → 팀 공용 계약(EligibilityResult)' 변환 규약이다:
  [D2] passed는 '가능'만 True, 4-state 원문은 verdict로 보존
  [D3] failed_reasons는 gate·supp 축의 미충족/확인필요만 (info 축 제외)
"""
from agents.tools.eligibility import _to_result


def _row(verdict, axes, bid_id="R26BK01483740_001"):
    """compute_match_results 1행 모양(집계 컬럼은 이 테스트에 무관)."""
    return {"bid_id": bid_id, "verdict": verdict, "axes": axes,
            "gate_failed": 0, "required": 0, "satisfied": 0, "need_review": 0}


def test_가능은_통과이고_실패사유가_없다():
    row = _row("가능", [
        {"axis": "license", "class": "gate", "status": "충족", "detail": "전기공사업"},
        {"axis": "cert", "class": "info", "status": "미충족", "detail": "ISO9001"},
    ])
    result = _to_result(row)
    assert result.passed is True
    assert result.verdict == "가능"
    assert result.failed_reasons == []      # 통과면 사유를 만들지 않는다


def test_불가는_게이트_미충족_축을_사유로_싣는다():
    row = _row("불가", [
        {"axis": "region", "class": "gate", "status": "미충족", "detail": "대전 제한"},
        {"axis": "license", "class": "gate", "status": "충족", "detail": "전기공사업"},
    ])
    result = _to_result(row)
    assert result.passed is False
    assert result.verdict == "불가"
    assert [r.field for r in result.failed_reasons] == ["region"]
    assert result.failed_reasons[0].required == "대전 제한"
    assert result.failed_reasons[0].actual == "미충족"


def test_보완가능은_통과가_아니지만_verdict로_구분된다():
    """passed(bool)만 보면 '불가'와 같다 — verdict가 있어야 화면에서 갈린다."""
    row = _row("보완가능", [
        {"axis": "performance", "class": "supp", "status": "미충족", "detail": "실적 10억"},
    ])
    result = _to_result(row)
    assert result.passed is False
    assert result.verdict == "보완가능"
    assert [r.field for r in result.failed_reasons] == ["performance"]


def test_표시축_info는_실패사유에서_빠진다():
    """인증(info)은 N/M 판정에 참여하지 않으므로 사유로 올리면 오해를 준다."""
    row = _row("확인필요", [
        {"axis": "cert", "class": "info", "status": "미충족", "detail": "ISO9001"},
        {"axis": "capacity", "class": "supp", "status": "확인필요", "detail": "시공능력"},
    ])
    result = _to_result(row)
    assert result.verdict == "확인필요"
    assert [r.field for r in result.failed_reasons] == ["capacity"]


def test_detail이_비면_status로_폴백한다():
    """axes의 detail은 NULL일 수 있다(실측 0건이지만 계약상 보장은 없다)."""
    row = _row("불가", [
        {"axis": "size", "class": "gate", "status": "미충족", "detail": None},
    ])
    reason = _to_result(row).failed_reasons[0]
    assert reason.required == "미충족"       # 빈 문자열로 새어나가지 않는다
    assert reason.actual == "미충족"


def test_axes가_비어도_터지지_않는다():
    """LEFT JOIN per_bid로 축이 하나도 없는 공고는 axes가 NULL로 온다."""
    result = _to_result(_row("가능", None))
    assert result.passed is True
    assert result.failed_reasons == []
