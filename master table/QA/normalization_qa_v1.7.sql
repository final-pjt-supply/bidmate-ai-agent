-- ============================================================================
-- BidMate 정규화 품질 검증 (v1.7 재정규화 후)  [RDS 조회 · DBeaver, 쓰기 없음]
--   2026-07-24. 런북 §3 "정규화 후 검증" + M1 인력 성과 + M6 대량행 스팟체크.
--
-- ⚠ 선행: v1.6 잔존 0 (재정규화 완료) 후 실행. 섞여 있으면 지표가 혼탁.
--
-- 기준선(핸드오버, 재정규화 전) — 회귀 판정용:
--   해석률(코드 NON-NULL 기준): 지역 96.8% · 면허 91.1% · 품목 84.2% · 인증 19.9%
--   인력: 실질 88% (진짜 미해석 12%). 인증 낮은 건 정상(대부분 비인증 제출서류).
--   각 지표가 기준선 ±1~2%p 안이면 정합. 크게 떨어지면 어댑터/별칭 회귀 의심.
-- ============================================================================


-- ═══════════ [A] 버전·무결성 정합 ═══════════

-- A1. 버전 분포 — 전건 v1.7 여야 함 (v1.6 잔존 = 재정규화 미완료)
SELECT normalizer_version, count(*)
FROM bid_require_summary GROUP BY 1 ORDER BY 2 DESC;

-- A2. 고아 자식행 (FK CASCADE라 0이 정상 — 방어적 확인)
SELECT 'licenses' t, count(*) orphan FROM bid_require_licenses c
  LEFT JOIN bid_require_summary s USING (bid_ntce_no,bid_ntce_ord) WHERE s.bid_ntce_no IS NULL
UNION ALL SELECT 'regions',    count(*) FROM bid_require_regions c
  LEFT JOIN bid_require_summary s USING (bid_ntce_no,bid_ntce_ord) WHERE s.bid_ntce_no IS NULL
UNION ALL SELECT 'personnel',  count(*) FROM bid_require_personnel c
  LEFT JOIN bid_require_summary s USING (bid_ntce_no,bid_ntce_ord) WHERE s.bid_ntce_no IS NULL
UNION ALL SELECT 'items',      count(*) FROM bid_require_items c
  LEFT JOIN bid_require_summary s USING (bid_ntce_no,bid_ntce_ord) WHERE s.bid_ntce_no IS NULL
UNION ALL SELECT 'certs',      count(*) FROM bid_require_certs c
  LEFT JOIN bid_require_summary s USING (bid_ntce_no,bid_ntce_ord) WHERE s.bid_ntce_no IS NULL;

-- A3. 축별 총 행수 (재정규화 전후 대조 — 급감/급증 없어야. 전: summary 18,978 / 총행 86,100)
SELECT 'summary' t, count(*) FROM bid_require_summary
UNION ALL SELECT 'licenses',     count(*) FROM bid_require_licenses
UNION ALL SELECT 'regions',      count(*) FROM bid_require_regions
UNION ALL SELECT 'personnel',    count(*) FROM bid_require_personnel
UNION ALL SELECT 'items',        count(*) FROM bid_require_items
UNION ALL SELECT 'performances', count(*) FROM bid_require_performances
UNION ALL SELECT 'certs',        count(*) FROM bid_require_certs
UNION ALL SELECT 'capacity',     count(*) FROM bid_require_capacity
UNION ALL SELECT 'size',         count(*) FROM bid_require_size
UNION ALL SELECT 'credit',       count(*) FROM bid_require_credit
UNION ALL SELECT 'region_duty',  count(*) FROM bid_require_region_duty
ORDER BY 1;


-- ═══════════ [B] 축별 해석률 — 기준선 회귀 판정 (핵심) ═══════════
-- resolved = 마스터 코드 NON-NULL. 기준선과 대조.
SELECT 'license'  AS axis, count(*) AS tot,
       count(*) FILTER (WHERE license_code IS NOT NULL) AS resolved,
       round(100.0*count(*) FILTER (WHERE license_code IS NOT NULL)/NULLIF(count(*),0),1) AS pct,
       91.1 AS 기준선
FROM bid_require_licenses
UNION ALL
SELECT 'region', count(*), count(*) FILTER (WHERE region_code IS NOT NULL),
       round(100.0*count(*) FILTER (WHERE region_code IS NOT NULL)/NULLIF(count(*),0),1), 96.8
FROM bid_require_regions
UNION ALL
SELECT 'item', count(*), count(*) FILTER (WHERE item_code IS NOT NULL),
       round(100.0*count(*) FILTER (WHERE item_code IS NOT NULL)/NULLIF(count(*),0),1), 84.2
FROM bid_require_items
UNION ALL
SELECT 'cert', count(*), count(*) FILTER (WHERE cert_code IS NOT NULL),
       round(100.0*count(*) FILTER (WHERE cert_code IS NOT NULL)/NULLIF(count(*),0),1), 19.9
FROM bid_require_certs
UNION ALL
SELECT 'capacity(업종)', count(*), count(*) FILTER (WHERE license_code IS NOT NULL),
       round(100.0*count(*) FILTER (WHERE license_code IS NOT NULL)/NULLIF(count(*),0),1), NULL
FROM bid_require_capacity
ORDER BY axis;


-- ═══════════ [C] 인력 축 — M1 성과 상세 ═══════════

-- C1. 해석 상태 4분류 (진짜미해석 = 기준선 대비 줄어야 함. 전: 716)
SELECT count(*) AS tot,
       count(*) FILTER (WHERE qual_code IS NOT NULL)                         AS 코드해석,
       count(*) FILTER (WHERE qual_code IS NULL AND method <> 'none')        AS 등급무관_정상,
       count(*) FILTER (WHERE qual_code IS NULL AND method =  'none')        AS 진짜미해석,
       round(100.0*count(*) FILTER (WHERE qual_code IS NOT NULL
              OR (qual_code IS NULL AND method<>'none'))/NULLIF(count(*),0),1) AS 실질해석률_pct
FROM bid_require_personnel;

-- C2. qual_type 분포 — role/grade/degree가 실제로 붙었나 (M1 신규 티어 반영)
SELECT m.qual_type, count(*)
FROM bid_require_personnel p JOIN personnel_grade_master m USING (qual_code)
GROUP BY 1 ORDER BY 2 DESC;

-- C3. M1 신규 티어(role/grade/degree)로 해석된 상위 코드
SELECT p.qual_code, m.qual_name, m.qual_type, count(*)
FROM bid_require_personnel p JOIN personnel_grade_master m USING (qual_code)
WHERE m.qual_type IN ('role','grade','degree')
GROUP BY 1,2,3 ORDER BY 4 DESC LIMIT 25;

-- C4. 남은 진짜 미해석 상위 (다음 별칭 배치 워크리스트)
SELECT grade_raw, count(*)
FROM bid_require_personnel
WHERE qual_code IS NULL AND method = 'none' AND grade_raw IS NOT NULL
GROUP BY 1 ORDER BY 2 DESC LIMIT 30;


-- ═══════════ [D] method 분포 — 해석 경로 건전성 ═══════════
-- rule0/alias/rule+alias/none. M1 후 personnel의 alias 비중이 올라야 정상.
SELECT 'license' axis, method, count(*) FROM bid_require_licenses  GROUP BY 1,2
UNION ALL SELECT 'region',    method, count(*) FROM bid_require_regions   GROUP BY 1,2
UNION ALL SELECT 'personnel', method, count(*) FROM bid_require_personnel GROUP BY 1,2
UNION ALL SELECT 'item',      method, count(*) FROM bid_require_items     GROUP BY 1,2
UNION ALL SELECT 'cert',      method, count(*) FROM bid_require_certs     GROUP BY 1,2
ORDER BY axis, 3 DESC;


-- ═══════════ [E] 교차 라우팅 검증 (나라장터 관행 — 면허칸↔품목칸) ═══════════
-- source: 면허 테이블에 item_field 유입(품목칸 업종→면허), 품목 테이블에 license_field 유입(면허칸 물품→품목)
SELECT 'licenses' t, source, count(*) FROM bid_require_licenses GROUP BY 1,2
UNION ALL SELECT 'items', source, count(*) FROM bid_require_items GROUP BY 1,2
ORDER BY 1,3 DESC;


-- ═══════════ [F] FK 강등 원칙 — name_raw 보존 (미해석도 원문 살아야) ═══════════
-- 미해석(code NULL)인데 name_raw 비었으면 원문 유실 = 문제. 전부 0 이어야 정상.
SELECT 'licenses' t, count(*) 원문유실 FROM bid_require_licenses
  WHERE license_code IS NULL AND (name_raw IS NULL OR btrim(name_raw)='')
UNION ALL SELECT 'regions', count(*) FROM bid_require_regions
  WHERE region_code IS NULL AND (name_raw IS NULL OR btrim(name_raw)='')
UNION ALL SELECT 'items', count(*) FROM bid_require_items
  WHERE item_code IS NULL AND (name_raw IS NULL OR btrim(name_raw)='')
UNION ALL SELECT 'certs', count(*) FROM bid_require_certs
  WHERE cert_code IS NULL AND (name_raw IS NULL OR btrim(name_raw)='');


-- ═══════════ [G] 실적·시공능력 파싱 상태 ═══════════
SELECT 'performance' t, parse_status, count(*) FROM bid_require_performances GROUP BY 1,2
UNION ALL SELECT 'capacity', parse_status, count(*) FROM bid_require_capacity GROUP BY 1,2
ORDER BY 1,3 DESC;

-- G2. 실적 unit 화이트리스트(원/건) 밖·NULL 비율 (확인필요 유발)
SELECT unit, count(*), agg_type FROM bid_require_performances GROUP BY 1,3 ORDER BY 2 DESC;


-- ═══════════ [H] 규모·신용 enum 정합 ═══════════
SELECT 'size' t, size_limit AS val, count(*) FROM bid_require_size GROUP BY 1,2
UNION ALL SELECT 'credit', COALESCE(min_grade,'(여부만)'), count(*) FROM bid_require_credit GROUP BY 1,2
ORDER BY 1,3 DESC;


-- ═══════════ [I] 대량행 스팟체크 (파싱 폭주 의심 이상치) ═══════════
-- 공고당 축 행수 top. 비정상적으로 크면(예: personnel 22, performances 33) 원문 확인 대상.
SELECT 'personnel' axis, bid_ntce_no, bid_ntce_ord, count(*)
FROM bid_require_personnel GROUP BY 1,2,3 ORDER BY 4 DESC LIMIT 5;
SELECT 'certs' axis, bid_ntce_no, bid_ntce_ord, count(*)
FROM bid_require_certs GROUP BY 1,2,3 ORDER BY 4 DESC LIMIT 5;
SELECT 'regions' axis, bid_ntce_no, bid_ntce_ord, count(*)
FROM bid_require_regions GROUP BY 1,2,3 ORDER BY 4 DESC LIMIT 5;
SELECT 'performances' axis, bid_ntce_no, bid_ntce_ord, count(*)
FROM bid_require_performances GROUP BY 1,2,3 ORDER BY 4 DESC LIMIT 5;


-- ═══════════ [J] 코드 dedup 검증 (한 공고 내 동일 코드 중복) ═══════════
-- 면허: 같은 or_group 내 같은 코드 2행 이상 = dedup 실패 (or_group 다르면 정상)
SELECT bid_ntce_no, bid_ntce_ord, or_group, license_code, count(*)
FROM bid_require_licenses WHERE license_code IS NOT NULL
GROUP BY 1,2,3,4 HAVING count(*) > 1 ORDER BY 5 DESC LIMIT 20;

-- 인력: 같은 (공고, qual_code, role_field) 2행 이상 (중복 요구 — 대개 비정상)
SELECT bid_ntce_no, bid_ntce_ord, qual_code, role_field, count(*)
FROM bid_require_personnel WHERE qual_code IS NOT NULL
GROUP BY 1,2,3,4 HAVING count(*) > 1 ORDER BY 5 DESC LIMIT 20;
