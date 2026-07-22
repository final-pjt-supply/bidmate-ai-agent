"""[4] 응답 생성 — 신호를 자연어로 조립. 수치 계산 금지, 렌더링만."""
import logging
import re
from pathlib import Path

from agents import llm
from agents.llm import ModelTier
from agents.logging_util import node_logger
from agents.schemas import Citation

logger = logging.getLogger(__name__)

_PROMPT = (Path(__file__).parent.parent / "prompts" / "respond.md").read_text(
    encoding="utf-8")
_NUM_RE = re.compile(r"\d+(?:[,.]\d+)*")


def _eligibility_block(state) -> str:
    lines = []
    for r in state["eligibility"]:
        if r.passed:
            lines.append(f"- {r.bid_id}: 자격 통과")
        else:
            reasons = "; ".join(f"{f.field}: 요구 {f.required} / 보유 {f.actual}"
                                for f in r.failed_reasons)
            lines.append(f"- {r.bid_id}: 자격 미달 ({reasons})")
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
    """답변의 수치가 신호에 없으면 위반 목록으로 반환."""
    signal_nums = set(_NUM_RE.findall(signals))
    return [n for n in _NUM_RE.findall(answer) if n not in signal_nums]


def build_summary(state) -> str:
    """last_summary — 결정적 템플릿(LLM 안 씀)."""
    bids = {r.bid_id for r in state["eligibility"]} | \
           {c.bid_id for c in state["chunks"]}
    keys = sorted((state.get("resolved_filters") or {}).keys())
    return f"직전 턴: 공고 {len(bids)}건 안내 (조건: {', '.join(keys) or '없음'})"


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
    answer = llm.invoke(ModelTier.SYNTHESIS,
                        messages=[{"role": "user", "content": prompt}],
                        max_tokens=1500)

    violations = check_grounding(answer, signals)
    if violations:
        logger.warning("grounding 위반 탐지: %s", violations)

    citations = [Citation(bid_id=c.bid_id, file_id=c.file_id,
                          chunk_idx=c.chunk_idx, text=c.text)
                 for c in state["chunks"]]
    return {"answer": answer, "citations": citations}
