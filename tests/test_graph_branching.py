"""분기 함수 단위 검증 — route × bid_ids 상태 공간을 빠짐없이 훑는다.

그래프 E2E(test_graph.py)는 "이 route면 이 노드가 돌았다"를 보지만, 경계값
(빈 리스트 / None / 키 없음 / 공백 id)이 섞였을 때 어디로 가는지는 분기 함수를
직접 불러야 드러난다. 여기서 오차가 있으면 잘못된 노드가 조용히 돈다.
"""
import pytest

from agents.graph import (_found_bids, _route_after_bid_search,
                          _route_after_router, _route_after_scope)

# bid_ids가 올 수 있는 모양. "구했다"로 볼 것은 마지막 둘뿐이다.
_EMPTY_SHAPES = [
    ({}, "키 없음"),
    ({"bid_ids": None}, "None"),
    ({"bid_ids": []}, "빈 리스트"),
    (None, "resolved_filters 자체가 None"),
]
_FILLED_SHAPES = [
    ({"bid_ids": ["R001_000"]}, "1건"),
    ({"bid_ids": ["R001_000", "R002_000"]}, "2건"),
    ({"bid_ids": ["R001_000"], "region": "경기"}, "다른 필터와 함께"),
]


def _state(route, resolved_filters):
    return {"route": route, "resolved_filters": resolved_filters}


# ---- _found_bids: 네 가지 빈 모양이 하나의 False로 좁혀지는가 ----

@pytest.mark.parametrize("filters,label", _EMPTY_SHAPES)
def test_found_bids_false_for_every_empty_shape(filters, label):
    assert _found_bids(_state("상세", filters)) is False, label


@pytest.mark.parametrize("filters,label", _FILLED_SHAPES)
def test_found_bids_true_when_filled(filters, label):
    assert _found_bids(_state("상세", filters)) is True, label


# ---- router 뒤: route만 보고 갈린다 ----

@pytest.mark.parametrize("route,expected", [
    ("검색", "bid_search"),      # 특정할 공고가 없다 — 바로 찾는다
    ("상세", "scope"),
    ("자격", "scope"),
    ("기타", "exit"),
])
def test_route_after_router(route, expected):
    assert _route_after_router(_state(route, None)) == expected


def test_route_after_router_ignores_bid_ids():
    """라우터 직후에는 아직 공고를 구한 적이 없다 — 값이 있어도 무시한다."""
    for route, expected in (("검색", "bid_search"), ("상세", "scope"),
                            ("자격", "scope"), ("기타", "exit")):
        assert _route_after_router(
            _state(route, {"bid_ids": ["R001_000"]})) == expected


# ---- scope 뒤: 구했는가 × route ----

@pytest.mark.parametrize("filters,label", _FILLED_SHAPES)
def test_scope_found_goes_to_worker(filters, label):
    assert _route_after_scope(_state("상세", filters)) == "retrieval", label
    assert _route_after_scope(_state("자격", filters)) == "eligibility", label


@pytest.mark.parametrize("filters,label", _EMPTY_SHAPES)
def test_scope_empty_detail_falls_to_bid_search(filters, label):
    assert _route_after_scope(_state("상세", filters)) == "bid_search", label


@pytest.mark.parametrize("filters,label", _EMPTY_SHAPES)
def test_scope_empty_eligibility_exits(filters, label):
    """'가능' 공고 0건 — 빈 스코프로 판정하면 라이브 전건으로 떨어진다."""
    assert _route_after_scope(_state("자격", filters)) == "exit", label


@pytest.mark.parametrize("route", ["검색", "기타"])
def test_scope_rejects_routes_that_should_not_reach_it(route):
    """검색·기타는 scope를 거치지 않는다 — 도달했으면 배선 버그다."""
    with pytest.raises(ValueError, match="scope를 거칠 수 없는 route"):
        _route_after_scope(_state(route, {"bid_ids": ["R001_000"]}))


# ---- bid_search 뒤: 검색은 목록이 답, 상세는 발췌까지, 못 찾으면 둘 다 빠짐 ----

@pytest.mark.parametrize("filters,label", _FILLED_SHAPES)
@pytest.mark.parametrize("route,expected", [
    ("검색", "respond"),
    ("상세", "retrieval"),
])
def test_route_after_bid_search_when_found(route, expected, filters, label):
    assert _route_after_bid_search(_state(route, filters)) == expected, label


@pytest.mark.parametrize("filters,label", _EMPTY_SHAPES)
@pytest.mark.parametrize("route", ["검색", "상세"])
def test_route_after_bid_search_exits_when_not_found(route, filters, label):
    """공고를 끝내 못 찾았다 — 전체 검색을 또 돌리거나 빈 신호로 답하지 않는다."""
    assert _route_after_bid_search(_state(route, filters)) == "exit", label
