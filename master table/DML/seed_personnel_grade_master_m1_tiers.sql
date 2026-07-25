-- ============================================================================
-- seed: personnel_grade_master — M1 인력 축 보완 (qual_type + 티어 + grade_rank + 별칭)
--   create_matser_table.sql 의 personnel_grade_master 정의(qual_type/grade_rank 포함) 뒤에 적재.
--   멱등(ADD COLUMN IF NOT EXISTS / ON CONFLICT DO NOTHING / NOT EXISTS 가드) — 재실행 안전.
--   → matching_tool/{match_engine_v1,compute_match_results}.sql 의 인력 축이 이 데이터에 의존.
--
-- 설계:
--   * 회사 측 표현 = 직접 신고. company_personnel 스키마 변경 없음 —
--     회사가 ROLE_*/*GRADE_* 티어 코드를 직접 골라 등록(qual_code FK 수용).
--   * 코드 관례: KGRADE_(역량등급)·QGRADE_(자격총칭)·SGRADE_(감리원)
--     → 신규 ROLE_(역할)·DEGREE_(학위)·SKGRADE_(숙련기술자) 프리픽스 추가.
--   * grade_rank = 동일 field(family) 내 등급 순서. 엔진이 '이상'을 rank>= 합산으로 판정.
--
-- v1 한계(의도된 보류):
--   * '이상' rank 비교는 역량등급·감리원·숙련기술자·학위 family 한정.
--     자격수준("기사 이상")·경력("5년 이상")·OR 복합("기술사·기능장")은 v2.
--
-- ★ 쓰기 계약: bid_require_personnel은 건드리지 않는다(정규화 배치가 유일 기록자).
--   별칭 실반영은 normalize_output_adapter.py 재실행으로 이뤄진다.
-- ============================================================================

BEGIN;

-- ═══════════ [A] qual_type 차원 + grade_rank (기배포 DB 마이그레이션, 멱등) ═══════════
--   신규 배포는 create_matser_table.sql 에 이미 컬럼이 있어 no-op.
ALTER TABLE personnel_grade_master
  ADD COLUMN IF NOT EXISTS qual_type VARCHAR(12) NOT NULL DEFAULT 'license';
ALTER TABLE personnel_grade_master DROP CONSTRAINT IF EXISTS chk_pgm_qual_type;
ALTER TABLE personnel_grade_master ADD CONSTRAINT chk_pgm_qual_type
  CHECK (qual_type IN ('license','role','grade','degree'));
ALTER TABLE personnel_grade_master
  ADD COLUMN IF NOT EXISTS grade_rank SMALLINT;

UPDATE personnel_grade_master SET qual_type = 'grade'
WHERE (qual_code LIKE 'KGRADE\_%' OR qual_code LIKE 'QGRADE\_%'
    OR qual_code LIKE 'SGRADE\_%' OR qual_code LIKE 'SKGRADE\_%')
  AND qual_type <> 'grade';

-- ═══════════ [B] 티어 등록 (멱등) ═══════════
INSERT INTO personnel_grade_master (qual_code, qual_name, grade, field, qual_type) VALUES
  -- 역할 (건설기술진흥법·용역 배치기준 관행)
  ('ROLE_RESP_ENG',  '책임기술인',            NULL, '역할', 'role'),
  ('ROLE_PART_ENG',  '참여기술인',            NULL, '역할', 'role'),
  ('ROLE_PM_ENG',    '사업책임기술인',        NULL, '역할', 'role'),
  ('ROLE_SITE_REP',  '현장대리인',            NULL, '역할', 'role'),
  ('ROLE_CHIEF_SUP', '수석감리원',            NULL, '역할', 'role'),
  ('ROLE_SUP',       '감리원',                NULL, '역할', 'role'),
  ('ROLE_QC_ENG',    '품질기술자',            NULL, '역할', 'role'),
  ('ROLE_SAFE_ENG',  '안전기술자',            NULL, '역할', 'role'),
  ('ROLE_ENV_MGR',   '환경담당자',            NULL, '역할', 'role'),
  ('ROLE_TECH_SUP',  '기술지원기술자',        NULL, '역할', 'role'),
  ('ROLE_CM',        '건설사업관리기술인',    NULL, '역할', 'role'),
  ('ROLE_RESP_CM',   '책임건설사업관리기술인',NULL, '역할', 'role'),
  ('ROLE_GEN_ENG',   '일반기술자(등급무관)',  NULL, '역할', 'role'),
  -- 숙련기술자 등급
  ('SKGRADE_JR',     '초급숙련기술자',        '초급', '숙련기술자', 'grade'),
  ('SKGRADE_MID',    '중급숙련기술자',        '중급', '숙련기술자', 'grade'),
  ('SKGRADE_HI',     '고급숙련기술자',        '고급', '숙련기술자', 'grade'),
  -- 학위
  ('DEGREE_PHD',     '박사',                  NULL, '학위', 'degree'),
  ('DEGREE_MS',      '석사',                  NULL, '학위', 'degree'),
  ('DEGREE_BS',      '학사',                  NULL, '학위', 'degree')
ON CONFLICT (qual_code) DO NOTHING;

-- ═══════════ [B2] grade_rank 부여 (family=field 내 순서, 멱등) ═══════════
-- 역량등급 (기존 KGRADE_* — field='역량등급')
UPDATE personnel_grade_master SET grade_rank = CASE grade
  WHEN '초급' THEN 1 WHEN '중급' THEN 2 WHEN '고급' THEN 3 WHEN '특급' THEN 4 END
WHERE qual_code LIKE 'KGRADE\_%';
-- 감리원 (기존 SGRADE_* — field='감리원')
UPDATE personnel_grade_master SET grade_rank = CASE grade
  WHEN '초급' THEN 1 WHEN '중급' THEN 2 WHEN '고급' THEN 3 WHEN '특급' THEN 4 END
WHERE qual_code LIKE 'SGRADE\_%';
-- 숙련기술자 (신규 SKGRADE_* — field='숙련기술자')
UPDATE personnel_grade_master SET grade_rank = CASE grade
  WHEN '초급' THEN 1 WHEN '중급' THEN 2 WHEN '고급' THEN 3 END
WHERE qual_code LIKE 'SKGRADE\_%';
-- 학위 (신규 DEGREE_* — field='학위')
UPDATE personnel_grade_master SET grade_rank = CASE qual_code
  WHEN 'DEGREE_BS' THEN 1 WHEN 'DEGREE_MS' THEN 2 WHEN 'DEGREE_PHD' THEN 3 END
WHERE qual_code LIKE 'DEGREE\_%';

-- ═══════════ [C] 별칭 배치 (미해석 전량 목록 실측 표기, 멱등) ═══════════
-- source='m1_batch'로 구분. (기존 관례: entity_type='personnel', source='manual')
INSERT INTO master_alias (entity_type, alias_text, canonical_code, source)
SELECT 'personnel', v.alias_text, v.code, 'm1_batch'
FROM (VALUES
  -- 책임기술인 계열 (표기 변형 + 축약)
  ('분야별책임기술인','ROLE_RESP_ENG'), ('분야별 책임기술인','ROLE_RESP_ENG'),
  ('분야책임기술인','ROLE_RESP_ENG'),   ('분야별 책임기술자','ROLE_RESP_ENG'),
  ('분야별책임기술자','ROLE_RESP_ENG'), ('책임기술자','ROLE_RESP_ENG'),
  ('책임기술인','ROLE_RESP_ENG'),       ('책임','ROLE_RESP_ENG'),
  -- 사업책임·총괄
  ('사업책임기술인','ROLE_PM_ENG'),     ('사업책임기술자','ROLE_PM_ENG'),
  ('총괄책임자','ROLE_PM_ENG'),         ('총괄기술자','ROLE_PM_ENG'),
  ('총괄책임자(긴급대응반장)','ROLE_PM_ENG'),
  -- 참여기술인 계열
  ('분야별참여기술인','ROLE_PART_ENG'), ('분야별 참여기술인','ROLE_PART_ENG'),
  ('참여기술자','ROLE_PART_ENG'),       ('참여기술인','ROLE_PART_ENG'),
  -- 현장대리인
  ('현장대리인','ROLE_SITE_REP'),       ('현장대리인 겸임','ROLE_SITE_REP'),
  -- 감리원
  ('수석감리원','ROLE_CHIEF_SUP'),      ('감리원','ROLE_SUP'),
  ('상근감리원','ROLE_SUP'),            ('감리원 또는 수석감리원','ROLE_SUP'),
  -- 품질·안전·환경
  ('품질기술자','ROLE_QC_ENG'),         ('품질담당','ROLE_QC_ENG'),
  ('분야별 책임자(품질관리)','ROLE_QC_ENG'),
  ('안전기술자','ROLE_SAFE_ENG'),       ('안전관리담당자','ROLE_SAFE_ENG'),
  ('안전담당','ROLE_SAFE_ENG'),         ('분야별 책임자(안전)','ROLE_SAFE_ENG'),
  ('환경담당자','ROLE_ENV_MGR'),
  -- 건설사업관리(CM)
  ('기술지원기술자','ROLE_TECH_SUP'),   ('기술지원건설사업관리기술인','ROLE_TECH_SUP'),
  ('분야별건설사업관리기술인','ROLE_CM'),('건설사업관리기술인','ROLE_CM'),
  ('책임건설사업관리기술인','ROLE_RESP_CM'),
  -- 일반(등급무관) 계열
  ('일반기술자','ROLE_GEN_ENG'),        ('기술자','ROLE_GEN_ENG'),
  ('일반','ROLE_GEN_ENG'),              ('분야별 기술자','ROLE_GEN_ENG'),
  -- 역량등급 ('이상' 요구 = 해당 등급 코드로 별칭 → 엔진이 rank>= 합산으로 '이상' 판정)
  ('안전관리(초급) 이상','KGRADE_JR'),  ('안전관리 초급 이상','KGRADE_JR'),
  ('건설기술자 중 중급기술인 이상','KGRADE_MID'),
  ('건설기술인 중 해당 직무분야의 중급기술인 이상','KGRADE_MID'),
  ('건설기술자 중 해당 직무분야의 중급기술자 이상','KGRADE_MID'),
  ('증급','KGRADE_MID'),                -- 원문 오탈자(중급)
  -- 감리사 표기 등급
  ('특급(수석감리사)','SGRADE_SP'),     ('고급(감리사)','SGRADE_HI'),
  ('중급(감리사)','SGRADE_MID'),        ('초급(감리사보)','SGRADE_JR'),
  -- 숙련기술자 등급
  ('초급숙련기술자','SKGRADE_JR'),      ('중급숙련기술자','SKGRADE_MID'),
  ('고급숙련기술자','SKGRADE_HI'),
  -- 학위
  ('박사','DEGREE_PHD'),               ('박사학위소지','DEGREE_PHD'),
  ('석사','DEGREE_MS'),                ('석·박사급 이상','DEGREE_MS'),
  ('학사','DEGREE_BS'),                ('학사학위 이상','DEGREE_BS'),
  ('학사급 이상','DEGREE_BS'),         ('대학 졸업학력 이상','DEGREE_BS')
) AS v(alias_text, code)
WHERE NOT EXISTS (
  SELECT 1 FROM master_alias ma
  WHERE ma.entity_type = 'personnel' AND ma.alias_text = v.alias_text
);

COMMIT;

-- ═══════════ [검증] rank 부여 확인 (쓰기 없음) ═══════════
--   SELECT field AS family, grade, grade_rank, count(*)
--   FROM personnel_grade_master
--   WHERE qual_type = 'grade' AND grade_rank IS NOT NULL
--   GROUP BY 1,2,3 ORDER BY 1,3;
--
-- [실반영] 별칭은 정규화 어댑터 재실행(normalize_output_adapter.py)으로 bid_require_personnel에 적용:
--   DRY_RUN=1 → 대상·행 수 확인 후 DRY_RUN=0 재적재(공고별 멱등).
