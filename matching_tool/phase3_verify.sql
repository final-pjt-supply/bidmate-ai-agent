-- ============================================================================
-- Phase 3 배포 검증 — cert·credit 격상 + info 삭제 전후 비교
--   2026-07-29. 같은 커넥션에서 순서대로. 현재 캐시(1,367건)가 Phase1.1+v1.9 상태라
--   그대로 '진짜 before'다 — ⑥ 재계산 전에는 캐시를 건드리지 말 것.
--   ★ 배포 전 프론트 통보 선행(M4): axes 에서 class='info' 값 소멸.
-- ============================================================================

-- ⓪ 【배포 전】 현행(=캐시와 동일 로직) 분포 확인만
SELECT 'before' AS 시점, verdict, COUNT(*) FROM match_results
 WHERE company_id = 9001 GROUP BY 2 ORDER BY 3 DESC;

-- ★ 여기서 compute_match_results.sql(Phase3 판) 전체 실행 → 함수 교체 → ① 부터.

-- ─────────────────────────────────────────────────────────────
-- ① 전이 매트릭스 — 캐시(격상 전) vs 신함수(격상 후)
--    기대 이동: cert·credit 이 required 에 편입되며
--      · 가능 → 보완가능/확인필요 (인증 미보유·미해석 보유 공고)
--      · 확인필요(축0개·info만 있던 공고) → 판정 확정 쪽
--    gate 는 안 늘었으므로 '불가' 신규 유입은 없어야 정상(있다면 즉시 중단).
-- ─────────────────────────────────────────────────────────────
WITH now_res AS (SELECT * FROM compute_match_results(9001))
SELECT COALESCE(c.verdict, '(신규)') AS 이전, COALESCE(n.verdict, '(마감 이탈)') AS 이후,
       COUNT(*) AS 건수
  FROM match_results c
  FULL JOIN now_res n USING (bid_ntce_no, bid_ntce_ord)
 WHERE COALESCE(c.company_id, 9001) = 9001
 GROUP BY 1, 2 ORDER BY 3 DESC;

-- ─────────────────────────────────────────────────────────────
-- ② class 계약 검증 — info 가 세상에서 사라졌는가 (기대: gate/supp 만)
-- ─────────────────────────────────────────────────────────────
SELECT ax->>'class' AS class, ax->>'axis' AS 축, COUNT(*) AS 행수
  FROM compute_match_results(9001) m
  CROSS JOIN LATERAL jsonb_array_elements(m.axes) AS ax
 GROUP BY 1, 2 ORDER BY 1, 2;

-- ─────────────────────────────────────────────────────────────
-- ③ cert 격상 효과 — status 분포 + 확인필요 유입원 규모
-- ─────────────────────────────────────────────────────────────
SELECT ax->>'status' AS cert_status, COUNT(*) AS 공고수
  FROM compute_match_results(9001) m
  CROSS JOIN LATERAL jsonb_array_elements(m.axes) AS ax
 WHERE ax->>'axis' = 'cert'
 GROUP BY 1 ORDER BY 2 DESC;

-- ─────────────────────────────────────────────────────────────
-- ④ credit 격상 규모 — 기대: 라이브 소수(전체 min_grade 15건의 부분집합)
--    fp 위양성·등급 미상은 축 미생성이므로 여기 안 나와야 정상.
-- ─────────────────────────────────────────────────────────────
SELECT ax->>'status' AS credit_status, ax->>'detail' AS detail, COUNT(*) AS 공고수
  FROM compute_match_results(9001) m
  CROSS JOIN LATERAL jsonb_array_elements(m.axes) AS ax
 WHERE ax->>'axis' = 'credit'
 GROUP BY 1, 2 ORDER BY 3 DESC;

-- ─────────────────────────────────────────────────────────────
-- ⑤ 불변식 — 전부 0 이어야 정상
-- ─────────────────────────────────────────────────────────────
SELECT
  COUNT(*) FILTER (WHERE satisfied > required)   AS inv1,
  COUNT(*) FILTER (WHERE need_review > required) AS inv2,
  COUNT(*) FILTER (WHERE gate_failed > required) AS inv3,
  COUNT(*) FILTER (WHERE verdict <> CASE
      WHEN required = 0    THEN '확인필요'
      WHEN gate_failed > 0 THEN '불가'
      WHEN need_review > 0 THEN '확인필요'
      WHEN satisfied < required THEN '보완가능'
      ELSE '가능' END)                           AS inv4_verdict_재현,
  COUNT(*) FILTER (WHERE EXISTS (SELECT 1 FROM jsonb_array_elements(axes) ax
                                  WHERE ax->>'class' = 'info')) AS inv5_info_잔존
FROM compute_match_results(9001);

-- ─────────────────────────────────────────────────────────────
-- ⑥ 【①~⑤ 확인 후】 캐시 재계산 — 회원별 반복
-- ─────────────────────────────────────────────────────────────
-- DELETE FROM match_results WHERE company_id = 9001;
-- INSERT INTO match_results
--   (company_id, bid_ntce_no, bid_ntce_ord, verdict, required, satisfied,
--    gate_failed, need_review, axes, normalizer_version)
-- SELECT * FROM compute_match_results(9001);
-- SELECT count(*) FROM match_results WHERE company_id = 9001;
