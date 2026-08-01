"""run.py의 결정적 문구 — 판정 미제공 사유 3갈래 (N-2b).

DB를 타지 않는다. 진단 함수(missing_verdict_reasons)는 도구 층에서 이미
테스트돼 있으므로, 여기서는 **사유 → 문구 매핑과 실패 시 폴백**만 본다.

이 문구들은 LLM이 만들지 않고 코드가 정하는 고정 문구다. 틀린 사유를 단정하면
사용자가 낼 수 있는 공고를 포기하거나(마감으로 오해) 없는 공고를 기다리게 된다.
"""
import agents.run as run_mod
from agents.run import (_NO_VERDICT, _NO_VERDICT_CLOSED, _NO_VERDICT_NOT_FOUND,
                        _NO_VERDICT_NO_DATA, _no_verdict_answer)
from agents.tools.eligibility import (MISSING_CLOSED, MISSING_NO_DATA,
                                      MISSING_NOT_FOUND)


def _fake_reason(monkeypatch, reason):
    monkeypatch.setattr(run_mod, "missing_verdict_reasons",
                        lambda ids: {ids[0]: reason})


def test_마감된_공고는_마감이라고_말한다(monkeypatch):
    _fake_reason(monkeypatch, MISSING_CLOSED)
    assert _no_verdict_answer("B-1") == _NO_VERDICT_CLOSED
    assert "마감" in _NO_VERDICT_CLOSED


def test_없는_공고는_확인을_요청한다(monkeypatch):
    _fake_reason(monkeypatch, MISSING_NOT_FOUND)
    assert _no_verdict_answer("B-1") == _NO_VERDICT_NOT_FOUND
    # 마감으로 오해시키지 않는 것이 핵심이다
    assert "마감" not in _NO_VERDICT_NOT_FOUND


def test_요건_미정리_공고는_아직이라고_말한다(monkeypatch):
    _fake_reason(monkeypatch, MISSING_NO_DATA)
    assert _no_verdict_answer("B-1") == _NO_VERDICT_NO_DATA
    # 실측 151건짜리 갈래 — 마감도 부재도 아니다
    assert "마감" not in _NO_VERDICT_NO_DATA
    assert "찾지 못" not in _NO_VERDICT_NO_DATA


def test_사유를_모르면_사유를_단정하지_않는다(monkeypatch):
    _fake_reason(monkeypatch, "알 수 없는 사유")
    assert _no_verdict_answer("B-1") == _NO_VERDICT
    assert "마감" not in _NO_VERDICT          # 종전 문구의 결함이 재발하지 않게


def test_진단이_실패해도_답변은_나간다(monkeypatch):
    def boom(ids):
        raise RuntimeError("DB 접속 실패")

    monkeypatch.setattr(run_mod, "missing_verdict_reasons", boom)
    assert _no_verdict_answer("B-1") == _NO_VERDICT   # 예외가 새지 않는다


def test_세_문구는_서로_다르다():
    문구 = {_NO_VERDICT_CLOSED, _NO_VERDICT_NOT_FOUND, _NO_VERDICT_NO_DATA}
    assert len(문구) == 3
