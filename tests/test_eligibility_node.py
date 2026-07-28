"""eligibility 노드 — 도구 호출 배선과 개수 상한만 본다 (DB 없이).

판정 자체는 tools/eligibility.py 책임이고 test_eligibility.py가 덮는다.
여기서 지키려는 건 두 가지다:
  · state의 resolved_filters.bid_ids가 도구까지 그대로 전달되는가
  · 대화 경로에 실리는 판정 개수가 상한을 넘지 않는가 (프롬프트 폭증 방어)
"""
import agents.nodes.eligibility as node_mod
from agents.nodes.eligibility import _MAX_ELIGIBILITY_ROWS, eligibility_node
from agents.schemas import EligibilityResult


def _r(verdict, i=0):
    return EligibilityResult(bid_id=f"B{i:04d}", passed=(verdict == "가능"),
                             verdict=verdict)


def _stub(monkeypatch, results):
    """도구를 가짜로 갈아끼우고, 호출 인자를 받아볼 상자를 돌려준다."""
    받은인자 = {}

    def fake(company_id, *, bid_ids=None):
        받은인자["company_id"] = company_id
        받은인자["bid_ids"] = bid_ids
        return results

    monkeypatch.setattr(node_mod, "evaluate_eligibility", fake)
    return 받은인자


def test_bid_ids가_도구까지_전달된다(monkeypatch):
    받은인자 = _stub(monkeypatch, [])
    eligibility_node({"company_id": "9001",
                      "resolved_filters": {"bid_ids": ["B1", "B2"]}})
    assert 받은인자 == {"company_id": "9001", "bid_ids": ["B1", "B2"]}


def test_필터가_없어도_터지지_않는다(monkeypatch):
    받은인자 = _stub(monkeypatch, [])
    out = eligibility_node({"company_id": "9001", "resolved_filters": None})
    assert 받은인자["bid_ids"] is None
    assert out == {"eligibility": []}


def test_상한_이하면_순서를_건드리지_않는다(monkeypatch):
    """특정 공고를 물어본 경우 순서가 뒤바뀌면 오히려 헷갈린다."""
    원본 = [_r("불가", 0), _r("가능", 1), _r("확인필요", 2)]
    _stub(monkeypatch, 원본)
    out = eligibility_node({"company_id": "9001", "resolved_filters": {}})
    assert [r.bid_id for r in out["eligibility"]] == ["B0000", "B0001", "B0002"]


def test_상한을_넘으면_유용한_순서로_자른다(monkeypatch):
    """'불가'가 절반 이상이라, 정렬 없이 자르면 화면이 전부 불가로 덮인다."""
    많음 = ([_r("불가", i) for i in range(100)]
            + [_r("가능", 900), _r("보완가능", 901), _r("확인필요", 902)])
    _stub(monkeypatch, 많음)

    실린것 = eligibility_node({"company_id": "9001",
                              "resolved_filters": {}})["eligibility"]

    assert len(실린것) == _MAX_ELIGIBILITY_ROWS
    # 앞에서 그냥 잘랐다면 셋 다 살아남지 못한다
    assert [r.verdict for r in 실린것[:3]] == ["가능", "보완가능", "확인필요"]
    assert all(r.verdict == "불가" for r in 실린것[3:])


def test_모르는_verdict도_터지지_않고_뒤로_밀린다(monkeypatch):
    """스텁이나 구버전 DB가 verdict=None을 주는 경로가 남아 있다."""
    많음 = [_r(None, i) for i in range(_MAX_ELIGIBILITY_ROWS)] + [_r("가능", 999)]
    _stub(monkeypatch, 많음)

    실린것 = eligibility_node({"company_id": "9001",
                              "resolved_filters": {}})["eligibility"]
    assert 실린것[0].verdict == "가능"
    assert len(실린것) == _MAX_ELIGIBILITY_ROWS
