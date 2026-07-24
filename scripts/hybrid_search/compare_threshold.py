"""하한선 적용 전후 비교.

같은 질의를 하한선 없이 / 적용해서 돌려 무엇이 잘리는지 본다.

실행: python compare_threshold.py
"""
import sys
from pathlib import Path

# 프로젝트 루트를 import 경로에 추가 — 하위 폴더에서 실행해도 agents를 찾는다
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agents.tools.search import recommend_bids

QUERIES = [
    "청소 용역 계약 기간",
    "SW 관련된 사업 공고",
    "정보시스템 구축 사업",
    "전기 공사 공고",
    "오늘 마감인 공고",          # 검색으로 답할 수 없는 질의 — 빈 결과가 정상
]

TOP_K = 5
RATIO = 0.4
MIN_CHUNKS = 2


def show(label: str, recs) -> None:
    print(f"  [{label}] {len(recs)}건")
    if not recs:
        print("    (결과 없음)")
        return
    for i, r in enumerate(recs, 1):
        h, info = r.hit, r.info
        name = (info.bid_ntce_nm if info else "(정보 없음)") or "(공고명 없음)"
        print(f"    {i}. {h.score:6.3f} | 청크 {h.matched_chunks:2d}개 | {name[:46]}")


def main() -> None:
    for q in QUERIES:
        print("=" * 76)
        print(f"질의: {q}")
        print("=" * 76)

        off = recommend_bids(q, top_k=TOP_K, min_score_ratio=0, min_chunks=1)
        on = recommend_bids(q, top_k=TOP_K,
                            min_score_ratio=RATIO, min_chunks=MIN_CHUNKS)

        show("하한선 없음", off)
        if off:
            print(f"       (컷오프 = 1위 {off[0].hit.score:.3f} × {RATIO} "
                  f"= {off[0].hit.score * RATIO:.3f})")
        print()
        show(f"하한선 적용 (비율 {RATIO}, 최소 청크 {MIN_CHUNKS})", on)

        cut = len(off) - len(on)
        if cut > 0:
            print(f"\n  → {cut}건 제외됨")
        print()


if __name__ == "__main__":
    main()