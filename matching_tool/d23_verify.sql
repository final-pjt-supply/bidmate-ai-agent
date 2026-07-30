-- ============================================================================
-- d23_verify.sql — v2.3(유형별 기대 게이트 가드) 배포 검증 (2026-07-30)
--   순서: compute_match_results.sql(v2.3) 실행 → ①~③ → pytest 21종 → 캐시(새벽 자동).
--   기대 전이: 대각선 + 가능→확인필요 ~33(물품 11·용역 13·공사 9) + 보완가능→확인필요 소수.
--   안전선: 불가 유입 0 (가드는 낙관 경로만 캡).
-- ============================================================================

-- ① 전이 매트릭스 (v2.2 캐시 vs v2.3 신함수)
--   ※ 캐시가 아직 v2.2 재계산 전(어제 새벽분)이면 D-21·22 이동이 섞여 보인다 —
--     그 경우 보완가능→확인필요 21·가능→확인필요 3이 추가로 나타나는 게 정상.
SELECT o.verdict AS 이전, n.verdict AS 이후, COUNT(*) AS 건수
FROM match_results o
JOIN compute_match_results(9001) n
  ON n.bid_ntce_no = o.bid_ntce_no AND n.bid_ntce_ord = o.bid_ntce_ord
WHERE o.company_id = 9001
GROUP BY 1, 2 ORDER BY 3 DESC;

-- ② 불변식 — 기대 게이트 결측 + 낙관 판정 잔존 (기대 0)
SELECT COUNT(*) AS 결측낙관_잔존
FROM compute_match_results(9001) n
JOIN bid_table bt ON bt.bid_ntce_no = n.bid_ntce_no AND bt.bid_ntce_ord = n.bid_ntce_ord
LEFT JOIN LATERAL jsonb_array_elements(n.axes) ax ON TRUE
WHERE n.verdict IN ('가능', '보완가능')
GROUP BY n.bid_ntce_no, n.bid_ntce_ord, bt.bid_category
HAVING (bt.bid_category = 'thng'
        AND NOT COALESCE(BOOL_OR(ax->>'axis' = 'item'), false))
    OR (bt.bid_category IN ('servc', 'cnstwk')
        AND NOT COALESCE(BOOL_OR(ax->>'axis' = 'license'), false))
LIMIT 5;
-- ↑ 행이 하나도 안 나와야 통과 (나오면 그 공고가 위반 사례)

-- ③ 분포 — 기대: 가능 ~128 / 확인필요 ~274 / 불가 899(불변) / 보완가능 ≤4
SELECT verdict, COUNT(*) FROM compute_match_results(9001) GROUP BY 1 ORDER BY 2 DESC;

-- ④ 표본 — 발단 사례류가 실제로 내려갔는지 (물품 · item 없음 · 이전 가능)
SELECT n.bid_ntce_no, bt.bid_ntce_nm AS 공고명, n.verdict
FROM compute_match_results(9001) n
JOIN bid_table bt ON bt.bid_ntce_no = n.bid_ntce_no AND bt.bid_ntce_ord = n.bid_ntce_ord
WHERE n.bid_ntce_no IN ('R26BK01638447','R26BK01638450','R26BK01639577','R26BK01644219');
-- ↑ 코팅기·캡슐충진기·로봇·크레인 — 전부 '확인필요' 여야 함

-- ⑤ pytest (터미널): pytest -m live -s -q tests/test_eligibility_live.py
--   기대 21개 통과 — 유도() D-23 캡 반영 + 신규 핀 테스트.
