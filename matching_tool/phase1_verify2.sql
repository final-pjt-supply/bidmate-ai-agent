-- ============================================================================
-- Phase 1 재검증 (v2) — 1차 검증의 전후 비교 무효를 복구
--   2026-07-29. 원인: ⓪(before 스냅샷)이 함수 교체 후에 실행됨 →
--   mr_before/mr_after 둘 다 신함수 결과라 모든 비교가 자기 대조였다.
--   (판별 근거: capacity 분모 before=84 — 84는 dedup '후' 값. 구함수면 137 근처.)
--
--   ★★ 전제: ⑧(캐시 재계산)을 아직 안 돌렸을 것. match_results 가 구함수 결과일 때만
--      블록 A 가 유효하다. 이미 재계산했다면 블록 A 는 건너뛰고 B~D 만.
--   전부 읽기 전용.
-- ============================================================================

-- ─────────────────────────────────────────────────────────────
-- A. 진짜 전이 매트릭스 — 캐시(구함수) vs 신함수, 교집합 한정
--    캐시는 마지막 재계산 시점의 라이브 집합이라 (마감 이탈)이 다수 나오는 게 정상.
--    보고 싶은 건 대각선 밖: 불가→보완가능(direct_prod 격하), 보완가능/확인필요→불가(item 격상).
-- ─────────────────────────────────────────────────────────────
WITH now_res AS (SELECT * FROM compute_match_results(9001))
SELECT COALESCE(c.verdict, '(캐시에 없음=신규)') AS 이전_캐시,
       COALESCE(n.verdict, '(마감 이탈)')        AS 이후_신함수,
       COUNT(*) AS 건수
  FROM match_results c
  FULL JOIN now_res n USING (bid_ntce_no, bid_ntce_ord)
 WHERE COALESCE(c.company_id, 9001) = 9001
 GROUP BY 1, 2
 ORDER BY 3 DESC;

-- A-2. 대각선 밖 이동의 표본 20건 — 축 단위 사유 확인용
WITH now_res AS (SELECT * FROM compute_match_results(9001))
SELECT c.verdict AS 이전, n.verdict AS 이후, n.bid_ntce_no, n.bid_ntce_ord,
       (SELECT STRING_AGG(ax->>'axis' || '=' || (ax->>'status'), ', ')
          FROM jsonb_array_elements(n.axes) ax
         WHERE ax->>'class' = 'gate' AND ax->>'status' <> '충족') AS 게이트_비충족
  FROM match_results c
  JOIN now_res n USING (bid_ntce_no, bid_ntce_ord)
 WHERE c.company_id = 9001 AND c.verdict <> n.verdict
 LIMIT 20;

-- ─────────────────────────────────────────────────────────────
-- B. 인력 dedup 키 사다리 — 함수와 무관하게 원천 테이블에서 직접 측정
--    1차 검증 실측: 현행 키(전 필드 동일)로 501 → 접힘 거의 없음. 어느 키가 245 를
--    만드는지 여기서 확정한 뒤 함수 키를 교체한다.
--      raw        = 라이브 인력 행 전부 (method='ignored' 제외)
--      k1_전필드  = 현행 Phase1 키 (qual_code, qual_name, role_field, grade_raw, headcount, method)
--      k2_평가키  = 판정에 실제 쓰이는 것만 (qual_code|원문라벨, headcount)
--      k3_코드만  = (qual_code, headcount) — 미해석은 원문으로 구분 유지
-- ─────────────────────────────────────────────────────────────
WITH live AS (
  SELECT bid_ntce_no, bid_ntce_ord FROM bid_table
   WHERE bid_clse_dt IS NULL OR bid_clse_dt > (now() AT TIME ZONE 'Asia/Seoul')
), pers AS (
  SELECT r.* FROM bid_require_personnel r JOIN live USING (bid_ntce_no, bid_ntce_ord)
   WHERE COALESCE(r.method,'') <> 'ignored'
)
SELECT
  (SELECT COUNT(*) FROM pers) AS raw,
  (SELECT COUNT(*) FROM (SELECT DISTINCT bid_ntce_no, bid_ntce_ord, qual_code, qual_name,
                                role_field, grade_raw, headcount, method FROM pers) t) AS k1_전필드,
  (SELECT COUNT(*) FROM (SELECT DISTINCT bid_ntce_no, bid_ntce_ord,
                                COALESCE(qual_code, 'RAW:' || lower(regexp_replace(
                                  COALESCE(NULLIF(grade_raw,''), qual_name, role_field, ''), '\s', '', 'g'))),
                                headcount FROM pers) t) AS k2_평가키,
  (SELECT COUNT(*) FROM (SELECT DISTINCT bid_ntce_no, bid_ntce_ord, qual_code, headcount
                           FROM pers WHERE qual_code IS NOT NULL) t)
  + (SELECT COUNT(*) FROM (SELECT DISTINCT bid_ntce_no, bid_ntce_ord,
                                  COALESCE(NULLIF(grade_raw,''), qual_name, role_field, ''), headcount
                             FROM pers WHERE qual_code IS NULL) t) AS k3_코드만;

-- B-2. k1 로 안 접히는 중복의 실물 표본 — 어떤 필드가 다른지 눈으로 확인
WITH live AS (
  SELECT bid_ntce_no, bid_ntce_ord FROM bid_table
   WHERE bid_clse_dt IS NULL OR bid_clse_dt > (now() AT TIME ZONE 'Asia/Seoul')
), pers AS (
  SELECT r.* FROM bid_require_personnel r JOIN live USING (bid_ntce_no, bid_ntce_ord)
   WHERE COALESCE(r.method,'') <> 'ignored'
), dup_bids AS (
  SELECT bid_ntce_no, bid_ntce_ord,
         COALESCE(qual_code, 'RAW:' || lower(regexp_replace(
           COALESCE(NULLIF(grade_raw,''), qual_name, role_field, ''), '\s', '', 'g'))) AS k2,
         headcount, COUNT(*) AS n
    FROM pers GROUP BY 1,2,3,4 HAVING COUNT(*) > 1
)
SELECT p.bid_ntce_no, p.bid_ntce_ord, p.qual_code, p.qual_name, p.role_field,
       p.grade_raw, p.headcount, p.method
  FROM pers p
  JOIN dup_bids d ON d.bid_ntce_no = p.bid_ntce_no AND d.bid_ntce_ord = p.bid_ntce_ord
                 AND d.headcount = p.headcount
 ORDER BY p.bid_ntce_no, p.bid_ntce_ord, p.headcount
 LIMIT 40;

-- ─────────────────────────────────────────────────────────────
-- C. D-07 단독행 검증 — min_value<=0 행이 '결정적'인 공고만 골라 확인
--    (다른 정상 행과 공존하면 BOOL_AND 가 NULL 을 건너뛰어 충족 유지 — 이건 기존 의미론)
--    기대: 전 행이 문제인 공고의 신함수 축 status = 확인필요 (구함수/캐시에선 충족이었을 것)
-- ─────────────────────────────────────────────────────────────
WITH live AS (
  SELECT bid_ntce_no, bid_ntce_ord FROM bid_table
   WHERE bid_clse_dt IS NULL OR bid_clse_dt > (now() AT TIME ZONE 'Asia/Seoul')
), all_bad AS (
  SELECT r.bid_ntce_no, r.bid_ntce_ord, 'performance' AS 축
    FROM bid_require_performances r JOIN live USING (bid_ntce_no, bid_ntce_ord)
   GROUP BY 1,2
  HAVING BOOL_AND(r.min_value IS NOT NULL AND r.min_value <= 0
                  AND r.unit IS NOT NULL AND r.parse_status <> 'unparsed')
  UNION ALL
  SELECT r.bid_ntce_no, r.bid_ntce_ord, 'capacity'
    FROM bid_require_capacity r JOIN live USING (bid_ntce_no, bid_ntce_ord)
   GROUP BY 1,2
  HAVING BOOL_AND(r.min_value IS NOT NULL AND r.min_value <= 0 AND r.parse_status <> 'unparsed')
)
-- (수정 2026-07-29-2: 쉼표 LATERAL 뒤 LEFT JOIN 이 앞 FROM 그룹(ab)을 참조 못 하는
--  42P01 → 쉼표 제거, 단일 조인 체인으로 재구성)
SELECT ab.축, ax->>'status' AS 신함수_status, c.verdict AS 캐시_verdict, COUNT(*) AS 건수
  FROM all_bad ab
  JOIN compute_match_results(9001) n
    ON n.bid_ntce_no = ab.bid_ntce_no AND n.bid_ntce_ord = ab.bid_ntce_ord
  CROSS JOIN LATERAL jsonb_array_elements(n.axes) AS ax
  LEFT JOIN match_results c ON c.company_id = 9001
    AND c.bid_ntce_no = ab.bid_ntce_no AND c.bid_ntce_ord = ab.bid_ntce_ord
 WHERE ax->>'axis' = ab.축
 GROUP BY 1, 2, 3
 ORDER BY 1, 4 DESC;

-- ─────────────────────────────────────────────────────────────
-- D. [D-18 후보] 축별 미해석 의미 불일치의 노출 규모
--    personnel·performance·capacity 는 BOOL_AND 가 NULL(미해석 행)을 건너뛰어,
--    미해석이 섞여 있어도 나머지가 통과하면 '충족'이 된다.
--    item·cert·direct_prod 는 미해석이 있으면 '확인필요'로 막는다.
--    → '충족인데 미해석 행 보유' 공고 수 = 의미론을 통일할 때 이동할 규모.
-- ─────────────────────────────────────────────────────────────
WITH live AS (
  SELECT bid_ntce_no, bid_ntce_ord FROM bid_table
   WHERE bid_clse_dt IS NULL OR bid_clse_dt > (now() AT TIME ZONE 'Asia/Seoul')
), unres AS (
  SELECT bid_ntce_no, bid_ntce_ord, 'performance' AS 축
    FROM bid_require_performances r JOIN live USING (bid_ntce_no, bid_ntce_ord)
   WHERE r.min_value IS NULL OR r.min_value <= 0 OR r.unit IS NULL OR r.parse_status = 'unparsed'
   GROUP BY 1,2
  UNION ALL
  SELECT bid_ntce_no, bid_ntce_ord, 'capacity'
    FROM bid_require_capacity r JOIN live USING (bid_ntce_no, bid_ntce_ord)
   WHERE r.min_value IS NULL OR r.min_value <= 0 OR r.parse_status = 'unparsed'
   GROUP BY 1,2
  UNION ALL
  SELECT bid_ntce_no, bid_ntce_ord, 'personnel'
    FROM bid_require_personnel r JOIN live USING (bid_ntce_no, bid_ntce_ord)
   WHERE COALESCE(r.method,'') <> 'ignored' AND r.qual_code IS NULL AND r.method = 'none'
   GROUP BY 1,2
)
SELECT u.축, ax->>'status' AS 현재_status, COUNT(*) AS 공고수
  FROM unres u
  JOIN compute_match_results(9001) n
    ON n.bid_ntce_no = u.bid_ntce_no AND n.bid_ntce_ord = u.bid_ntce_ord
  CROSS JOIN LATERAL jsonb_array_elements(n.axes) AS ax
 WHERE ax->>'axis' = u.축
 GROUP BY 1, 2
 ORDER BY 1, 3 DESC;
