-- ============================================================================
-- 005: [일회성 정비 — 2026-07-21 실행 완료, 기록 보존용]
-- bid_table jsonb 필드 JSON null('null'::jsonb) → SQL NULL 정리
--
-- 배경: 7/15 09:32 일회성 벌크 병합(정기 배치 db.py 아님)이 미발견 값을
--   JSON null로 저장 → IS NOT NULL 필터를 통과해 채움률 집계·매칭 오염.
--   상세: 노션 트러블슈팅 > "bid_table jsonb 필드 JSON null 오염"
-- 결과: 총 92,741건 정리 (licenses 4,803 / item 7,658 / region 15,030 /
--   perf 16,335 / cap 17,505 / pers 16,771 / certs 14,639), 정리 후 0건 확인
-- 재발 방지: bid_table 쓰기는 merge/db.py 경유만 (협의 안건 6번)
-- 멱등: WHERE 조건상 재실행 안전 (이미 정리된 행은 미대상)
-- ============================================================================

UPDATE bid_table SET required_licenses  = NULL WHERE jsonb_typeof(required_licenses)  = 'null';
UPDATE bid_table SET item_codes         = NULL WHERE jsonb_typeof(item_codes)         = 'null';
UPDATE bid_table SET region_limit_names = NULL WHERE jsonb_typeof(region_limit_names) = 'null';
UPDATE bid_table SET performance_reqs   = NULL WHERE jsonb_typeof(performance_reqs)   = 'null';
UPDATE bid_table SET capacity_reqs      = NULL WHERE jsonb_typeof(capacity_reqs)      = 'null';
UPDATE bid_table SET personnel_reqs     = NULL WHERE jsonb_typeof(personnel_reqs)     = 'null';
UPDATE bid_table SET required_certs     = NULL WHERE jsonb_typeof(required_certs)     = 'null';

-- 검증 (전부 0이어야 함)
-- SELECT
--   COUNT(*) FILTER (WHERE jsonb_typeof(required_licenses)  = 'null') AS lic,
--   COUNT(*) FILTER (WHERE jsonb_typeof(item_codes)         = 'null') AS item,
--   COUNT(*) FILTER (WHERE jsonb_typeof(region_limit_names) = 'null') AS region,
--   COUNT(*) FILTER (WHERE jsonb_typeof(performance_reqs)   = 'null') AS perf,
--   COUNT(*) FILTER (WHERE jsonb_typeof(capacity_reqs)      = 'null') AS cap,
--   COUNT(*) FILTER (WHERE jsonb_typeof(personnel_reqs)     = 'null') AS pers,
--   COUNT(*) FILTER (WHERE jsonb_typeof(required_certs)     = 'null') AS certs
-- FROM bid_table;