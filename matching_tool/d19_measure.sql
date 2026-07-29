-- ============================================================================
-- D-19 1단계(인력 합산 강화) — 사전 측정 + 사후 검증 (2026-07-29, 2시간 스프린트)
--   순서: ⓪~② 실행(사전) → compute_match_results.sql(v1.5판) 배포 → ③~⑤(사후)
--   전부 읽기 전용. 캐시 재계산은 ⑤ 확인 후 별도.
-- ============================================================================

-- ─────────────────────────────────────────────────────────────
-- ⓪ 분야 중복 규모 — 같은 (공고, 자격코드)에 행이 2개 이상 (과대 충족 모집단)
-- ─────────────────────────────────────────────────────────────
WITH live AS (
  SELECT bid_ntce_no, bid_ntce_ord FROM bid_table
   WHERE bid_clse_dt IS NULL OR bid_clse_dt > (now() AT TIME ZONE 'Asia/Seoul')
), pool AS (
  SELECT r.bid_ntce_no, r.bid_ntce_ord, r.qual_code,
         COUNT(*) AS n_rows, SUM(r.headcount) AS tot_req, MAX(r.headcount) AS max_row
    FROM (SELECT DISTINCT r.bid_ntce_no, r.bid_ntce_ord, r.qual_code, r.qual_name,
                 r.role_field, r.grade_raw, r.headcount, r.method
            FROM bid_require_personnel r JOIN live USING (bid_ntce_no, bid_ntce_ord)
           WHERE COALESCE(r.method,'') <> 'ignored' AND r.qual_code IS NOT NULL) r
   GROUP BY 1,2,3
)
SELECT COUNT(*) FILTER (WHERE n_rows > 1)                       AS 분야분산_풀수,
       COUNT(DISTINCT bid_ntce_no || '|' || bid_ntce_ord)
         FILTER (WHERE n_rows > 1)                              AS 해당_공고수,
       SUM(n_rows) FILTER (WHERE n_rows > 1)                    AS 행수,
       SUM(tot_req) FILTER (WHERE n_rows > 1)                   AS 합산_요구인원,
       SUM(max_row) FILTER (WHERE n_rows > 1)                   AS 현행_실효요구인원
  FROM pool;
-- 읽는 법: 합산_요구인원 vs 현행_실효요구인원의 차이 = 지금 공짜로 통과되던 인원 규모.

-- ─────────────────────────────────────────────────────────────
-- ① 【시뮬레이션】 합산 강화 시 미충족으로 뒤집히는 풀 (9001 기준)
--    현재: 행마다 "보유 ≥ 행 요구"라 전부 충족 / 합산 후: "보유 < 합계"면 미충족.
--    함수의 평가식(등급형 rank 합산 / 그 외 코드 보유)을 그대로 재현한다.
-- ─────────────────────────────────────────────────────────────
WITH live AS (
  SELECT bid_ntce_no, bid_ntce_ord FROM bid_table
   WHERE bid_clse_dt IS NULL OR bid_clse_dt > (now() AT TIME ZONE 'Asia/Seoul')
), req AS (
  SELECT DISTINCT r.bid_ntce_no, r.bid_ntce_ord, r.qual_code, r.qual_name,
         r.role_field, r.grade_raw, r.headcount, r.method
    FROM bid_require_personnel r JOIN live USING (bid_ntce_no, bid_ntce_ord)
   WHERE COALESCE(r.method,'') <> 'ignored' AND r.qual_code IS NOT NULL
), pool AS (
  SELECT p.bid_ntce_no, p.bid_ntce_ord, p.qual_code,
         SUM(p.headcount) AS tot_req, MAX(p.headcount) AS max_row, COUNT(*) AS n_rows,
         CASE WHEN m.qual_type = 'grade' AND m.grade_rank IS NOT NULL THEN
           (SELECT COALESCE(SUM(cp.headcount),0) FROM company_personnel cp
              JOIN personnel_grade_master gm ON gm.qual_code = cp.qual_code
             WHERE cp.company_id = 9001 AND gm.qual_type='grade'
               AND gm.field = m.field AND gm.grade_rank >= m.grade_rank)
         ELSE
           COALESCE((SELECT cp.headcount FROM company_personnel cp
                      WHERE cp.company_id = 9001 AND cp.qual_code = p.qual_code),0)
         END AS 보유
    FROM req p LEFT JOIN personnel_grade_master m ON m.qual_code = p.qual_code
   GROUP BY 1,2,3, m.qual_type, m.grade_rank, m.field
)
SELECT COUNT(*) FILTER (WHERE 보유 >= max_row AND 보유 < tot_req) AS 뒤집히는_풀,
       COUNT(DISTINCT bid_ntce_no || '|' || bid_ntce_ord)
         FILTER (WHERE 보유 >= max_row AND 보유 < tot_req)         AS 뒤집히는_공고,
       COUNT(*) AS 전체_풀
  FROM pool;

-- ─────────────────────────────────────────────────────────────
-- ② 과대 충족 실증 표본 20건 — 배포 후 회귀 기준 (이 공고들이 움직여야 성공)
-- ─────────────────────────────────────────────────────────────
WITH live AS (
  SELECT bid_ntce_no, bid_ntce_ord FROM bid_table
   WHERE bid_clse_dt IS NULL OR bid_clse_dt > (now() AT TIME ZONE 'Asia/Seoul')
), req AS (
  SELECT DISTINCT r.bid_ntce_no, r.bid_ntce_ord, r.qual_code, r.qual_name,
         r.role_field, r.headcount, r.method
    FROM bid_require_personnel r JOIN live USING (bid_ntce_no, bid_ntce_ord)
   WHERE COALESCE(r.method,'') <> 'ignored' AND r.qual_code IS NOT NULL
), pool AS (
  SELECT p.bid_ntce_no, p.bid_ntce_ord, p.qual_code, MIN(p.qual_name) AS qual_name,
         SUM(p.headcount) AS tot_req, MAX(p.headcount) AS max_row, COUNT(*) AS n_rows,
         left(STRING_AGG(DISTINCT NULLIF(p.role_field,''), '·'), 60) AS 분야들,
         CASE WHEN m.qual_type = 'grade' AND m.grade_rank IS NOT NULL THEN
           (SELECT COALESCE(SUM(cp.headcount),0) FROM company_personnel cp
              JOIN personnel_grade_master gm ON gm.qual_code = cp.qual_code
             WHERE cp.company_id = 9001 AND gm.qual_type='grade'
               AND gm.field = m.field AND gm.grade_rank >= m.grade_rank)
         ELSE
           COALESCE((SELECT cp.headcount FROM company_personnel cp
                      WHERE cp.company_id = 9001 AND cp.qual_code = p.qual_code),0)
         END AS 보유
    FROM req p LEFT JOIN personnel_grade_master m ON m.qual_code = p.qual_code
   GROUP BY 1,2,3, m.qual_type, m.grade_rank, m.field
)
SELECT bid_ntce_no, bid_ntce_ord, qual_name, 분야들,
       n_rows AS 분야수, tot_req AS 요구합, 보유
  FROM pool
 WHERE 보유 >= max_row AND 보유 < tot_req
 ORDER BY n_rows DESC, tot_req DESC
 LIMIT 20;

-- ═══════ 여기서 compute_match_results.sql (v1.5판) 배포 ═══════

-- ─────────────────────────────────────────────────────────────
-- ③ 【사후】 전이 매트릭스 — 캐시(합산 전) vs 신함수
--    기대: 가능→보완가능 이동 = ①의 뒤집히는_공고 부분집합. 불가 신규 유입 0.
-- ─────────────────────────────────────────────────────────────
WITH now_res AS (SELECT * FROM compute_match_results(9001))
SELECT COALESCE(c.verdict, '(신규)') AS 이전, COALESCE(n.verdict, '(마감 이탈)') AS 이후,
       COUNT(*) AS 건수
  FROM match_results c
  FULL JOIN now_res n USING (bid_ntce_no, bid_ntce_ord)
 WHERE COALESCE(c.company_id, 9001) = 9001
 GROUP BY 1, 2 ORDER BY 3 DESC;

-- ─────────────────────────────────────────────────────────────
-- ④ 【사후】 ②의 표본이 실제로 움직였는지 — 인력 축 status 확인
--    (②에서 나온 공고번호 몇 개를 아래 IN 목록에 넣어 실행)
-- ─────────────────────────────────────────────────────────────
-- SELECT m.bid_ntce_no, ax->>'status' AS 인력_status, ax->>'detail' AS detail,
--        ax->>'required' AS 요구
--   FROM compute_match_results(9001) m
--   CROSS JOIN LATERAL jsonb_array_elements(m.axes) AS ax
--  WHERE ax->>'axis' = 'personnel'
--    AND m.bid_ntce_no IN ('여기에', '표본', '공고번호');

-- ─────────────────────────────────────────────────────────────
-- ⑤ 【사후】 불변식 + placeholder 문구 반영 확인
-- ─────────────────────────────────────────────────────────────
SELECT
  COUNT(*) FILTER (WHERE satisfied > required)   AS inv1,
  COUNT(*) FILTER (WHERE need_review > required) AS inv2,
  COUNT(*) FILTER (WHERE verdict <> CASE
      WHEN required = 0 THEN '확인필요' WHEN gate_failed > 0 THEN '불가'
      WHEN need_review > 0 THEN '확인필요'
      WHEN satisfied < required THEN '보완가능' ELSE '가능' END) AS inv4,
  COUNT(*) FILTER (WHERE axes::text LIKE '%품목 미상%')          AS 구문구_잔존   -- 0 기대
FROM compute_match_results(9001);
