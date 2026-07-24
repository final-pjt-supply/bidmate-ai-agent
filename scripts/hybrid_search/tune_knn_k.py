"""knn_k 상향 실험 — k를 키우면 오히려 빨라지는지 확인.

배경
  필터가 걸린 HNSW 검색에서는 k가 작을수록 느려지는 현상이 관측됐다
  (k=4000 → 6초, k=400 → 21초). 필터 통과분을 채우려고 그래프를 더
  헤매기 때문으로 추정된다. 그렇다면 k를 더 키우면 더 빨라질 수 있다.

  또한 필터 유무별 시간을 함께 재어 "필터 비용"을 정량화한다.
  이 값이 크면 마감일 색인(range 필터 전환)의 기대 효과를 가늠할 수 있다.

실행: python tune_knn_k.py
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
from agents.tools.search import _build_hybrid_query, _build_query, _to_hit

QUERIES = [
    "소프트웨어 관련된 공고",
    "청소 용역 계약 기간",
    "전기 공사 공고",
]

FETCH_SIZE = 200
KNN_K_MAX = 10000

# (라벨, knn_k, 필터 사용)
COMBOS = [
    ("k4000  필터O", 4000, True),    # 현재 설정
    ("k6000  필터O", 6000, True),
    ("k8000  필터O", 8000, True),
    ("k10000 필터O", 10000, True),
    ("k4000  필터X", 4000, False),   # 필터 비용 측정용
    ("k10000 필터X", 10000, False),
]

ROUNDS = 3
COOLDOWN_SEC = 0.5


def search_ms(query: str, vector: list[float], allowed: list[str] | None,
              knn_k: int) -> tuple[float, int]:
    """검색 소요 ms와 반환 청크 수."""
    s = get_settings()
    builder = _build_hybrid_query if s.use_normalization else _build_query
    body = builder(
        text=query, vector=vector, bid_ids=allowed, types=None,
        size=FETCH_SIZE, knn_weight=s.knn_weight,
        bm25_weight=s.bm25_weight, knn_k=knn_k,
    )
    start = time.perf_counter()
    resp = get_client().search(index=s.opensearch_index, body=body)
    elapsed = (time.perf_counter() - start) * 1000
    n = len(resp.get("hits", {}).get("hits", []))
    return elapsed, n


def main() -> None:
    print("=" * 74)
    print(f" knn_k 상향 실험 — fetch_size={FETCH_SIZE} 고정, {ROUNDS}라운드")
    print("=" * 74)

    print("\n 준비 중...")
    vectors = {q: embed_query(q) for q in QUERIES}
    allowed = filters_to_bid_ids(None, only_open=True, latest_ord_only=True)
    n_filter = len(allowed) if allowed else 0
    print(f" 필터 ID {n_filter:,}개")

    print(" 워밍업...", end="", flush=True)
    for q in QUERIES:
        search_ms(q, vectors[q], allowed, 4000)
        print(".", end="", flush=True)
    print(" 완료")

    times: dict[str, list[float]] = {label: [] for label, *_ in COMBOS}
    counts: dict[str, int] = {}
    total = len(COMBOS) * len(QUERIES) * ROUNDS
    done = 0

    for rnd in range(1, ROUNDS + 1):
        order = COMBOS[:]
        random.shuffle(order)
        print(f"\n [라운드 {rnd}/{ROUNDS}]")
        for label, k, use_filter in order:
            print(f"   {label} ", end="", flush=True)
            ids = allowed if use_filter else None
            for q in QUERIES:
                ms, n = search_ms(q, vectors[q], ids, k)
                times[label].append(ms)
                counts[label] = n
                done += 1
                print(".", end="", flush=True)
            med = statistics.median(times[label][-len(QUERIES):])
            print(f" {med:>6.0f}ms  [{done}/{total}]")
            time.sleep(COOLDOWN_SEC)

    print("\n" + "=" * 74)
    print(f" {'조합':<14} {'knn_k':>7} {'중앙값':>10} {'최소':>9} {'최대':>9} {'청크':>6}")
    print(" " + "-" * 72)
    base = statistics.median(times[COMBOS[0][0]])
    for label, k, use_filter in COMBOS:
        ts = times[label]
        med = statistics.median(ts)
        mark = f"  {base/med:.1f}x" if label != COMBOS[0][0] else ""
        print(f" {label:<14} {k:>7} {med:>8.0f}ms {min(ts):>7.0f}ms "
              f"{max(ts):>7.0f}ms {counts[label]:>6}{mark}")

    print("\n" + "=" * 74)
    print(" 해석")
    print("=" * 74)

    # 필터 유무 비교 (같은 k)
    f_on = statistics.median(times["k4000  필터O"])
    f_off = statistics.median(times["k4000  필터X"])
    print(f"  필터 비용 (k=4000): {f_on:.0f}ms vs {f_off:.0f}ms "
          f"→ 필터가 {f_on/f_off:.1f}배 느리게 만듦")
    print(f"  필터 ID {n_filter:,}개를 terms로 넘기는 비용이다.")
    print()

    # k 상향 효과
    ks = [(k, statistics.median(times[l])) for l, k, uf in COMBOS if uf]
    best_k, best_ms = min(ks, key=lambda x: x[1])
    if best_k != 4000 and best_ms < f_on * 0.9:
        print(f"  → k={best_k}가 가장 빠름 ({f_on:.0f}ms → {best_ms:.0f}ms). "
              f"KNN_CANDIDATE_MULTIPLIER 상향 검토")
    else:
        print(f"  → k 상향으로는 개선 없음 (현재 k=4000이 최적)")
        print(f"     필터 비용({f_on/f_off:.1f}배)이 지배적 → 마감일 색인으로")
        print(f"     terms 필터를 range 필터로 바꾸는 것이 근본 해결책")


if __name__ == "__main__":
    main()