"""검색 응답 시간 계측 — 어느 단계가 병목인지 분해한다.

측정 구간
  1) PostgreSQL 유효 공고 조회 (filters_to_bid_ids)
  2) Cloudflare 질의 임베딩 (embed_query)
  3) OpenSearch 하이브리드 검색
  4) PostgreSQL 공고 메타 조회 (fetch_bid_info)

실행: python measure_latency.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# 프로젝트 루트를 import 경로에 추가 — 하위 폴더에서 실행해도 agents를 찾는다
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import statistics
import time
from contextlib import contextmanager

from agents.clients.embedding import embed_query
from agents.clients.opensearch import get_client
from agents.config import get_settings
from agents.tools.bid_info import fetch_bid_info, filters_to_bid_ids
from agents.tools.search import (
    _aggregate_by_bid,
    _apply_thresholds,
    _build_hybrid_query,
    _build_query,
    _to_hit,
)

QUERIES = [
    "소프트웨어 관련된 공고",
    "청소 용역 계약 기간",
    "전기 공사 공고",
]

ROUNDS = 3          # 질의당 반복 횟수 (첫 회는 캐시 영향이 있어 여러 번 잰다)
TOP_K = 5


class Timer:
    def __init__(self) -> None:
        self.marks: dict[str, float] = {}

    @contextmanager
    def measure(self, name: str):
        start = time.perf_counter()
        try:
            yield
        finally:
            self.marks[name] = (time.perf_counter() - start) * 1000


def run_once(query: str, top_k: int = TOP_K) -> dict[str, float]:
    """recommend_bids와 같은 흐름을 단계별로 계측하며 실행한다."""
    s = get_settings()
    t = Timer()

    # 1) 유효 공고 조회
    with t.measure("1_pg_filter"):
        allowed = filters_to_bid_ids(None, only_open=True, latest_ord_only=True)

    # 2) 질의 임베딩
    with t.measure("2_embedding"):
        vector = embed_query(query)

    # 3) OpenSearch 검색
    fetch_size = top_k * 40          # 합산 방식 기본 배수
    knn_k = min(fetch_size * s.knn_candidate_multiplier, 10000)
    builder = _build_hybrid_query if s.use_normalization else _build_query
    body = builder(
        text=query, vector=vector, bid_ids=allowed, types=None,
        size=fetch_size, knn_weight=s.knn_weight, bm25_weight=s.bm25_weight,
        knn_k=knn_k,
    )
    with t.measure("3_opensearch"):
        resp = get_client().search(index=s.opensearch_index, body=body)

    hits = [_to_hit(h) for h in resp.get("hits", {}).get("hits", [])]

    # 집계·하한선 (순수 연산)
    with t.measure("3b_aggregate"):
        bids = _aggregate_by_bid(hits, aggregate=s.aggregate, sum_top_n=s.sum_top_n)
        bids = _apply_thresholds(
            bids, min_score=s.min_score, min_score_ratio=s.min_score_ratio,
            min_chunks=s.min_chunks, min_results=s.min_results,
        )[:top_k]

    # 4) 공고 메타 조회
    with t.measure("4_pg_meta"):
        if bids:
            fetch_bid_info([b.bid_id for b in bids])

    t.marks["total"] = sum(v for k, v in t.marks.items() if k != "total")
    t.marks["_n_filter_ids"] = len(allowed) if allowed else 0
    t.marks["_n_hits"] = len(hits)
    t.marks["_n_results"] = len(bids)
    return t.marks


def main() -> None:
    print("=" * 74)
    print(f" 검색 응답 시간 계측 — 질의 {len(QUERIES)}개 × {ROUNDS}회")
    print("=" * 74)

    s = get_settings()
    print(f"\n설정: 정규화={s.use_normalization} | knn배수={s.knn_candidate_multiplier}"
          f" | 집계={s.aggregate} | top_k={TOP_K}")
    print(f"      fetch_size={TOP_K * 40} | knn_k={min(TOP_K * 40 * s.knn_candidate_multiplier, 10000)}")

    all_runs: list[dict[str, float]] = []

    for q in QUERIES:
        print(f"\n{'-' * 74}")
        print(f"질의: {q}")
        print("-" * 74)
        runs = []
        for i in range(ROUNDS):
            m = run_once(q)
            runs.append(m)
            all_runs.append(m)
            print(f"  {i+1}회차: 총 {m['total']:7.0f}ms  "
                  f"(PG필터 {m['1_pg_filter']:5.0f} | 임베딩 {m['2_embedding']:5.0f} | "
                  f"검색 {m['3_opensearch']:5.0f} | 집계 {m['3b_aggregate']:4.0f} | "
                  f"메타 {m['4_pg_meta']:4.0f})")
        last = runs[-1]
        print(f"  → 필터 ID {last['_n_filter_ids']:,}개 | "
              f"청크 {last['_n_hits']}건 → 공고 {last['_n_results']}건")

    # 종합
    print("\n" + "=" * 74)
    print(" 단계별 종합 (중앙값)")
    print("=" * 74)
    steps = [
        ("1_pg_filter", "PostgreSQL 유효공고 조회"),
        ("2_embedding", "Cloudflare 임베딩"),
        ("3_opensearch", "OpenSearch 검색"),
        ("3b_aggregate", "집계·하한선 (순수 연산)"),
        ("4_pg_meta", "PostgreSQL 메타 조회"),
    ]
    total_median = statistics.median(r["total"] for r in all_runs)
    print(f"  {'단계':<28} {'중앙값':>10} {'비중':>8}")
    print("  " + "-" * 50)
    for key, label in steps:
        med = statistics.median(r[key] for r in all_runs)
        pct = med / total_median * 100
        bar = "#" * max(1, int(pct / 3))
        print(f"  {label:<28} {med:>8.0f}ms {pct:>6.1f}%  {bar}")
    print("  " + "-" * 50)
    print(f"  {'합계':<28} {total_median:>8.0f}ms")

    print("\n" + "=" * 74)
    print(" 해석")
    print("=" * 74)
    med = {k: statistics.median(r[k] for r in all_runs) for k, _ in steps}
    top = max(med, key=med.get)
    label = dict(steps)[top]
    print(f"  최대 병목: {label} ({med[top]:.0f}ms, 전체의 {med[top]/total_median*100:.0f}%)")
    print()
    if med["1_pg_filter"] > 300:
        print("  · PG 필터가 무겁다 → 유효 공고 목록 캐싱(5~10분) 검토")
    if med["2_embedding"] > 500:
        print("  · 임베딩이 무겁다 → 질의 임베딩 캐싱 검토 (같은 질의 재사용)")
    if med["3_opensearch"] > 1000:
        print("  · OpenSearch가 무겁다 → fetch_size/knn_k 축소, bid_id 필터 크기 검토")
    if total_median < 2000:
        print("  · 전체 2초 미만 — LLM 단계(재순위 등) 추가 여유 있음")
    elif total_median < 4000:
        print("  · 전체 2~4초 — LLM 추가 시 체감 저하 우려, 최적화 선행 권장")
    else:
        print("  · 전체 4초 초과 — LLM 추가 전 최적화 필수")


if __name__ == "__main__":
    main()