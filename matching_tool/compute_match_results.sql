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
-- ── 변경 이력 ──────────────────────────────────────────────
-- 2026-07-27  [D3] axes 페이로드에 required·actual 분리 적재.
--   10개 ax_* CTE 가 req_value·act_value 를 만들고 per_bid 가 JSON 키로 싣는다.
--   detail 은 유지(하위호환·요약용). 컬럼명을 req_value/act_value 로 둔 이유는
--   per_bid 의 출력 별칭 required 및 ax_credit 의 c.required(boolean)와 겹치기 때문.
--   ★ axis_all 이 SELECT * UNION ALL 이라 축을 추가하면 두 컬럼을 반드시 채워야 한다
--     (안 채우면 함수 생성 실패 — 이게 D3 재발 방지 장치다).
--   소비자: agents/tools/eligibility.py::_to_result (키 없으면 detail 로 폴백).
--
-- 2026-07-27  [신용 가드] credit 축 위양성 차단.
--   "신용정보 관리규약에 의한 채무불이행 또는 금융질서 문란자"는 입찰보증금 납부조건
--   또는 부정당업자 결격사유지 신용등급 요구가 아니다. 실측 3,611건 중 933건(25.8%).
--   credit_ev/credit_fp 가 extraction_evidence 근거로 판별해 '충족' 처리한다.
--   축을 지우지 않는 이유는 required 분모를 흔들지 않기 위해서. 원복은 CASE 첫 줄 삭제.
--   ※ 대증요법이다. 근본 수정은 추출 프롬프트(결격사유를 credit_rating_req 로 잡지 않기).
--
-- 2026-07-27  [TZ] CURRENT_DATE → params.today_kst 통일.
--   CURRENT_DATE 는 세션 TimeZone 의 날짜라, 세션이 UTC 면 KST 00:00~09:00 구간에서
--   하루 전이 나온다. live 조건은 AT TIME ZONE 으로 세션 무관인데 직생·실적·인증
--   3축만 CURRENT_DATE 를 써서 규약이 갈려 있었다(같은 데이터인데 세션따라 판정이 달라짐).
--   → 이제 함수 전체가 세션 tz 무관. pg_timezone 고정에 의존하지 않는다.
--
-- 2026-07-27(2차)  [D3-b] required/actual 을 '사람이 읽는 값'으로 승격.
--   1차는 계약(키 분리)만 닫았고 값은 코드·개수였다. 이번에 표시값을 채운다.
--     · region : region_code(41000) → region_master.region_name(경기도)
--     · size   : size_limit(sme_only) → '중소기업만' 등 한글 라벨
--     · N-of-M 6축(license·item·direct_prod·personnel·performance·capacity)
--                : '3개 요건/2개 충족' → 실제 항목명 나열
--   계약 의미를 통일한다:
--     req_value = 공고가 요구하는 항목 전체
--     act_value = 그중 우리가 충족한 항목 (없으면 '(없음)')
--     → 미충족 항목은 두 값의 차집합. detail 은 종전대로 개수 요약을 유지한다.
--   이름 출처는 bid_require_* 의 *_name 우선, 없으면 name_raw(원문), 그래도 없으면
--   마스터 조인. 정규화 실패(code IS NULL) 건도 원문으로는 보이게 하려는 것.
--   ★ 길이 방어: 집계 문자열에 left(...,300). respond 가 이 값을 LLM 프롬프트에
--     싣기 때문에 품목명(VARCHAR 300) 다건이면 토큰이 폭증한다.
--
-- 2026-07-27(3차)  [면허 or_group 보정] license 게이트가 과도하게 조이던 문제.
--   실측으로 드러난 결함 셋 중 SQL 로 덮을 수 있는 둘을 처리한다.
--     A. 인수분해 누락 — 원문 "A과 B / A과 C / A과 D" 조합 나열을 or_group 에 그대로
--        옮겨서 같은 면허가 여러 그룹에 반복된다(실측 건물(시설)관리용역 ×5).
--        판정(BOOL_AND)은 안 바뀌지만 분모와 표시가 망가진다 → 면허코드 집합으로 접는다.
--     C. item_field 경유를 AND 로 취급 — or_group 이 두 네임스페이스로 갈려 있다:
--          license_field → '1','2',…      item_field(route:license) → 'x1','x2',…
--        (실측 100% 대응: item_field 4,545건 전부 x 접두, method=route:license)
--        품목 경유는 "이 품목을 하려면 이 면허 중 하나"라는 유추라 의미상 OR 인데
--        x1..xN 이 각각 AND 그룹으로 세어져 live 31건이 과도하게 조여지고 있었다.
--        → item_field 전체를 단일 OR 그룹 'x' 로 병합.
--   ※ 남은 결함 B(다중 코드 유실)는 SQL 로 복구 불가 — name_raw 에만 있고 license_code
--     에 없어서 company_licenses 와 조인할 대상이 없다. live 8건이라 v2 백로그.
--     근본 수정은 어댑터(인수분해 + 다중 코드 추출) + 재정규화.
--   ※ 병합은 느슨한 쪽 오류를 택한 것이다. gate 축에서 과조임은 참여 가능한 공고를
--     숨겨 사용자가 존재조차 모르게 하지만, 느슨하면 bid_ntce_dtl_url 로 확인 가능하다.
--
-- 2026-07-28  [축0개] 판정축이 하나도 없는 공고를 '가능'으로 내던 위양성 차단.
--   verdict CASE 에 required=0 분기를 **맨 앞에** 넣는다.
--   required=0 이면 gate_failed=0·need_review=0·satisfied=0 이라
--   기존 CASE 가 전부 흘러내려 마지막 ELSE '가능' 에 걸렸다.
--   축 0개는 "요구조건이 없다"가 아니라 "공고에서 조건을 못 뽑아냈다"이다.
--   전자면 '가능'이 맞지만 후자다 — 라이브에서 근거 0줄짜리 '지원 가능' 배지가 된다.
--   실측(회사 9001, 2026-07-28 라이브 1,257건): 가능 270건 중 92건(34.1%)이 축 0개.
--     → 가능 270 → 178, 확인필요 131 → 223. 다른 verdict 는 이동 없음
--       (required=0 인 행은 정의상 전부 '가능' 쪽에만 있었다).
--   해당하는 두 경우를 한 분기가 같이 잡는다:
--     ① per_bid LEFT JOIN 미스 — bid_require_* 에 행 자체가 없음
--     ② class='info'(cert) 축만 있음 — 표시용이라 판정 분모에 안 들어감
--   ※ 이건 대증요법이 아니라 판정 규칙 자체의 수정이다. 근본 원인(추출 실패)은
--     정규화 개선으로 축이 채워지면 자연히 이 분기에 안 걸리게 된다.
-- ============================================================================

CREATE OR REPLACE FUNCTION public.compute_match_results(p_company_id bigint)
 RETURNS TABLE(company_id bigint, bid_ntce_no character varying, bid_ntce_ord character varying, verdict text, required integer, satisfied integer, gate_failed integer, need_review integer, axes jsonb, normalizer_version character varying)
 LANGUAGE sql
 STABLE
AS $function$
WITH params AS (
  SELECT p_company_id AS company_id,
         -- [TZ] 기준일. CURRENT_DATE 는 세션 TimeZone 의 날짜라 세션이 UTC 면 KST 00:00~09:00
         --      구간에서 하루 전이 나온다. live 조건은 AT TIME ZONE 으로 세션 무관인데
         --      직생·실적·인증 3축만 CURRENT_DATE 를 써서 규약이 갈려 있었다.
         --      → 같은 데이터·같은 회사인데 세션 tz 에 따라 판정이 달라진다. 여기서 통일한다.
         (now() AT TIME ZONE 'Asia/Seoul')::date AS today_kst
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
  SELECT r.bid_ntce_no, r.bid_ntce_ord,
         -- [3차] or_group 이 두 네임스페이스로 갈려 있다(실측 100% 대응):
         --   license_field → '1','2',…   item_field(route:license) → 'x1','x2',…
         --   품목 경유는 "이 품목을 하려면 이 면허 중 하나"라는 유추라 의미상 OR 인데,
         --   x1..xN 이 각각 별도 AND 그룹으로 세어져 게이트를 과하게 조이고 있었다.
         --   → 품목 경유 전체를 단일 OR 그룹 'x' 로 접는다.
         CASE WHEN r.source = 'item_field' THEN 'x' ELSE r.or_group END AS or_group,
         BOOL_OR(cl.license_code IS NOT NULL) AS grp_match,
         BOOL_OR(r.license_code IS NULL)      AS grp_unresolved,
         -- [D3-b] OR 그룹 내부는 '또는'으로 묶는다. 표준명 없으면 원문(name_raw).
         COALESCE(STRING_AGG(DISTINCT COALESCE(r.license_name, r.name_raw), ' 또는 '),
                  '(미해석)')                  AS grp_label,
         -- [3차] 중복 판별 키. 라벨이 아니라 '면허코드 집합'으로 접어야 한다.
         --   같은 면허가 표준명/원문 두 표기로 들어오는 사례가 있어서
         --   (학술.연구용역 vs 학술연구용역업[업종코드1169],
         --    정보통신공사업 vs 정보통신공사업 면허증) 라벨 기준으로는 안 접힌다.
         --   미해석(code NULL)은 같다고 단정할 수 없으므로 원문을 키에 넣어 구분 유지.
         array_to_string(
           array_agg(DISTINCT COALESCE(r.license_code::text, 'RAW:' || r.name_raw)
                     ORDER BY COALESCE(r.license_code::text, 'RAW:' || r.name_raw)),
           '|')                               AS grp_key
  FROM bid_require_licenses r
  JOIN live_bids lb ON lb.bid_ntce_no = r.bid_ntce_no AND lb.bid_ntce_ord = r.bid_ntce_ord
  CROSS JOIN params p
  LEFT JOIN company_licenses cl ON cl.company_id = p.company_id AND cl.license_code = r.license_code
  GROUP BY 1,2,3
),
-- [3차] 코드 집합이 같은 그룹을 하나로 접는다(실측 건물(시설)관리용역 ×5 등).
--   grp_match/grp_unresolved 는 코드 집합이 같으면 반드시 같으므로 어느 행을 남겨도 된다.
--   라벨은 짧은 쪽을 남긴다 — 표준명이 원문보다 짧은 경향이 있다.
lic_dedup AS (
  SELECT DISTINCT ON (bid_ntce_no, bid_ntce_ord, grp_key)
         bid_ntce_no, bid_ntce_ord, grp_match, grp_unresolved, grp_label
  FROM lic_group
  ORDER BY bid_ntce_no, bid_ntce_ord, grp_key, length(grp_label), grp_label
),
ax_license AS (
  SELECT bid_ntce_no, bid_ntce_ord, 'license' AS axis, 'gate' AS class,
         CASE WHEN BOOL_AND(grp_match)                           THEN '충족'
              WHEN BOOL_OR(NOT grp_match AND NOT grp_unresolved) THEN '미충족'
              ELSE '확인필요' END AS status,
         COUNT(*) FILTER (WHERE grp_match) || '/' || COUNT(*) || ' 그룹 충족'          AS detail,
         left(STRING_AGG(grp_label, ', ' ORDER BY grp_label), 300)                    AS req_value,
         COALESCE(left(STRING_AGG(grp_label, ', ' ORDER BY grp_label)
                       FILTER (WHERE grp_match), 300), '(없음)')                      AS act_value
  FROM lic_dedup GROUP BY 1,2
),

-- ═══════════════ ② 지역 (gate) ═══════════════
hq AS (
  SELECT cr.region_code, rm.region_name          -- [D3-b] 표시용 이름 동반
  FROM company_regions cr
  CROSS JOIN params p
  LEFT JOIN region_master rm ON rm.region_code = cr.region_code
  WHERE cr.company_id = p.company_id AND cr.region_type = 'hq'
),
reg_rows AS (
  SELECT r.bid_ntce_no, r.bid_ntce_ord,
         BOOL_OR(r.flag = 'nationwide') AS any_nationwide,
         BOOL_OR(r.region_code IS NOT NULL AND EXISTS (
             SELECT 1 FROM hq
             WHERE hq.region_code = r.region_code
                OR hq.region_code LIKE r.region_code || '%')) AS hq_match,
         BOOL_OR(r.region_code IS NULL OR r.flag = 'site_ref') AS any_unresolved,
         -- [D3-b] region_code → region_name. 전국(nationwide) 행은 목록에서 뺀다.
         STRING_AGG(DISTINCT COALESCE(r.region_name, r.name_raw), ', ')
           FILTER (WHERE COALESCE(r.flag,'') <> 'nationwide')  AS req_regions
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
              ELSE '요구 지역 불일치/미해석' END AS detail,
         CASE WHEN rr.any_nationwide THEN '전국 (제한없음)'
              ELSE COALESCE(left(rr.req_regions, 300), '(미해석)') END               AS req_value,
         COALESCE((SELECT left(STRING_AGG(COALESCE(region_name, region_code), ', '), 300)
                     FROM hq), '(본점 미등록)')                                       AS act_value
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
         z.size_limit || ' vs ' || COALESCE(cq.company_size,'(미등록)') AS detail,
         -- [D3-b] 코드 → 한글 라벨. CHECK 제약값과 1:1 (bid_require.sql / company_info.sql).
         CASE z.size_limit
           WHEN 'sme_only'        THEN '중소기업만'
           WHEN 'small_only'      THEN '소기업만'
           WHEN 'no_large'        THEN '대기업 제외'
           WHEN 'no_conglomerate' THEN '대기업집단 제외'
           ELSE COALESCE(z.size_limit, '(미해석)') END                                 AS req_value,
         CASE cq.company_size
           WHEN 'small'        THEN '소기업'
           WHEN 'medium'       THEN '중기업'
           WHEN 'mid_large'    THEN '중견기업'
           WHEN 'conglomerate' THEN '대기업집단'
           ELSE COALESCE(cq.company_size, '(미등록)') END                              AS act_value
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
                                 OR ci.direct_prod_valid_until >= p.today_kst)) AS n_ok,
         -- [D3-b] 품목명 나열
         STRING_AGG(DISTINCT COALESCE(i.item_name, i.name_raw), ', ')                 AS req_names,
         STRING_AGG(DISTINCT COALESCE(i.item_name, i.name_raw), ', ')
           FILTER (WHERE ci.item_code IS NOT NULL AND ci.has_direct_production
                     AND (ci.direct_prod_valid_until IS NULL
                          OR ci.direct_prod_valid_until >= p.today_kst))              AS ok_names
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
         '직생확인 ' || n_ok || '/' || n_req AS detail,
         COALESCE(left(req_names, 300), '(미해석)')                                    AS req_value,
         COALESCE(left(ok_names, 300), '(없음)')                                       AS act_value
  FROM dp_rows
),

-- ═══════════════ ④b 품목 등록 (supp) ═══════════════
item_rows AS (
  SELECT i.bid_ntce_no, i.bid_ntce_ord,
         COUNT(*) AS n_req,
         COUNT(*) FILTER (WHERE i.item_code IS NULL)      AS n_unres,
         COUNT(*) FILTER (WHERE ci.item_code IS NOT NULL) AS n_ok,
         STRING_AGG(DISTINCT COALESCE(i.item_name, i.name_raw), ', ')                 AS req_names,
         STRING_AGG(DISTINCT COALESCE(i.item_name, i.name_raw), ', ')
           FILTER (WHERE ci.item_code IS NOT NULL)                                    AS ok_names
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
         '품목 등록 ' || n_ok || '/' || n_req AS detail,
         COALESCE(left(req_names, 300), '(미해석)')                                    AS req_value,
         COALESCE(left(ok_names, 300), '(없음)')                                       AS act_value
  FROM item_rows
),

-- ═══════════════ ⑤ 인력 (supp) — M1 rank 반영 ═══════════════
comp_person_total AS (
  SELECT COALESCE(SUM(cp.headcount),0) AS total
  FROM company_personnel cp CROSS JOIN params p WHERE cp.company_id = p.company_id
),
pers_eval AS (
  SELECT r.bid_ntce_no, r.bid_ntce_ord,
    -- [D3-b] 표시 라벨. grade_raw(원문) 우선 — '중급기술자 이상' 처럼 등급 조건이 살아 있다.
    left(COALESCE(NULLIF(r.grade_raw,''), m.qual_name, r.qual_name, r.role_field, '인력'), 40)
      || ' ' || r.headcount || '명' AS label,
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
         '인력 요건 ' || COUNT(*) FILTER (WHERE met) || '/' || COUNT(*) AS detail,
         COALESCE(left(STRING_AGG(label, ', '), 300), '(미해석)')                      AS req_value,
         COALESCE(left(STRING_AGG(label, ', ') FILTER (WHERE met), 300), '(없음)')     AS act_value
  FROM pers_eval GROUP BY 1,2
),

-- ═══════════════ ⑥ 실적 (supp) ═══════════════
perf_rows AS (
  SELECT r.id, r.bid_ntce_no, r.bid_ntce_ord,
         -- [D3-b] 이름 컬럼이 없어 필드에서 조립한다. category_raw(분야) + 기간 + 금액/건수.
         CASE
           WHEN r.min_value IS NULL OR r.unit IS NULL OR r.parse_status = 'unparsed'
             THEN COALESCE(left(NULLIF(r.category_raw,''), 40), '(미해석 실적요건)')
           ELSE COALESCE(left(NULLIF(r.category_raw,''), 40) || ' ', '')
                || '최근 ' || r.period_years || '년 '
                || CASE WHEN r.unit = '원' AND r.min_value >= 100000000
                          THEN trim(to_char(r.min_value / 100000000.0, 'FM9999990.9')) || '억원'
                        WHEN r.unit = '원'
                          THEN to_char(r.min_value::numeric, 'FM999,999,999,999') || '원'
                        ELSE to_char(r.min_value::numeric, 'FM999999999') || '건' END
                || CASE WHEN r.agg_type = 'sum' THEN ' (합산)' ELSE '' END
         END AS label,
         CASE
           WHEN r.min_value IS NULL OR r.unit IS NULL OR r.parse_status = 'unparsed' THEN NULL
           WHEN r.unit = '원' AND COALESCE(r.agg_type,'single') = 'single' THEN
             (SELECT COALESCE(MAX(pr.contract_amt),0) FROM company_performance_records pr, params p
               WHERE pr.company_id = p.company_id
                 AND pr.end_date >= p.today_kst - (r.period_years * INTERVAL '1 year')
                 AND (r.field_code IS NULL OR pr.field_code = r.field_code)) >= r.min_value
           WHEN r.unit = '원' THEN
             (SELECT COALESCE(SUM(pr.contract_amt),0) FROM company_performance_records pr, params p
               WHERE pr.company_id = p.company_id
                 AND pr.end_date >= p.today_kst - (r.period_years * INTERVAL '1 year')
                 AND (r.field_code IS NULL OR pr.field_code = r.field_code)) >= r.min_value
           WHEN r.unit = '건' THEN
             (SELECT COUNT(*) FROM company_performance_records pr, params p
               WHERE pr.company_id = p.company_id
                 AND pr.end_date >= p.today_kst - (r.period_years * INTERVAL '1 year')
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
         '실적 요건 ' || COUNT(*) FILTER (WHERE met) || '/' || COUNT(*) AS detail,
         COALESCE(left(STRING_AGG(label, ', '), 300), '(미해석)')                      AS req_value,
         COALESCE(left(STRING_AGG(label, ', ') FILTER (WHERE met), 300), '(없음)')     AS act_value
  FROM perf_rows GROUP BY 1,2
),

-- ═══════════════ ⑦ 시공능력 (supp) ═══════════════
cap_rows AS (
  SELECT r.id, r.bid_ntce_no, r.bid_ntce_ord,
         -- [D3-b] 업종(license_master) + 금액. license_code NULL 이면 총액 요건.
         COALESCE(lm.license_name, '총액') || ' '
           || CASE WHEN r.min_value IS NULL THEN '(미해석)'
                   WHEN r.min_value >= 100000000
                     THEN trim(to_char(r.min_value / 100000000.0, 'FM9999990.9')) || '억원'
                   ELSE to_char(r.min_value::numeric, 'FM999,999,999,999') || '원' END AS label,
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
  LEFT JOIN license_master lm ON lm.license_code = r.license_code
),
ax_capacity AS (
  SELECT bid_ntce_no, bid_ntce_ord, 'capacity' AS axis, 'supp' AS class,
         CASE WHEN BOOL_AND(met)         THEN '충족'
              WHEN BOOL_OR(met IS FALSE) THEN '미충족'
              ELSE '확인필요' END AS status,
         '시공능력 ' || COUNT(*) FILTER (WHERE met) || '/' || COUNT(*) AS detail,
         COALESCE(left(STRING_AGG(label, ', '), 300), '(미해석)')                      AS req_value,
         COALESCE(left(STRING_AGG(label, ', ') FILTER (WHERE met), 300), '(없음)')     AS act_value
  FROM cap_rows GROUP BY 1,2
),

-- ═══════════════ ⑧ 신용 (supp) — 위양성 가드 ═══════════════
-- credit_rating_req 근거 스니펫만 뽑는다. extraction_evidence 는 두 세대로 shape 가 다르다:
--   구(백필) = [{page, field, snippet}, ...]        · 신(일일) = {field: [{page, snippet, ...}]}
credit_ev AS (
  SELECT lb.bid_ntce_no, lb.bid_ntce_ord, e->>'snippet' AS snip
  FROM live_bids lb
  JOIN bid_table bt ON bt.bid_ntce_no = lb.bid_ntce_no AND bt.bid_ntce_ord = lb.bid_ntce_ord,
       LATERAL jsonb_array_elements(bt.extraction_evidence) AS e
  WHERE jsonb_typeof(bt.extraction_evidence) = 'array'
    AND e->>'field' = 'credit_rating_req'
  UNION ALL
  SELECT lb.bid_ntce_no, lb.bid_ntce_ord, e->>'snippet'
  FROM live_bids lb
  JOIN bid_table bt ON bt.bid_ntce_no = lb.bid_ntce_no AND bt.bid_ntce_ord = lb.bid_ntce_ord,
       LATERAL jsonb_array_elements(bt.extraction_evidence -> 'credit_rating_req') AS e
  WHERE jsonb_typeof(bt.extraction_evidence) = 'object'
    AND jsonb_typeof(bt.extraction_evidence -> 'credit_rating_req') = 'array'
),
-- 근거가 전부 결격사유/입찰보증금 조항인 공고 = 신용등급 요구가 아님(위양성)
credit_fp AS (
  SELECT bid_ntce_no, bid_ntce_ord
  FROM credit_ev
  GROUP BY 1,2
  HAVING BOOL_AND(snip ~ '(채무불이행|금융질서\s*문란|신용정보\s*관리규약)')
),
ax_credit AS (
  SELECT c.bid_ntce_no, c.bid_ntce_ord, 'credit' AS axis, 'supp' AS class,
         CASE WHEN fp.bid_ntce_no IS NOT NULL THEN '충족'   -- ← 가드: 위양성. 지우면 원복.
              WHEN NOT c.required THEN '충족'
              WHEN c.min_grade IS NOT NULL THEN '확인필요'   -- v1 도달 불가(min_grade 전건 NULL)
              WHEN cq.credit_rating IS NOT NULL THEN '충족'
              ELSE '미충족' END AS status,
         CASE WHEN fp.bid_ntce_no IS NOT NULL THEN '신용등급 요구 아님 (결격사유 오인식 보정)'
              WHEN c.min_grade IS NOT NULL THEN '요구등급 ' || c.min_grade || ' (v1 비교불가)'
              WHEN cq.credit_rating IS NOT NULL THEN '신용평가 보유(' || cq.credit_rating || ')'
              ELSE '신용평가 미보유' END AS detail,
         CASE WHEN fp.bid_ntce_no IS NOT NULL THEN '(요구 없음)'
              WHEN NOT c.required          THEN '(요구 없음)'
              WHEN c.min_grade IS NOT NULL THEN c.min_grade
              ELSE '신용평가등급 보유' END                                             AS req_value,
         COALESCE(cq.credit_rating, '(미보유)')                                        AS act_value
  FROM bid_require_credit c
  JOIN live_bids lb ON lb.bid_ntce_no = c.bid_ntce_no AND lb.bid_ntce_ord = c.bid_ntce_ord
  LEFT JOIN credit_fp fp ON fp.bid_ntce_no = c.bid_ntce_no AND fp.bid_ntce_ord = c.bid_ntce_ord
  LEFT JOIN comp_qual cq ON TRUE
),

-- ═══════════════ ⑨ 인증 (info) ═══════════════
cert_rows AS (
  SELECT r.bid_ntce_no, r.bid_ntce_ord,
         COUNT(*) AS n_req,
         COUNT(*) FILTER (WHERE r.cert_code IS NULL) AS n_unres,
         COUNT(*) FILTER (WHERE cc.cert_code IS NOT NULL
                            AND (cc.valid_until IS NULL OR cc.valid_until >= p.today_kst)) AS n_ok,
         STRING_AGG(DISTINCT r.cert_name, ', ')
           FILTER (WHERE r.cert_code IS NOT NULL AND cc.cert_code IS NULL) AS missing_names,
         STRING_AGG(DISTINCT COALESCE(r.cert_name, r.name_raw), ', ')                 AS req_names,
         STRING_AGG(DISTINCT COALESCE(r.cert_name, r.name_raw), ', ')
           FILTER (WHERE cc.cert_code IS NOT NULL
                     AND (cc.valid_until IS NULL OR cc.valid_until >= p.today_kst))   AS ok_names
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
           || COALESCE(' · 취득하면 가능: ' || missing_names, '') AS detail,
         COALESCE(left(req_names, 300), '(미해석)')                                    AS req_value,
         COALESCE(left(ok_names, 300), '(없음)')                                       AS act_value
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
             'axis', axis, 'class', class, 'status', status, 'detail', detail,
             'required', req_value, 'actual', act_value)
           ORDER BY CASE class WHEN 'gate' THEN 1 WHEN 'supp' THEN 2 ELSE 3 END, axis) AS axes
  FROM axis_all GROUP BY 1,2
)
SELECT p.company_id,
       s.bid_ntce_no, s.bid_ntce_ord,
       CASE WHEN COALESCE(b.required,0) = 0    THEN '확인필요'   -- [축0개] 아래 주석
            WHEN COALESCE(b.gate_failed,0) > 0 THEN '불가'
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
$function$
;

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