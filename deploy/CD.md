# Agent Nginx Blue/Green CI/CD

BidMate 에이전트는 PR에서 검증한 뒤 `main`에 머지된 Git commit SHA 이미지를 ECR에
보관하고, SSM으로 프라이빗 EC2의 비활성 슬롯에 배포한다. 백엔드
(`bidmate-backend/deploy/`)와 같은 방식이며, 같은 EC2 인스턴스를 공유한다.

```text
Pull request
  → pytest + import 스모크
  → linux/arm64 Docker build 검증 + nginx -t
  → main merge
  → GitHub OIDC 단기 AWS 자격증명
  → ECR SHA 이미지
  → SSM Run Command
  → inactive Blue/Green container
  → /health + EC2 role 확인
  → Nginx 원자적 전환
  → 실제 :8010 스모크 테스트 (/version, /turn 422)
  → 이전 슬롯 drain + graceful stop
```

장기 AWS access key는 GitHub에 저장하지 않는다. 백엔드 CI에 있는
`AGENT_REPO_TOKEN` 단계는 여기엔 없다 — 이 레포가 바로 그 private 레포다.

## 포트 지도

```text
:8000  nginx (백엔드)    →  blue 127.0.0.1:8001  / green 127.0.0.1:8002
:8010  nginx (에이전트)  →  blue 127.0.0.1:8011  / green 127.0.0.1:8012
```

**에이전트는 8001을 쓰지 않는다.** 8001은 백엔드 blue 슬롯이라
(`docker run --publish 127.0.0.1:8001:8000`) 에이전트가 점유하면 백엔드 배포가
포트 충돌로 실패한다. 인수인계 문서와 systemd 유닛이 8001 전제로 쓰여 있었고,
그 전제를 여기서 뒤집는다(`deploy/bidmate-agent.service`도 8010으로 정정).

전부 루프백 바인딩이라 보안그룹 변경이 없다. 백엔드가 HTTP로 전환할 때
base_url을 `http://127.0.0.1:8010`으로 잡는다(백엔드 레포 변경 — 아래 참고).

## 아직 트래픽 경로가 아니다

`bidmate-backend/app/agents/chat_service.py`가 `from agents.run import run_agent`로
같은 프로세스에서 직접 호출한다(ADR 0005 임베드). 즉 **지금 이 CD를 돌려도 실제
사용자 트래픽은 여기를 지나지 않는다.** 실제 배포 경로는 여전히 "백엔드가 이미지
빌드 시 git에서 `bidmate-agents`를 당겨오는 것"이다.

백엔드가 `httpx.Client(base_url="http://127.0.0.1:8010")`로 `/turn`을 부르도록
바꾸는 순간부터 이 파이프라인이 트래픽 경로가 된다. 그 전까지는 배포 리허설이다.

## GitHub 설정

Secret: 없음.

Variables:

| 이름 | 값 |
|---|---|
| `AWS_ROLE_ARN` | GitHub OIDC 배포 역할 ARN (에이전트 전용, 백엔드 역할과 별개) |
| `AWS_REGION` | `ap-northeast-2` |
| `ECR_REPOSITORY` | `bidmate-agent` |
| `EC2_INSTANCE_ID` | `i-0e0c7b8ee9b25de06` |
| `EC2_ENV_FILE` | `/home/ubuntu/bidding-agent/.env` |
| `DRAIN_SECONDS` | `30` |
| `CD_ENABLED` | 최초 검증 전 `false` |

`production` GitHub Environment를 만들고 필요하면 required reviewer를 설정한다. 자동
배포는 `CD_ENABLED=true`일 때만 동작한다. 비활성 상태에서도 `main`에서
`workflow_dispatch`를 실행하고 `confirm=deploy`를 입력하면 최초 배포를 검증할 수 있다.

## AWS 1회 설정

정책 원본은 `deploy/aws/`에 있다.

- GitHub 역할: `bidmate-agent` ECR push, 지정 EC2 SSM 명령, 명령 결과 조회
  (`github-actions-trust-policy.json`, `github-actions-permissions-policy.json`)
- EC2 역할: `bidmate-agent` ECR pull (`ec2-ecr-pull-policy.json`) +
  `/bidmate/agent` 로그 쓰기 (`ec2-agent-logs-policy.json`)
- ECR: immutable tag, scan-on-push, 최근 이미지 30개 lifecycle
  (`ecr-lifecycle-policy.json`)

> **trust policy의 `sub` 형식 주의.** 백엔드는 조직/레포 ID가 박힌 형식
> (`repo:final-pjt-supply@296341922/bidmate-backend@1309384344:ref:...`)을 쓴다.
> 여기엔 표준 형식(`repo:final-pjt-supply/bidmate-ai-agent:ref:refs/heads/main`)을
> 넣어뒀다. 역할을 만든 뒤 첫 OIDC 요청이 `AccessDenied`로 떨어지면 CloudTrail의
> 실제 `sub` 클레임을 보고 그 값으로 맞춘다.

브리지 네트워크의 컨테이너가 EC2 역할을 받으려면 IMDSv2 응답 hop limit이 2여야 한다
(백엔드 도입 때 이미 설정했다면 그대로면 된다).

```bash
aws ec2 modify-instance-metadata-options \
  --instance-id i-0e0c7b8ee9b25de06 \
  --http-endpoint enabled \
  --http-tokens required \
  --http-put-response-hop-limit 2
```

EC2에서 한 번 실행한다.

```bash
sudo bash deploy/bootstrap-blue-green.sh
```

이 스크립트는 기존 서비스를 중지하지 않는다. `apt`도 **없는 패키지가 있을 때만** 돈다 —
이 호스트는 백엔드를 서비스 중이라 docker 업그레이드가 일어나면 `bidmate-api-*`
컨테이너가 재시작된다.

> **실측 상태 (2026-08-04, `ssm send-command`).** `bidmate-agent` systemd 유닛은
> 이 호스트에 **설치된 적이 없다**(`is-enabled` → not-found). 8001은 백엔드 blue
> 컨테이너, 8000은 nginx가 잡고 있고 **8010은 비어 있다.** 즉 첫 CD는 밀어낼
> 서비스가 없어 legacy cutover 경로를 타지 않는다 — nginx가 빈 8010을 그냥 받는다.
> 스크립트의 레거시 처리 분기는 유닛이 설치된 환경을 위한 것이다(그 경우 Blue를
> 8011에 띄워 검사한 뒤에만 유닛을 멈추고, 실패하면 nginx를 걷고 되살린다).

## 로그 — 컨테이너 전환 후에도 `/bidmate/agent` 유지

systemd 시절 경로는 `StandardOutput=append:/var/log/bidmate-agent/agent.jsonl` →
CloudWatch Agent(`deploy/cloudwatch-agent.json`) → `/bidmate/agent`였다.

컨테이너는 stdout이 파일로 안 가므로, **Docker `awslogs` 로그 드라이버로 같은 로그
그룹에 직접 넣는다.** 그래야 이미 걸어둔 메트릭 필터(모니터링 트랙)가 전환 후에도
그대로 동작한다. 스트림 이름은 `{slot}/{commit-sha}`다.

- `LOG_JSON=1`은 이미지 `ENV`에 고정돼 있다(systemd `Environment=`와 같은 스위치).
- `mode=non-blocking`이라 CloudWatch가 느려도 요청 처리를 막지 않는다.
- `awslogs-create-group=false` — 로그 그룹이 없으면 컨테이너가 아예 안 뜬다.
  `bootstrap-blue-green.sh`가 이걸 미리 경고한다.
- 부작용: `docker logs`가 비게 된다. 실패 시 원인은 CloudWatch 스트림에 있고,
  롤백 로그가 스트림 이름을 찍어준다.

컨테이너 전환이 끝나면 CloudWatch Agent의 `/var/log/bidmate-agent/agent.jsonl`
수집 항목은 더 이상 새 데이터를 받지 않는다(레거시 유닛이 멈추므로). 롤백 대비로
당분간 남겨둔다.

## 배포 검증과 롤백

비활성 슬롯은 다음 순서로 검증한다.

1. `/health`: FastAPI 프로세스 생존
2. boto3가 EC2 역할 자격증명을 얻는지 확인 (Bedrock·RDS·OpenSearch 전제)
3. Nginx 전환 후 `/version`: SHA와 슬롯 일치 — **전환 여부를 증명하는 유일한 검사**
4. `POST /turn` 빈 본문 → 422: 라우팅과 `AgentRequest` 계약 생존

백엔드에 있는 `/ready`는 두지 않았다. 에이전트는 DB 스키마를 소유하지 않고, 실제
의존성(Bedrock·OpenSearch·RDS)은 2번의 자격증명 검사로 대신 가른다. `/turn`을 정상
호출하는 스모크는 배포마다 Bedrock 토큰을 태우므로 쓰지 않는다.

전환 전 실패는 활성 슬롯에 영향을 주지 않는다. 전환 후 실패는 이전 Nginx 링크와 이전
컨테이너를 복원하고, 최초 전환이었다면 nginx를 멈춘 뒤 systemd 유닛을 되살린다.
성공 후에는 drain 시간 동안 기존 요청을 기다린 다음 Docker `SIGTERM`과 30초
timeout으로 이전 컨테이너를 종료한다.

EC2의 `flock`(`/var/lock/bidmate-agent-deploy.lock`)과 GitHub Actions concurrency가
동시 배포를 차단한다. 백엔드 배포와는 락·슬롯·nginx 링크가 모두 분리돼 있어 동시에
돌아도 서로를 밀어내지 않는다.

## 알려진 제약

- **세션 연속성 없음.** 에이전트는 stateless라 `session_context`가 요청·응답으로
  오가지만, 배포 중 전환은 진행 중인 요청만 drain으로 보호한다.
- **`.env`는 Docker `--env-file`이 읽는다.** Python 쪽 `.env` 파싱은 `python-dotenv`
  필수라는 규칙(CLAUDE.md)과 별개다. 다만 Docker `--env-file`도 따옴표를 값의
  일부로 취급하니, 백엔드와 같은 파일을 쓰는 이상 형식을 바꾸지 말 것.
- **Bedrock 자격증명이 필수다.** `agents/llm.py`가 임포트 시점에 `load_dotenv()`를
  부르고, 실제 호출은 EC2 역할 또는 `.env` 키로 나간다.
