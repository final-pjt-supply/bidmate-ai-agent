"""에이전트 전 구간 수동 프로브 — B 파트(자격요건) 경로 확인용.

    python -m scripts.agent_probe 9001
    python -m scripts.agent_probe 9001 "이 공고 우리가 지원할 수 있어?"
    python -m scripts.agent_probe 9001 --live-retrieval

agents/cli.py 와 무엇이 다른가
    cli.py 는 company_id 가 "demo-company" 로 박혀 있다. 그 문자열은
    compute_match_results(%s::bigint) 에 그대로 들어가서 Postgres 가
    invalid input syntax for type bigint 로 죽는다. 즉 자격요건을 건드리는
    질의는 cli.py 로 한 건도 못 돌린다. 여기서는 인자로 받는다.

무엇을 끊고 무엇을 실물로 두는가
    retrieval  : 기본 스텁. OpenSearch 가 VPC 도메인이라 배스천 터널 없이는
                 못 붙는다. --live-retrieval 로 실물 전환(터널 필요).
    router     : 실물. Bedrock Haiku 4.5 를 호출한다.
    respond    : 실물. Bedrock Sonnet 4.6 을 호출한다. 질의 1건당 최소 2콜.
    eligibility: 실물. 실 RDS 의 compute_match_results 를 탄다.
    scoring    : run.py 가 아직 stubs.scoring_node 를 배선한다. 답변에
                 R26BK_STUB01 이나 '실적 여유율 24점' 이 보이면 그 스텁이다.

읽는 법
    [판정] 은 도구가 DB 에서 받아온 원본 분포다. 노드가 상한 20건으로 자른
    뒤 respond 로 넘어가므로, 답변에 보이는 공고 수와 다를 수 있다. 그
    차이 자체가 관찰 대상이다.
"""
from __future__ import annotations

import logging
import sys
from collections import Counter

import agents.nodes.eligibility as eligibility_mod
import agents.nodes.retrieval as retrieval_mod
from agents.nodes import stubs
from agents.nodes.eligibility import _MAX_ELIGIBILITY_ROWS
from agents.run import run_agent
from agents.schemas import AgentRequest, EntryContext

logging.basicConfig(level=logging.INFO,
                    format="%(levelname)s %(name)s %(message)s")

# 기본 프롬프트 — 특정 공고를 안 박는 것만 둔다. bid_id 가 필요한 시나리오는
# 인자로 직접 넘긴다(공고번호는 매일 바뀌므로 코드에 박지 않는다).
DEFAULT_PROMPTS = [
    "우리 회사가 지금 지원할 수 있는 공고 알려줘",
    "지원 불가능한 공고는 뭐야?",
    "우리가 자격이 안 되는 이유를 알려줘",
    "직접생산 확인이 필요한 공고가 있어?",
]

_captured: dict = {}


def _install_spy() -> None:
    """도구가 실제로 무엇을 돌려줬는지 가로채 본다(동작은 안 바꾼다)."""
    real = eligibility_mod.evaluate_eligibility

    def spy(company_id, bid_ids=None, **kw):
        rows = real(company_id, bid_ids=bid_ids, **kw)
        _captured["rows"] = rows
        _captured["bid_ids"] = bid_ids
        return rows

    eligibility_mod.evaluate_eligibility = spy


def _stub_retrieval() -> None:
    retrieval_mod.retrieve_chunks = (
        lambda query, filters=None, **kw: stubs.retrieval_node({})["chunks"])


def _report() -> None:
    rows = _captured.pop("rows", None)
    if rows is None:
        print("  [판정] 호출 없음 — 이 질의는 eligibility 노드를 안 탔다")
        return
    분포 = Counter(r.verdict for r in rows)
    실린수 = min(len(rows), _MAX_ELIGIBILITY_ROWS)
    print(f"  [판정] 도구 {len(rows)}건 {dict(분포)}  "
          f"→ 대화 경로 {실린수}건 (bid_ids={_captured.get('bid_ids')})")
    사유 = Counter(f.field for r in rows for f in r.failed_reasons)
    if 사유:
        print(f"  [사유축] {dict(사유.most_common())}")


def main() -> None:
    argv = [a for a in sys.argv[1:] if a != "--live-retrieval"]
    live_retrieval = "--live-retrieval" in sys.argv
    if not argv:
        print(__doc__)
        sys.exit(2)

    company_id, *rest = argv
    prompts = rest or DEFAULT_PROMPTS

    if not live_retrieval:
        _stub_retrieval()
    _install_spy()

    ctx = None
    for q in prompts:
        print("\n" + "─" * 70)
        print(f"질의> {q}")
        resp = run_agent(AgentRequest(
            query=q, company_id=company_id,
            entry_context=EntryContext(), session_context=ctx))
        ctx = resp.session_context

        print(f"  [action] {resp.action}")
        _report()
        if resp.action == "clarify":
            print(f"  [되묻기] {resp.clarify_message}")
        elif resp.action == "redirect":
            print(f"  [필터] "
                  f"{resp.redirect_filters.model_dump(exclude_none=True)}")
        else:
            print(f"\n{resp.answer}\n")
            for c in resp.citations:
                print(f"  └ [{c.bid_id}#{c.chunk_idx}] {c.text[:60]}…")


if __name__ == "__main__":
    main()
