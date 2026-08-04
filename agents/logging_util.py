"""공통 로깅 기반 — 노드 진입/종료 + 구조화(JSON) 로깅 (L1, 2026-07-31).

세 층으로 구성된다.

  ① request_id 컨텍스트   한 턴(run_agent 호출)의 상관 ID. 노드·도구·LLM 래퍼는
                          이 값을 모른다 — 포매터가 모든 레코드에 자동으로 붙인다.
  ② 턴 메트릭 누산기      llm_calls·tokens_in/out 등을 턴 단위로 합산. LLM 래퍼가
                          add_turn_metric()으로 쌓고, turn_end가 합계를 싣는다.
  ③ JsonFormatter         한 레코드 = JSON 한 줄. logger 호출의 extra= 필드를
                          최상위 키로 승격한다. CloudWatch 메트릭 필터가 이 키를
                          읽는다({ $.event = "llm_call" } 등).

핸들러 설치(setup_json_logging)는 **앱 진입점만** 호출한다 — 서비스 main,
LOG_JSON=1을 켠 CLI. 라이브러리 코드(노드·도구)는 로거만 쓰고 핸들러 정책을
모른다. 이 원칙 덕에 백엔드 임베드 상태(분리 전)에서도 충돌이 없다.

전문(질의·신호·답변 원문)은 로그에 싣지 않는다 — 종전 원칙 유지
(개인정보 + 로그 비용 + 프롬프트 유출 방지, 셋 다 이유다).
"""
import contextvars
import functools
import json
import logging
import time
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ── ① 상관 ID / ② 턴 메트릭 ──────────────────────────────────────

_request_id: contextvars.ContextVar = contextvars.ContextVar(
    "request_id", default=None)
_turn_metrics: contextvars.ContextVar = contextvars.ContextVar(
    "turn_metrics", default=None)


def new_request_id() -> str:
    """턴 시작(run_agent 진입)에서 호출 — 상관 ID 발급 + 메트릭 초기화."""
    rid = uuid.uuid4().hex[:12]
    _request_id.set(rid)
    _turn_metrics.set({})
    return rid


def add_turn_metric(key: str, n: int) -> None:
    """턴 누산기에 더한다. 턴 밖(배치·테스트 단독 호출)이면 조용히 무시 —
    계측이 주 경로를 깨면 안 된다(N-2a 진단과 같은 fail-safe 원칙)."""
    m = _turn_metrics.get()
    if m is not None:
        m[key] = m.get(key, 0) + n


def get_turn_metrics() -> dict:
    return dict(_turn_metrics.get() or {})


# ── ③ JSON 포매터 ────────────────────────────────────────────────

# LogRecord 기본 속성 — extra= 승격에서 제외할 키. 버전별 차이(taskName 등)를
# 하드코딩 대신 실제 레코드에서 뽑는다.
_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) \
    | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    """레코드 → JSON 한 줄. extra= 필드를 최상위 키로 승격한다.

    비-직렬화 값은 str()로 강등(default=str) — 로깅이 TypeError로 앱을
    죽이는 일은 없어야 한다.
    """

    def format(self, record: logging.LogRecord) -> str:
        doc = {
            "ts": datetime.fromtimestamp(
                record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        rid = _request_id.get()
        if rid:
            doc["request_id"] = rid
        for k, v in record.__dict__.items():
            if k not in _RESERVED and not k.startswith("_"):
                doc[k] = v
        if record.exc_info and record.exc_info[0]:
            doc["exc_type"] = record.exc_info[0].__name__
        return json.dumps(doc, ensure_ascii=False, default=str)


# ── ④ 제3자 로거 소음 정리 ───────────────────────────────────────

# 이 로거들의 줄은 우리 포매터를 타지 않아 JSON도 아니고, 메트릭 필터
# ({ $.event = ... })에도 걸리지 않는다 — 값은 0인데 비용은 그대로다.
# CloudWatch Logs는 수집량으로 과금되고, 컨테이너 전환 뒤에는 stdout이 awslogs
# 드라이버로 곧장 로그 그룹에 들어가므로 걸러낼 지점이 여기밖에 없다.
#   uvicorn.access  이미지 HEALTHCHECK가 30초마다 /health를 친다 → 하루 2,880줄
#   httpx/httpcore  턴마다 외부 호출 1줄씩
#   opensearch      질의마다 요청 1줄
#   botocore/boto3  Bedrock 호출 경로의 내부 로그
# 끄는 게 아니라 WARNING으로 올리는 것이다 — 진짜 오류는 그대로 올라온다.
_QUIET = ("uvicorn.access", "httpx", "httpcore", "opensearch",
          "botocore", "boto3", "urllib3", "s3transfer")


def quiet_third_party(level: int = logging.WARNING) -> None:
    """제3자·웹서버 로거를 WARNING 이상으로 올린다.

    uvicorn은 자기 로거에 propagate=False로 핸들러를 따로 달기 때문에, 루트
    핸들러를 갈아끼우는 것만으로는 access 로그가 잡히지 않는다. 레벨을 올리는
    쪽이 uvicorn의 로깅 설정과 충돌하지 않으면서 확실하다.
    """
    for name in _QUIET:
        logging.getLogger(name).setLevel(level)


def setup_json_logging(level: int = logging.INFO,
                       quiet: bool = True) -> None:
    """루트 로거를 JSON 한 줄 출력으로 전환한다. 앱 진입점 전용.

    quiet=True면 제3자 로거 소음도 함께 정리한다. 진입점은 둘 다 원하므로
    기본값을 켜두고, 분리해서 쓰고 싶을 때만 끈다.
    """
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    if quiet:
        quiet_third_party()


# ── 턴 요약 이벤트 ───────────────────────────────────────────────

def log_turn_end(*, route, action: str, result: str,
                 total_ms: int, bids_shown: int = 0) -> None:
    """turn_end — 한 턴을 요약하는 한 줄. run_agent의 모든 반환 경로가 부른다.

    result 값(대시보드 결과 유형 분포의 축):
      answer / out_of_scope / not_found / no_verdict / no_eligible / redirect
    llm_calls·tokens_in/out 합계는 누산기에서 자동으로 붙는다.
    """
    metrics = get_turn_metrics()
    logger.info(
        "turn_end route=%s action=%s result=%s total_ms=%d llm_calls=%d",
        route, action, result, total_ms, metrics.get("llm_calls", 0),
        extra={"event": "turn_end", "route": str(route), "action": action,
               "result": result, "total_ms": total_ms,
               "bids_shown": bids_shown, **metrics})


# ── 노드 로거 (기존 동작 유지 + event 필드 추가) ──────────────────

def _summarize(output: dict) -> str:
    parts = []
    for k, v in output.items():
        if isinstance(v, list):
            parts.append(f"{k}={len(v)}")
        elif isinstance(v, str):
            parts.append(f"{k}=str({len(v)})")
        elif v is None:
            parts.append(f"{k}=None")
        else:
            parts.append(f"{k}={type(v).__name__}")
    return " ".join(parts)


def node_logger(node_name: str):
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(state):
            start = time.monotonic()
            logger.info("node=%s enter", node_name,
                        extra={"event": "node_enter", "node": node_name})
            out = fn(state)
            ms = (time.monotonic() - start) * 1000
            logger.info("node=%s exit duration_ms=%.0f %s",
                        node_name, ms, _summarize(out),
                        extra={"event": "node_exit", "node": node_name,
                               "duration_ms": round(ms)})
            return out
        return wrapper
    return deco
