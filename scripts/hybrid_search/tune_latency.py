"""검색 파라미터 조합별 속도·품질 비교 (측정 편향 제거판).

이전 버전의 문제
  조합을 항상 같은 순서로 연속 실행해, 앞선 조합의 OpenSearch 캐시가
  뒤 조합에 영향을 주고 누적 부하로 뒤로 갈수록 느려졌다. 그 결과
  knn_k를 줄였는데 오히려 9배 느려지는 비상식적 수치가 나왔다.

이번 버전의 대응
  - 워밍업: 측정 전 버리는 실행을 넣어 연결·캐시 상태를 맞춘다
  - 순서 무작위화: 라운드마다 조합 순서를 섞어 순서 편향을 제거한다
  - 반복 측정: 여러 라운드를 돌려 중앙값을 쓴다
  - 조합 간 대기: 누적 부하가 가라앉을 시간을 준다

실행: python tune_latency.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# 프로젝트 루트를 import 경로에 추가 — 하위 폴더에서 실행해도 agents를 찾는다
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import random
import statistics
import time

from agents.clients.embedding import embed_query
from agents.clients.opensearch import get_client
from agents.config import get_settings
from agents.tools.bid_info import filters_to_bid_ids
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

# (라벨, fetch_size, knn 배수)
# 필터 유무는 결과가 33%로 크게 달라져(마감 지난 공고 혼입) 비교에서 제외
COMBOS = [
    ("fetch200 x20", 200, 20),   # 현재 설정 (knn_k=4000)
    ("fetch200 x10", 200, 10),   # 2000
    ("fetch200 x5 ", 200, 5),    # 1000
    ("fetch200 x2 ", 200, 2),    # 400
    ("fetch150 x5 ", 150, 5),    # 750
    ("fetch100 x5 ", 100, 5),    # 500
]

ROUNDS = 3
COOLDOWN_SEC = 0.5
TOP_K = 5
KNN_K_MAX = 10000
BASELINE = COMBOS[0][0]


def search(query: str, vector: list[float], allowed: list[str] | None,
           fetch_size: int, knn_mult: int) -> tuple[list, float]:
    """한 조합으로 검색해 (공고 리스트, OpenSearch 소요 ms)를 반환한다."""
    s = get_settings()
    knn_k = min(fetch_size * knn_mult, KNN_K_MAX)
    builder = _build_hybrid_query if s.use_normalization else _build_query
    body = builder(
        text=query, vector=vector, bid_ids=allowed, types=None,
        size=fetch_size, knn_weight=s.knn_weight,
        bm25_weight=s.bm25_weight, knn_k=knn_k,
    )

    start = time.perf_counter()
    resp = get_client().search(index=s.opensearch_index, body=body)
    elapsed = (time.perf_counter() - start) * 1000

    hits = [_to_hit(h) for h in resp.get("hits", {}).get("hits", [])]
    bids = _aggregate_by_bid(hits, aggregate=s.aggregate, sum_top_n=s.sum_top_n)
    bids = _apply_thresholds(
        bids, min_score=s.min_score, min_score_ratio=s.min_score_ratio,
        min_chunks=s.min_chunks, min_results=s.min_results,
    )[:TOP_K]
    return bids, elapsed


def overlap(base: list[str], other: list[str]) -> float:
    if not base:
        return 1.0
    return len(set(base) & set(other)) / len(base)


def main() -> None:
    s = get_settings()
    print("=" * 78)
    print(f" 파라미터 비교 — 조합 {len(COMBOS)}개 x 질의 {len(QUERIES)}개 x {ROUNDS}라운드")
    print("=" * 78)
    print(f" 설정: 정규화={s.use_normalization} | 집계={s.aggregate}"
          f" | 하한선 {s.min_score}/{s.min_score_ratio}/{s.min_chunks}")
    print(" 편향 제거: 워밍업 + 순서 무작위화 + 반복 측정 + 조합 간 대기")

    print("\n 준비 중 (임베딩·필터 조회)...")
    vectors = {q: embed_query(q) for q in QUERIES}
    allowed = filters_to_bid_ids(None, only_open=True, latest_ord_only=True)
    print(f" 필터 ID {len(allowed) if allowed else 0:,}개")

    print(" 워밍업...", end="", flush=True)
    for q in QUERIES:
        search(q, vectors[q], allowed, 200, 20)
        print(".", end="", flush=True)
    print(" 완료")

    times: dict[str, list[float]] = {label: [] for label, *_ in COMBOS}
    last_bids: dict[tuple[str, str], list[str]] = {}

    total = len(COMBOS) * len(QUERIES) * ROUNDS
    done = 0

    for rnd in range(1, ROUNDS + 1):
        order = COMBOS[:]
        random.shuffle(order)
        print(f"\n [라운드 {rnd}/{ROUNDS}] 순서: "
              f"{' '.join(l.strip() for l, *_ in order)}")

        for label, fetch, mult in order:
            print(f"   {label} ", end="", flush=True)
            for q in QUERIES:
                bids, ms = search(q, vectors[q], allowed, fetch, mult)
                times[label].append(ms)
                last_bids[(label, q)] = [b.bid_id for b in bids]
                done += 1
                print(".", end="", flush=True)
            med = statistics.median(times[label][-len(QUERIES):])
            print(f" {med:>6.0f}ms  [{done}/{total}]")
            time.sleep(COOLDOWN_SEC)

    print("\n" + "=" * 78)
    print(f" {'조합':<14} {'knn_k':>7} {'중앙값':>9} {'최소':>8} {'최대':>8} {'기준대비':>9} {'결과수':>7}")
    print(" " + "-" * 76)

    base_med = statistics.median(times[BASELINE])
    for label, fetch, mult in COMBOS:
        ts = times[label]
        med = statistics.median(ts)
        ov = statistics.mean(
            overlap(last_bids[(BASELINE, q)], last_bids[(label, q)]) for q in QUERIES
        )
        cnt = statistics.mean(len(last_bids[(label, q)]) for q in QUERIES)
        speed = f"{base_med / med:.1f}x" if med and label != BASELINE else ""
        print(f" {label:<14} {min(fetch*mult, KNN_K_MAX):>7} {med:>7.0f}ms "
              f"{min(ts):>6.0f}ms {max(ts):>6.0f}ms {ov*100:>7.0f}% {cnt:>6.1f}  {speed}")

    print("\n" + "=" * 78)
    print(" 해석")
    print("=" * 78)
    print("  기준대비 = 결과 공고가 현재 설정(fetch200 x20)과 겹치는 비율")
    print("  최소~최대 편차가 크면 부하 영향 — 재실행해 재현되는지 확인 권장")
    print()

    best = None
    for label, fetch, mult in COMBOS[1:]:
        med = statistics.median(times[label])
        ov = statistics.mean(
            overlap(last_bids[(BASELINE, q)], last_bids[(label, q)]) for q in QUERIES
        )
        if ov >= 0.9 and med < base_med:
            if best is None or med < statistics.median(times[best]):
                best = label
    if best:
        med = statistics.median(times[best])
        print(f"  추천: {best.strip()} — {base_med:.0f}ms -> {med:.0f}ms "
              f"({base_med/med:.1f}배 빠름, 결과 90% 이상 동일)")
    else:
        print("  결과를 유지하면서 유의미하게 빨라지는 조합 없음.")
        print("  → 파라미터 조정의 한계. 색인 구조 변경(마감일 색인 등) 검토 필요")


if __name__ == "__main__":
    main()