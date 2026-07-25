"""[4] 응답 생성 — 신호를 자연어로 조립. 수치 계산 금지, 렌더링만.

출력 통제(ADR 0006): LLM은 RespondOutput 슬롯(내용)만 채우고, 최종 답변 문자열의
양식은 render_answer()가 결정적으로 조립한다. 문서 발췌(제3자 작성)를 통한 간접
프롬프트 인젝션이 성공해도 산출물이 슬롯 밖으로 나갈 수 없다.
"""
import json
import logging
import re
from pathlib import Path

from pydantic import BaseModel

from agents import llm
from agents.llm import ModelTier
from agents.logging_util import node_logger
from agents.schemas import Citation

logger = logging.getLogger(__name__)

_PROMPT = (Path(__file__).parent.parent / "prompts" / "respond.md").read_text(
    encoding="utf-8")
_NUM_RE = re.compile(r"\d+(?:[,.]\d+)*")
_DATE_TOKEN_RE = re.compile(r"^\d{4}\.\d{1,2}\.\d{1,2}$")

# 출력 위생 — 악성 문서 발췌가 슬롯으로 흘러들어도 프론트에서 실행 가능한
# 형태(HTML 태그=XSS, 링크/이미지=대화 내용 유출 채널)로 나가지 않게 한다.
_MD_LINK_RE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")   # [t](u)·![t](u) → t
_TAG_RE = re.compile(r"<[^>]+>")
_URL_RE = re.compile(r"https?://\S+")


class BidItem(BaseModel):
    """공고 1건 설명 슬롯 — respond 내부 전용(팀 계약 아님)."""
    bid_id: str
    text: str


class RespondOutput(BaseModel):
    """LLM이 채우는 내용 슬롯. 전 필드 required — 구조화 출력 스키마에서
    선택 필드를 남기지 않는다(누락 폴백 분기 자체를 없앤다)."""
    headline: str
    items: list[BidItem]
    caveat: str | None


def sanitize(text: str) -> str:
    """URL·HTML 태그·마크다운 링크 제거. 프롬프트로 막고(respond.md 절대 규칙)
    여기서 한 번 더 거른다 — 프롬프트는 확률, 이 함수는 보장."""
    cleaned = _MD_LINK_RE.sub(r"\1", text)
    cleaned = _TAG_RE.sub("", cleaned)
    cleaned = _URL_RE.sub("", cleaned)
    if cleaned != text:
        logger.warning("sanitize: 답변 슬롯에서 링크/태그 제거됨")
    return cleaned.strip()


def render_answer(out: RespondOutput) -> str:
    """양식은 코드가 소유한다 — 한 줄 결론 → 공고별 목록 → 참고 순, 결정적."""
    lines = [sanitize(out.headline)]
    lines += [f"- {sanitize(i.bid_id)}: {sanitize(i.text)}" for i in out.items]
    if out.caveat:
        lines.append(f"참고: {sanitize(out.caveat)}")
    return "\n".join(line for line in lines if line)


# verdict(게이트 3-state) → 표시 라벨. verdict가 없으면(stub 등) passed로 폴백.
_VERDICT_LABEL = {
    "가능": "자격 통과",
    "불가": "자격 미달",
    "보완가능": "보완하면 가능",
    "확인필요": "확인 필요",
}


def _eligibility_block(state) -> str:
    lines = []
    for r in state["eligibility"]:
        label = _VERDICT_LABEL.get(r.verdict) if r.verdict else None
        if label is None:                       # verdict 없음(stub) → 기존 2분법
            label = "자격 통과" if r.passed else "자격 미달"
        reasons = "; ".join(f"{f.field}: 요구 {f.required} / 보유 {f.actual}"
                            for f in r.failed_reasons)
        line = f"- {r.bid_id}: {label}"
        if reasons:
            line += f" ({reasons})"
        lines.append(line)
    return "\n".join(lines) or "(자격 판정 없음)"


def _scores_block(state) -> str:
    lines = []
    for s in state["scores"]:
        lines.append(f"- {s.bid_id}: 자격 매칭도 {s.total}점")
        lines.extend(f"  - {i.axis} {i.points}점: {i.note}" for i in s.breakdown)
    return "\n".join(lines) or "(매칭도 없음)"


def _chunks_block(state) -> str:
    return "\n".join(f"[{c.bid_id}#{c.chunk_idx}] {c.text}"
                     for c in state["chunks"]) or "(발췌 없음)"


def check_grounding(answer: str, signals: str) -> list[str]:
    """답변의 수치가 신호에 없으면 위반 목록으로 반환.

    신호 토큰은 날짜(2024.07.22)·천단위 콤마(10,000,000)처럼 여러 구획이
    하나의 정규식 토큰으로 묶인다. 답변이 그 구획 일부(날짜)나 콤마 제거형
    (정규화된 금액)을 인용해도 그라운딩 위반으로 보지 않도록, 허용 집합을
    원본 토큰 ∪ 콤마 제거형 ∪ (날짜형 토큰 한정) `.` 분해 구획으로 넓힌다.

    구획 분해는 `YYYY.M.D` 형태(`_DATE_TOKEN_RE`)의 날짜 토큰에만 적용한다.
    모든 토큰에 적용하면 "2.5배"의 "5"나 "87.745%"의 "87", "1,234,567"의
    "234" 같은 소수·금액 내부 자릿수가 무관한 답변 수치와 우연히 겹쳐
    그라운딩 검증을 무력화한다 — 날짜가 아닌 토큰은 분해하지 않는다.
    """
    signal_tokens = _NUM_RE.findall(signals)
    allowed = set(signal_tokens)
    for tok in signal_tokens:
        allowed.add(tok.replace(",", ""))
        if _DATE_TOKEN_RE.match(tok):
            allowed.update(tok.split("."))

    violations = []
    for tok in _NUM_RE.findall(answer):
        if tok in allowed or tok.replace(",", "") in allowed:
            continue
        violations.append(tok)
    return violations


def build_summary(state) -> str:
    """last_summary — 결정적 템플릿(LLM 안 씀)."""
    bids = {r.bid_id for r in state["eligibility"]} | \
           {c.bid_id for c in state["chunks"]}
    keys = sorted((state.get("resolved_filters") or {}).keys())
    return f"직전 턴: 공고 {len(bids)}건 안내 (조건: {', '.join(keys) or '없음'})"


def _fallback_answer(state) -> str:
    """그라운딩 재생성까지 실패하면 생성문 대신 신호 원문만 안내한다.
    발췌는 신호 그 자체라 정의상 그라운딩 위반이 불가능하다(안전 우선)."""
    chunks = "\n".join(f"- {sanitize(c.text)}" for c in state["chunks"])
    return ("답변 생성 중 확인되지 않은 수치가 반복 감지되어, 확인된 공고 "
            "원문 발췌를 그대로 안내합니다.\n" + (chunks or "(발췌 없음)"))


@node_logger("respond")
def respond_node(state: dict) -> dict:
    signals = "\n".join([_eligibility_block(state), _scores_block(state),
                         _chunks_block(state)])
    prompt = _PROMPT.format(
        eligibility_block=_eligibility_block(state),
        scores_block=_scores_block(state),
        chunks_block=_chunks_block(state),
        query=state["query"],
    )
    messages = [{"role": "user", "content": prompt}]

    answer = None
    for attempt in (1, 2):                 # 위반 시 위반 목록을 붙여 1회 재생성
        raw = llm.invoke(ModelTier.SYNTHESIS, messages=messages,
                         max_tokens=1500,
                         output_schema=RespondOutput.model_json_schema())
        candidate = render_answer(RespondOutput.model_validate(raw))
        violations = check_grounding(candidate, signals)
        if not violations:
            answer = candidate
            break
        logger.warning("grounding 위반 탐지(attempt=%d): %s", attempt, violations)
        messages = messages + [
            {"role": "assistant",
             "content": json.dumps(raw, ensure_ascii=False)},
            {"role": "user",
             "content": "다음 수치는 신호에 없다: " + ", ".join(violations)
                        + ". 신호에 있는 수치만 사용해 다시 작성하라."},
        ]
    if answer is None:
        logger.warning("grounding 재생성 실패 — 원문 발췌 폴백으로 대체")
        answer = _fallback_answer(state)

    citations = [Citation(bid_id=c.bid_id, file_id=c.file_id,
                          chunk_idx=c.chunk_idx, text=c.text)
                 for c in state["chunks"]]
    return {"answer": answer, "citations": citations}
