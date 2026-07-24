-- ============================================================================
-- BidMate 자격매칭 스키마 v2.4 — 회원 측(company) + 매칭 결과  [백엔드 전달용]
--   ⚠️ 실행 금지: companies는 백엔드 소유(Alembic). 이 파일은 합의·반영용 제안 DDL.
--   2026-07-23. 100b를 v2.4 확정본으로 통합.
--
-- 대칭: 공고 측 bid_require_* 9축과 회원 측 보유 데이터가 코드로 매칭.
--       규모·신용은 공고 측만 자식 테이블화, 회원 측은 company_qualifications 1:1 컬럼 유지
--       (회사 속성은 본래 1:1이므로 위성 테이블 불필요 — 비대칭이 정상).
-- 표준명 병기: 회원 테이블도 code+이름 함께 저장 (공고 측 bid_require_*와 대칭, 뷰 폐지).
--   이름은 매칭 키가 아닌 표시용 — 코드가 정본, 이름은 쓰기 시점에 마스터에서 채운다.
-- ============================================================================

-- 계정 (백엔드 확정)
CREATE TABLE IF NOT EXISTS companies (
  id BIGSERIAL PRIMARY KEY,
  name VARCHAR(100) NOT NULL,
  biz_reg_no VARCHAR(10) UNIQUE,
  email VARCHAR(200),
  cognito_sub VARCHAR(100) UNIQUE,
  created_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'Asia/Seoul'),
  updated_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'Asia/Seoul')
);

-- 자격 스칼라 (1:1) — 규모·신용의 회원 측 보유값이 여기 (공고 측 bid_require_size/credit와 매칭)
CREATE TABLE IF NOT EXISTS company_qualifications (
  company_id BIGINT PRIMARY KEY REFERENCES companies(id) ON DELETE CASCADE,
  company_size VARCHAR(20) NOT NULL CHECK (company_size IN ('small','medium','mid_large','conglomerate')),
  credit_rating VARCHAR(10),                    -- 신용등급 (bid_require_credit와 매칭. v1은 보유 여부)
  created_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'Asia/Seoul'),
  updated_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'Asia/Seoul')
);

-- 보유 면허
CREATE TABLE IF NOT EXISTS company_licenses (
  company_id BIGINT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  license_code VARCHAR(20) NOT NULL REFERENCES license_master(license_code),
  license_name VARCHAR(100),                    -- 표준명 병기 (쓰기 시 마스터에서 채움)
  PRIMARY KEY (company_id, license_code)
);

-- 소재지 (본점 hq + 지사 branch 통합, hq는 회사당 1행)
CREATE TABLE IF NOT EXISTS company_regions (
  company_id BIGINT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  region_code VARCHAR(10) NOT NULL REFERENCES region_master(region_code),
  region_name VARCHAR(60),                       -- 표준명 병기
  region_type VARCHAR(10) NOT NULL CHECK (region_type IN ('hq','branch')),
  PRIMARY KEY (company_id, region_code)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_company_regions_hq ON company_regions (company_id) WHERE region_type='hq';

-- 기술인력
CREATE TABLE IF NOT EXISTS company_personnel (
  company_id BIGINT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  qual_code VARCHAR(20) NOT NULL REFERENCES personnel_grade_master(qual_code),
  qual_name VARCHAR(100),                        -- 표준명 병기
  headcount SMALLINT NOT NULL CHECK (headcount > 0),
  PRIMARY KEY (company_id, qual_code)
);

-- 조달등록 품목 + 직접생산확인
CREATE TABLE IF NOT EXISTS company_items (
  company_id BIGINT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  item_code VARCHAR(10) NOT NULL REFERENCES item_code_master(item_code),
  item_name VARCHAR(200),                        -- 표준명 병기
  has_direct_production BOOLEAN NOT NULL DEFAULT FALSE,
  direct_prod_valid_until DATE,
  PRIMARY KEY (company_id, item_code)
);

-- 실적 대장 (건별 — 집계는 매칭 시점)
CREATE TABLE IF NOT EXISTS company_performance_records (
  record_id BIGSERIAL PRIMARY KEY,
  company_id BIGINT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  contract_name VARCHAR(200) NOT NULL,
  field_code VARCHAR(20) REFERENCES license_master(license_code),
  field_name VARCHAR(100),                       -- 분야 표준명 병기
  contract_amt BIGINT NOT NULL CHECK (contract_amt >= 0),
  end_date DATE NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cpr_company ON company_performance_records (company_id, end_date);

-- 보유 인증 (유효기간)
CREATE TABLE IF NOT EXISTS company_certs (
  company_id BIGINT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  cert_code VARCHAR(30) NOT NULL REFERENCES cert_master(cert_code),
  cert_name VARCHAR(100),                        -- 표준명 병기
  valid_until DATE,
  PRIMARY KEY (company_id, cert_code)
);

-- 업종별 시공능력평가액
CREATE TABLE IF NOT EXISTS company_capacity_evals (
  company_id BIGINT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  license_code VARCHAR(20) NOT NULL REFERENCES license_master(license_code),
  license_name VARCHAR(100),                    -- 표준명 병기
  eval_amount BIGINT NOT NULL CHECK (eval_amount >= 0),
  eval_year SMALLINT,
  PRIMARY KEY (company_id, license_code)
);
CREATE INDEX IF NOT EXISTS idx_cce_company ON company_capacity_evals (company_id);

-- (뷰 폐지) 표준명을 각 테이블에 병기 저장하므로 조회용 뷰 불필요 — 공고 측 bid_require_*와 대칭.
--   이름은 표시용이고 코드가 정본이다. 마스터 표준명이 개정되면(드묾) 병기 이름은
--   재동기화 대상 — 쓰기 시 마스터 조인으로 채우고, 필요 시 주기적 갱신 배치로 최신화한다.


-- ═══════════════════════════════════════════════════════════
-- 매칭 결과 = bid↔company 유일 연결점 + "자격 N/M 충족"의 정본
--   충족형 9축: 면허·지역·인력·품목(직생)·실적·인증·시공능력·규모·신용
--   required(M)=요구 축 수, satisfied(N)=충족 축 수 (N≤M)
--   verdict: need_review>0→확인필요 / N=M→추천 / N=0→미달 / else→부분충족
--   지명경쟁·공동수급 여부 배지 = bid_require_summary 컬럼에서 직접 표시 (매칭 미참여)
-- ═══════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS match_results (
  company_id BIGINT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  bid_ntce_no VARCHAR(40) NOT NULL, bid_ntce_ord VARCHAR(10) NOT NULL,
  verdict VARCHAR(20) NOT NULL CHECK (verdict IN ('추천','부분충족','확인필요','미달')),
  satisfied SMALLINT NOT NULL CHECK (satisfied >= 0),
  required SMALLINT NOT NULL CHECK (required >= 0),
  need_review SMALLINT NOT NULL DEFAULT 0,
  axes JSONB NOT NULL,                            -- [{axis, required, satisfied, reason}]
  normalizer_version VARCHAR(10),
  computed_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'Asia/Seoul'),
  PRIMARY KEY (company_id, bid_ntce_no, bid_ntce_ord),
  FOREIGN KEY (bid_ntce_no, bid_ntce_ord) REFERENCES bid_require_summary (bid_ntce_no, bid_ntce_ord) ON DELETE CASCADE,
  CHECK (satisfied <= required)
);
CREATE INDEX IF NOT EXISTS idx_mr_bid ON match_results (bid_ntce_no, bid_ntce_ord);
CREATE INDEX IF NOT EXISTS idx_mr_company ON match_results (company_id, verdict);
-- 화면: SELECT satisfied, required, axes FROM match_results WHERE company_id=? AND bid_id=?