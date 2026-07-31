# 챗봇 에이전트 로깅 → Grafana 대시보드 구현 계획

작성: 강태주(B), 2026-07-31. 전제: 에이전트를 백엔드에서 분리해 별도 포트 서비스로
운영(팀 결정). 이 문서 = 로그 현황 실사 + 수집 아키텍처 + 대시보드 설계 + 단계 계획.

---

## 1. 인프라 실사 결과 (이미 있는 것 — 여기에 얹는다)

**Grafana는 이미 운영 중이다.** 프론트 EC2에서 도커로 구동(포트 3300, SSM 터널 접속,
계정 4인 발급 완료), 대시보드·데이터소스·알람이 **코드로 프로비저닝**된다
(`bidmate-frontend/deploy/grafana/`). 실사로 확정한 사실:

- **데이터소스 2개**: ① CloudWatch(기본값, EC2 인스턴스 롤 인증, ap-northeast-2)
  ② RDS PostgreSQL(`grafana_ro` — 현재 **bid_table SELECT만** 허용)
- **기존 대시보드 2종**: "파이프라인 상태"(16패널 — 수집·DLQ·Lambda 오류·p95 등),
  "회원 지표"(5패널 — RDS 직조회). 스타일 관례: 상단 stat 나열 + 하단 timeseries,
  **Logs Insights 조회는 비용 때문에 접힌 row로 격리**.
- **알람 프로비저닝 전례** 있음(member-signup.yaml).
- 대시보드 수정은 Export JSON → 레포 반영이 규약.

→ **결론: 새 스택(Loki 등)을 세우지 않는다.** 에이전트 로그를 CloudWatch Logs로
보내고 기존 Grafana에 대시보드 JSON을 추가하는 것이 최소·정합 경로다.

## 2. 현재 로그 인벤토리 (레포 전수 실사, 2026-07-31)

| 위치 | 레벨 | 내용 | 대시보드 재료 가치 |
|---|---|---|---|
| `logging_util.node_logger` | INFO | 전 노드 enter/exit + **duration_ms** + 산출 요약(건수) | ★ 노드별 지연·처리량의 원천 |
| `router` | INFO | `router route=검색` — 분류 결과 | ★ route 분포 |
| `scope` | INFO/W | bid_ids 건수 + 가능 총계 / 가능 0건 | ★ 자격 갈래 건강도 |
| `bid_search` | W | 공고 특정 실패(0건) | ★ 검색 실패율 |
| `tools/eligibility` | W | **판정 미제공 사유 3갈래**(N-2a: 마감/공고없음/판정미산출) + 진단 실패 | ★ 사유 분포 패널로 직행 |
| `respond` | W | grounding 위반(attempt·토큰) / 재생성 실패 **폴백** / sanitize 제거 / 중복 항목 제거 | ★★ 품질 깔때기의 핵심 |
| `llm` | W | 재시도(code·latency_ms·다음 대기) / 소진 | ★ Bedrock 건강도 |
| `nodes/eligibility` | W | 캡 절단(현재 미도달) | 낮음 |
| `stubs` | W | 스텁 호출 감지 | 배선 사고 감지 |
| `clients/embedding` (C) | **print()** | 임베딩 재시도 | **수집 불가 — 로거 전환 필요** |
| `cli` | - | basicConfig(INFO) — REPL 전용 | 서비스 모드 무관 |

특징: **실패 경로(WARNING)는 이미 충실**하다 — 지난 며칠의 작업(N-2a 등)이 전부 로그
계측을 남겨놔서 품질 패널 재료가 준비돼 있다. 부족한 건 아래 갭이다.

## 3. 갭 분석 — 대시보드를 만들 수 없는 이유 7가지

1. **비구조화** — 전부 사람 읽는 한국어 문자열. LogQL/Insights 파싱이 취약하다.
2. **상관 ID 없음** — 한 턴의 로그(라우터→노드→LLM)를 묶을 키가 없다.
3. **턴 요약 이벤트 없음** — "이 대화 턴이 route=자격, 총 3.2s, 정상 응답"을 말해주는
   한 줄이 없다. 지금은 node_logger 조각을 사람이 이어 붙여야 한다.
4. **성공 경로 카운트 빈약** — 정상 턴의 결과 유형(answer/OUT_OF_SCOPE/NOT_FOUND/
   NO_VERDICT...)이 로그에 안 남는다(run.py 무로그).
5. **LLM 사용량 미기록** — Bedrock 응답의 usage(입출력 토큰)를 버린다. 비용 패널 불가.
6. **수집 파이프 없음** — stdout에서 끝난다. 서비스 분리 후 CloudWatch 연결이 필요.
7. **embedding이 print()** — 로깅 체계 밖(C와 협의해 logger 전환).

참고: A가 7/28에 트레이스 로깅 계획을 세워두고 미착수 상태(M3 항목 "턴별 노드 지연·
재생성 횟수 조회") — **이번 작업이 그 자리를 겸하므로 A와 합류 협의 1회 필요**
(logging_util·run.py가 A 소유였던 것도 함께 — 로깅 임무가 B로 온 만큼 소유권 재확인).

## 4. 목표 아키텍처

```
에이전트 서비스(별도 포트, uvicorn/도커)
  └ python logging + JSON 포매터(신규)     ← 코드 변경의 전부
      └ stdout → 도커 awslogs 드라이버(또는 CW agent) → CloudWatch Logs
                                                (로그 그룹: /bidmate/agent 제안)
          ├ 메트릭 필터 → CloudWatch Metrics   ← 자주 보는 수치는 승격
          │                                      (Insights 조회 비용 회피 — 팀 관례)
          └ Logs Insights                      ← 드릴다운 전용(접힌 row)
Grafana(기존) ── CloudWatch 데이터소스(기존) ── 신규 대시보드 3종(JSON 프로비저닝)
             └── RDS 데이터소스(기존) ── 판정 분포 패널(grafana_ro에 match_results
                                          SELECT 권한 추가 요청 1건)
```

CI/CD 담당(백엔드)과 결정할 것: 도커 로그 드라이버 vs CW agent(배포 방식 정합),
로그 그룹 명명·보존기간(제안: 30일), 서비스 분리 일정과의 순서.

## 5. 로깅 설계 (구현 스펙)

**JSON 라인 스키마(공통 필드)**: `ts, level, event, request_id, route, node,
duration_ms, company_id_h(해시 — 원문 금지)` + 이벤트별 필드.

**이벤트 체계(기존 로그를 코드화 — 새 계측은 3곳뿐):**

| event | 필드 | 원천 |
|---|---|---|
| `turn_end` ★신규 | route, action, result(answer/out_of_scope/not_found/no_verdict/no_eligible/fallback), total_ms, llm_calls, grounding_retries, bids_shown | run.py 1줄 |
| `node_exit` | node, duration_ms, out_counts | node_logger 확장 |
| `llm_call` ★신규 | tier, latency_ms, tokens_in/out, retries | llm.py (Bedrock usage 필드) |
| `grounding_violation` / `fallback` | attempt, token_count | respond 기존 로그 코드화 |
| `no_verdict` | reason(마감/공고없음/판정미산출), count | N-2a 기존 로그 코드화 |
| `search_empty` / `eligible_zero` / `llm_retry` / `stub_called` / `error` ★신규(전역 핸들러) | - | 기존+서비스 층 |

**구현 지점 최소화**: ① `logging_util`에 JSON Formatter + `contextvars` 기반
request_id(턴 진입 시 발급) — 노드 코드 무수정으로 node_exit 커버 ② run.py에
turn_end 1줄 ③ llm.py에 usage 기록 ④ 기존 warning들에 event 코드 부여(문자열은
유지 — 사람도 읽게). **전문(신호·답변·질의 원문)은 로그에 싣지 않는다** — 기존
"전문은 남기지 않는다(로그 정책 합의 전)" 원칙 유지 + 개인정보·비용 양쪽 이유.

## 6. 대시보드 설계 (기존 스타일 관례 미러)

**D1 챗봇 운영** (`agent-ops.json`): stat 줄 — 오늘 대화 수 / 오류율 / 폴백 발생 /
p95 턴 지연 / LLM 재시도율. timeseries — 시간대별 대화량(route 스택), 턴 지연
p50/p95, route 분포 pie. *전부 메트릭 필터 기반(조회 비용 0).*

**D2 챗봇 품질** (`agent-quality.json`): grounding 깔때기(위반→재생성→폴백),
결과 유형 분포(answer/기타/못찾음/판정없음), **판정 미제공 사유 분포**(N-2a 로그),
검색 0건율·자격 0건율, [RDS] verdict 분포 추이(match_results GROUP BY — 판정 함수
배포가 분포를 흔들면 여기서 보인다 = 캐시 정합 감시 겸용). 하단 접힌 row: 최근
폴백·오류 로그(Insights, 비용 주의 라벨).

**D3 노드·모델 성능** (`agent-perf.json`): 노드별 p95 지연(router/scope/bid_search/
eligibility/retrieval/respond), LLM 티어별 지연·호출수, **일별 토큰 사용량**(비용
추정), Bedrock 재시도·스로틀.

**알람 후보(기존 alerting 프로비저닝 전례 따름)**: 폴백 ≥1/시간, 오류율 >5%/15분,
p95 턴 지연 >15s/15분, stub_called ≥1(배선 사고).

## 7. 단계별 계획

| 단계 | 내용 | 완료 판정 | 의존 |
|---|---|---|---|
| **L0 협의** | A와 로깅 소유권·트레이스 계획 합류 / CI·CD와 로그 드라이버·그룹·보존 / C에 embedding print→logger | 결정 3건 기록 | 없음 — 즉시 |
| **L1 구조화 로깅** | JSON Formatter + request_id + turn_end + llm usage + event 코드화 + 단위 테스트(스키마 고정) | 로컬 실행 시 JSON 라인 출력, 테스트 green | L0 소유권 |
| **L2 수집 연결** | 서비스 분리 산출물에 로그 드라이버 설정 → CloudWatch 로그 그룹 유입 확인 | CW에서 turn_end 검색됨 | 서비스 분리(백엔드) |
| **L3 메트릭+D1** | 메트릭 필터 6종 + agent-ops.json 프로비저닝 | 대시보드에 실트래픽 표시 | L2 |
| **L4 D2·D3** | 품질·성능 대시보드 + grafana_ro에 match_results 권한(요청) | 3종 프로비저닝 머지 | L3 |
| **L5 알람·문서** | 알람 4종 + 그라파나 접속 페이지에 대시보드 안내 추가 | 알람 테스트 발화 1회 | L4 |

L1은 서비스 분리와 무관하게 지금 시작 가능(stdout JSON은 로컬에서도 유효).
예상 규모: L1 1일, L3 반일, L4 1일, L5 반일 + 협의.

## 8. 리스크·원칙

Insights 조회 비용 → 메트릭 필터 우선, Insights는 접힌 row(팀 관례). company_id는
해시로만. 로그에 전문 금지(기존 원칙 유지). A 트레이스 계획과의 중복 → L0에서 합류.
서비스 분리 일정 미정 → L1을 선행해 분리 시점에 붙이기만 하면 되게.
