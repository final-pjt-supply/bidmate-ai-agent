"""FastAPI 래퍼 — 백엔드가 HTTP로 부르는 에이전트 진입점.

배포: uvicorn agents.service:app --host 127.0.0.1 --port 8001 --workers 1
루프백 전용이라 앱 레벨 인증은 두지 않는다(바인드 주소·보안그룹으로 격리).
에이전트는 stateless — 세션 컨텍스트는 요청(session_context)으로 들어오고
응답(session_context)으로 나가며, 서버는 아무것도 저장하지 않는다.
"""
from __future__ import annotations

from fastapi import FastAPI

from agents.run import run_agent
from agents.schemas import AgentRequest, AgentResponse

app = FastAPI(title="BidMate Agent", version="0.1.0")


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/turn", response_model=AgentResponse)
def turn(req: AgentRequest) -> AgentResponse:
    # run_agent는 동기(Bedrock 블로킹). async가 아닌 def로 두면 FastAPI가
    # 스레드풀에서 돌려 이벤트 루프를 막지 않는다 — 임베드 때의 스레드풀
    # 점유가 이제 이 프로세스 안에서만 일어난다(백엔드로 안 번진다).
    return run_agent(req)
