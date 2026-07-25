-- ============================================================================
-- BidMate 매칭 함수 — compute_match_results(company_id)  [RDS · DBeaver 1회 생성]
--   2026-07-24. 02 엔진을 DB 함수로 감쌈. 매칭 로직의 단일 정본.
--
-- 무엇:
--   회사 1곳 × **라이브 공고**(투찰마감 bid_clse_dt 미도래)만 9축 매칭 → verdict 행 반환.
--   RETURNS TABLE 이라 두 가지로 다 쓸 수 있음:
--     · on-read (실시간)  : SELECT * FROM compute_match_results(9001);
--     · precompute(캐시)  : INSERT INTO match_results (...) SELECT * FROM compute_match_results(9001);
--
-- 라이브 필터  [2026-07-25 개정 — 에이전트 검색(C)과 기준 통일]:
--   bid_clse_dt IS NULL OR bid_clse_dt > now(KST). 인덱스 idx_bid_table_clse 사용.
--   마감일시 공란(수의계약 등)은 '판단 불가'라 살아있는 것으로 본다.
--     └ 개정 전에는 제외했으나, 검색(C)은 포함하고 있어 해당 공고가 화면에는 뜨는데
--       자격 판정만 통째로 빠지는 누락이 있었다(실측 43/19,076건).
--
--   ★ 라이브 정의는 두 곳에 있다. 한쪽만 고치면 위 누락이 재발한다:
--       ① 여기 live_bids
--       ② agents/tools/bid_info.py 의 _OPEN_CONDITION
--     반드시 같은 식으로 함께 유지할 것.
--
-- 축 분류·판정 규칙은 02_match_engine_v1.sql과 100% 동일(정본). 여기선 params → 함수 인자,
--   대상 공고 → live_bids로 바뀐 것만 다름.
-- ============================================================================

CREATE OR REPLACE FUNCTION compute_match_results(p_company_id BIGINT)
RETURNS TABLE (
  company_id         BIGINT,
  bid_ntce_no        VARCHAR(40),
  bid_ntce_ord       VARCHAR(10),
  verdict            TEXT,
  required           INT,
  satisfied          INT,
  gate_failed        INT,
  need_review        INT,
  axes               JSONB,
  normalizer_version VARCHAR(10)
)
LANGUAGE sql STABLE AS $$
WITH params AS (
  SELECT p_company_id AS company_id
),
-- 라이브 공고 = 투찰마감 미도래. summary 전체 컬럼 보존(region_limit_type·normalizer_version 등).
live_bids AS (
  SELECT s.*
  FROM bid_require_summary s
  JOIN bid_table bt USING (bid_ntce_no, bid_ntce_ord)
  -- ↓ bid_info._OPEN_CONDITION 과 동일한 식 (동시 유지 대상 — 상단 주석 참조)
  WHERE (bt.bid_clse_dt IS NULL
      OR bt.bid_clse_dt > (now() AT TIME ZONE 'Asia/Seoul'))
),

-- ═══════════════ ① 면허 (gate) ═══════════════
lic_group AS (
  SELECT r.bid_ntce_no, r.bid_ntce_ord, r.or_group,
         BOOL_OR(cl.license_code IS NOT NULL) AS grp_match,
         BOOL_OR(r.license_code IS NULL)      AS grp_unresolved
  FROM bid_require_licenses r
  JOIN live_bids lb ON lb.bid_ntce_no = r.bid_ntce_no AND lb.bid_ntce_ord = r.bid_ntce_ord
  CROSS JOIN params p
  LEFT JOIN company_licenses cl ON cl.company_id = p.company_id AND cl.license_code = r.license_code
  GROUP BY 1,2,3
),
ax_license AS (
  SELECT bid_ntce_no, bid_ntce_ord, 'license' AS axis, 'gate' AS class,
         CASE WHEN BOOL_AND(grp_match)                           THEN '충족'
              WHEN BOOL_OR(NOT grp_match AND NOT grp_unresolved) THEN '미충족'
              ELSE '확인필요' END AS status,
         COUNT(*) FILTER (WHERE grp_match) || '/' || COUNT(*) || ' 그룹 충족' AS detail
  FROM lic_group GROUP BY 1,2
),

-- ═══════════════ ② 지역 (gate) ═══════════════
hq AS (
  SELECT cr.region_code FROM company_regions cr CROSS JOIN params p
  WHERE cr.company_id = p.company_id AND cr.region_type = 'hq'
),
reg_rows AS (
  SELECT r.bid_ntce_no, r.bid_ntce_ord,
         BOOL_OR(r.flag = 'nationwide') AS any_nationwide,
         BOOL_OR(r.region_code IS NOT NULL AND EXISTS (
             SELECT 1 FROM hq
             WHERE hq.region_code = r.region_code
                OR hq.region_code LIKE r.region_code || '%')) AS hq_match,
         BOOL_OR(r.region_code IS NULL OR r.flag = 'site_ref') AS any_unresolved
  FROM bid_require_regions r
  JOIN live_bids lb ON lb.bid_ntce_no = r.bid_ntce_no AND lb.bid_ntce_ord = r.bid_ntce_ord
  GROUP BY 1,2
),
ax_region AS (
  SELECT s.bid_ntce_no, s.bid_ntce_ord, 'region' AS axis, 'gate' AS class,
         CASE WHEN rr.any_nationwide OR rr.hq_match THEN '충족'
              WHEN NOT EXISTS (SELECT 1 FROM hq)    THEN '확인필요'
              WHEN rr.any_unresolved                THEN '확인필요'
              ELSE '미충족' END AS status,
         CASE WHEN rr.any_nationwide THEN '전국 (제한없음)'
              WHEN rr.hq_match       THEN '본점 소재지 충족'
              ELSE '요구 지역 불일치/미해석' END AS detail
  FROM live_bids s
  JOIN reg_rows rr USING (bid_ntce_no, bid_ntce_ord)
  WHERE s.region_limit_type = 'hq_location'
),

-- ═══════════════ ③ 규모 (gate) ═══════════════
comp_qual AS (
  SELECT q.* FROM company_qualifications q CROSS JOIN params p WHERE q.company_id = p.company_id
),
ax_size AS (
  SELECT z.bid_ntce_no, z.bid_ntce_ord, 'size' AS axis, 'gate' AS class,
         CASE WHEN cq.company_id IS NULL THEN '확인필요'
              WHEN z.size_limit = 'sme_only'   AND cq.company_size IN ('small','medium') THEN '충족'
              WHEN z.size_limit = 'small_only' AND cq.company_size = 'small'             THEN '충족'
              WHEN z.size_limit IN ('no_large','no_conglomerate')
                   AND cq.company_size <> 'conglomerate' THEN '충족'
              ELSE '미충족' END AS status,
         z.size_limit || ' vs ' || COALESCE(cq.company_size,'(미등록)') AS detail
  FROM bid_require_size z
  JOIN live_bids lb ON lb.bid_ntce_no = z.bid_ntce_no AND lb.bid_ntce_ord = z.bid_ntce_ord
  LEFT JOIN comp_qual cq ON TRUE
),

-- ═══════════════ ④a 직생 (gate) ═══════════════
dp_rows AS (
  SELECT i.bid_ntce_no, i.bid_ntce_ord,
         COUNT(*) AS n_req,
         COUNT(*) FILTER (WHERE i.item_code IS NULL) AS n_unres,
         COUNT(*) FILTER (WHERE ci.item_code IS NOT NULL AND ci.has_direct_production
                            AND (ci.direct_prod_valid_until IS NULL
                                 OR ci.direct_prod_valid_until >= CURRENT_DATE)) AS n_ok
  FROM bid_require_items i
  JOIN live_bids lb ON lb.bid_ntce_no = i.bid_ntce_no AND lb.bid_ntce_ord = i.bid_ntce_ord
  CROSS JOIN params p
  LEFT JOIN company_items ci ON ci.company_id = p.company_id AND ci.item_code = i.item_code
  WHERE i.direct_production_req
  GROUP BY 1,2
),
ax_direct_prod AS (
  SELECT bid_ntce_no, bid_ntce_ord, 'direct_prod' AS axis, 'gate' AS class,
         CASE WHEN n_ok = n_req           THEN '충족'
              WHEN n_ok + n_unres = n_req THEN '확인필요'
              ELSE '미충족' END AS status,
         '직생확인 ' || n_ok || '/' || n_req AS detail
  FROM dp_rows
),

-- ═══════════════ ④b 품목 등록 (supp) ═══════════════
item_rows AS (
  SELECT i.bid_ntce_no, i.bid_ntce_ord,
         COUNT(*) AS n_req,
         COUNT(*) FILTER (WHERE i.item_code IS NULL)      AS n_unres,
         COUNT(*) FILTER (WHERE ci.item_code IS NOT NULL) AS n_ok
  FROM bid_require_items i
  JOIN live_bids lb ON lb.bid_ntce_no = i.bid_ntce_no AND lb.bid_ntce_ord = i.bid_ntce_ord
  CROSS JOIN params p
  LEFT JOIN company_items ci ON ci.company_id = p.company_id AND ci.item_code = i.item_code
  WHERE NOT i.direct_production_req
  GROUP BY 1,2
),
ax_item AS (
  SELECT bid_ntce_no, bid_ntce_ord, 'item' AS axis, 'supp' AS class,
         CASE WHEN n_ok = n_req           THEN '충족'
              WHEN n_ok + n_unres = n_req THEN '확인필요'
              ELSE '미충족' END AS status,
         '품목 등록 ' || n_ok || '/' || n_req AS detail
  FROM item_rows
),

-- ═══════════════ ⑤ 인력 (supp) — M1 rank 반영 ═══════════════
comp_person_total AS (
  SELECT COALESCE(SUM(cp.headcount),0) AS total
  FROM company_personnel cp CROSS JOIN params p WHERE cp.company_id = p.company_id
),
pers_eval AS (
  SELECT r.bid_ntce_no, r.bid_ntce_ord,
    CASE
      WHEN r.qual_code IS NULL AND r.method = 'none' THEN NULL
      WHEN r.qual_code IS NULL THEN
        (SELECT total FROM comp_person_total) >= r.headcount
      WHEN m.qual_type = 'grade' AND m.grade_rank IS NOT NULL THEN
        (SELECT COALESCE(SUM(cp.headcount),0)
           FROM company_personnel cp
           JOIN personnel_grade_master gm ON gm.qual_code = cp.qual_code
           CROSS JOIN params p
          WHERE cp.company_id = p.company_id
            AND gm.qual_type = 'grade' AND gm.field = m.field
            AND gm.grade_rank >= m.grade_rank) >= r.headcount
      ELSE
        COALESCE((SELECT cp.headcount FROM company_personnel cp CROSS JOIN params p
                   WHERE cp.company_id = p.company_id AND cp.qual_code = r.qual_code), 0) >= r.headcount
    END AS met
  FROM bid_require_personnel r
  JOIN live_bids lb ON lb.bid_ntce_no = r.bid_ntce_no AND lb.bid_ntce_ord = r.bid_ntce_ord
  LEFT JOIN personnel_grade_master m ON m.qual_code = r.qual_code
  WHERE COALESCE(r.method,'') <> 'ignored'
),
ax_personnel AS (
  SELECT bid_ntce_no, bid_ntce_ord, 'personnel' AS axis, 'supp' AS class,
         CASE WHEN BOOL_AND(met)         THEN '충족'
              WHEN BOOL_OR(met IS FALSE) THEN '미충족'
              ELSE '확인필요' END AS status,
         '인력 요건 ' || COUNT(*) FILTER (WHERE met) || '/' || COUNT(*) AS detail
  FROM pers_eval GROUP BY 1,2
),

-- ═══════════════ ⑥ 실적 (supp) ═══════════════
perf_rows AS (
  SELECT r.id, r.bid_ntce_no, r.bid_ntce_ord,
         CASE
           WHEN r.min_value IS NULL OR r.unit IS NULL OR r.parse_status = 'unparsed' THEN NULL
           WHEN r.unit = '원' AND COALESCE(r.agg_type,'single') = 'single' THEN
             (SELECT COALESCE(MAX(pr.contract_amt),0) FROM company_performance_records pr, params p
               WHERE pr.company_id = p.company_id
                 AND pr.end_date >= CURRENT_DATE - (r.period_years * INTERVAL '1 year')
                 AND (r.field_code IS NULL OR pr.field_code = r.field_code)) >= r.min_value
           WHEN r.unit = '원' THEN
             (SELECT COALESCE(SUM(pr.contract_amt),0) FROM company_performance_records pr, params p
               WHERE pr.company_id = p.company_id
                 AND pr.end_date >= CURRENT_DATE - (r.period_years * INTERVAL '1 year')
                 AND (r.field_code IS NULL OR pr.field_code = r.field_code)) >= r.min_value
           WHEN r.unit = '건' THEN
             (SELECT COUNT(*) FROM company_performance_records pr, params p
               WHERE pr.company_id = p.company_id
                 AND pr.end_date >= CURRENT_DATE - (r.period_years * INTERVAL '1 year')
                 AND (r.field_code IS NULL OR pr.field_code = r.field_code)) >= r.min_value
         END AS met
  FROM bid_require_performances r
  JOIN live_bids lb ON lb.bid_ntce_no = r.bid_ntce_no AND lb.bid_ntce_ord = r.bid_ntce_ord
),
ax_performance AS (
  SELECT bid_ntce_no, bid_ntce_ord, 'performance' AS axis, 'supp' AS class,
         CASE WHEN BOOL_AND(met)         THEN '충족'
              WHEN BOOL_OR(met IS FALSE) THEN '미충족'
              ELSE '확인필요' END AS status,
         '실적 요건 ' || COUNT(*) FILTER (WHERE met) || '/' || COUNT(*) AS detail
  FROM perf_rows GROUP BY 1,2
),

-- ═══════════════ ⑦ 시공능력 (supp) ═══════════════
cap_rows AS (
  SELECT r.id, r.bid_ntce_no, r.bid_ntce_ord,
         CASE
           WHEN r.min_value IS NULL OR r.parse_status = 'unparsed' THEN NULL
           WHEN r.license_code IS NOT NULL THEN
             COALESCE((SELECT ce.eval_amount FROM company_capacity_evals ce, params p
                        WHERE ce.company_id = p.company_id AND ce.license_code = r.license_code),0) >= r.min_value
           ELSE
             (SELECT COALESCE(SUM(ce.eval_amount),0) FROM company_capacity_evals ce, params p
               WHERE ce.company_id = p.company_id) >= r.min_value
         END AS met
  FROM bid_require_capacity r
  JOIN live_bids lb ON lb.bid_ntce_no = r.bid_ntce_no AND lb.bid_ntce_ord = r.bid_ntce_ord
),
ax_capacity AS (
  SELECT bid_ntce_no, bid_ntce_ord, 'capacity' AS axis, 'supp' AS class,
         CASE WHEN BOOL_AND(met)         THEN '충족'
              WHEN BOOL_OR(met IS FALSE) THEN '미충족'
              ELSE '확인필요' END AS status,
         '시공능력 ' || COUNT(*) FILTER (WHERE met) || '/' || COUNT(*) AS detail
  FROM cap_rows GROUP BY 1,2
),

-- ═══════════════ ⑧ 신용 (supp) ═══════════════
ax_credit AS (
  SELECT c.bid_ntce_no, c.bid_ntce_ord, 'credit' AS axis, 'supp' AS class,
         CASE WHEN NOT c.required THEN '충족'
              WHEN c.min_grade IS NOT NULL THEN '확인필요'
              WHEN cq.credit_rating IS NOT NULL THEN '충족'
              ELSE '미충족' END AS status,
         CASE WHEN c.min_grade IS NOT NULL THEN '요구등급 ' || c.min_grade || ' (v1 비교불가)'
              WHEN cq.credit_rating IS NOT NULL THEN '신용평가 보유(' || cq.credit_rating || ')'
              ELSE '신용평가 미보유' END AS detail
  FROM bid_require_credit c
  JOIN live_bids lb ON lb.bid_ntce_no = c.bid_ntce_no AND lb.bid_ntce_ord = c.bid_ntce_ord
  LEFT JOIN comp_qual cq ON TRUE
),

-- ═══════════════ ⑨ 인증 (info) ═══════════════
cert_rows AS (
  SELECT r.bid_ntce_no, r.bid_ntce_ord,
         COUNT(*) AS n_req,
         COUNT(*) FILTER (WHERE r.cert_code IS NULL) AS n_unres,
         COUNT(*) FILTER (WHERE cc.cert_code IS NOT NULL
                            AND (cc.valid_until IS NULL OR cc.valid_until >= CURRENT_DATE)) AS n_ok,
         STRING_AGG(DISTINCT r.cert_name, ', ')
           FILTER (WHERE r.cert_code IS NOT NULL AND cc.cert_code IS NULL) AS missing_names
  FROM bid_require_certs r
  JOIN live_bids lb ON lb.bid_ntce_no = r.bid_ntce_no AND lb.bid_ntce_ord = r.bid_ntce_ord
  CROSS JOIN params p
  LEFT JOIN company_certs cc ON cc.company_id = p.company_id AND cc.cert_code = r.cert_code
  GROUP BY 1,2
),
ax_cert AS (
  SELECT bid_ntce_no, bid_ntce_ord, 'cert' AS axis, 'info' AS class,
         CASE WHEN n_ok = n_req           THEN '충족'
              WHEN n_ok + n_unres = n_req THEN '확인필요'
              ELSE '미충족' END AS status,
         '인증 ' || n_ok || '/' || n_req
           || COALESCE(' · 취득하면 가능: ' || missing_names, '') AS detail
  FROM cert_rows
),

-- ═══════════════ 축 통합 → verdict ═══════════════
axis_all AS (
  SELECT * FROM ax_license     UNION ALL
  SELECT * FROM ax_region      UNION ALL
  SELECT * FROM ax_size        UNION ALL
  SELECT * FROM ax_direct_prod UNION ALL
  SELECT * FROM ax_item        UNION ALL
  SELECT * FROM ax_personnel   UNION ALL
  SELECT * FROM ax_performance UNION ALL
  SELECT * FROM ax_capacity    UNION ALL
  SELECT * FROM ax_credit      UNION ALL
  SELECT * FROM ax_cert
),
per_bid AS (
  SELECT bid_ntce_no, bid_ntce_ord,
         COUNT(*) FILTER (WHERE class IN ('gate','supp'))                        AS required,
         COUNT(*) FILTER (WHERE class IN ('gate','supp') AND status = '충족')     AS satisfied,
         COUNT(*) FILTER (WHERE class = 'gate'           AND status = '미충족')   AS gate_failed,
         COUNT(*) FILTER (WHERE class IN ('gate','supp') AND status = '확인필요') AS need_review,
         JSONB_AGG(JSONB_BUILD_OBJECT(
             'axis', axis, 'class', class, 'status', status, 'detail', detail)
           ORDER BY CASE class WHEN 'gate' THEN 1 WHEN 'supp' THEN 2 ELSE 3 END, axis) AS axes
  FROM axis_all GROUP BY 1,2
)
SELECT p.company_id,
       s.bid_ntce_no, s.bid_ntce_ord,
       CASE WHEN COALESCE(b.gate_failed,0) > 0 THEN '불가'
            WHEN COALESCE(b.need_review,0) > 0 THEN '확인필요'
            WHEN COALESCE(b.satisfied,0) < COALESCE(b.required,0) THEN '보완가능'
            ELSE '가능' END::TEXT,
       COALESCE(b.required,0)::INT,
       COALESCE(b.satisfied,0)::INT,
       COALESCE(b.gate_failed,0)::INT,
       COALESCE(b.need_review,0)::INT,
       COALESCE(b.axes, '[]'::jsonb),
       s.normalizer_version
FROM live_bids s
CROSS JOIN params p
LEFT JOIN per_bid b USING (bid_ntce_no, bid_ntce_ord);
$$;

-- ── 사용 ────────────────────────────────────────────────────
-- on-read (백엔드가 로그인/새로고침 때 호출):
--   SELECT verdict, required, satisfied, axes FROM compute_match_results(9001)
--   ORDER BY (verdict='가능') DESC;                       -- 가능부터
--
-- precompute (match_results에 캐시하려면):
--   DELETE FROM match_results WHERE company_id = 9001;
--   INSERT INTO match_results
--     (company_id, bid_ntce_no, bid_ntce_ord, verdict, required, satisfied,
--      gate_failed, need_review, axes, normalizer_version)
--   SELECT * FROM compute_match_results(9001);
--
-- 검증(라이브 한정 분포):
--   SELECT verdict, count(*) FROM compute_match_results(9001) GROUP BY 1 ORDER BY 2 DESC;