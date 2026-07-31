# monitoring/ — 챗봇 에이전트 관측(로깅·대시보드) 산출물

로깅/모니터링 트랙(2026-07-31~)의 문서·명세·스크립트를 모은다.
**코드는 여기 없다** — 구조화 로깅 구현은 `agents/logging_util.py`(기반)와
`agents/llm.py`·`agents/run.py`(계측·배선)에 있고, 테스트는 `tests/test_logging.py`다.
런타임 모듈은 패키지 구조를 따라야 해서 옮기지 않는다.

> ⚠️ 이 폴더를 `logging/`으로 개명하지 말 것 — 레포 루트가 sys.path에 오르는 순간
> 표준 라이브러리 `logging`을 가려서 전체 코드가 깨진다.

## 파일 지도

| 파일 | 내용 |
|---|---|
| `agent_logging_dashboard_plan.md` | 전체 계획 정본 — 인프라 실사, 로그 인벤토리, 갭 7종, 아키텍처, 단계(L0~L5) |
| `metric_filters_spec.md` | L3 메트릭 필터 10종 명세(패턴·값·차원) + 선행 작업 목록 |
| (예정) `put_metric_filters.sh` | 필터 10종 생성 멱등 스크립트 — L3에서 작성 |

## 여기 없는 것 (정본 위치)

- **대시보드 JSON**: `bidmate-frontend/deploy/grafana/dashboards/agent-perf.json`
  — Grafana 프로비저닝이 읽는 곳이 정본이다(사본을 두면 드리프트).
  D1(agent-ops)·D2(agent-quality)도 완성되면 같은 곳에 둔다.
- **Grafana 접속 방법·계정**: 노션 "그라파나 접속방법" 페이지.

## 상태 (2026-07-31)

- L1 구조화 로깅: 구현 완료 — `feat/structured-logging` 브랜치 (커밋 1e3241b·0b3fadd),
  전체 pytest 179 passed, CLI 스모크(LOG_JSON=1)로 request_id·llm_call·turn_end 확인.
- D3(LLM 사용량·성능): Bedrock 네이티브 메트릭 기반 — 코드 무관, 즉시 동작.
- 대기: PR 리뷰(A — respond 이벤트 코드화 요청 포함), 서비스 분리(L2 선행),
  CI/CD에 로그 그룹 `/bidmate/agent` 요구사항 전달.
