"""대화형 검색 테스트 CLI.

터미널에서 질의를 직접 입력해 검색 도구를 시험해본다.

실행: python cli.py

명령어
    <아무 문장>          추천 목록 (검색 + 공고 정보)
    /search <질의>       청크 단위 RAG 검색 (근거 원문 확인)
    /rec <질의>          추천 목록 (명시적)
    /set <키> <값>       옵션 변경 (top_k, open, ord)
    /help                도움말
    /quit                종료

옵션
    /set top_k 10        결과 개수
    /set open false      마감 필터 끄기 (마감된 공고도 포함)
    /set ord false       최신 차수 필터 끄기
"""
from __future__ import annotations

import sys
import traceback

from agents.tools.search import recommend_bids, search_bids

OPTS = {
    "top_k": 5,
    "open": True,   # 마감 지난 공고 제외
    "ord": True,    # 최신 차수만
}

CATEGORY_NAMES = {
    "cnstwk": "공사",
    "servc": "용역",
    "thng": "물품",
    "frgcpt": "외자",
}


def fmt_money(v) -> str:
    return f"{v:,}원" if v else "-"


def fmt_dt(v) -> str:
    return v.strftime("%Y-%m-%d %H:%M") if v else "-"


def cmd_search(query: str) -> None:
    if not query:
        print("  사용법: /search <질의>")
        return
    result = search_bids(query, top_k=OPTS["top_k"])
    print(f"\n[RAG 검색] 청크 {len(result.chunks)}건 (전체 매칭 {result.total_hits})")
    print("-" * 70)
    if not result.chunks:
        print("  (결과 없음)")
        return
    for i, c in enumerate(result.chunks, 1):
        text = c.text.replace("\n", " ").strip()
        print(f"  [{i}] {c.score:.3f} | {c.bid_id} | {c.type}")
        print(f"      {text[:100]}...")


def cmd_recommend(query: str) -> None:
    if not query:
        print("  사용법: /rec <질의>")
        return
    recs = recommend_bids(
        query,
        top_k=OPTS["top_k"],
        only_open=OPTS["open"],
        latest_ord_only=OPTS["ord"],
    )
    print(f"\n[추천 목록] 공고 {len(recs)}건")
    print("-" * 70)
    if not recs:
        print("  (결과 없음 — /set open false 로 마감 필터를 꺼보세요)")
        return
    for i, r in enumerate(recs, 1):
        info = r.info
        if info is None:
            print(f"  [{i}] {r.hit.bid_id} (bid_table에 정보 없음)")
            continue
        cat = CATEGORY_NAMES.get(info.bid_category or "", info.bid_category or "-")
        print(f"  [{i}] {info.bid_ntce_nm or '(공고명 없음)'}   (점수 {r.hit.score:.3f})")
        print(f"      {info.dminstt_nm or '-'} | {cat} | 차수 {info.bid_ntce_ord}")
        print(f"      마감 {fmt_dt(info.bid_clse_dt)} | 추정가 {fmt_money(info.presmpt_prce)}")
        if info.cntrct_cncls_mthd_nm:
            print(f"      {info.cntrct_cncls_mthd_nm} / {info.sucsfbid_mthd_nm or '-'}")
        print(f"      {info.bid_id} | 걸린 청크 {r.hit.matched_chunks}개")


def cmd_set(args: list[str]) -> None:
    if len(args) < 2:
        print(f"  현재 옵션: {OPTS}")
        return
    key, raw = args[0], args[1].lower()
    if key == "top_k" and raw.isdigit():
        OPTS["top_k"] = max(1, min(int(raw), 50))
    elif key in ("open", "ord"):
        OPTS[key] = raw in ("true", "1", "on", "y")
    else:
        print("  사용법: /set top_k 10 | /set open false | /set ord true")
        return
    print(f"  옵션 변경됨: {OPTS}")


def main() -> None:
    print("=" * 70)
    print(" 입찰 공고 검색 에이전트 — 대화형 테스트")
    print(" /help 로 명령어 확인, /quit 로 종료")
    print("=" * 70)

    while True:
        try:
            line = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n종료합니다.")
            return

        if not line:
            continue

        try:
            if line in ("/quit", "/exit", "/q"):
                print("종료합니다.")
                return
            if line == "/help":
                print(__doc__)
                continue

            if line.startswith("/"):
                parts = line[1:].split()
                cmd, args = parts[0], parts[1:]
                if cmd == "search":
                    cmd_search(" ".join(args))
                elif cmd == "rec":
                    cmd_recommend(" ".join(args))
                elif cmd == "set":
                    cmd_set(args)
                else:
                    print(f"  알 수 없는 명령: /{cmd} (/help 참고)")
            else:
                cmd_recommend(line)

        except Exception:
            print("  오류 발생:")
            traceback.print_exc(limit=3, file=sys.stdout)


if __name__ == "__main__":
    main()