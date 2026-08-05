#!/usr/bin/env bash
#
# ECR의 불변 이미지 하나를 비활성 슬롯에 띄우고, 직접 헬스체크를 통과한 뒤에만
# Nginx 링크를 원자적으로 교체한다. SSM Run Command가 root로 실행한다.
#
# 백엔드 deploy/deploy-blue-green.sh 이식본. 다른 점:
#   - 포트 지도가 8010/8011/8012 (백엔드는 8000/8001/8002)
#   - 레거시 유닛이 systemd bidmate-agent (백엔드는 bidmate-api)
#   - Alembic 마이그레이션 단계 없음 (에이전트는 DB 스키마를 소유하지 않는다)
#   - 전환 후 스모크가 /health·/version·/turn 422 (백엔드는 /bids·/me)
set -Eeuo pipefail

readonly LEGACY_UNIT="bidmate-agent"
readonly BLUE_CONTAINER="bidmate-agent-blue"
readonly GREEN_CONTAINER="bidmate-agent-green"
readonly BLUE_PORT="8011"
readonly GREEN_PORT="8012"
readonly PUBLIC_PORT="8010"
readonly LOG_GROUP="/bidmate/agent"
readonly DEPLOY_ROOT="/opt/bidmate-agent"
readonly NGINX_ROOT="/etc/nginx/bidmate-agent"
readonly ACTIVE_LINK="/etc/nginx/conf.d/bidmate-agent.conf"
readonly STATE_FILE="${DEPLOY_ROOT}/active-slot"
readonly LOCK_FILE="/var/lock/bidmate-agent-deploy.lock"

image_uri="${1:-}"
aws_region="${2:-}"
env_file="${3:-/home/ubuntu/bidding-agent/.env}"
blue_source="${4:-}"
green_source="${5:-}"
drain_seconds="${6:-30}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root." >&2
  exit 1
fi

if [[ ! "${image_uri}" =~ ^[0-9]{12}\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com/[^:]+:[0-9a-f]{40}$ ]]; then
  echo "Image must be a private ECR URI tagged with a full Git commit SHA." >&2
  exit 1
fi

if [[ -z "${aws_region}" || ! -f "${env_file}" ]]; then
  echo "AWS region and an existing environment file are required." >&2
  exit 1
fi

if [[ ! -f "${blue_source}" || ! -f "${green_source}" ]]; then
  echo "Blue and Green Nginx configuration files are required." >&2
  exit 1
fi

if [[ ! "${drain_seconds}" =~ ^[0-9]+$ ]]; then
  echo "drain_seconds must be an integer." >&2
  exit 1
fi

for command_name in aws curl docker flock install nginx systemctl timeout; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "Required command not found: ${command_name}" >&2
    exit 1
  fi
done

install -d -m 0755 "${DEPLOY_ROOT}" "${NGINX_ROOT}"

exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
  echo "Another agent deployment is already running." >&2
  exit 1
fi

version="${image_uri##*:}"
registry="${image_uri%%/*}"

# ⚠ `readlink -f`는 마지막 경로 요소가 **없어도** 그 경로를 그대로 찍고 exit 0을 낸다.
# 그대로 쓰면 최초 배포(링크 없음)가 "" 대신 ACTIVE_LINK 자기 자신을 돌려주고,
# 아래 case의 `*)`에 걸려 "Unexpected Nginx active target"으로 죽는다.
# 2026-08-04 첫 배포가 정확히 이걸로 실패했다. 존재 여부를 먼저 본다.
resolve_active_link() {
  if [[ -e "${ACTIVE_LINK}" || -L "${ACTIVE_LINK}" ]]; then
    readlink -f "${ACTIVE_LINK}" 2>/dev/null || true
  fi
}

active_slot=""
linked_conf="$(resolve_active_link)"
case "${linked_conf}" in
  "${NGINX_ROOT}/blue.conf")
    active_slot="blue"
    ;;
  "${NGINX_ROOT}/green.conf")
    active_slot="green"
    ;;
  "")
    if [[ -f "${STATE_FILE}" ]]; then
      echo "Active-slot state exists without an Nginx link; manual recovery is required." >&2
      exit 1
    fi
    ;;
  *)
    echo "Unexpected Nginx active target: ${linked_conf}" >&2
    exit 1
    ;;
esac

# 트래픽의 단일 진실은 원자적 Nginx 링크다. 전환과 상태파일 갱신 사이에 호스트가
# 재부팅되어 상태파일이 어긋난 경우를 여기서 복구한다.
if [[ -n "${active_slot}" ]]; then
  recorded_slot="$(tr -d '[:space:]' < "${STATE_FILE}" 2>/dev/null || true)"
  if [[ "${recorded_slot}" != "${active_slot}" ]]; then
    printf '%s\n' "${active_slot}" > "${STATE_FILE}.recovered"
    mv -Tf "${STATE_FILE}.recovered" "${STATE_FILE}"
  fi
fi

if [[ "${active_slot}" == "blue" ]]; then
  candidate_slot="green"
  candidate_container="${GREEN_CONTAINER}"
  candidate_port="${GREEN_PORT}"
  old_container="${BLUE_CONTAINER}"
elif [[ "${active_slot}" == "green" ]]; then
  candidate_slot="blue"
  candidate_container="${BLUE_CONTAINER}"
  candidate_port="${BLUE_PORT}"
  old_container="${GREEN_CONTAINER}"
else
  # 최초 컨테이너 배포: Blue가 준비될 때까지 레거시 systemd 유닛을 그대로 둔다.
  candidate_slot="blue"
  candidate_container="${BLUE_CONTAINER}"
  candidate_port="${BLUE_PORT}"
  old_container=""
fi

candidate_conf="${NGINX_ROOT}/${candidate_slot}.conf"
log_stream="${candidate_slot}/${version}"
previous_link="$(resolve_active_link)"
legacy_was_active="false"
legacy_stopped="false"
switched="false"
link_changed="false"

if systemctl is-active --quiet "${LEGACY_UNIT}"; then
  legacy_was_active="true"
fi

if [[ -n "${active_slot}" ]]; then
  if [[ "$(docker inspect --format '{{.State.Running}}' "${old_container}" 2>/dev/null || true)" != "true" ]]; then
    echo "Active ${active_slot} container is not running; refusing an unsafe switch." >&2
    exit 1
  fi
fi

rollback() {
  local exit_code="$?"
  trap - ERR
  set +e

  echo "Deployment failed; restoring the previous traffic target." >&2
  # awslogs 드라이버라 `docker logs`는 보통 비어 있다. 실패 원인은 CloudWatch의
  # 아래 스트림에 있다 — 컨테이너가 뜨기 전에 죽었다면 docker logs 쪽에 남는다.
  echo "Candidate logs: ${LOG_GROUP} stream ${log_stream}" >&2
  docker logs --tail 200 "${candidate_container}" >&2 2>/dev/null || true
  docker inspect --format 'State: {{.State.Status}} ExitCode: {{.State.ExitCode}}' \
    "${candidate_container}" >&2 2>/dev/null || true

  if [[ "${link_changed}" == "true" ||
        "${switched}" == "true" ||
        "${legacy_stopped}" == "true" ]]; then
    if [[ -n "${active_slot}" ]]; then
      docker start "${old_container}" >/dev/null 2>&1 || true
    fi

    if [[ -n "${previous_link}" ]]; then
      ln -sfn "${previous_link}" "${ACTIVE_LINK}.rollback"
      mv -Tf "${ACTIVE_LINK}.rollback" "${ACTIVE_LINK}"
    else
      rm -f "${ACTIVE_LINK}"
    fi

    if nginx -t; then
      if [[ -z "${previous_link}" && "${legacy_was_active}" == "true" ]]; then
        # 최초 전환 롤백: uvicorn을 되살리기 전에 8010을 먼저 비워야 한다.
        systemctl stop nginx || true
      else
        systemctl reload nginx || true
      fi
    fi

    if [[ "${legacy_was_active}" == "true" ]]; then
      systemctl enable "${LEGACY_UNIT}" >/dev/null 2>&1 || true
      systemctl restart "${LEGACY_UNIT}" || true
    fi

    if [[ -n "${active_slot}" ]]; then
      printf '%s\n' "${active_slot}" > "${STATE_FILE}.rollback"
      mv -Tf "${STATE_FILE}.rollback" "${STATE_FILE}"
    else
      rm -f "${STATE_FILE}" "${STATE_FILE}.next"
    fi
  fi

  docker rm --force "${candidate_container}" >/dev/null 2>&1 || true
  docker logout "${registry}" >/dev/null 2>&1 || true
  exit "${exit_code}"
}
trap rollback ERR

wait_for_endpoint() {
  local url="$1"
  local _

  for _ in $(seq 1 30); do
    if curl --fail --silent --show-error --max-time 4 "${url}" >/dev/null; then
      return 0
    fi
    sleep 2
  done
  return 1
}

# ⚠ nginx graceful reload는 구 워커가 drain되는 동안에도 새 연결을 받는다. reload
# 직후에 /version을 한 번만 읽으면 구 슬롯이 답하고, 멀쩡한 후보를 두고 "전환
# 실패"로 판정해 롤백한다. 2026-08-05 첫 blue→green 전환이 정확히 이걸로 실패했다
# (최초 배포는 nginx를 새로 띄우는 경로라 구 워커가 없어 8/4에는 안 드러났다).
# 백엔드 wait_for_version 이식 — 재시도 15회 × 2초.
wait_for_version() {
  local expect_slot="$1" expect_version="$2" payload _

  for _ in $(seq 1 15); do
    payload="$(
      curl --fail --silent --max-time 5 \
        "http://127.0.0.1:${PUBLIC_PORT}/version" 2>/dev/null || true
    )"
    if [[ "${payload}" == *"\"version\":\"${expect_version}\""* &&
          "${payload}" == *"\"slot\":\"${expect_slot}\""* ]]; then
      return 0
    fi
    sleep 2
  done

  echo "Nginx did not switch to ${expect_slot}: ${payload}" >&2
  return 1
}

echo "Authenticating the EC2 role to ${registry}."
aws ecr get-login-password --region "${aws_region}" \
  | docker login --username AWS --password-stdin "${registry}" >/dev/null

echo "Pulling immutable image ${image_uri}."
docker pull "${image_uri}"

install -m 0644 "${blue_source}" "${NGINX_ROOT}/blue.conf"
install -m 0644 "${green_source}" "${NGINX_ROOT}/green.conf"

docker rm --force "${candidate_container}" >/dev/null 2>&1 || true

# 로그: awslogs 드라이버로 기존 /bidmate/agent 로그 그룹에 직접 넣는다. 그래야
# systemd 시절 걸어둔 메트릭 필터가 컨테이너 전환 후에도 그대로 산다(모니터링 트랙).
# non-blocking: CloudWatch가 느려도 에이전트 요청 처리를 막지 않는다.
docker run --detach \
  --name "${candidate_container}" \
  --env-file "${env_file}" \
  --env "AWS_REGION=${aws_region}" \
  --env "AWS_DEFAULT_REGION=${aws_region}" \
  --env "APP_VERSION=${version}" \
  --env "DEPLOYMENT_SLOT=${candidate_slot}" \
  --publish "127.0.0.1:${candidate_port}:8000" \
  --restart unless-stopped \
  --stop-timeout 30 \
  --log-driver awslogs \
  --log-opt "awslogs-region=${aws_region}" \
  --log-opt "awslogs-group=${LOG_GROUP}" \
  --log-opt "awslogs-stream=${log_stream}" \
  --log-opt awslogs-create-group=false \
  --log-opt mode=non-blocking \
  --log-opt max-buffer-size=4m \
  --label com.bidmate.service=agent \
  --label "com.bidmate.slot=${candidate_slot}" \
  --label "com.bidmate.version=${version}" \
  "${image_uri}" >/dev/null

wait_for_endpoint "http://127.0.0.1:${candidate_port}/health"

# 브리지 네트워크의 컨테이너가 Bedrock·RDS·OpenSearch용 EC2 역할을 받는지 확인한다.
# (IMDSv2 hop limit이 2가 아니면 여기서 걸린다 — 배포 전에 실패해야 하는 조건)
timeout 15 docker exec "${candidate_container}" python -c \
  "import boto3; c=boto3.Session().get_credentials(); assert c is not None; c.get_frozen_credentials()"

ln -sfn "${candidate_conf}" "${ACTIVE_LINK}.next"
mv -Tf "${ACTIVE_LINK}.next" "${ACTIVE_LINK}"
link_changed="true"
nginx -t

if [[ "${legacy_was_active}" == "true" ]]; then
  systemctl stop "${LEGACY_UNIT}"
  legacy_stopped="true"
fi

systemctl enable --now nginx
systemctl reload nginx
switched="true"

wait_for_endpoint "http://127.0.0.1:${PUBLIC_PORT}/health"

# /health는 두 슬롯 다 200이라 전환 여부를 구별하지 못한다. /version만이 nginx가
# 실제로 후보 슬롯을 보고 있음을 증명한다(service.py의 /version 주석 참고).
# 단발이 아니라 폴링이다 — 위 wait_for_version 주석의 drain 창 때문.
wait_for_version "${candidate_slot}" "${version}"

# 계약 스모크: 빈 본문 POST가 422여야 한다. FastAPI 라우팅과 AgentRequest 검증이
# 살아 있다는 뜻이다. 정상 /turn을 때리면 배포마다 Bedrock 토큰을 태우므로 쓰지 않는다.
turn_status="$(
  curl --silent --output /dev/null --write-out '%{http_code}' --max-time 10 \
    --request POST --header 'Content-Type: application/json' --data '{}' \
    "http://127.0.0.1:${PUBLIC_PORT}/turn"
)"
if [[ "${turn_status}" != "422" ]]; then
  echo "Contract smoke test expected 422 from /turn, got ${turn_status}." >&2
  false
fi

printf '%s\n' "${candidate_slot}" > "${STATE_FILE}.next"
mv -Tf "${STATE_FILE}.next" "${STATE_FILE}"

if [[ "${legacy_was_active}" == "true" ]]; then
  systemctl disable "${LEGACY_UNIT}" >/dev/null 2>&1 || true
fi

if [[ -n "${old_container}" ]]; then
  echo "Draining ${active_slot} for ${drain_seconds}s."
  sleep "${drain_seconds}"
  docker stop --time 30 "${old_container}" >/dev/null
fi

trap - ERR
docker image prune --force >/dev/null
docker logout "${registry}" >/dev/null 2>&1 || true

echo "Deployment succeeded: ${candidate_slot} serves ${version}."
