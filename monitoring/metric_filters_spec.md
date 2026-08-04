# CloudWatch 메트릭 필터 명세 (L3) — 챗봇 에이전트 로그 → 메트릭

작성: 강태주(B), 2026-07-31. 전제: L1 구조화 로깅(feat/structured-logging) 머지,
서비스 분리 배포가 stdout을 로그 그룹 **`/bidmate/agent`**로 수집(L2 — CI/CD 요청사항).

## 왜 메트릭 필터인가

Grafana에서 Logs Insights를 직접 그리면 **조회마다 과금**된다(팀 관례: Insights는 접힌
row에 격리). 메트릭 필터는 로그 유입 시점에 한 번 숫자를 뽑아 CloudWatch Metrics로
승격시키므로 대시보드 새로고침이 공짜다. D1(운영)·D2(품질)의 모든 상시 패널은 아래
필터가 만드는 메트릭만 쓴다.

## 네임스페이스: `Bidmate/Agent`

| # | 필터 이름 | 패턴 | 값 | 차원 | 만들어지는 패널 |
|---|---|---|---|---|---|
| 1a | turn-count | `{ $.event = "turn_end" }` | `1` | route=`$.route` | 대화량, route 분포, 오류율 분모 |
| 1b | turn-result | `{ $.event = "turn_end" }` | `1` | result=`$.result` | 결과 유형 분포(D2) |
| 2 | turn-latency | `{ $.event = "turn_end" }` | `$.total_ms` | route=`$.route` | 턴 지연 p50/p95 (통계는 CW가 계산) |
| 3 | llm-tokens-in | `{ $.event = "llm_call" }` | `$.tokens_in` | tier=`$.tier` | 티어별 입력 토큰(비용 정밀판 — 네이티브 메트릭과 교차 검증) |
| 4 | llm-tokens-out | `{ $.event = "llm_call" }` | `$.tokens_out` | tier=`$.tier` | 티어별 출력 토큰 |
| 5 | llm-latency | `{ $.event = "llm_call" }` | `$.latency_ms` | tier=`$.tier` | 티어별 LLM 지연 |
| 6 | llm-retry | `{ $.event = "llm_retry" }` | `1` | tier=`$.tier`, code=`$.code` | 재시도율, 스로틀 원인 분해 |
| 7 | node-latency | `{ $.event = "node_exit" }` | `$.duration_ms` | node=`$.node` | 노드별 p95 지연 (D3 추가 패널) |
| 8 | no-verdict-closed | `{ $.event = "no_verdict" }` | `$.n_closed` | - | 판정 미제공 사유 분포(마감) |
| 9 | no-verdict-notfound | `{ $.event = "no_verdict" }` | `$.n_not_found` | - | 〃 (공고없음) |
| 10 | no-verdict-nodata | `{ $.event = "no_verdict" }` | `$.n_no_data` | - | 〃 (판정미산출 — 151건 갈래 관측) |

메트릭 이름은 필터 이름의 CamelCase(TurnCount, TurnResult, TurnLatencyMs, LlmTokensIn, ...)로
통일. 단위: ms 값은 Milliseconds, 나머지 Count.

> 왜 1a/1b로 쪼갰나 (2026-07-31 D1·D2 설계 중 정정): 한 메트릭에 route·result
> 차원을 둘 다 걸면 CloudWatch에는 (route×result) 조합별 시리즈만 생겨서,
> "route별 합계"나 "result별 합계"를 Grafana에서 뽑으려면 매번 SEARCH 수식으로
> 재집계해야 한다. 같은 로그 패턴에 1차원 필터 두 개를 거는 쪽이 조회가 단순하고
> 비용도 같다(메트릭 필터 자체는 무료, 커스텀 메트릭 과금은 시리즈 수 기준 — 조합
> 폭발이 없어 오히려 저렴). 필터 총수 10 → 11.

## 알람용 집계 메트릭 3종 (2026-08-04 추가 — L5 전제)

| # | 필터 이름 | 패턴 | 값 | 차원 | 쓰이는 곳 |
|---|---|---|---|---|---|
| 12 | turn-total | `{ $.event = "turn_end" }` | `1` | 없음 (defaultValue 0) | 대화 두절 알람 |
| 13 | turn-latency-all | `{ $.event = "turn_end" }` | `$.total_ms` | 없음 (**기본값도 없음**) | 턴 지연 p95 알람 |
| 14 | llm-retry-total | `{ $.event = "llm_retry" }` | `1` | 없음 (defaultValue 0) | 재시도 급증 알람 |

**왜 사본을 따로 만드나.** CloudWatch는 커스텀 메트릭을 차원 축으로 롤업하지 않는다.
`TurnCount`에는 `route` 차원이 붙어 있어 "전체 턴 수"라는 계열이 아예 존재하지 않고,
알람은 계열을 **하나** 지목해야 한다(대시보드는 계열을 겹쳐 그리면 되지만, 알람에서는
`SEARCH()` 표현식을 쓸 수 없다). 같은 로그 그룹에 같은 패턴으로 필터를 하나 더 거는
것은 허용되므로, 무차원 사본을 세 개 만든다. 필터는 무료이고 메트릭만 3개 늘어난다.

**13번에 `defaultValue`를 넣지 않는 이유 — 이게 함정이다.** `defaultValue`는 "패턴에
매칭되지 않은 로그 줄마다 그 값을 낸다"는 뜻이다. 카운터에는 좋다(빈 구간이 0으로
채워져 그래프가 끊기지 않는다). 그러나 **지연 시간에 쓰면 백분위가 망가진다** — 무관한
로그 줄이 전부 `0ms` 표본으로 섞여 들어가 p95가 0쪽으로 끌려 내려간다. 그래서
`turn-latency-all`만 차원도 기본값도 없이 등록한다(`put_metric_filters.sh`의 `'-'` 규약).

## 아직 로그가 안 나와서 만들 수 없는 필터 (선행 작업 필요)

| 필터 | 필요한 이벤트 | 선행 작업 |
|---|---|---|
| grounding-violation / fallback | `grounding_violation`, `fallback` | respond.py 경고에 event 코드 부여 — **A 소유, PR 리뷰에서 함께 제안** |
| search-empty / eligible-zero | `search_empty`, `eligible_zero` | bid_search·scope 경고 코드화 — 〃 |
| embedding-retry | `embedding_retry` | C의 print() → logger 전환 — C 협의 |

이것들이 D2(품질)의 grounding 깔때기 패널 재료다 — 없어도 D2의 나머지(결과 유형·사유
분포)는 위 1·8~10으로 그려진다.

## 배포 방법 (둘 중 하나 — CI/CD와 결정)

- **IaC가 없으므로 v1은 AWS CLI 스크립트**로: `aws logs put-metric-filter` 10회를 담은
  멱등 셸 스크립트를 레포(`deploy/` 또는 `scripts/`)에 커밋 — 파이프라인 인프라와 같은
  "코드로 기록" 원칙.
- 백엔드가 Terraform/CDK를 도입한다면 그쪽 모듈로 이관.

## 검증

각 필터 생성 후: 테스트 트래픽 1턴(LOG_JSON CLI 또는 스테이징 호출) → CloudWatch
Metrics에서 `Bidmate/Agent` 네임스페이스에 TurnCount=1 확인 → Grafana 패널 연결.
알람(L5)은 이 메트릭 위에 얹는다: 오류율·폴백·p95 임계.
