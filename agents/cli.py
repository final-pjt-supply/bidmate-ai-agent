"""수동 테스트용 REPL — python -m agents.cli

세션 컨텍스트는 이 프로세스 메모리에만 둔다(백엔드 대역).
명령: /reset 세션 초기화, /ctx 현재 컨텍스트 출력, /exit 종료
"""
import logging
import os

from agents.run import run_agent
from agents.schemas import AgentRequest, EntryContext

if os.getenv("LOG_JSON"):
    # 수집 파이프 검증용 — 서비스와 같은 JSON 한 줄 출력 (L1)
    from agents.logging_util import setup_json_logging
    setup_json_logging()
else:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


def main() -> None:
    ctx = None
    entry_bid = input("entry bid_id (Case 2 진입, 없으면 엔터): ").strip() or None
    while True:
        query = input("\n질의> ").strip()
        if not query:
            continue
        if query == "/exit":
            break
        if query == "/reset":
            ctx = None
            entry_bid = None
            logger.info("세션 초기화")
            continue
        if query == "/ctx":
            logger.info("session_context=%s",
                        ctx.model_dump_json(indent=2) if ctx else None)
            continue

        resp = run_agent(AgentRequest(
            query=query, company_id="demo-company",
            entry_context=EntryContext(bid_id=entry_bid),
            session_context=ctx))
        ctx = resp.session_context

        if resp.action == "clarify":
            print(f"\n[되묻기] {resp.clarify_message}")
        elif resp.action == "redirect":
            print(f"\n[추천 화면 이동] 필터: "
                  f"{resp.redirect_filters.model_dump(exclude_none=True)}")
        else:
            print(f"\n{resp.answer}")
            for c in resp.citations:
                print(f"  └ 근거 [{c.bid_id}#{c.chunk_idx}] {c.text[:60]}…")


if __name__ == "__main__":
    main()
