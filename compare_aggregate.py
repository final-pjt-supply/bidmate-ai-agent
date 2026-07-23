"""공고 점수 집계 방식 비교: max vs sum_topn.

같은 질의를 두 방식으로 돌려 순위가 어떻게 달라지는지 본다.

실행: python compare_aggregate.py
"""
from agents.tools.search import recommend_bids

QUERIES = [
    "소프트웨어 관련된 공고",
    "SW 관련된 사업 공고",
    "청소 용역 계약 기간",
    "전기 공사 공고",
    "정보시스템 구축 사업",
]

TOP_K = 5


def show(label: str, recs) -> None:
    print(f"  [{label}]")
    if not recs:
        print("    (결과 없음)")
        return
    for i, r in enumerate(recs, 1):
        h, info = r.hit, r.info
        name = (info.bid_ntce_nm if info else "(정보 없음)") or "(공고명 없음)"
        cat = (info.bid_category if info else "-") or "-"
        print(f"    {i}. {h.score:6.3f} | 청크 {h.matched_chunks:2d}개 "
              f"(합산 {h.summed_chunks}) | 최고 {h.max_score:.3f} | {cat}")
        print(f"       {name[:52]}")


def main() -> None:
    for q in QUERIES:
        print("=" * 76)
        print(f"질의: {q}")
        print("=" * 76)

        show("max — 최고 청크 점수만", recommend_bids(q, top_k=TOP_K, aggregate="max"))
        print()
        show("sum_topn — 상위 5개 합산",
             recommend_bids(q, top_k=TOP_K, aggregate="sum_topn", sum_top_n=5))
        print()


if __name__ == "__main__":
    main()