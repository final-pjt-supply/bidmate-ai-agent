"""절대 점수 하한선 튜닝.

관련 있는 질의와 관련 없는 질의를 나란히 놓고,
절대 하한선을 바꿔가며 무엇이 잘리는지 본다.

목표: 무관 질의는 0건으로 만들되, 관련 질의는 온전히 남기는 값 찾기.

실행: python tune_threshold.py
"""
import sys
from pathlib import Path

# 프로젝트 루트를 import 경로에 추가 — 하위 폴더에서 실행해도 agents를 찾는다
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agents.tools.search import recommend_bids

# 검색으로 답할 수 있는 질의 (결과가 남아야 함)
GOOD = [
    "청소 용역 계약 기간",
    "SW 관련된 사업 공고",
    "정보시스템 구축 사업",
    "전기 공사 공고",
    "건설 공사 실적 기준",
]

# 검색으로 답할 수 없는 질의 (결과가 없어야 바람직)
BAD = [
    "오늘 마감인 공고",
    "가장 최근에 올라온 공고",
    "제일 비싼 공고 알려줘",
    "안녕하세요",
]

THRESHOLDS = [0.0, 0.9, 1.0, 1.1, 1.2, 1.5]
TOP_K = 5


def scores(query: str) -> list[float]:
    """하한선 없이 검색해 점수만 뽑는다."""
    recs = recommend_bids(query, top_k=TOP_K, min_score_ratio=0, min_chunks=1)
    return [r.hit.score for r in recs]


def main() -> None:
    print("점수 수집 중...\n")
    good_scores = {q: scores(q) for q in GOOD}
    bad_scores = {q: scores(q) for q in BAD}

    print("=" * 76)
    print("질의별 점수 분포")
    print("=" * 76)
    print("\n[검색 가능 질의]")
    for q, s in good_scores.items():
        top = f"{s[0]:.3f}" if s else "-"
        rest = " ".join(f"{v:.2f}" for v in s[1:])
        print(f"  {q[:28]:30s} 1위 {top} | {rest}")

    print("\n[검색 불가 질의]")
    for q, s in bad_scores.items():
        top = f"{s[0]:.3f}" if s else "-"
        rest = " ".join(f"{v:.2f}" for v in s[1:])
        print(f"  {q[:28]:30s} 1위 {top} | {rest}")

    print("\n" + "=" * 76)
    print("절대 하한선별 통과 건수")
    print("=" * 76)
    header = f"  {'임계값':<8}"
    for q in list(GOOD) + list(BAD):
        header += f"{q[:8]:>10}"
    print(header)
    print("  " + "-" * 74)

    for t in THRESHOLDS:
        row = f"  {t:<8.1f}"
        for q in GOOD:
            n = sum(1 for v in good_scores[q] if v >= t)
            row += f"{n:>10}"
        for q in BAD:
            n = sum(1 for v in bad_scores[q] if v >= t)
            row += f"{n:>10}"
        print(row)

    print("\n  왼쪽 5개(검색 가능)는 많이 남고, 오른쪽 4개(검색 불가)는 0이 되는 값이 이상적")

    # 자동 추천
    print("\n" + "=" * 76)
    best = None
    for t in THRESHOLDS:
        if t == 0:
            continue
        bad_total = sum(sum(1 for v in bad_scores[q] if v >= t) for q in BAD)
        good_total = sum(sum(1 for v in good_scores[q] if v >= t) for q in GOOD)
        good_max = sum(len(good_scores[q]) for q in GOOD)
        print(f"  임계값 {t:.1f}: 검색가능 {good_total}/{good_max} 유지, "
              f"검색불가 {bad_total}건 통과")
        if bad_total == 0 and best is None:
            best = t
    if best:
        print(f"\n  → 무관 질의를 완전히 차단하는 최소 임계값: {best}")
    else:
        print("\n  → 시험한 범위에서 무관 질의를 완전히 막는 값 없음")


if __name__ == "__main__":
    main()