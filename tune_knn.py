"""knn 후보 배수 튜닝.

정규화 하이브리드에서 knn 후보 수를 바꿔가며 점수 분포 변화를 본다.
후보가 적으면 knn 결과와 BM25 결과의 겹침이 적어 점수가 0.5에 몰린다.

실행: python tune_knn.py
"""
from agents.tools.search import search_bids

QUERIES = [
    "전기철도 유지보수 자격 요건",
    "소프트웨어 개발 용역 입찰 조건",
    "건설 공사 실적 기준",
    "청소 용역 계약 기간",
    "감리 업체 선정 평가 배점",
]

MULTIPLIERS = [5, 20, 50]
TOP_K = 5


def analyze(query: str, mult: int) -> dict:
    r = search_bids(query, top_k=TOP_K, normalize=True, knn_multiplier=mult)
    scores = [round(h.score, 4) for h in r.hits]
    # 0.5 근처(한쪽 목록에만 걸린 문서)가 몇 개인지
    half = sum(1 for s in scores if abs(s - 0.5) < 0.001)
    spread = (max(scores) - min(scores)) if scores else 0.0
    return {
        "scores": scores,
        "unique": len(set(scores)),
        "half": half,
        "spread": round(spread, 4),
        "types": [h.chunk.type for h in r.hits],
    }


def main() -> None:
    totals = {m: {"unique": 0, "half": 0, "spread": 0.0} for m in MULTIPLIERS}

    for q in QUERIES:
        print("=" * 74)
        print(f"질의: {q}")
        print("-" * 74)
        for m in MULTIPLIERS:
            a = analyze(q, m)
            from collections import Counter
            t = dict(Counter(a["types"]))
            print(f"  x{m:<3d} 고유 {a['unique']}/{TOP_K} | 0.5개수 {a['half']} | "
                  f"점수폭 {a['spread']:.4f} | {t}")
            print(f"       {a['scores']}")
            totals[m]["unique"] += a["unique"]
            totals[m]["half"] += a["half"]
            totals[m]["spread"] += a["spread"]
        print()

    n = len(QUERIES) * TOP_K
    print("=" * 74)
    print("종합 (질의 5개 × top_k 5 = 25건 기준)")
    print("=" * 74)
    print(f"  {'배수':<6} {'고유점수':<12} {'0.5 개수':<12} {'평균 점수폭'}")
    for m in MULTIPLIERS:
        t = totals[m]
        print(f"  x{m:<5d} {t['unique']}/{n:<10} {t['half']}/{n:<10} "
              f"{t['spread']/len(QUERIES):.4f}")
    print()
    print("  고유점수↑, 0.5개수↓, 점수폭↑ 이면 순위가 잘 갈린 것")


if __name__ == "__main__":
    main()