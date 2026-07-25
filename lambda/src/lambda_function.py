"""
BidMate 공고 정규화 Lambda — normalize_output_adapter.run()을 스케줄로 자동 실행.
=====================================================================
증분 처리: 어댑터의 select_targets가 merged/partial 공고 중
  (미정규화 OR 재병합 OR normalizer_version 상이)만 골라 처리한다.
  → 평상시엔 "그 사이 새로 병합된 공고"만 소량 처리(람다 15분 안에 충분).

⚠ 전건 재정규화(버전 범프)는 이 람다로 하지 말 것 — 19k건은 15분 초과 위험.
   그건 로컬/일회성 스크립트로. (README 참고)

DB 접속 env (아래 우선순위로 해석):
  1) DB_DSN        : psycopg conninfo 문자열 통째로. 있으면 최우선.
  2) DB_SECRET_ARN : Secrets Manager ARN (시크릿=DSN 또는 {"DB_DSN":...}). 쓰면 IAM 권한 필요.
  3) 개별 env 조립 : DB_HOST / DB_NAME / DB_USER / DB_PASSWORD / DB_PORT(기본 5432)
                     ↑ realtime-merge-dev의 MERGE_DB_* 값을 그대로 DB_*에 복사하면 됨(팀 기존 방식).

  DRY_RUN        : "1"이면 미기록(계획 로그만). 기본 "0"(실제 적재).

이벤트로 1회성 오버라이드:
  {"dry_run": true}  → 이번 호출만 DRY_RUN (수동 점검용)
"""
import os
import json
import logging

log = logging.getLogger()
log.setLevel(logging.INFO)


def _resolve_dsn() -> str:
    """DB_DSN > Secrets Manager > 개별 env(DB_HOST 등) 순으로 conninfo 문자열 생성."""
    dsn = os.environ.get("DB_DSN")
    if dsn:
        return dsn

    arn = os.environ.get("DB_SECRET_ARN")
    if arn:
        import boto3                              # 람다 런타임 기본 포함
        secret = boto3.client("secretsmanager").get_secret_value(SecretId=arn)["SecretString"]
        try:
            j = json.loads(secret)
            return j.get("DB_DSN") or j.get("dsn") or secret
        except json.JSONDecodeError:
            return secret                         # 평문 DSN

    # 개별 env 조립 (merge 함수의 MERGE_DB_* 값을 DB_*로 복사한 경우)
    host = os.environ["DB_HOST"]                  # 없으면 KeyError로 명확히 실패
    user = os.environ["DB_USER"]
    pw   = os.environ["DB_PASSWORD"]
    name = os.environ.get("DB_NAME", "bidmate")
    port = os.environ.get("DB_PORT", "5432")
    return f"host={host} port={port} dbname={name} user={user} password={pw}"


def handler(event, context):
    import psycopg                               # v3
    import normalizer                            # 팀 원본 (번들 필요)
    import normalize_output_adapter as adapter

    # dry_run 우선순위: event > env
    if isinstance(event, dict) and "dry_run" in event:
        dry = bool(event["dry_run"])
    else:
        dry = os.environ.get("DRY_RUN", "0") == "1"

    with psycopg.connect(_resolve_dsn()) as conn:
        result = adapter.run(conn, normalizer, dry_run=dry)

    log.info("normalize done: %s", result)
    # result = {"bids": N, "rows": M, "failed": K, "dry_run": bool}
    # failed>0이면 미커밋 — 다음 스케줄에서 자동 재시도됨 (멱등)
    return result
