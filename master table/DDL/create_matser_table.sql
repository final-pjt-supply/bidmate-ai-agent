-- ============================================================================
-- 001: 마스터 테이블 4종 DDL (2026-07-21 RDS 적용 완료)
-- 목적: 자격 매칭용 표준 어휘 — 공고(LLM 추출 정규화)와 회사 프로필이 공통 참조
-- 설계 근거: 노션 의사결정사항 > 마스터 테이블 설계
-- 멱등: IF NOT EXISTS — 재실행 안전
-- ============================================================================

-- 면허/등록 마스터 (회사가 갖는 사업 자격)
CREATE TABLE IF NOT EXISTS license_master (
  license_code   VARCHAR(20) PRIMARY KEY,   -- 나라장터 업종코드(indstrytyCd) 차용 예정
  license_name   VARCHAR(100) NOT NULL,     -- 표준명
  category       VARCHAR(30) NOT NULL,      -- 법분야 분류
  law_basis      VARCHAR(100),              -- 근거법령
  parent_code    VARCHAR(20) REFERENCES license_master,
  is_active      BOOLEAN DEFAULT TRUE       -- 업종 폐지 대응
);

-- 행정구역 마스터 (지역제한 계층 매칭)
CREATE TABLE IF NOT EXISTS region_master (
  region_code    VARCHAR(10) PRIMARY KEY,   -- 법정동코드 기반 (시도 2자리/시군구 5자리)
  region_name    VARCHAR(50) NOT NULL,
  region_level   SMALLINT NOT NULL,         -- 1=시도, 2=시군구
  parent_code    VARCHAR(10) REFERENCES region_master  -- 시군구→시도 (계층 통과 매칭용)
);

-- 기술인력 자격/등급 마스터 (개인이 갖는 자격 — 인원수 비교용)
CREATE TABLE IF NOT EXISTS personnel_grade_master (
  qual_code      VARCHAR(20) PRIMARY KEY,   -- 행정표준코드 '국가자격면허' 코드
  qual_name      VARCHAR(100) NOT NULL,
  grade          VARCHAR(20),               -- 기술사/기능장/산업기사/기사/기능사/역량등급
  field          VARCHAR(50)                -- 자격명 어간 (분야)
);

-- 물품코드 마스터
CREATE TABLE IF NOT EXISTS item_code_master (
  item_code      VARCHAR(10) PRIMARY KEY,   -- 8자리=품명 / 10자리=세부품명
  item_name      VARCHAR(300) NOT NULL,
  parent_code    VARCHAR(10),               -- 10자리 → 앞 8자리
  is_active      BOOLEAN DEFAULT TRUE,      -- useYn
  is_sme_product BOOLEAN DEFAULT FALSE      -- 중기간 경쟁제품 (별도 갱신)
);

-- 인증 마스터 (회사가 갖는 인증 — 유효기간 비교용)
CREATE TABLE IF NOT EXISTS cert_master (
  cert_code   VARCHAR(30) PRIMARY KEY,       -- 공식 코드 부재 → 자체 시맨틱 코드
  cert_name   VARCHAR(100) NOT NULL,
  category    VARCHAR(30)  NOT NULL,
  issuer      VARCHAR(100),
  law_basis   VARCHAR(150),
  is_active   BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE INDEX IF NOT EXISTS idx_cert_category ON cert_master (category);


-- 통합 별칭 테이블 (표기 변형 → 표준 코드 번역 사전, 3개 도메인 공용)
CREATE TABLE IF NOT EXISTS master_alias (
  entity_type    VARCHAR(20) NOT NULL,      -- 'license' / 'region' / 'personnel'
  alias_text     VARCHAR(200) NOT NULL,
  canonical_code VARCHAR(20) NOT NULL,
  source         VARCHAR(20) DEFAULT 'manual',  -- manual / llm / rule
  PRIMARY KEY (entity_type, alias_text)
);


