-- ============================================================================
-- Phase 1 배포 검증 — compute_match_results 재배포 전후 비교
--   2026-07-29. 같은 DBeaver 커넥션(같은 세션)에서 순서대로 실행할 것 — pg_temp 공유.
--   회사 9001 기준. 빈 프로필 검증(⑥)만 별도 회사 id 를 넣어 실행.
--
--   순서:  ⓪ 실행 → compute_match_results.sql 전체 실행(재배포) → ①~⑦ 실행
--   전부 읽기 전용( pg_temp 제외 ). 캐시 재계산(⑧)은 결과 확인 후 마지막에.
-- ============================================================================

-- ─────────────────────────────────────────────────────────────
-- ⓪ 【배포 전】 현행 함수 결과 스냅샷
-- ─────────────────────────────────────────────────────────────
DROP TABLE IF EXISTS pg_temp.mr_before;
CREATE TEMP TABLE mr_before AS SELECT * FROM compute_match_results(9001);

SELECT 'before' AS 시점, verdict, COUNT(*) AS 건수
  FROM pg_temp.mr_before GROUP BY 2 ORDER BY 3 DESC;

-- ★ 여기서 compute_match_results.sql 파일 전체를 실행해 함수를 교체한 뒤 ①로.

-- ─────────────────────────────────────────────────────────────
-- ① 【배포 후】 신 함수 스냅샷 + verdict 분포
-- ─────────────────────────────────────────────────────────────
DROP TABLE IF EXISTS pg_temp.mr_after;
CREATE TEMP TABLE mr_after AS SELECT * FROM compute_match_results(9001);

SELECT 'after' AS 시점, verdict, COUNT(*) AS 건수
  FROM pg_temp.mr_after GROUP BY 2 ORDER BY 3 DESC;

-- ─────────────────────────────────────────────────────────────
-- ② verdict 전이 매트릭스 — 어떤 판정이 어디로 이동했는지 한눈에.
--    예상 이동: item 게이트화로 일부 보완가능/확인필요 → 불가,
--              direct_prod supp 화로 일부 불가 → 보완가능/확인필요,
--              D-07 로 일부 가능 → 확인필요. 라이브 유입/이탈은 (신규)/(마감) 행.
-- ─────────────────────────────────────────────────────────────
SELECT COALESCE(b.verdict, '(신규 라이브)') AS 이전,
       COALESCE(a.verdict, '(마감 이탈)')  AS 이후,
       COUNT(*) AS 건수
  FROM pg_temp.mr_before b
  FULL JOIN pg_temp.mr_after a USING (bid_ntce_no, bid_ntce_ord)
 GROUP BY 1, 2
 ORDER BY 3 DESC;

-- ─────────────────────────────────────────────────────────────
-- ③ 축 → class 매핑 검증 (계약 확인)
--    기대: item=gate / direct_prod=supp / cert·credit=info / 나머지 종전 유지.
--    두 class 가 섞여 나오는 축이 있으면 즉시 실패로 간주.
-- ─────────────────────────────────────────────────────────────
SELECT ax->>'axis' AS 축, ax->>'class' AS class, COUNT(*) AS 행수
  FROM pg_temp.mr_after m, LATERAL jsonb_array_elements(m.axes) AS ax
 GROUP BY 1, 2
 ORDER BY 1, 2;

-- ─────────────────────────────────────────────────────────────
-- ④ D-06 dedup 효과 — 인력·시공능력 분모 합 전후 비교
--    detail 'NN 요건 x/y' 의 y 합. 기대: after 가 유의미하게 작다
--    (진단 시점 실측: 인력 503→245, 시공능력 137→84. 라이브 변동으로 수치는 다를 수 있음).
-- ─────────────────────────────────────────────────────────────
SELECT s.시점, s.축, SUM(s.분모) AS 분모합, COUNT(*) AS 공고수
  FROM (
    SELECT 'before' AS 시점, ax->>'axis' AS 축,
           NULLIF(regexp_replace(split_part(ax->>'detail', '/', 2), '\D', '', 'g'), '')::int AS 분모
      FROM pg_temp.mr_before m, LATERAL jsonb_array_elements(m.axes) AS ax
     WHERE ax->>'axis' IN ('personnel', 'capacity')
    UNION ALL
    SELECT 'after', ax->>'axis',
           NULLIF(regexp_replace(split_part(ax->>'detail', '/', 2), '\D', '', 'g'), '')::int
      FROM pg_temp.mr_after m, LATERAL jsonb_array_elements(m.axes) AS ax
     WHERE ax->>'axis' IN ('personnel', 'capacity')
  ) s
 GROUP BY 1, 2
 ORDER BY 2, 1;

-- ─────────────────────────────────────────────────────────────
-- ⑤ D-07 가드 효과 — min_value <= 0 보유 라이브 공고의 축 status 이동
--    기대: before 에서 '충족'(위양성)이던 것이 after 에서 '확인필요' 포함으로.
-- ─────────────────────────────────────────────────────────────
WITH bad AS (
  SELECT DISTINCT r.bid_ntce_no, r.bid_ntce_ord, 'performance' AS 축
    FROM bid_require_performances r
    JOIN bid_table bt USING (bid_ntce_no, bid_ntce_ord)
   WHERE r.min_value <= 0
     AND (bt.bid_clse_dt IS NULL OR bt.bid_clse_dt > (now() AT TIME ZONE 'Asia/Seoul'))
  UNION ALL
  SELECT DISTINCT r.bid_ntce_no, r.bid_ntce_ord, 'capacity'
    FROM bid_require_capacity r
    JOIN bid_table bt USING (bid_ntce_no, bid_ntce_ord)
   WHERE r.min_value <= 0
     AND (bt.bid_clse_dt IS NULL OR bt.bid_clse_dt > (now() AT TIME ZONE 'Asia/Seoul'))
)
SELECT bad.축,
       bax.ax->>'status' AS 이전_status,
       aax.ax->>'status' AS 이후_status,
       COUNT(*) AS 건수
  FROM bad
  LEFT JOIN LATERAL (
    SELECT ax FROM pg_temp.mr_before m, LATERAL jsonb_array_elements(m.axes) AS ax
     WHERE m.bid_ntce_no = bad.bid_ntce_no AND m.bid_ntce_ord = bad.bid_ntce_ord
       AND ax->>'axis' = bad.축
  ) bax ON TRUE
  LEFT JOIN LATERAL (
    SELECT ax FROM pg_temp.mr_after m, LATERAL jsonb_array_elements(m.axes) AS ax
     WHERE m.bid_ntce_no = bad.bid_ntce_no AND m.bid_ntce_ord = bad.bid_ntce_ord
       AND ax->>'axis' = bad.축
  ) aax ON TRUE
 GROUP BY 1, 2, 3
 ORDER BY 1, 4 DESC;

-- ─────────────────────────────────────────────────────────────
-- ⑥ 정책 전가 검증 — 빈 프로필 회사로 실행
--    ★ 9001 은 프로필이 차 있어 이 경로가 안 보인다. 빈 프로필 테스트 회사 id 로 교체.
--    기대: region '본점 소재지 미등록'=미충족, size '(미등록)'=미충족,
--          direct_prod '회사 품목 미등록'=미충족(단 supp 라 불가 사유는 아님).
-- ─────────────────────────────────────────────────────────────
-- SELECT verdict, COUNT(*) FROM compute_match_results(/*빈프로필_id*/) GROUP BY 1 ORDER BY 2 DESC;
-- SELECT ax->>'axis' AS 축, ax->>'status' AS status, ax->>'detail' AS detail, COUNT(*)
--   FROM compute_match_results(/*빈프로필_id*/) m, LATERAL jsonb_array_elements(m.axes) AS ax
--  WHERE ax->>'class' IN ('gate','supp')
--  GROUP BY 1,2,3 ORDER BY 1, 4 DESC;

-- ─────────────────────────────────────────────────────────────
-- ⑦ 불변식 — 전부 0 이어야 정상
-- ─────────────────────────────────────────────────────────────
SELECT
  COUNT(*) FILTER (WHERE satisfied > required)                              AS inv1_satisfied_초과,
  COUNT(*) FILTER (WHERE need_review > required)                            AS inv2_review_초과,
  COUNT(*) FILTER (WHERE gate_failed > required)                            AS inv3_gate_초과,
  COUNT(*) FILTER (WHERE verdict <> CASE
      WHEN required = 0    THEN '확인필요'
      WHEN gate_failed > 0 THEN '불가'
      WHEN need_review > 0 THEN '확인필요'
      WHEN satisfied < required THEN '보완가능'
      ELSE '가능' END)                                                      AS inv4_verdict_재현불일치,
  COUNT(*) FILTER (WHERE required = 0 AND jsonb_array_length(axes) > 0
                     AND EXISTS (SELECT 1 FROM jsonb_array_elements(axes) ax
                                  WHERE ax->>'class' IN ('gate','supp')))   AS inv5_required0_모순
FROM pg_temp.mr_after;

-- ─────────────────────────────────────────────────────────────
-- ⑧ 【결과 확인 후】 캐시 재계산 — 회원별 반복 (9001 예시)
--    ⚠ D-13(마감 공고 잔류)도 이 DELETE 가 함께 청소한다.
-- ─────────────────────────────────────────────────────────────
-- DELETE FROM match_results WHERE company_id = 9001;
-- INSERT INTO match_results
--   (company_id, bid_ntce_no, bid_ntce_ord, verdict, required, satisfied,
--    gate_failed, need_review, axes, normalizer_version)
-- SELECT * FROM compute_match_results(9001);
