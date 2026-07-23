"""bool 병합 vs 정규화 하이브리드 비교.

같은 질의를 두 방식으로 검색해 점수 분포와 순위 변화를 본다.

실행: python compare_normalization.py
"""
from agents.tools.search import search_bids

QUERIES = [
    "전기철도 유지보수 자격 요건",
    "소프트웨어 개발 용역 입찰 조건",
    "건설 공사 실적 기준",
    "청소 용역 계약 기간",
    "감리 업체 선정 평가 배점",
]

TOP_K = 5


def analyze(query: str, normalize: bool) -> dict:
    r = search_bids(query, top_k=TOP_K, normalize=normalize)
    scores = [round(h.score, 4) for h in r.hits]
    return {
        "scores": scores,
        "unique": len(set(scores)),
        "ids": [h.chunk.bid_id for h in r.hits],
        "types": [h.chunk.type for h in r.hits],
        "chunks": r.hits,
    }


def main() -> None:
    total_bool_unique = 0
    total_norm_unique = 0
    total_n = 0

    for q in QUERIES:
        print("=" * 74)
        print(f"질의: {q}")
        print("=" * 74)

        b = analyze(q, normalize=False)
        n = analyze(q, normalize=True)

        print(f"  [bool 병합]   고유점수 {b['unique']}/{len(b['scores'])} | {b['scores']}")
        print(f"  [정규화 병합] 고유점수 {n['unique']}/{len(n['scores'])} | {n['scores']}")

        # 순위가 얼마나 바뀌었는지
        common = set(b["ids"]) & set(n["ids"])
        print(f"  겹치는 공고: {len(common)}/{len(b['ids'])}건")

        # 타입 분포 변화
        from collections import Counter
        print(f"  타입: bool={dict(Counter(b['types']))} → 정규화={dict(Counter(n['types']))}")

        print("\n  [정규화 결과 상위 3건]")
        for i, h in enumerate(n["chunks"][:3], 1):
            c = h.chunk
            text = c.text.replace("\n", " ").strip()
            print(f"    [{i}] {h.score:.4f} | {c.bid_id} | {c.type}")
            print(f"        {text[:60]}...")
        print()

        total_bool_unique += b["unique"]
        total_norm_unique += n["unique"]
        total_n += len(b["scores"])

    print("=" * 74)
    print("종합")
    print("=" * 74)
    print(f"  bool 병합   평균 고유점수: {total_bool_unique}/{total_n}")
    print(f"  정규화 병합 평균 고유점수: {total_norm_unique}/{total_n}")
    diff = total_norm_unique - total_bool_unique
    if diff > 0:
        print(f"  → 정규화가 순위를 {diff}건 더 잘 구별함")
    elif diff < 0:
        print(f"  → bool이 {-diff}건 더 구별함 (예상 밖 — 확인 필요)")
    else:
        print("  → 차이 없음")


if __name__ == "__main__":
    main()