#!/usr/bin/env bash
# ============================================================================
# CloudWatch 알람 등록 (L5) — 메트릭이 위험선을 넘으면 사람에게 알린다
#   전제: deploy/put_metric_filters.sh 가 먼저 돌아 Bidmate/Agent 네임스페이스가
#         있어야 한다. 다만 **메트릭이 아직 없어도 등록은 된다** — 알람이
#         INSUFFICIENT_DATA 로 대기하다가 데이터가 들어오면 평가를 시작한다.
#         그래서 서비스 개통 전에 미리 걸어둘 수 있다(필터와 같은 이유).
#
# 왜 무차원 메트릭을 쓰나:
#   CloudWatch는 커스텀 메트릭을 차원 축으로 롤업하지 않는다. TurnCount에는
#   route 차원이 붙어 있어 "전체 턴 수" 계열이 존재하지 않고, 알람은 계열을
#   하나 지목해야 한다(SEARCH 표현식은 알람에서 못 쓴다). 그래서 필터 스크립트가
#   TurnTotal·TurnLatencyAll·LlmRetryTotal 세 개를 무차원으로 따로 만든다.
#
# 멱등하다: put-metric-alarm 은 이름이 같으면 덮어쓴다.
#
# 사용:
#   ./deploy/put_alarms.sh                              등록(통지 없음)
#   ALARM_TOPIC_ARN=arn:aws:sns:... ./deploy/put_alarms.sh   등록 + 통지 연결
#   DRY_RUN=1 ./deploy/put_alarms.sh                    명령만 출력
#
# 임계값은 전부 환경변수로 덮어쓸 수 있다. 1~2주 실데이터를 보고 보정하는 것까지가
# 이 작업의 완결이다 — 처음 값은 근거 있는 추측이지 측정값이 아니다.
# ============================================================================
set -euo pipefail

NAMESPACE="${NAMESPACE:-Bidmate/Agent}"
REGION="${AWS_REGION:-ap-northeast-2}"
PREFIX="${ALARM_PREFIX:-bidmate-agent}"
DRY_RUN="${DRY_RUN:-0}"
TOPIC="${ALARM_TOPIC_ARN:-}"

# 임계값 (근거는 각 알람 주석)
RETRY_THRESHOLD="${RETRY_THRESHOLD:-5}"          # 15분 재시도 건수
LATENCY_P95_MS="${LATENCY_P95_MS:-15000}"        # 턴 지연 p95
NODATA_THRESHOLD="${NODATA_THRESHOLD:-20}"       # 1시간 판정미산출 건수
ENABLE_NO_TRAFFIC="${ENABLE_NO_TRAFFIC:-0}"      # 대화 두절 알람 통지 여부

alarm() {
  local name="$1" desc="$2" metric="$3" stat="$4" period="$5" evals="$6" \
        threshold="$7" op="$8" missing="$9" enabled="${10:-1}"

  local -a args=(
    --alarm-name "${PREFIX}-${name}"
    --alarm-description "$desc"
    --namespace "$NAMESPACE"
    --metric-name "$metric"
    --period "$period"
    --evaluation-periods "$evals"
    --threshold "$threshold"
    --comparison-operator "$op"
    --treat-missing-data "$missing"
    --region "$REGION"
  )
  # p95 같은 백분위는 --statistic 이 아니라 --extended-statistic 이다(상호 배타).
  if [[ "$stat" == p* ]]; then
    args+=(--extended-statistic "$stat")
  else
    args+=(--statistic "$stat")
  fi
  if [ -n "$TOPIC" ]; then
    args+=(--alarm-actions "$TOPIC" --ok-actions "$TOPIC")
  fi
  if [ "$enabled" = "1" ]; then
    args+=(--actions-enabled)
  else
    args+=(--no-actions-enabled)
  fi

  if [ "$DRY_RUN" = "1" ]; then
    printf '· %-22s %s %s %ss×%s %s %s\n' \
      "${PREFIX}-${name}" "$metric" "$stat" "$period" "$evals" "$op" "$threshold"
    return 0
  fi
  aws cloudwatch put-metric-alarm "${args[@]}"
  echo "✓ ${PREFIX}-${name}"
}

echo "네임스페이스 ${NAMESPACE} (${REGION})"
if [ -z "$TOPIC" ] && [ "$DRY_RUN" != "1" ]; then
  echo "  ⚠ ALARM_TOPIC_ARN 이 없다 — 알람은 등록되지만 아무에게도 통지되지 않는다."
  echo "    콘솔에서 상태는 볼 수 있다. 통지 채널(이메일/슬랙)이 정해지면 SNS 주제를"
  echo "    만들고 ALARM_TOPIC_ARN 을 주고 다시 실행하면 된다(멱등)."
fi

# ① LLM 재시도 급증 — Bedrock 스로틀·장애의 가장 이른 신호.
#    재시도 자체는 정상 운영에서도 가끔 난다. 15분에 5건이면 "가끔"이 아니다.
#    (진짜 원하는 신호는 llm_retry_exhausted 지만 현재 필터 계약에 없다 —
#     이벤트는 코드에 있으므로, 필요해지면 필터 1종 추가로 승격할 수 있다.)
alarm llm-retry-spike \
  "LLM 재시도 15분 ${RETRY_THRESHOLD}건 이상 — Bedrock 스로틀/장애 의심" \
  LlmRetryTotal Sum 900 1 "$RETRY_THRESHOLD" GreaterThanOrEqualToThreshold notBreaching

# ② 턴 지연 p95 — 사용자가 체감하는 느려짐. 2주기 연속이어야 울린다(순간 튐 무시).
alarm turn-latency-p95 \
  "턴 지연 p95 가 ${LATENCY_P95_MS}ms 초과 — 응답이 느려졌다" \
  TurnLatencyAll p95 900 2 "$LATENCY_P95_MS" GreaterThanThreshold notBreaching

# ③ 판정 미산출 급증 — 자격 판정 데이터가 비는 것은 대개 코드가 아니라
#    데이터 파이프라인 사고다(match_results 적재 중단 등).
alarm no-verdict-nodata \
  "1시간 판정미산출 ${NODATA_THRESHOLD}건 초과 — match_results 적재 확인" \
  NoVerdictNoData Sum 3600 1 "$NODATA_THRESHOLD" GreaterThanThreshold notBreaching

# ④ 대화 두절 — 2시간 동안 턴이 0건. 서비스 다운 또는 로그 파이프 고장.
#    treat-missing-data=breaching 이라 "데이터 자체가 없음"도 울린다. 그게 핵심이다.
#    ⚠ 기본은 통지 끔(--no-actions-enabled). 24시간 트래픽이 없는 지금 켜면
#      매일 밤 울린다. 상태는 콘솔에서 보이므로 관측은 되고, 트래픽이 붙은 뒤
#      ENABLE_NO_TRAFFIC=1 로 다시 실행하면 통지가 켜진다.
alarm no-turns \
  "2시간 동안 대화 0건 — 서비스 다운 또는 로그 수집 중단" \
  TurnTotal Sum 3600 2 1 LessThanThreshold breaching "$ENABLE_NO_TRAFFIC"

cat <<'EOS'

완료. 확인:
  aws cloudwatch describe-alarms --alarm-name-prefix bidmate-agent \
      --query 'MetricAlarms[].[AlarmName,StateValue,ActionsEnabled]' --output table

메트릭이 아직 없으면 INSUFFICIENT_DATA 가 정상이다. 실데이터 1~2주 뒤
임계값을 보정하고, 통지 채널이 정해지면 ALARM_TOPIC_ARN 을 주고 재실행할 것.
EOS
