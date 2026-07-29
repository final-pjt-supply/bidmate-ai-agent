-- ============================================================================
-- G-01 모순 해소 — "버려진 인증" vs "코드상 미해석은 적재됨"의 판별
--   2026-07-29. 읽기 전용.
--
-- 모순: ②-2 는 축0개 공고의 required_certs 에서 CSAP·ISMS-P 등을 발견했다(= 적재 안 됨).
--       그러나 route_cert 코드는 라우팅 4갈래(직생/규모/신용/IGNORE)에 안 걸린 문자열을
--       cert_code=NULL, method='none' 행으로 반드시 적재한다 — 드롭 경로가 없다.
--
-- 판별 논리:
--   설명(a) 표본 오염   → 모순 건이 전부 '미정규화'(summary 없음)로 분류된다.
--   설명(b) 배포본 상이 → '정규화됨(v1.8)'인데 적재대상 문자열 보유 & certs 행 0 이 남는다.
--                          이 경우 람다 배포본과 레포 HEAD 의 대조가 필요(운영 확인).
--   설명(c) 예외 드롭   → (b)와 같은 관측 + 특정 시간대/공고에 몰림. ④ 표본으로 정황 확인.
--
-- 키워드는 normalize_output_adapter.py 상수와 동일하게 복제(순서도 동일: 직생→규모→신용→IGNORE):
--   DIRECT: 직접생산|직생          SIZE: 중소기업확인|중소기업제품|소기업확인|소상공인
--   CREDIT: 신용평가등급|신용등급   IGNORE: 여성기업|장애인기업|사회적기업|공장등록|사업자등록
--                                          |납세증명|국세완납|지방세완납
-- ============================================================================

-- ─────────────────────────────────────────────────────────────
-- ⓪ 인증 문자열 전개 + 라우팅 분류 (이후 블록의 공통 재료)
--    cert_bucket: route_* / ignore = 의도된 드롭. load = 적재됐어야 하는 문자열.
-- ─────────────────────────────────────────────────────────────
DROP TABLE IF EXISTS pg_temp.g01;
CREATE TEMP TABLE g01 AS
SELECT b.bid_ntce_no, b.bid_ntce_ord,
       c.value #>> '{}' AS cert_raw,        -- 원소가 문자열이든 {"name":..} 이든 원문 추출
       COALESCE(c.value ->> 'name', c.value #>> '{}') AS cert_name,
       CASE
         WHEN COALESCE(c.value ->> 'name', c.value #>> '{}') ~ '직접생산|직생' THEN 'route_direct'
         WHEN COALESCE(c.value ->> 'name', c.value #>> '{}') ~ '중소기업확인|중소기업제품|소기업확인|소상공인' THEN 'route_size'
         WHEN COALESCE(c.value ->> 'name', c.value #>> '{}') ~ '신용평가등급|신용등급' THEN 'route_credit'
         WHEN COALESCE(c.value ->> 'name', c.value #>> '{}') ~ '여성기업|장애인기업|사회적기업|공장등록|사업자등록|납세증명|국세완납|지방세완납' THEN 'ignore'
         ELSE 'load' END AS cert_bucket,
       s.bid_ntce_no IS NOT NULL   AS has_summary,
       s.normalizer_version, s.normalized_at, b.merged_at, b.qual_status,
       EXISTS (SELECT 1 FROM bid_require_certs x
                WHERE x.bid_ntce_no = b.bid_ntce_no AND x.bid_ntce_ord = b.bid_ntce_ord) AS has_cert_rows
  FROM bid_table b
  LEFT JOIN bid_require_summary s
         ON s.bid_ntce_no = b.bid_ntce_no AND s.bid_ntce_ord = b.bid_ntce_ord
  CROSS JOIN LATERAL jsonb_array_elements(b.required_certs) AS c
 WHERE jsonb_typeof(b.required_certs) = 'array'
   AND jsonb_array_length(b.required_certs) > 0;

SELECT COUNT(*) AS 인증문자열_전체, COUNT(DISTINCT bid_ntce_no || '|' || bid_ntce_ord) AS 공고수
  FROM pg_temp.g01;

-- ─────────────────────────────────────────────────────────────
-- ① 【판별 핵심】 '적재됐어야 하는(load)' 문자열 보유 공고 × 정규화 상태 × certs 행 유무
--    모순 = 3행째(정규화됨 & certs 행 없음). 이 값이 0 이면 설명(a) 확정.
-- ─────────────────────────────────────────────────────────────
SELECT CASE WHEN NOT has_summary THEN '1_미정규화(모순 아님: 처리 전)'
            WHEN has_cert_rows   THEN '2_정규화됨·certs행 있음(정상)'
            ELSE '3_정규화됨·certs행 없음(★모순)' END AS 분류,
       COUNT(DISTINCT bid_ntce_no || '|' || bid_ntce_ord) AS 공고수,
       COUNT(*) AS load_문자열수,
       MIN(normalized_at) AS 정규화_최소, MAX(normalized_at) AS 정규화_최대
  FROM pg_temp.g01
 WHERE cert_bucket = 'load'
 GROUP BY 1
 ORDER BY 1;

-- ─────────────────────────────────────────────────────────────
-- ② 모순 건의 문자열 top 30 — ②-2 에서 봤던 CSAP·ISMS-P 가 여기 다시 나오면
--    "버려진 인증"의 정체가 (b)/(c)로 확정된다. 미정규화로 빠지면 (a).
-- ─────────────────────────────────────────────────────────────
SELECT cert_name AS 모순_문자열, COUNT(*) AS 건수
  FROM pg_temp.g01
 WHERE cert_bucket = 'load' AND has_summary AND NOT has_cert_rows
 GROUP BY 1
 ORDER BY 2 DESC, 1
 LIMIT 30;

-- ─────────────────────────────────────────────────────────────
-- ③ 반대 방향 물증 — 미해석 적재 경로(method='none')가 실제로 작동한 흔적.
--    행이 다수 존재하면 "적재 코드는 살아있다" → 모순 건은 특정 조건에서만 유실.
--    0 에 가깝다면 배포본이 미해석을 아예 적재하지 않는다는 강한 신호(설명 b).
-- ─────────────────────────────────────────────────────────────
SELECT method, COUNT(*) AS 행수,
       COUNT(*) FILTER (WHERE cert_code IS NULL) AS code_null
  FROM bid_require_certs
 GROUP BY 1 ORDER BY 2 DESC;

-- ③-2 method='none' 이 존재한다면 그 원문 top 20 (적재된 미해석의 실물)
SELECT name_raw, COUNT(*) AS 건수
  FROM bid_require_certs
 WHERE cert_code IS NULL
 GROUP BY 1 ORDER BY 2 DESC LIMIT 20;

-- ─────────────────────────────────────────────────────────────
-- ④ 모순 표본 10건 — 시각·상태 동반 덤프 (설명 c 의 정황 확인용)
-- ─────────────────────────────────────────────────────────────
SELECT DISTINCT ON (bid_ntce_no, bid_ntce_ord)
       bid_ntce_no, bid_ntce_ord, qual_status,
       merged_at, normalized_at, normalizer_version,
       cert_name AS load_문자열_예시
  FROM pg_temp.g01
 WHERE cert_bucket = 'load' AND has_summary AND NOT has_cert_rows
 ORDER BY bid_ntce_no, bid_ntce_ord, cert_name
 LIMIT 10;

-- ─────────────────────────────────────────────────────────────
-- ⑤ 【Phase 2 #7 입력】 라우팅 버킷별 전수 분포 + 'load' 문자열 빈도 상위 60
--    cert_master 적재 후보 목록의 원천. 빈도 컷(≥2) 위쪽이 1차 적재 대상.
-- ─────────────────────────────────────────────────────────────
SELECT cert_bucket AS 버킷, COUNT(*) AS 문자열수,
       COUNT(DISTINCT cert_name) AS 고유문자열수,
       COUNT(DISTINCT bid_ntce_no || '|' || bid_ntce_ord) AS 공고수
  FROM pg_temp.g01
 GROUP BY 1 ORDER BY 2 DESC;

SELECT cert_name AS 적재후보_문자열, COUNT(*) AS 빈도
  FROM pg_temp.g01
 WHERE cert_bucket = 'load'
 GROUP BY 1
HAVING COUNT(*) >= 2
 ORDER BY 2 DESC, 1
 LIMIT 60;

-- ⑤-2 커버리지 커브 — 빈도 상위 N 개 적재 시 문자열 커버율
WITH freq AS (
  SELECT cert_name, COUNT(*) AS n FROM pg_temp.g01 WHERE cert_bucket = 'load' GROUP BY 1
), ranked AS (
  SELECT n, ROW_NUMBER() OVER (ORDER BY n DESC) AS rk,
         SUM(n) OVER (ORDER BY n DESC, cert_name) AS cum,
         SUM(n) OVER () AS total
  FROM freq
)
SELECT rk AS 상위N, ROUND(100.0 * cum / total, 1) AS 커버율_pct
  FROM ranked
 WHERE rk IN (10, 20, 30, 50, 100, 200)
 ORDER BY rk;
