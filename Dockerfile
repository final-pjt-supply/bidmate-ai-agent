# syntax=docker/dockerfile:1.7
#
# BidMate Agent 런타임 이미지 (linux/arm64, EC2 Graviton).
# 백엔드 Dockerfile을 이식하되 private 레포 토큰 단계는 없다 — 여기가 그 레포다.

FROM python:3.12-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

# pyproject + 패키지 소스만 있으면 빌드된다(setuptools, packages 명시 나열).
COPY pyproject.toml ./pyproject.toml
COPY agents ./agents

# 에이전트 프로세스 전체 = [runtime] extra. base만으로는 langgraph·boto3가 없다.
# 프롬프트 원문(agents/prompts/*.md)은 package-data로 함께 설치된다.
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install ".[runtime]"


FROM python:3.12-slim-bookworm AS runtime

ARG APP_VERSION=dev

# LOG_JSON=1: systemd 유닛의 Environment=와 같은 스위치를 이미지에 고정한다.
# 컨테이너는 stdout으로 JSON 한 줄씩 뱉고 docker json-file 드라이버가 받는다.
ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_VERSION="${APP_VERSION}" \
    LOG_JSON=1

RUN groupadd --system --gid 10001 bidmate \
    && useradd --system --uid 10001 --gid bidmate --home-dir /app bidmate

WORKDIR /app

# 소스를 따로 COPY하지 않는다 — builder에서 site-packages로 이미 설치됐다.
COPY --from=builder /opt/venv /opt/venv

USER bidmate

# 컨테이너 안은 항상 8000. 슬롯 구분은 바깥 publish 포트(8011/8012)가 한다.
EXPOSE 8000
STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"]

# --workers 1: run_agent가 동기 함수라 FastAPI 스레드풀에서 돈다(service.py 주석).
# 워커를 늘리면 프로세스별 스레드풀이 곱해져 Bedrock 쿼터를 더 빨리 때린다.
CMD ["uvicorn", "agents.service:app", \
     "--host", "0.0.0.0", "--port", "8000", "--workers", "1", \
     "--proxy-headers", "--forwarded-allow-ips=*", \
     "--timeout-graceful-shutdown=30"]
