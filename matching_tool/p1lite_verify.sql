-- ============================================================
-- P1-lite 실 DB 검증 쿼리 세트 (matching_tool/p1lite_verify.sql)
-- 대상: 회사 9001 · match_results 캐시 · 2026-07-30 우선순위 기준
-- 실행: DBeaver 등에서 블록별로. 데이터가 매일 변하니 수치는 비율로 볼 것.
-- ============================================================

-- 공통 후보 CTE: '가능' + 라이브 + 축 카운트 (도구 SQL과 동일 조건)
-- ① Before/After top5 비교 — 한 화면에서 나란히
WITH cand AS (
    SELECT bt.bid_id, bt.bid_ntce_nm, bt.bid_clse_dt, bt.presmpt_prce,
           bt.sucsfbid_mthd_nm,
           (SELECT count(*) FROM jsonb_array_elements(COALESCE(m.axes,'[]'::jsonb)) a
             WHERE a->>'class'='gate') AS gate_cnt,
           (SELECT count(*) FROM jsonb_array_elements(COALESCE(m.axes,'[]'::jsonb)) a
             WHERE a->>'class'='supp' AND a->>'status'='충족') AS supp_met_cnt,
           (bt.sucsfbid_mthd_nm LIKE '%다수공급자%') AS is_mas
    FROM match_results m
    JOIN bid_table bt USING (bid_ntce_no, bid_ntce_ord)
    WHERE m.company_id = 9001
      AND m.verdict = '가능'
      AND (bt.bid_clse_dt IS NULL OR bt.bid_clse_dt > (NOW() AT TIME ZONE 'Asia/Seoul'))
)
SELECT 'OLD(마감임박순)' AS 기준, rank, bid_id, left(bid_ntce_nm, 30) AS 공고명,
       gate_cnt, supp_met_cnt, presmpt_prce, bid_clse_dt
FROM (SELECT c.*, row_number() OVER (ORDER BY bid_clse_dt NULLS LAST) AS rank
      FROM cand c) o WHERE rank <= 5
UNION ALL
SELECT 'NEW(근거·예산순)', rank, bid_id, left(bid_ntce_nm, 30),
       gate_cnt, supp_met_cnt, presmpt_prce, bid_clse_dt
FROM (SELECT c.*, row_number() OVER (
        ORDER BY gate_cnt DESC, supp_met_cnt DESC,
                 presmpt_prce DESC NULLS LAST, bid_id) AS rank
      FROM cand c WHERE NOT COALESCE(is_mas, false)) n WHERE rank <= 5
ORDER BY 기준, rank;

-- 확인 포인트:
--   NEW 5건의 (gate_cnt, supp_met_cnt, presmpt_prce)가 내림차순인가
--   NEW 5건 중 마감이 1~2일 내인 공고가 있는가 (있어도 기준상 정상 — 규모만 파악)
--   OLD와 NEW의 겹침이 몇 건인가 (변경 체감 크기)

-- ------------------------------------------------------------
-- ② MAS 제외 규모 — 총계가 얼마나 줄었나 (A 공지·백엔드 카운트 차이 설명용)
SELECT count(*)                                            AS 가능_라이브_전체,
       count(*) FILTER (WHERE bt.sucsfbid_mthd_nm LIKE '%다수공급자%') AS mas_건수,
       count(*) FILTER (WHERE bt.sucsfbid_mthd_nm IS NULL
                           OR bt.sucsfbid_mthd_nm NOT LIKE '%다수공급자%') AS 챗봇_총계_new
FROM match_results m
JOIN bid_table bt USING (bid_ntce_no, bid_ntce_ord)
WHERE m.company_id = 9001 AND m.verdict = '가능'
  AND (bt.bid_clse_dt IS NULL OR bt.bid_clse_dt > (NOW() AT TIME ZONE 'Asia/Seoul'));

-- ------------------------------------------------------------
-- ③ 축 카운트 분포 — 기준 ①②의 변별력 확인
--    (전부 같은 값에 몰려 있으면 사실상 예산순 정렬이라는 뜻 — 알아둘 것)
WITH cand AS (
    SELECT (SELECT count(*) FROM jsonb_array_elements(COALESCE(m.axes,'[]'::jsonb)) a
             WHERE a->>'class'='gate') AS gate_cnt,
           (SELECT count(*) FROM jsonb_array_elements(COALESCE(m.axes,'[]'::jsonb)) a
             WHERE a->>'class'='supp' AND a->>'status'='충족') AS supp_met_cnt
    FROM match_results m
    JOIN bid_table bt USING (bid_ntce_no, bid_ntce_ord)
    WHERE m.company_id = 9001 AND m.verdict = '가능'
      AND (bt.bid_clse_dt IS NULL OR bt.bid_clse_dt > (NOW() AT TIME ZONE 'Asia/Seoul'))
)
SELECT gate_cnt, supp_met_cnt, count(*) AS 건수
FROM cand GROUP BY 1, 2 ORDER BY 1 DESC, 2 DESC;

-- ------------------------------------------------------------
-- ④ '가능' = 전 축 충족 가정의 실측 재확인 (설계 전제 검증)
--    0건이어야 한다 — 있으면 verdict CASE와 axes가 어긋난 것 (즉시 공유)
SELECT count(*) AS 가정_위반_건수
FROM match_results m
WHERE m.company_id = 9001 AND m.verdict = '가능'
  AND EXISTS (SELECT 1 FROM jsonb_array_elements(COALESCE(m.axes,'[]'::jsonb)) a
              WHERE a->>'class' IN ('gate','supp') AND a->>'status' <> '충족');

-- ------------------------------------------------------------
-- ⑤ 도구 경로와 동일 쿼리의 실행 계획 (선택 — 지연 확인, ~165행이라 수 십 ms 기대)
-- EXPLAIN (ANALYZE, BUFFERS)  ← ①의 cand CTE에 붙여 실행. count(*) 함정 주의:
--   반드시 SELECT * 형태로 잴 것 (출력 미참조 시 조인 제거로 가짜 수치).
