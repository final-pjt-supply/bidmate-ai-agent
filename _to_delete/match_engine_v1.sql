-- ============================================================================
-- ⚠ DEPRECATED (2026-07-29) — 이 파일은 더 이상 정본이 아니다. 실행 금지.
--   매칭 로직의 단일 정본 = 같은 폴더의 compute_match_results.sql (DB 함수).
--   사유: Phase 1~3 개편(item 게이트 격상·direct_prod 보완 격하·책임 전가 정책·
--   cert/credit 판정 편입·info class 폐지·D-06/07/18 보정)이 함수에만 반영되었고,
--   이 파일 본문은 2026-07-24 v1 로직 그대로다. 실행하면 낡은 판정으로
--   match_results 를 덮는다. 이력 스냅샷으로만 보존한다.
--   비라이브(전 공고) 계산이 필요하면 이 파일을 고치지 말고, compute_match_results 의
--   live_bids CTE 범위를 바꾼 사본을 새로 만들 것 — 로직 이중 정의가 이 드리프트의 원인이었다.
-- ============================================================================

-- ============================================================================
-- BidMate 매칭 엔진 v1 (M4) — match_demo.sql 후속·서비스 쿼리  [RDS 실행 · DBeaver]
--   2026-07-24. 회사 1곳 ↔ 전체 공고(bid_require_*) 축별 비교 → verdict → match_results 적재.
--
-- 사용법:
--   1) 아래 params의 company_id 리터럴을 대상 회사로 수정
--   2) 스크립트 전체 실행 (BEGIN~COMMIT). 회사 단위 DELETE→INSERT 멱등 — 재실행 안전.
--
-- 축 분류 (01_match_results_v2.5.sql 헤더와 동일 — 정본):
--   gate: 면허(license) · 지역(region) · 규모(size) · 직생(direct_prod)
--   supp: 실적(performance) · 인력(personnel) · 시공능력(capacity) · 신용(credit) · 품목(item) ★확인
--   info: 인증(cert) — M2 강등, N/M 미참여, "취득하면 가능" 신호
--
-- v1 단순화 (주석으로 표면화 — v2 과제):
--   * 인력: M1 grade_rank 반영 — 등급 요구는 동일 family 내 '이상' 합산.
--     자격종목·역할은 정확일치. 자격수준('기사 이상') rank 비교는 v2.
--   * 지역: 본점(hq)만 비교. 코드 prefix 매칭 허용(회사 코드가 요구 코드 하위이면 충족)
--   * 규모 no_large/no_conglomerate: conglomerate만 제외 (company_size에 '대기업' 구분 없음)
--   * 시공능력 총액(license_code NULL): 보유 업종 평가액 SUM으로 근사
--   * 신용 min_grade 지정 공고: 등급 비교 미구현 → 확인필요 처리
-- ============================================================================

BEGIN;

-- ── 대상 회사 지정 ──────────────────────────────────────────
-- (엔진 본문 실행 전에 실제 company_id로 수정)

DELETE FROM match_results WHERE company_id = 1;  -- ★ params와 동일 값으로

INSERT INTO match_results
  (company_id, bid_ntce_no, bid_ntce_ord, verdict,
   required, satisfied, gate_failed, need_review, axes, normalizer_version)
WITH params AS (
  SELECT 1::BIGINT AS company_id                 -- ★ 대상 회사
),

-- ═══════════════ ① 면허 (gate) — or_group 내 OR, 그룹 간 AND ═══════════════
lic_group AS (
  SELECT r.bid_ntce_no, r.bid_ntce_ord, r.or_group,
         BOOL_OR(cl.license_code IS NOT NULL)                    AS grp_match,
         BOOL_OR(r.license_code IS NULL)                         AS grp_unresolved
  FROM bid_require_licenses r
  CROSS JOIN params p
  LEFT JOIN company_licenses cl
         ON cl.company_id = p.company_id AND cl.license_code = r.license_code
  GROUP BY 1,2,3
),
ax_license AS (
  SELECT bid_ntce_no, bid_ntce_ord, 'license' AS axis, 'gate' AS class,
         CASE WHEN BOOL_AND(grp_match)                             THEN '충족'
              WHEN BOOL_OR(NOT grp_match AND NOT grp_unresolved)   THEN '미충족'
              ELSE '확인필요' END AS status,
         COUNT(*) FILTER (WHERE grp_match) || '/' || COUNT(*) || ' 그룹 충족' AS detail
  FROM lic_group GROUP BY 1,2
),

-- ═══════════════ ② 지역 (gate) — 본점 기준, nationwide 통과 ═══════════════
hq AS (
  SELECT cr.region_code FROM company_regions cr CROSS JOIN params p
  WHERE cr.company_id = p.company_id AND cr.region_type = 'hq'
),
reg_rows AS (
  SELECT r.bid_ntce_no, r.bid_ntce_ord,
         BOOL_OR(r.flag = 'nationwide')                            AS any_nationwide,
         BOOL_OR(r.region_code IS NOT NULL AND EXISTS (
             SELECT 1 FROM hq
             WHERE hq.region_code = r.region_code
                OR hq.region_code LIKE r.region_code || '%'))      AS hq_match,
         BOOL_OR(r.region_code IS NULL OR r.flag = 'site_ref')     AS any_unresolved
  FROM bid_require_regions r GROUP BY 1,2
),
ax_region AS (
  SELECT s.bid_ntce_no, s.bid_ntce_ord, 'region' AS axis, 'gate' AS class,
         CASE WHEN rr.any_nationwide OR rr.hq_match THEN '충족'
              WHEN NOT EXISTS (SELECT 1 FROM hq)    THEN '확인필요'   -- 본점 미등록
              WHEN rr.any_unresolved                THEN '확인필요'
              ELSE '미충족' END AS status,
         CASE WHEN rr.any_nationwide THEN '전국 (제한없음)'
              WHEN rr.hq_match       THEN '본점 소재지 충족'
              ELSE '요구 지역 불일치/미해석' END AS detail
  FROM bid_require_summary s
  JOIN reg_rows rr USING (bid_ntce_no, bid_ntce_ord)
  WHERE s.region_limit_type = 'hq_location'
),

-- ═══════════════ ③ 규모 (gate) ═══════════════
comp_qual AS (
  SELECT q.* FROM company_qualifications q CROSS JOIN params p WHERE q.company_id = p.company_id
),
ax_size AS (
  SELECT z.bid_ntce_no, z.bid_ntce_ord, 'size' AS axis, 'gate' AS class,
         CASE WHEN cq.company_id IS NULL THEN '확인필요'             -- 회사 규모 미등록
              WHEN z.size_limit = 'sme_only'   AND cq.company_size IN ('small','medium') THEN '충족'
              WHEN z.size_limit = 'small_only' AND cq.company_size = 'small'             THEN '충족'
              WHEN z.size_limit IN ('no_large','no_conglomerate')
                   AND cq.company_size <> 'conglomerate'             THEN '충족'
              ELSE '미충족' END AS status,
         z.size_limit || ' vs ' || COALESCE(cq.company_size,'(미등록)') AS detail
  FROM bid_require_size z LEFT JOIN comp_qual cq ON TRUE
),

-- ═══════════════ ④a 직생 (gate) — 품목 중 직생확인 요구 행 ═══════════════
dp_rows AS (
  SELECT i.bid_ntce_no, i.bid_ntce_ord,
         COUNT(*)                                                          AS n_req,
         COUNT(*) FILTER (WHERE i.item_code IS NULL)                       AS n_unres,
         COUNT(*) FILTER (WHERE ci.item_code IS NOT NULL
                            AND ci.has_direct_production
                            AND (ci.direct_prod_valid_until IS NULL
                                 OR ci.direct_prod_valid_until >= CURRENT_DATE)) AS n_ok
  FROM bid_require_items i
  CROSS JOIN params p
  LEFT JOIN company_items ci ON ci.company_id = p.company_id AND ci.item_code = i.item_code
  WHERE i.direct_production_req
  GROUP BY 1,2
),
ax_direct_prod AS (
  SELECT bid_ntce_no, bid_ntce_ord, 'direct_prod' AS axis, 'gate' AS class,
         CASE WHEN n_ok = n_req                    THEN '충족'
              WHEN n_ok + n_unres = n_req          THEN '확인필요'    -- 미해석 제외 전부 충족
              ELSE '미충족' END AS status,
         '직생확인 ' || n_ok || '/' || n_req AS detail
  FROM dp_rows
),

-- ═══════════════ ④b 품목 등록 (supp ★확인) — 비직생 행 ═══════════════
item_rows AS (
  SELECT i.bid_ntce_no, i.bid_ntce_ord,
         COUNT(*) AS n_req,
         COUNT(*) FILTER (WHERE i.item_code IS NULL)       AS n_unres,
         COUNT(*) FILTER (WHERE ci.item_code IS NOT NULL)  AS n_ok
  FROM bid_require_items i
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
-- 판정 규칙 (요구 행 단위):
--   미해석(qual_code NULL & method='none')                   → NULL(확인필요)
--   등급무관(qual_code NULL & method<>'none')                → 회사 총원 합 >= 요구
--   등급(qual_type='grade', rank 有) → 동일 field(family) 내 rank>= 회사 인력 합 >= 요구  ('이상')
--   자격종목/역할(license·role) 또는 rank 없는 grade         → 해당 코드 정확일치 headcount >= 요구
comp_person_total AS (
  SELECT COALESCE(SUM(cp.headcount),0) AS total
  FROM company_personnel cp CROSS JOIN params p WHERE cp.company_id = p.company_id
),
pers_eval AS (
  SELECT r.bid_ntce_no, r.bid_ntce_ord,
    CASE
      WHEN r.qual_code IS NULL AND r.method = 'none' THEN NULL          -- 미해석
      WHEN r.qual_code IS NULL THEN
        (SELECT total FROM comp_person_total) >= r.headcount            -- 등급무관: 총원
      WHEN m.qual_type = 'grade' AND m.grade_rank IS NOT NULL THEN      -- 등급 '이상': family 내 rank>=
        (SELECT COALESCE(SUM(cp.headcount),0)
           FROM company_personnel cp
           JOIN personnel_grade_master gm ON gm.qual_code = cp.qual_code
           CROSS JOIN params p
          WHERE cp.company_id = p.company_id
            AND gm.qual_type = 'grade' AND gm.field = m.field
            AND gm.grade_rank >= m.grade_rank) >= r.headcount
      ELSE                                                              -- 자격종목·역할: 정확일치
        COALESCE((SELECT cp.headcount FROM company_personnel cp CROSS JOIN params p
                   WHERE cp.company_id = p.company_id AND cp.qual_code = r.qual_code), 0) >= r.headcount
    END AS met
  FROM bid_require_personnel r
  LEFT JOIN personnel_grade_master m ON m.qual_code = r.qual_code
  WHERE COALESCE(r.method,'') <> 'ignored'   -- 노이즈 행("해당 없음" 등) 실요건 아님 → 매칭 제외
),
ax_personnel AS (
  SELECT bid_ntce_no, bid_ntce_ord, 'personnel' AS axis, 'supp' AS class,
         CASE WHEN BOOL_AND(met)         THEN '충족'
              WHEN BOOL_OR(met IS FALSE) THEN '미충족'
              ELSE '확인필요' END AS status,
         '인력 요건 ' || COUNT(*) FILTER (WHERE met) || '/' || COUNT(*) AS detail
  FROM pers_eval GROUP BY 1,2
),

-- ═══════════════ ⑥ 실적 (supp) — 기간 내 단건/합산/건수 ═══════════════
perf_rows AS (
  SELECT r.id, r.bid_ntce_no, r.bid_ntce_ord,
         CASE
           WHEN r.min_value IS NULL OR r.unit IS NULL OR r.parse_status = 'unparsed'
             THEN NULL                                             -- 미해석 → 확인필요
           WHEN r.unit = '원' AND COALESCE(r.agg_type,'single') = 'single' THEN
             (SELECT COALESCE(MAX(pr.contract_amt),0) FROM company_performance_records pr, params p
               WHERE pr.company_id = p.company_id
                 AND pr.end_date >= CURRENT_DATE - (r.period_years * INTERVAL '1 year')
                 AND (r.field_code IS NULL OR pr.field_code = r.field_code)) >= r.min_value
           WHEN r.unit = '원' THEN                                  -- sum
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
),
ax_performance AS (
  SELECT bid_ntce_no, bid_ntce_ord, 'performance' AS axis, 'supp' AS class,
         CASE WHEN BOOL_AND(met)                THEN '충족'
              WHEN BOOL_OR(met IS FALSE)        THEN '미충족'
              ELSE '확인필요' END AS status,
         '실적 요건 ' || COUNT(*) FILTER (WHERE met) || '/' || COUNT(*) AS detail
  FROM perf_rows GROUP BY 1,2
),

-- ═══════════════ ⑦ 시공능력 (supp) — 업종별 / 총액(SUM 근사) ═══════════════
cap_rows AS (
  SELECT r.id, r.bid_ntce_no, r.bid_ntce_ord,
         CASE
           WHEN r.min_value IS NULL OR r.parse_status = 'unparsed' THEN NULL
           WHEN r.license_code IS NOT NULL THEN
             COALESCE((SELECT ce.eval_amount FROM company_capacity_evals ce, params p
                        WHERE ce.company_id = p.company_id
                          AND ce.license_code = r.license_code),0) >= r.min_value
           ELSE                                                     -- 총액
             (SELECT COALESCE(SUM(ce.eval_amount),0) FROM company_capacity_evals ce, params p
               WHERE ce.company_id = p.company_id) >= r.min_value
         END AS met
  FROM bid_require_capacity r
),
ax_capacity AS (
  SELECT bid_ntce_no, bid_ntce_ord, 'capacity' AS axis, 'supp' AS class,
         CASE WHEN BOOL_AND(met)         THEN '충족'
              WHEN BOOL_OR(met IS FALSE) THEN '미충족'
              ELSE '확인필요' END AS status,
         '시공능력 ' || COUNT(*) FILTER (WHERE met) || '/' || COUNT(*) AS detail
  FROM cap_rows GROUP BY 1,2
),

-- ═══════════════ ⑧ 신용 (supp) — v1 보유 여부, min_grade는 확인필요 ═══════════════
ax_credit AS (
  SELECT c.bid_ntce_no, c.bid_ntce_ord, 'credit' AS axis, 'supp' AS class,
         CASE WHEN NOT c.required THEN '충족'
              WHEN c.min_grade IS NOT NULL THEN '확인필요'          -- 등급 비교 v2
              WHEN cq.credit_rating IS NOT NULL THEN '충족'
              ELSE '미충족' END AS status,
         CASE WHEN c.min_grade IS NOT NULL THEN '요구등급 ' || c.min_grade || ' (v1 비교불가)'
              WHEN cq.credit_rating IS NOT NULL THEN '신용평가 보유(' || cq.credit_rating || ')'
              ELSE '신용평가 미보유' END AS detail
  FROM bid_require_credit c LEFT JOIN comp_qual cq ON TRUE
),

-- ═══════════════ ⑨ 인증 (info — M2 강등, N/M 미참여) ═══════════════
cert_rows AS (
  SELECT r.bid_ntce_no, r.bid_ntce_ord,
         COUNT(*) AS n_req,
         COUNT(*) FILTER (WHERE r.cert_code IS NULL) AS n_unres,
         COUNT(*) FILTER (WHERE cc.cert_code IS NOT NULL
                            AND (cc.valid_until IS NULL OR cc.valid_until >= CURRENT_DATE)) AS n_ok,
         STRING_AGG(DISTINCT r.cert_name, ', ')
           FILTER (WHERE r.cert_code IS NOT NULL AND cc.cert_code IS NULL) AS missing_names
  FROM bid_require_certs r
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

-- ═══════════════ 축 통합 → 공고별 verdict ═══════════════
axis_all AS (
  SELECT * FROM ax_license    UNION ALL
  SELECT * FROM ax_region     UNION ALL
  SELECT * FROM ax_size       UNION ALL
  SELECT * FROM ax_direct_prod UNION ALL
  SELECT * FROM ax_item       UNION ALL
  SELECT * FROM ax_personnel  UNION ALL
  SELECT * FROM ax_performance UNION ALL
  SELECT * FROM ax_capacity   UNION ALL
  SELECT * FROM ax_credit     UNION ALL
  SELECT * FROM ax_cert
),
per_bid AS (
  SELECT bid_ntce_no, bid_ntce_ord,
         COUNT(*) FILTER (WHERE class IN ('gate','supp'))                      AS required,
         COUNT(*) FILTER (WHERE class IN ('gate','supp') AND status = '충족')   AS satisfied,
         COUNT(*) FILTER (WHERE class = 'gate'           AND status = '미충족') AS gate_failed,
         COUNT(*) FILTER (WHERE class IN ('gate','supp') AND status = '확인필요') AS need_review,
         JSONB_AGG(JSONB_BUILD_OBJECT(
             'axis', axis, 'class', class, 'status', status, 'detail', detail)
           ORDER BY CASE class WHEN 'gate' THEN 1 WHEN 'supp' THEN 2 ELSE 3 END, axis) AS axes
  FROM axis_all GROUP BY 1,2
)
SELECT p.company_id, s.bid_ntce_no, s.bid_ntce_ord,
       CASE WHEN COALESCE(b.gate_failed,0) > 0 THEN '불가'
            WHEN COALESCE(b.need_review,0) > 0 THEN '확인필요'
            WHEN COALESCE(b.satisfied,0) < COALESCE(b.required,0) THEN '보완가능'
            ELSE '가능' END                         AS verdict,
       COALESCE(b.required,0), COALESCE(b.satisfied,0),
       COALESCE(b.gate_failed,0), COALESCE(b.need_review,0),
       COALESCE(b.axes, '[]'::jsonb),
       s.normalizer_version
FROM bid_require_summary s
CROSS JOIN params p
LEFT JOIN per_bid b USING (bid_ntce_no, bid_ntce_ord);

COMMIT;

-- ── 실행 후 요약 ─────────────────────────────────────────────
-- SELECT verdict, count(*) FROM match_results WHERE company_id = 1 GROUP BY 1 ORDER BY 2 DESC;
