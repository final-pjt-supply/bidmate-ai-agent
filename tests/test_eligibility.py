"""자격요건 매칭 도구의 매핑 규약 테스트 — DB 없이 _to_result만 검증한다.

DB 함수(compute_match_results) 자체의 정확성은 SQL 쪽 책임이고, 여기서
지키려는 건 'DB 행 → 팀 공용 계약(EligibilityResult)' 변환 규약이다:
  [D2] passed는 '가능'만 True, 4-state 원문은 verdict로 보존
  [D3] failed_reasons는 gate·supp 축의 미충족/확인필요만 (info 축 제외)
       + required(요구값)·actual(보유값) 분리 — 2026-07-27

axes의 required/actual은 compute 2026-07-27 배포부터 실린다. 이 파일은 두
경로를 모두 덮는다: 키가 있을 때(정상)와 없을 때(구버전 DB → 폴백).
"""
from agents.tools.eligibility import _to_result

# compute의 ax_* CTE 전체. 축이 추가되면 여기도 늘려야 한다.
_ALL_AXES = ("license", "region", "size", "direct_prod", "item",
             "personnel", "performance", "capacity", "credit", "cert")


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


# ── [D3] required/actual 분리 ────────────────────────────────────────────

def test_D3_새_키가_있으면_detail_대신_그걸_쓴다():
    """compute가 두 값을 분리해 실으면 그대로 통과시킨다."""
    row = _row("불가", [
        {"axis": "size", "class": "gate", "status": "미충족",
         "detail": "sme_only vs (미등록)",      # 요약은 그대로 남아 있고
         "required": "sme_only", "actual": "(미등록)"},   # 분리값을 쓴다
    ])
    reason = _to_result(row).failed_reasons[0]
    assert reason.required == "sme_only"
    assert reason.actual == "(미등록)"


def test_D3_판정값이_보유칸으로_새지_않는다():
    """D3의 본질. '보유 미충족'은 문장으로 성립하지 않는다.

    respond가 이 문자열을 LLM 프롬프트에 그대로 싣기 때문에, 축이 추가될 때
    req_value/act_value를 안 채우면 여기서 잡힌다.
    """
    for axis in _ALL_AXES:
        row = _row("불가", [
            {"axis": axis, "class": "gate", "status": "미충족",
             "detail": "요약", "required": "요구값", "actual": "보유값"},
        ])
        reason = _to_result(row).failed_reasons[0]
        assert reason.actual not in {"충족", "미충족", "확인필요"}, axis
        assert reason.actual == "보유값", axis


def test_D3_키가_없으면_이전_동작_그대로():
    """DB 미배포 상태. 코드를 먼저 머지해도 안전하다는 보장.

    폴백이 있어서 SQL 배포와 코드 머지의 순서에 의존하지 않는다.
    (여기서 actual이 '미충족'인 것은 구버전 동작을 재현한 것이지 기대값이 아니다)
    """
    row = _row("불가", [
        {"axis": "size", "class": "gate", "status": "미충족",
         "detail": "sme_only vs (미등록)"},      # required/actual 없음
    ])
    reason = _to_result(row).failed_reasons[0]
    assert reason.required == "sme_only vs (미등록)"
    assert reason.actual == "미충족"


def test_D3_새_키가_빈_문자열이면_폴백한다():
    """SQL에서 COALESCE를 놓친 축이 생겨도 빈 칸으로 새지 않는다."""
    row = _row("불가", [
        {"axis": "size", "class": "gate", "status": "미충족",
         "detail": "sme_only vs (미등록)", "required": "", "actual": None},
    ])
    reason = _to_result(row).failed_reasons[0]
    assert reason.required == "sme_only vs (미등록)"
    assert reason.actual == "미충족"


# ─────────────── axes 패스스루 (9축 체크리스트, 2026-07-28) ───────────────
# failed_reasons는 "왜 안 되는가"만 담는다. 화면이 "무엇을 확인했는가"까지
# 보여주려면 충족 축도 내려가야 한다. 아래는 그 통로가 막히지 않는지 본다.

def test_axes는_충족_축까지_전부_실린다():
    row = _row("보완가능", [
        {"axis": "license", "class": "gate", "status": "충족",
         "detail": "전기공사업", "required": "전기공사업", "actual": "전기공사업"},
        {"axis": "performance", "class": "supp", "status": "미충족",
         "detail": "실적 10억", "required": "10억원", "actual": "3억원"},
        {"axis": "cert", "class": "info", "status": "확인필요",
         "detail": "ISO9001", "required": "ISO9001", "actual": "(미해석)"},
    ])
    result = _to_result(row)

    assert [a.axis for a in result.axes] == ["license", "performance", "cert"]
    assert [a.axis_class for a in result.axes] == ["gate", "supp", "info"]

    # 충족 축은 failed_reasons에는 없고 axes에만 있다 — 이게 추가의 이유다.
    assert "license" not in {r.field for r in result.failed_reasons}
    assert result.axes[0].actual == "전기공사업"

    # info 축도 axes에는 남는다 (판정엔 관여 안 하지만 화면엔 보여야 한다)
    assert result.axes[2].axis == "cert"


def test_axes_집계값이_그대로_넘어온다():
    row = _row("확인필요", [])
    row.update(required=9, satisfied=6, need_review=2)
    result = _to_result(row)
    assert (result.required_count, result.satisfied_count,
            result.need_review_count) == (9, 6, 2)


def test_axes가_없어도_빈_리스트다():
    """구버전 DB·집계 컬럼 누락 어느 쪽이든 터지지 않는다."""
    assert _to_result(_row("가능", None)).axes == []
    assert _to_result({"bid_id": "x", "verdict": "가능", "axes": []}).axes == []


def test_모르는_축이_와도_나머지는_살아남는다():
    """축 하나가 이상하다고 공고 전체가 사라지면 안 된다."""
    row = _row("불가", [
        {"axis": "region", "class": "gate", "status": "미충족",
         "detail": "대전 제한", "required": "대전", "actual": "서울"},
        {"axis": "미래축", "class": "unknown", "status": "미충족"},   # 모르는 class
        {"axis": "region", "class": "gate", "status": "???"},        # 모르는 status
        "문자열",                                                     # dict도 아님
    ])
    result = _to_result(row)
    assert [a.axis for a in result.axes] == ["region"]
    # 사유 쪽도 마찬가지 — 모르는 class/status는 원래부터 걸러진다
    assert [r.field for r in result.failed_reasons] == ["region"]
