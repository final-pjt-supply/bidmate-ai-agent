"""retrieve_chunks 도구 테스트 — 노드가 부를 형태 그대로 호출.

실행: python test_retrieve.py
"""
from agents.tools.search import retrieve_chunks

CASES = [
    # (설명, query, filters)
    ("조건 없음", "청소 용역 계약 기간", None),
    ("업무구분 = 용역", "소프트웨어 개발", {"category": "servc"}),
    ("마감 7일 이내", "전기 공사", {"deadline_within_days": 7}),
    ("예산 5억 이상", "정보시스템 구축", {"budget_min": 500_000_000}),
    ("특정 공고(Case 2)", "자격 요건", {"bid_ids": ["R26BK01634638_000"]}),
]


def main() -> None:
    for label, query, filters in CASES:
        print("=" * 72)
        print(f"[{label}] query={query!r} filters={filters}")
        print("-" * 72)
        chunks = retrieve_chunks(query, filters=filters, top_k=3)
        if not chunks:
            print("  (결과 없음)")
            continue
        for i, c in enumerate(chunks, 1):
            text = c.text.replace("\n", " ").strip()
            print(f"  [{i}] {c.bid_id} | {c.type} | chunk_idx={c.chunk_idx}")
            print(f"      {text[:70]}...")
        print(f"  → Chunk {len(chunks)}건 (점수 필드 없음 = 계약 준수)")
        print()


if __name__ == "__main__":
    main()