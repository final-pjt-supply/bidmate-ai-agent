-- ============================================================================
-- d2122_verify.sql — v2.2(D-21 헐값 가드 + D-22 게이트0 가드) 배포 검증 (2026-07-30)
--   순서: compute_match_results.sql(v2.2) 실행 → ①~④ → 통과 시 ⑤ 캐시 재계산(선택,
--   안 돌리면 새벽 #80 자동). ※ 이번엔 전이가 대각선만이 아니다 — 의도된 이동:
--     · 보완가능 → 확인필요 (~21건, D-22)
--     · 가능/보완가능 → 확인필요 (capacity 헐값이 판정 margin이던 공고, D-21)
--   '불가' 유입은 0이어야 한다(두 가드 모두 확인필요 방향으로만 민다).
-- ============================================================================

-- ① 전이 매트릭스 (v2.1 캐시 vs v2.2 신함수)
SELECT o.verdict AS 이전, n.verdict AS 이후, COUNT(*) AS 건수
FROM match_results o
JOIN compute_match_results(9001) n
  ON n.bid_ntce_no = o.bid_ntce_no AND n.bid_ntce_ord = o.bid_ntce_ord
WHERE o.company_id = 9001
GROUP BY 1, 2 ORDER BY 3 DESC;

-- ② 불변식 A — 게이트0 보완가능 잔존 (기대 0, D-22 가동 증거)
SELECT COUNT(*) AS 게이트0_보완가능_잔존
FROM (
  SELECT n.bid_ntce_no, n.bid_ntce_ord, n.verdict,
         COUNT(*) FILTER (WHERE ax->>'class' = 'gate') AS n_gate
  FROM compute_match_results(9001) n,
       LATERAL jsonb_array_elements(n.axes) ax
  GROUP BY 1, 2, 3
) t
WHERE verdict = '보완가능' AND n_gate = 0;

-- ③ 불변식 B — 헐값 capacity 판정 잔존 (기대 0, D-21 가동 증거)
--   전 행이 헐값(0 < min_value < 1천만)인 공고의 capacity 축은 확인필요여야 한다.
SELECT COUNT(*) AS 헐값판정_잔존
FROM (
  SELECT r.bid_ntce_no, r.bid_ntce_ord,
         BOOL_AND(COALESCE(r.min_value, 0) > 0 AND r.min_value < 10000000) AS all_cheap
  FROM bid_require_capacity r
  GROUP BY 1, 2
) c
JOIN compute_match_results(9001) n
  ON n.bid_ntce_no = c.bid_ntce_no AND n.bid_ntce_ord = c.bid_ntce_ord,
     LATERAL jsonb_array_elements(n.axes) ax
WHERE c.all_cheap
  AND ax->>'axis' = 'capacity'
  AND ax->>'status' <> '확인필요';

-- ④ 분포 + 불가 유입 확인 — 이전 캐시 분포와 비교(불가 수는 그대로여야 함)
SELECT verdict, COUNT(*) FROM compute_match_results(9001) GROUP BY 1 ORDER BY 2 DESC;

-- ④b (참고) 다음 결정을 위한 측정 — 게이트0 인데 '가능'인 공고 수.
--   D-22는 미충족만 낮췄다. supp 전부 충족이면 여전히 '가능'인데, 같은 논리로
--   이것도 확인필요로 낮출지는 이 숫자를 보고 별도 결정한다(과도 보수화 경계).
SELECT COUNT(*) AS 게이트0_가능
FROM (
  SELECT n.bid_ntce_no, n.bid_ntce_ord, n.verdict,
         COUNT(*) FILTER (WHERE ax->>'class' = 'gate') AS n_gate
  FROM compute_match_results(9001) n,
       LATERAL jsonb_array_elements(n.axes) ax
  GROUP BY 1, 2, 3
) t
WHERE verdict = '가능' AND n_gate = 0;

-- ⑤ (선택) 즉시 캐시 재계산 — 안 돌리면 새벽 #80 자동
-- BEGIN;
-- DELETE FROM match_results WHERE company_id = 9001;
-- INSERT INTO match_results
--   (company_id, bid_ntce_no, bid_ntce_ord, verdict, required, satisfied,
--    gate_failed, need_review, axes, normalizer_version)
-- SELECT * FROM compute_match_results(9001);
-- COMMIT;

-- ⑥ live 테스트 (터미널): pytest -m live -s -q tests/test_eligibility_live.py
--   기대 20개 통과 — 유도() 개정 + D-22 고정 테스트 신규.
