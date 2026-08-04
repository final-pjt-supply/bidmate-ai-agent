#!/usr/bin/env bash
#
# 프라이빗 EC2 1회 준비. 레거시 uvicorn 유닛(bidmate-agent)을 멈추지 않는다.
# 첫 deploy-blue-green.sh 실행이 가드를 통과한 뒤에만 8010을 인수한다.
#
# 백엔드 bootstrap을 이미 돌린 호스트라면 docker·nginx·aws는 이미 있다.
# 이 스크립트는 멱등이라 그대로 다시 돌려도 된다 — 에이전트 전용 디렉토리만 추가된다.
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo bash deploy/bootstrap-blue-green.sh" >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive

# ⚠ 이 호스트는 이미 백엔드를 서비스 중이다(nginx :8000, bidmate-api-* 컨테이너).
# 이미 깔린 패키지에 apt-get install을 걸면 새 버전이 있을 때 업그레이드가 일어나고,
# docker 업그레이드는 실행 중인 백엔드 컨테이너를 재시작시킨다. 그래서 **없는 것만**
# 설치한다. 2026-08-04 실측 기준 다섯 개 전부 이미 있어서 apt는 아예 돌지 않는다.
missing_packages=()
for package_name in ca-certificates curl docker.io nginx unzip; do
  if ! dpkg-query -W -f='${Status}' "${package_name}" 2>/dev/null \
    | grep -q 'ok installed'; then
    missing_packages+=("${package_name}")
  fi
done

if [[ "${#missing_packages[@]}" -gt 0 ]]; then
  echo "Installing missing packages: ${missing_packages[*]}"
  apt-get update
  apt-get install --yes --no-install-recommends "${missing_packages[@]}"
else
  echo "All required packages present; skipping apt (live backend must not bounce)."
fi

# 이미 running이면 --now는 재시작하지 않는다(enable만 멱등하게 붙는다).
systemctl enable --now docker
systemctl enable --now nginx

install -d -m 0755 /etc/nginx/bidmate-agent
install -d -m 0755 /opt/bidmate-agent

if id ubuntu >/dev/null 2>&1; then
  usermod --append --groups docker ubuntu
fi

if ! command -v aws >/dev/null 2>&1; then
  case "$(uname -m)" in
    aarch64 | arm64)
      aws_arch="aarch64"
      ;;
    x86_64 | amd64)
      aws_arch="x86_64"
      ;;
    *)
      echo "Unsupported architecture: $(uname -m)" >&2
      exit 1
      ;;
  esac

  temp_dir="$(mktemp -d /tmp/bidmate-awscli.XXXXXX)"
  cleanup() {
    case "${temp_dir}" in
      /tmp/bidmate-awscli.*)
        rm -rf -- "${temp_dir}"
        ;;
    esac
  }
  trap cleanup EXIT

  curl --fail --silent --show-error --location \
    "https://awscli.amazonaws.com/awscli-exe-linux-${aws_arch}.zip" \
    --output "${temp_dir}/awscliv2.zip"
  unzip -q "${temp_dir}/awscliv2.zip" -d "${temp_dir}"
  "${temp_dir}/aws/install" --update
fi

for command_name in aws curl docker flock nginx; do
  command -v "${command_name}" >/dev/null
done

# awslogs 로그 드라이버는 그룹을 만들지 않는다(--log-opt awslogs-create-group=false).
# systemd + CloudWatch Agent 경로로 이미 만들어져 있어야 정상이다.
if ! aws logs describe-log-groups \
  --log-group-name-prefix /bidmate/agent \
  --query 'logGroups[?logGroupName==`/bidmate/agent`]' \
  --output text | grep --quiet .; then
  echo "WARNING: CloudWatch log group /bidmate/agent not found." >&2
  echo "         Create it before the first deployment or containers fail to start." >&2
fi

nginx -t
docker --version
aws --version

cat <<'EOF'
Blue/Green runtime bootstrap complete.

에이전트 트래픽은 아직 그대로다(systemd bidmate-agent가 8010). 첫 배포 전에:
1. /home/ubuntu/bidding-agent/.env 를 그대로 둘 것 (Bedrock 자격증명 필수)
2. EC2 역할에 bidmate-agent ECR pull + /bidmate/agent logs 쓰기 권한 부여
3. IMDS 응답 hop limit을 2로 (브리지 컨테이너가 EC2 역할을 받게)
4. CD_ENABLED=false 인 상태에서 Agent CD를 수동 실행(confirm=deploy)
EOF
