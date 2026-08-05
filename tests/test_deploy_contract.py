"""배포 스크립트(deploy/deploy-blue-green.sh)의 전환 검증 계약 테스트.

test_service.py가 앱 쪽 계약(/version 키 이름)을 고정한다면 여기서는 스크립트 쪽
계약을 고정한다.

nginx graceful reload는 구 워커가 drain되는 동안에도 새 연결을 받는다. 그래서
reload 직후에 /version을 **한 번만** 읽으면 구 슬롯이 답하고, 배포는 멀쩡한
컨테이너를 두고 "전환 실패"로 판정해 롤백한다. 2026-08-05 첫 blue→green 전환이
정확히 이걸로 실패했다(최초 배포는 nginx를 새로 띄우는 경로라 구 워커가 없어
이 창이 없었고, 그래서 8/4에는 드러나지 않았다).

`bash -n`은 이 회귀를 못 잡는다 — 문법은 멀쩡하기 때문이다.
"""
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "deploy-blue-green.sh"
LINES = SCRIPT.read_text(encoding="utf-8").splitlines()
TEXT = "\n".join(LINES)


def _function_line_range(name: str) -> tuple[int, int]:
    """`name() {` ... 여는 열의 `}` 까지의 줄 번호 범위(0-based, 끝 포함)."""
    start = next(i for i, line in enumerate(LINES) if line.startswith(f"{name}() {{"))
    end = next(i for i in range(start + 1, len(LINES)) if LINES[i] == "}")
    return start, end


def test_switch_verification_retries_instead_of_reading_once():
    start, end = _function_line_range("wait_for_version")
    body = "\n".join(LINES[start:end])

    # 재시도 루프가 있어야 drain 창을 넘긴다. 백엔드와 같은 15회 × 2초.
    assert "for _ in $(seq 1 15)" in body
    assert "sleep 2" in body


def test_switch_verification_is_actually_called_after_reload():
    assert 'wait_for_version "${candidate_slot}" "${version}"' in TEXT


def test_version_is_read_only_through_the_retrying_helper():
    # 헬퍼 밖에서 /version을 직접 긁으면 단발 판정이 되살아난다.
    start, end = _function_line_range("wait_for_version")

    outside = [
        (number, line)
        for number, line in enumerate(LINES)
        if "/version" in line
        and not line.lstrip().startswith("#")
        and not start <= number <= end
    ]

    assert outside == []
