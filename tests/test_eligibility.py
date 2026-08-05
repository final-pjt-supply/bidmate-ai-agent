"""자격요건 매칭 도구의 매핑 규약 테스트 — DB 없이 _to_result만 검증한다.

DB 함수(compute_match_results) 자체의 정확성은 SQL 쪽 책임이고, 여기서
지키려는 건 'DB 행 → 팀 공용 계약(EligibilityResult)' 변환 규약이다:
  [D2] passed는 '가능'만 True, 4-state 원문은 verdict로 보존
  [D3] failed_reasons는 gate·supp 축의 미충족/확인필요만 (info 축 제외)
       + required(요구값)·actual(보유값) 분리 — 2026-07-27

axes의 required/actual은 compute 2026-07-27 배포부터 실린다. 이 파일은 두
경로를 모두 덮는다: 키가 있을 때(정상)와 없을 때(구버전 DB → 폴백).
"""
import logging
import sys
import types

import agents.tools.eligibility as eligibility_mod
from agents.tools.eligibility import (_to_result, evaluate_eligibility,
                                      missing_verdict_reasons, remedy_hint)

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
    """인증·신용(info)은 N/M 판정에 참여하지 않으므로 사유로 올리면 오해를 준다.

    credit은 2026-07-28(3차)에 supp → info로 내려갔다. _to_result는 class만
    보고 거르므로, 여기에 credit을 세워두지 않으면 SQL이 credit을 supp로
    되돌려도 어떤 테스트도 깨지지 않는다.
    """
    row = _row("확인필요", [
        {"axis": "cert", "class": "info", "status": "미충족", "detail": "ISO9001"},
        {"axis": "credit", "class": "info", "status": "미충족", "detail": "AA- 이상"},
        {"axis": "capacity", "class": "supp", "status": "확인필요", "detail": "시공능력"},
    ])
    result = _to_result(row)
    assert result.verdict == "확인필요"
    assert [r.field for r in result.failed_reasons] == ["capacity"]
    # 사유에서만 빠지는 것이지 화면에서까지 사라지면 안 된다
    assert {a.axis for a in result.axes} == {"cert", "credit", "capacity"}


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


def test_캡_산출_확인필요는_사유가_비는_게_정상이다():
    """v2.2~v2.3 확인필요 캡(D-22 게이트 축 0개 supp 미충족 / D-23 기대 게이트
    결측)은 사유가 '미충족인 축'이 아니라 '없는 축'이다 — failed_reasons가
    비어야 한다. 이걸 버그로 오인해 사유를 만들어 채우면 그게 회귀다.
    소비처가 이 갈래를 알아보는 시그니처는 need_review_count == 0 이다."""
    row = _row("확인필요", [
        {"axis": "region", "class": "gate", "status": "충족",
         "detail": "제한없음", "required": "제한없음", "actual": "서울"},
    ])
    row.update(required=1, satisfied=1, need_review=0)
    result = _to_result(row)
    assert result.verdict == "확인필요"
    assert result.failed_reasons == []       # 전 축 충족인데 확인필요 = 캡 산출
    assert result.need_review_count == 0


# ─────────────── 입력 순서 보존 (N-1, 2026-07-30) ───────────────
# scope 노드는 우선순위 순으로 고른 bid_ids를 넘기는데, _fetch의 SQL에는
# ORDER BY가 없어 행 순서가 플랜(해시 조인 등)에 좌우된다. 도구가 입력 순서를
# 복원하지 않으면 respond 신호에서 공고 목록 블록(scope 순서)과 자격 판정
# 블록(DB 순서)이 어긋난다. 순서는 호출 계약의 속성이므로 여기(DB 없이)서 지킨다.

def _rows_of(*bid_ids):
    return [_row("가능", [], bid_id=b) for b in bid_ids]


def test_N1_bid_ids_지정_시_입력_순서를_보존한다(monkeypatch):
    # DB가 어떤 순서로 돌려주든 결과는 요청 순서다
    monkeypatch.setattr(eligibility_mod, "_fetch",
                        lambda cid, bids: _rows_of("C", "A", "B"))
    out = evaluate_eligibility("9001", bid_ids=["A", "B", "C"])
    assert [r.bid_id for r in out] == ["A", "B", "C"]


def test_N1_bid_ids_미지정이면_정렬하지_않는다(monkeypatch):
    """전건 조회(백엔드 경로)는 순서 계약이 없다 — DB가 준 순서 그대로.

    (이 경로의 유용순 정렬은 대화 상한 초과 시 노드 _cap의 책임이다)"""
    monkeypatch.setattr(eligibility_mod, "_fetch",
                        lambda cid, bids: _rows_of("C", "A", "B"))
    out = evaluate_eligibility("9001")
    assert [r.bid_id for r in out] == ["C", "A", "B"]


def test_N1_요청에_없는_공고는_터지지_않고_맨_뒤로_보낸다(monkeypatch):
    """정상 경로에선 나올 수 없지만(_fetch가 ANY로 거른다) 방어적으로.

    KeyError로 판정 전체가 죽는 것보다, 낯선 공고가 뒤에 붙는 편이 낫다."""
    monkeypatch.setattr(eligibility_mod, "_fetch",
                        lambda cid, bids: _rows_of("X", "A"))
    out = evaluate_eligibility("9001", bid_ids=["A"])
    assert [r.bid_id for r in out] == ["A", "X"]


# ─────────────── 판정 미제공 사유 진단 (N-2a, 2026-07-30) ───────────────
# 같은 "판정 없음"이라도 마감/공고없음/판정미산출(라이브인데 요구조건 데이터
# 미비 — 실측 151/1,470건)은 사용자에게 할 말이 다르다. 진단은 관측 보조라
# 반환 계약을 바꾸지 않고, 실패해도 판정 반환을 막지 않아야 한다.
# bid_info(psycopg 의존)는 sys.modules 주입으로 대체해 DB 없이 검증한다.

def _fake_bid_info(monkeypatch, *, exists=(), live=(), boom=False):
    def _fetch_info(ids):
        if boom:
            raise RuntimeError("DB down")
        return {b: object() for b in ids if b in exists}

    fake = types.SimpleNamespace(
        fetch_bid_info=_fetch_info,
        filter_open_bids=lambda ids: {b for b in ids if b in live},
    )
    monkeypatch.setitem(sys.modules, "agents.tools.bid_info", fake)


def test_N2_사유_3갈래를_가른다(monkeypatch):
    _fake_bid_info(monkeypatch, exists={"CLOSED", "LIVE"}, live={"LIVE"})
    out = missing_verdict_reasons(["CLOSED", "GONE", "LIVE"])
    assert out == {"CLOSED": "마감", "GONE": "공고없음", "LIVE": "판정미산출"}


def test_N2_판정_없으면_사유를_로그에_남기고_반환은_그대로다(monkeypatch, caplog):
    monkeypatch.setattr(eligibility_mod, "_fetch", lambda cid, bids: [])
    _fake_bid_info(monkeypatch, exists={"CLOSED"}, live=set())
    with caplog.at_level(logging.WARNING):
        out = evaluate_eligibility("9001", bid_ids=["CLOSED", "GONE"])
    assert out == []                          # 반환 계약 불변 — 로그만 남긴다
    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "마감" in joined and "공고없음" in joined


def test_N2_진단이_죽어도_판정_반환은_산다(monkeypatch, caplog):
    monkeypatch.setattr(eligibility_mod, "_fetch",
                        lambda cid, bids: _rows_of("A"))
    _fake_bid_info(monkeypatch, boom=True)
    with caplog.at_level(logging.WARNING):
        out = evaluate_eligibility("9001", bid_ids=["A", "GONE"])
    assert [r.bid_id for r in out] == ["A"]   # 확보한 판정은 그대로 나간다
    assert any("진단 실패" in r.getMessage() for r in caplog.records)


def test_N2_전부_찾았으면_진단_쿼리를_부르지_않는다(monkeypatch):
    monkeypatch.setattr(eligibility_mod, "_fetch",
                        lambda cid, bids: _rows_of("A", "B"))
    called = []
    fake = types.SimpleNamespace(
        fetch_bid_info=lambda ids: called.append(ids) or {},
        filter_open_bids=lambda ids: set(),
    )
    monkeypatch.setitem(sys.modules, "agents.tools.bid_info", fake)
    evaluate_eligibility("9001", bid_ids=["A", "B"])
    assert called == []                       # 누락이 없으면 추가 왕복도 없다


# ─────────────── 보완 경로 힌트 (N-5, 2026-07-31) ───────────────
# supp 미달 축의 "다음 행동"을 신호에 실을 정적 지식. 게이트 축에는 힌트를
# 만들지 않는다(성립하지 않는 행동 유도 방지). 예외는 미등록 계열 하나뿐.

def test_N5_supp_축은_전부_보완_경로가_있다():
    for axis in ("performance", "capacity", "personnel",
                 "cert", "credit", "direct_prod"):
        assert remedy_hint(axis), axis


def test_N5_게이트_축과_모르는_축은_힌트가_없다():
    for axis in ("license", "region", "size", "item", "미래축"):
        assert remedy_hint(axis) is None, axis


def test_N5_미등록_보유값은_축과_무관하게_프로필_입력_유도다():
    # 게이트 축(license)이라도 미입력 공백이면 "입력하면 해소"가 유효하다
    hint = remedy_hint("license", "(미등록)")
    assert hint and "프로필" in hint
    # supp 축도 미등록이면 축별 경로(공동수급 등)보다 입력 유도가 정확하다
    assert remedy_hint("performance", "(없음)") == hint


def test_N5_보유값이_실값이면_축별_경로를_준다():
    hint = remedy_hint("performance", "3억원")
    assert hint and "공동수급" in hint
