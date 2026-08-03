#!/usr/bin/env bash
# ============================================================================
# CloudWatch 메트릭 필터 등록 (L3) — 챗봇 에이전트 로그 → 메트릭
#   명세: monitoring/metric_filters_spec.md
#
# 왜 스크립트인가:
#   콘솔에서 11번 클릭하면 무엇을 왜 만들었는지가 아무 데도 안 남는다. 팀에 IaC가
#   없으므로 "코드로 기록"을 셸 스크립트로 대신한다(파이프라인 인프라와 같은 원칙).
#
# 멱등하다: put-metric-filter 는 (로그 그룹, 필터 이름)이 같으면 덮어쓴다.
#   몇 번을 돌려도 상태가 같으므로 실패 후 재실행이 안전하다.
#
# 실행 전제 (L2):
#   로그 그룹 /bidmate/agent 가 이미 있어야 한다. 서비스 분리 배포가 만든다
#   (deploy/cloudwatch-agent.json). 없으면 아래 사전 점검에서 멈춘다.
#
# ⚠ 메트릭 필터는 소급 적용이 안 된다. 등록 이전에 흘러간 로그는 메트릭이 되지
#   않으므로, 로그 그룹이 생기면 **되도록 빨리** 실행할 것.
#
# 사용:
#   ./deploy/put_metric_filters.sh              등록
#   DRY_RUN=1 ./deploy/put_metric_filters.sh    명령만 출력(자격증명 불필요)
# ============================================================================
set -euo pipefail

LOG_GROUP="${LOG_GROUP:-/bidmate/agent}"
NAMESPACE="${NAMESPACE:-Bidmate/Agent}"
REGION="${AWS_REGION:-ap-northeast-2}"
DRY_RUN="${DRY_RUN:-0}"

# 메트릭 이름은 대시보드 JSON(bidmate-frontend/deploy/grafana/dashboards/)이
# 그대로 참조한다. 여기서 바꾸면 패널이 조용히 빈다 — 양쪽을 함께 고칠 것.
put() {
  local name="$1" pattern="$2" metric="$3" value="$4" unit="$5" dims="${6:-}"

  # dimensions 와 defaultValue 는 함께 쓸 수 없다(AWS 제약). 차원을 쓰는 필터는
  # 해당 필드가 없는 로그 줄에서 아무 값도 내지 않는다 — 그게 맞는 동작이다.
  local transform
  if [ -n "$dims" ]; then
    transform=$(printf '[{"metricName":"%s","metricNamespace":"%s","metricValue":"%s","unit":"%s","dimensions":%s}]' \
      "$metric" "$NAMESPACE" "$value" "$unit" "$dims")
  else
    transform=$(printf '[{"metricName":"%s","metricNamespace":"%s","metricValue":"%s","unit":"%s","defaultValue":0}]' \
      "$metric" "$NAMESPACE" "$value" "$unit")
  fi

  if [ "$DRY_RUN" = "1" ]; then
    printf '· %-22s %s\n' "$name" "$transform"
    return
  fi

  aws logs put-metric-filter \
    --region "$REGION" \
    --log-group-name "$LOG_GROUP" \
    --filter-name "$name" \
    --filter-pattern "$pattern" \
    --metric-transformations "$transform"
  printf '✓ %s\n' "$name"
}

# ── 사전 점검 ───────────────────────────────────────────────────────────────
if [ "$DRY_RUN" != "1" ]; then
  if ! aws logs describe-log-groups --region "$REGION" \
        --log-group-name-prefix "$LOG_GROUP" \
        --query "logGroups[?logGroupName=='${LOG_GROUP}'] | length(@)" \
        --output text | grep -q '^1$'; then
    echo "✗ 로그 그룹 ${LOG_GROUP} 이 없다. L2(서비스 분리 배포 + CloudWatch Agent)가" >&2
    echo "  먼저 끝나야 한다 — deploy/cloudwatch-agent.json 참조." >&2
    exit 1
  fi
fi

echo "로그 그룹 ${LOG_GROUP} → 네임스페이스 ${NAMESPACE} (${REGION})"

# ── 턴 (turn_end) ───────────────────────────────────────────────────────────
# 1a/1b: 같은 로그 줄에 1차원 필터 두 개를 건다. 한 메트릭에 route·result 를 함께
#   걸면 (route×result) 조합 시리즈만 생겨서 축별 합계를 매번 SEARCH 로 재집계해야
#   한다. 쪼개는 쪽이 조회가 단순하고 시리즈 수도 적다.
put turn-count   '{ $.event = "turn_end" }' TurnCount     1              Count        '{"route":"$.route"}'
put turn-result  '{ $.event = "turn_end" }' TurnResult    1              Count        '{"result":"$.result"}'
put turn-latency '{ $.event = "turn_end" }' TurnLatencyMs '$.total_ms'   Milliseconds '{"route":"$.route"}'

# ── LLM (llm_call / llm_retry) ─────────────────────────────────────────────
# 토큰은 Bedrock 네이티브 메트릭에도 있다. 이쪽은 티어(라우터/합성)별로 갈리는 것이
# 차이고, 두 값을 교차 검증하면 계측 누락도 잡힌다.
put llm-tokens-in  '{ $.event = "llm_call" }'  LlmTokensIn   '$.tokens_in'  Count        '{"tier":"$.tier"}'
put llm-tokens-out '{ $.event = "llm_call" }'  LlmTokensOut  '$.tokens_out' Count        '{"tier":"$.tier"}'
put llm-latency    '{ $.event = "llm_call" }'  LlmLatencyMs  '$.latency_ms' Milliseconds '{"tier":"$.tier"}'
put llm-retry      '{ $.event = "llm_retry" }' LlmRetry      1              Count        '{"tier":"$.tier","code":"$.code"}'

# ── 노드 (node_exit) ───────────────────────────────────────────────────────
# node_logger 가 전 노드에 붙어 있어, 노드가 추가돼도 차원 값만 늘고 필터는 그대로다
# (rewrite 노드가 그렇게 자동 편입됐다).
put node-latency '{ $.event = "node_exit" }' NodeLatencyMs '$.duration_ms' Milliseconds '{"node":"$.node"}'

# ── 판정 미제공 사유 (no_verdict) ──────────────────────────────────────────
# 한 줄에 세 갈래 건수가 함께 실리므로 필터도 셋이다(값이 서로 다른 필드).
# 차원이 없어 defaultValue=0 이 붙는다 — 사유가 0건인 턴도 0으로 기록돼 그래프가
# 끊기지 않는다.
put no-verdict-closed   '{ $.event = "no_verdict" }' NoVerdictClosed   '$.n_closed'    Count
put no-verdict-notfound '{ $.event = "no_verdict" }' NoVerdictNotFound '$.n_not_found' Count
put no-verdict-nodata   '{ $.event = "no_verdict" }' NoVerdictNoData   '$.n_no_data'   Count

# 아직 못 만드는 필터 3종(grounding·검색0건·임베딩 재시도)은 해당 경고에 event 코드가
# 없어서다. A·C 협의 후 여기에 추가한다 — monitoring/metric_filters_spec.md 참조.

cat <<'MSG'

완료. 검증 순서:
  1) 테스트 트래픽 1턴  LOG_JSON=1 python -m agents.cli
  2) 메트릭 도착 확인   aws cloudwatch list-metrics --namespace Bidmate/Agent
  3) Grafana D1·D2 패널에 값이 뜨는지 확인
MSG
