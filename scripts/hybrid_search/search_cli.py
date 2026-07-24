"""대화형 검색 테스트 CLI.

터미널에서 질의를 직접 입력해 검색 도구를 시험해본다.

실행: python cli.py

명령어
    <아무 문장>          추천 목록 (검색 + 공고 정보)
    /search <질의>       청크 단위 RAG 검색 (근거 원문 확인)
    /rec <질의>          추천 목록 (명시적)
    /set <키> <값>       옵션 변경
                         top_k, open, ord, agg(max|sum_topn),
                         topn, ratio, minchunks, minscore
    /stats               유효 공고 수 확인 (데이터 진단)
    /help                도움말
    /quit                종료

옵션
    /set top_k 10        결과 개수
    /set open false      마감 필터 끄기 (마감된 공고도 포함)
    /set ord false       최신 차수 필터 끄기
"""
from __future__ import annotations

import sys
from pathlib import Path

# 프로젝트 루트를 import 경로에 추가 — 하위 폴더에서 실행해도 agents를 찾는다
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import sys
import traceback

from agents.tools.bid_info import open_bid_ids
from agents.tools.search import recommend_bids, search_bids

OPTS = {
    "top_k": 5,
    "open": True,        # 마감 지난 공고 제외
    "ord": True,         # 최신 차수만
    "agg": "sum_topn",   # 집계: max | sum_topn
    "topn": 5,           # 합산에 쓸 청크 수
    "minscore": 0.9,     # 절대 점수 하한 (0=미적용)
    "ratio": 0.4,        # 1위 대비 비율 하한 (0=미적용)
    "minchunks": 2,      # 최소 걸린 청크 수
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
    print(f"\n[RAG 검색] 청크 {len(result.hits)}건 (전체 매칭 {result.total_hits})")
    print("-" * 70)
    if not result.hits:
        print("  (결과 없음)")
        return
    for i, h in enumerate(result.hits, 1):
        c = h.chunk
        text = c.text.replace("\n", " ").strip()
        print(f"  [{i}] {h.score:.3f} | {c.bid_id} | {c.type}")
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
        aggregate=OPTS["agg"],
        sum_top_n=OPTS["topn"],
        min_score=OPTS["minscore"],
        min_score_ratio=OPTS["ratio"],
        min_chunks=OPTS["minchunks"],
    )
    print(f"\n[추천 목록] 공고 {len(recs)}건")
    print("-" * 70)
    if not recs:
        print("  (관련 공고를 찾지 못했습니다)")
        print(f"   하한선: 점수 {OPTS['minscore']} / 1위대비 {OPTS['ratio']} "
              f"/ 최소청크 {OPTS['minchunks']}")
        print("   → /set minscore 0 /set ratio 0 으로 끄고 확인 가능")
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
        print(f"      {info.bid_id} | 청크 {r.hit.matched_chunks}개"
              f"(합산 {r.hit.summed_chunks}) | 최고 {r.hit.max_score:.3f}")


def cmd_stats() -> None:
    """데이터 진단: 업무구분별 유효 공고 수."""
    print("\n[데이터 진단] 마감 전 + 최신 차수 공고 수")
    print("-" * 70)
    total = len(open_bid_ids())
    print(f"  전체      {total:>6,}건")
    for code, name in CATEGORY_NAMES.items():
        n = len(open_bid_ids(category=code))
        print(f"  {name:<8} {n:>6,}건")
    if total == 0:
        print("\n  유효 공고가 없습니다. bid_table의 마감일시를 확인하세요.")


def cmd_set(args: list[str]) -> None:
    if len(args) < 2:
        print(f"  현재 옵션: {OPTS}")
        return
    key, raw = args[0], args[1].lower()
    if key == "top_k" and raw.isdigit():
        OPTS["top_k"] = max(1, min(int(raw), 50))
    elif key in ("open", "ord"):
        OPTS[key] = raw in ("true", "1", "on", "y")
    elif key == "agg" and raw in ("max", "sum_topn"):
        OPTS["agg"] = raw
    elif key == "topn" and raw.isdigit():
        OPTS["topn"] = max(1, int(raw))
    elif key == "minchunks" and raw.isdigit():
        OPTS["minchunks"] = max(1, int(raw))
    elif key in ("minscore", "ratio"):
        try:
            OPTS[key] = float(raw)
        except ValueError:
            print(f"  {key}는 숫자여야 합니다")
            return
    else:
        print("  사용법: /set top_k 10 | /set agg sum_topn | /set topn 5")
        print("          /set ratio 0.4 | /set minchunks 2 | /set minscore 0")
        print("          /set open false | /set ord true")
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
                elif cmd == "stats":
                    cmd_stats()
                else:
                    print(f"  알 수 없는 명령: /{cmd} (/help 참고)")
            else:
                cmd_recommend(line)

        except Exception:
            print("  오류 발생:")
            traceback.print_exc(limit=3, file=sys.stdout)


if __name__ == "__main__":
    main()