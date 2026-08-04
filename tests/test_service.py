"""FastAPI 래퍼(agents/service.py)의 배포 계약 테스트.

여기서 고정하는 건 기능이 아니라 **deploy/deploy-blue-green.sh가 의존하는 계약**이다.
전환 검증이 /version의 키 이름과 /turn의 422에 걸려 있어서, 그게 조용히 바뀌면
배포는 매번 "전환 실패"로 판정하고 롤백한다. 그 회귀를 CI에서 잡으려고 둔다.
"""
from fastapi.testclient import TestClient

from agents.service import app

client = TestClient(app)


def test_health_returns_ok():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_version_exposes_deploy_script_contract(monkeypatch):
    # deploy-blue-green.sh가 문자열로 매칭하는 두 키. 이름이 바뀌면 전환 검증이 깨진다.
    monkeypatch.setenv("APP_VERSION", "a" * 40)
    monkeypatch.setenv("DEPLOYMENT_SLOT", "green")

    payload = client.get("/version").json()

    assert payload == {"version": "a" * 40, "slot": "green"}


def test_version_falls_back_outside_containers(monkeypatch):
    # systemd 레거시로 떴을 때. 이 조합은 전환 대상이 아니라는 표식이기도 하다.
    monkeypatch.delenv("APP_VERSION", raising=False)
    monkeypatch.delenv("DEPLOYMENT_SLOT", raising=False)

    assert client.get("/version").json() == {"version": "dev", "slot": "legacy"}


def test_turn_rejects_empty_body_with_422():
    # 전환 후 스모크가 기대하는 상태 코드. Bedrock을 태우지 않고 계약만 확인한다.
    assert client.post("/turn", json={}).status_code == 422
