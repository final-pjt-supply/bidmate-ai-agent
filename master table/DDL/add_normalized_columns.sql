-- ============================================================================
-- 008: bid_table 정규화 병렬 컬럼 추가 (P2 Stage 2 선행 DDL)
-- 설계 원칙: LLM 추출 원본(jsonb)은 불변 보존, 정규화 결과는 *_norm에 기록.
--            매칭 쿼리는 *_norm만 읽는다. 재정규화 = *_norm 재계산 (원본 무손실).
-- 멱등: IF NOT EXISTS
-- ============================================================================

ALTER TABLE bid_table ADD COLUMN IF NOT EXISTS required_licenses_norm jsonb;
--  [{ "or_group": "g1", "codes": ["0001","0003"], "method": "rule+alias",
--     "qualifier": "주력분야:기계설비공사", "name_raw": "토목(또는 토목건축)공사업 등록" }]
--  codes 복수 = OR (하나라도 보유 시 충족), 항목 간 = AND. method='ignored'/'none' 항목도 기록.

ALTER TABLE bid_table ADD COLUMN IF NOT EXISTS region_limit_codes jsonb;
--  { "codes": ["41","52110"], "flags": ["nationwide"|"site_ref"], "unmatched": ["원문표기"] }

ALTER TABLE bid_table ADD COLUMN IF NOT EXISTS personnel_reqs_norm jsonb;
--  [{ "field": "토목", "grade_raw": "중급기술자 이상", "qual_codes": ["KGRADE_MID"],
--     "count": 1, "method": "rule+alias" }]   -- qual_codes 빈 배열 + method='special:no_grade' = 인원수만 비교

ALTER TABLE bid_table ADD COLUMN IF NOT EXISTS item_codes_norm jsonb;
--  { "codes": ["1013160101"], "license_routed": ["1169"], "ignored": n }

ALTER TABLE bid_table ADD COLUMN IF NOT EXISTS normalizer_version VARCHAR(10);
ALTER TABLE bid_table ADD COLUMN IF NOT EXISTS normalized_at TIMESTAMP;

CREATE INDEX IF NOT EXISTS idx_bid_normalized ON bid_table (normalizer_version)
  WHERE normalizer_version IS NOT NULL;
