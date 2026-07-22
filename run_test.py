"""검색 도구 자동 테스트: RAG 검색 vs 추천 목록.

실행: python run_test.py
"""
from agents.tools.search import recommend_bids, search_bids

QUERIES = [
    "청소 용역 계약 기간",
    "전기철도 유지보수 자격 요건",
    "소프트웨어 개발 용역 입찰 조건",
]


def fmt_money(v) -> str:
    return f"{v:,}원" if v else "-"


def fmt_dt(v) -> str:
    return v.strftime("%Y-%m-%d %H:%M") if v else "-"


def run_one(query: str) -> None:
    print("=" * 72)
    print(f"질의: {query}")
    print("=" * 72)

    # 1) 챗봇 RAG용 — 청크 단위, 중복 허용
    print("\n[RAG 검색] search_bids — 근거 청크 수집")
    print("-" * 72)
    result = search_bids(query, top_k=3)
    if not result.chunks:
        print("  (결과 없음)")
    for i, c in enumerate(result.chunks, 1):
        text = c.text.replace("\n", " ").strip()
        print(f"  [{i}] {c.score:.3f} | {c.bid_id} | {c.type}")
        print(f"      {text[:70]}...")

    # 2) 추천 목록용 — 공고 단위 + bid_table 메타
    print("\n[추천 목록] recommend_bids — 공고 정보 포함")
    print("-" * 72)
    recs = recommend_bids(query, top_k=3)
    if not recs:
        print("  (결과 없음 — 마감/차수 필터로 전부 걸러졌을 수 있음)")
        return
    for i, r in enumerate(recs, 1):
        info = r.info
        if info is None:
            print(f"  [{i}] {r.hit.bid_id} (bid_table에 정보 없음)")
            continue
        print(f"  [{i}] {info.bid_ntce_nm}   (점수 {r.hit.score:.3f})")
        print(f"      {info.dminstt_nm} | 차수 {info.bid_ntce_ord}")
        print(f"      마감 {fmt_dt(info.bid_clse_dt)} | 추정가 {fmt_money(info.presmpt_prce)}")


def main() -> None:
    for q in QUERIES:
        run_one(q)
        print()


if __name__ == "__main__":
    main()