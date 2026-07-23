-- ============================================================================
-- BidMate 자격매칭 스키마 v2.4 — 공고 측(bid_require) + 인증 마스터  [실행 대상]
--   2026-07-23. 100a/100c 반복 파일을 v2.4 확정본으로 통합.
--
-- 원칙: bid_table 무수정 · 관계형 행 단위 · 마스터 FK · 코드+표준명 병기.
-- v2.4: 스칼라 게이트 폐기 — 규모(bid_require_size)·신용(bid_require_credit) 자식 테이블,
--       직생은 bid_require_items.direct_production_req로 흡수. 충족형 9축.
--       지명경쟁·공동수급은 summary '표시전용'(자격매칭 미참여).
--
-- 선행: 마스터 5종 존재 가정 (001/007에서 구축) —
--       license_master · region_master · personnel_grade_master · item_code_master · master_alias.
--       이 파일은 companies 의존 없음 (회원 측은 ddl_v2.4_company_draft.sql).
--
-- ★ 쓰기 계약 (정규화 배치 = 유일 기록자, 멱등):
--   공고 단위 트랜잭션 = DELETE bid_require_summary(no,ord) [CASCADE로 자식 소거]
--                        → INSERT summary → INSERT 자식들.  같은 공고 재실행 시 결과 동일.
--   재정규화 대상 = qual_status IN ('merged','partial') AND
--                   (summary 없음 OR bid_table.merged_at > normalized_at OR 버전 상이).
--   모든 TIMESTAMP는 KST naive.
-- ============================================================================


-- ═══════════════════════════════════════════════════════════
-- [A] cert_master — 인증 마스터 (v2.3 신설). 나머지 4 마스터는 001/007 참조.
--     ⚠ 시드(인증 32+11종 · 별칭)는 cert_master_deploy.sql이 소유·선행 배포한다.
--        이 파일은 테이블 존재만 보장(IF NOT EXISTS). 단독 실행 시 빈 마스터가 되며
--        매칭은 에러 없이 '요구 인증 미해석 → 확인필요'로 동작한다.
-- ═══════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS cert_master (
  cert_code   VARCHAR(30) PRIMARY KEY,       -- 공식 코드 부재 → 자체 시맨틱 코드
  cert_name   VARCHAR(100) NOT NULL,
  category    VARCHAR(30)  NOT NULL,
  issuer      VARCHAR(100),
  law_basis   VARCHAR(150),
  is_active   BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE INDEX IF NOT EXISTS idx_cert_category ON cert_master (category);



-- ═══════════════════════════════════════════════════════════
-- [B] bid_require_* — 공고 자격요건 (정규화 배치가 유일 기록자)
-- ═══════════════════════════════════════════════════════════

-- 앵커: 공고당 1행. 스칼라 게이트 폐기 후 남은 것 = 지역기준·스코어링·표시전용·메타
CREATE TABLE IF NOT EXISTS bid_require_summary (
  bid_ntce_no  VARCHAR(40) NOT NULL,
  bid_ntce_ord VARCHAR(10) NOT NULL,
  bid_id       VARCHAR(60) GENERATED ALWAYS AS (bid_ntce_no || '_' || bid_ntce_ord) STORED,
  bid_category VARCHAR(10) NOT NULL CHECK (bid_category IN ('cnstwk','servc','thng','frgcpt')),
  -- 지역 축 기준 (본점/지사 분기의 근거)
  region_limit_type VARCHAR(20) CHECK (region_limit_type IS NULL OR region_limit_type IN ('hq_location','none')),
  -- 표시전용 (자격매칭 미참여 — 여부만)
  designated_competition BOOLEAN,          -- 지명경쟁 (배지)
  joint_supply_method    VARCHAR(100),     -- 공동수급방식 (배지)
  -- 지역 완화 신호 (게이트 아님)
  region_duty_joint_contract BOOLEAN,
  region_duty_rate           NUMERIC(5,2),
  -- 조건 표시
  joint_venture_allowed BOOLEAN,
  subcontract_allowed   BOOLEAN,
  -- 스코어링 (통과자 랭킹)
  award_cutline_type  VARCHAR(20) CHECK (award_cutline_type IS NULL OR award_cutline_type IN ('score','rate','lowest_price')),
  award_cutline_value NUMERIC,
  tech_weight NUMERIC,  price_weight NUMERIC,
  -- 파이프라인 메타
  normalizer_version VARCHAR(10) NOT NULL,
  normalized_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'Asia/Seoul'),
  PRIMARY KEY (bid_ntce_no, bid_ntce_ord),
  FOREIGN KEY (bid_ntce_no, bid_ntce_ord)
      REFERENCES bid_table (bid_ntce_no, bid_ntce_ord) ON DELETE CASCADE
);
CREATE INDEX        IF NOT EXISTS idx_bqs_cat   ON bid_require_summary (bid_category);
CREATE UNIQUE INDEX IF NOT EXISTS uq_bqs_bid_id ON bid_require_summary (bid_id);

-- ① 면허
CREATE TABLE IF NOT EXISTS bid_require_licenses (
  id BIGSERIAL PRIMARY KEY,
  bid_ntce_no VARCHAR(40) NOT NULL, bid_ntce_ord VARCHAR(10) NOT NULL,
  or_group VARCHAR(20) NOT NULL DEFAULT '1',
  license_code VARCHAR(20) REFERENCES license_master(license_code),  -- NULL=미해석(확인필요)
  license_name VARCHAR(300),                        -- ★표준명 병기
  method VARCHAR(20) NOT NULL,                       -- rule0/alias/rule+alias/none
  source VARCHAR(20) NOT NULL DEFAULT 'license_field' CHECK (source IN ('license_field','item_field')),
  qualifier VARCHAR(200), name_raw TEXT NOT NULL,
  FOREIGN KEY (bid_ntce_no,bid_ntce_ord) REFERENCES bid_require_summary(bid_ntce_no,bid_ntce_ord) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_bql_bid ON bid_require_licenses (bid_ntce_no,bid_ntce_ord);
CREATE INDEX IF NOT EXISTS idx_bql_code ON bid_require_licenses (license_code);

-- ② 지역
CREATE TABLE IF NOT EXISTS bid_require_regions (
  id BIGSERIAL PRIMARY KEY,
  bid_ntce_no VARCHAR(40) NOT NULL, bid_ntce_ord VARCHAR(10) NOT NULL,
  region_code VARCHAR(10) REFERENCES region_master(region_code),     -- NULL=미해석
  region_name VARCHAR(50), method VARCHAR(20) NOT NULL,
  flag VARCHAR(20) CHECK (flag IS NULL OR flag IN ('nationwide','site_ref')),
  name_raw TEXT NOT NULL,
  FOREIGN KEY (bid_ntce_no,bid_ntce_ord) REFERENCES bid_require_summary(bid_ntce_no,bid_ntce_ord) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_bqr_bid ON bid_require_regions (bid_ntce_no,bid_ntce_ord);
CREATE INDEX IF NOT EXISTS idx_bqr_code ON bid_require_regions (region_code);

-- ③ 인력
CREATE TABLE IF NOT EXISTS bid_require_personnel (
  id BIGSERIAL PRIMARY KEY,
  bid_ntce_no VARCHAR(40) NOT NULL, bid_ntce_ord VARCHAR(10) NOT NULL,
  qual_code VARCHAR(20) REFERENCES personnel_grade_master(qual_code),  -- NULL=등급무관/미해석
  qual_name VARCHAR(100), role_field VARCHAR(100),
  headcount SMALLINT NOT NULL DEFAULT 1 CHECK (headcount > 0),
  method VARCHAR(20) NOT NULL, grade_raw TEXT,
  FOREIGN KEY (bid_ntce_no,bid_ntce_ord) REFERENCES bid_require_summary(bid_ntce_no,bid_ntce_ord) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_bqp_bid ON bid_require_personnel (bid_ntce_no,bid_ntce_ord);

-- ④ 품목 + 직접생산 (v2.4 직생 흡수)
CREATE TABLE IF NOT EXISTS bid_require_items (
  id BIGSERIAL PRIMARY KEY,
  bid_ntce_no VARCHAR(40) NOT NULL, bid_ntce_ord VARCHAR(10) NOT NULL,
  item_code VARCHAR(10) REFERENCES item_code_master(item_code),      -- NULL=미해석
  item_name VARCHAR(300),
  direct_production_req BOOLEAN NOT NULL DEFAULT FALSE,               -- 이 품목 직생확인 요구
  method VARCHAR(20) NOT NULL,
  source VARCHAR(20) NOT NULL DEFAULT 'item_field' CHECK (source IN ('item_field','license_field')),
  name_raw TEXT NOT NULL,
  FOREIGN KEY (bid_ntce_no,bid_ntce_ord) REFERENCES bid_require_summary(bid_ntce_no,bid_ntce_ord) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_bqi_bid ON bid_require_items (bid_ntce_no,bid_ntce_ord);
CREATE INDEX IF NOT EXISTS idx_bqi_code ON bid_require_items (item_code);

-- ⑤ 실적
CREATE TABLE IF NOT EXISTS bid_require_performances (
  id BIGSERIAL PRIMARY KEY,
  bid_ntce_no VARCHAR(40) NOT NULL, bid_ntce_ord VARCHAR(10) NOT NULL,
  parse_status VARCHAR(10) NOT NULL DEFAULT 'parsed' CHECK (parse_status IN ('parsed','partial','unparsed')),
  unit VARCHAR(10) CHECK (unit IS NULL OR unit IN ('원','건')),      -- NULL=화이트리스트밖/미해석→확인필요
  min_value NUMERIC,                                                  -- NULL=미해석→확인필요
  agg_type VARCHAR(10) CHECK (agg_type IS NULL OR agg_type IN ('single','sum','count')),
  period_years SMALLINT NOT NULL DEFAULT 5,
  field_code VARCHAR(20) REFERENCES license_master(license_code),     -- 분야(선택)
  category_raw TEXT, basis_raw TEXT, scope_raw TEXT,
  FOREIGN KEY (bid_ntce_no,bid_ntce_ord) REFERENCES bid_require_summary(bid_ntce_no,bid_ntce_ord) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_bqperf_bid ON bid_require_performances (bid_ntce_no,bid_ntce_ord);

-- ⑥ 인증
CREATE TABLE IF NOT EXISTS bid_require_certs (
  id BIGSERIAL PRIMARY KEY,
  bid_ntce_no VARCHAR(40) NOT NULL, bid_ntce_ord VARCHAR(10) NOT NULL,
  cert_code VARCHAR(30) REFERENCES cert_master(cert_code),           -- NULL=미해석
  cert_name VARCHAR(100), method VARCHAR(20) NOT NULL, name_raw TEXT NOT NULL,
  FOREIGN KEY (bid_ntce_no,bid_ntce_ord) REFERENCES bid_require_summary(bid_ntce_no,bid_ntce_ord) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_bqc_bid ON bid_require_certs (bid_ntce_no,bid_ntce_ord);
CREATE INDEX IF NOT EXISTS idx_bqc_code ON bid_require_certs (cert_code);

-- ⑦ 시공능력 (공사)
CREATE TABLE IF NOT EXISTS bid_require_capacity (
  id BIGSERIAL PRIMARY KEY,
  bid_ntce_no VARCHAR(40) NOT NULL, bid_ntce_ord VARCHAR(10) NOT NULL,
  capacity_type VARCHAR(30) NOT NULL DEFAULT '시공능력평가액',
  license_code VARCHAR(20) REFERENCES license_master(license_code),  -- 업종(NULL=총액)
  min_value BIGINT,                                                   -- NULL=미해석→확인필요
  parse_status VARCHAR(10) NOT NULL DEFAULT 'parsed' CHECK (parse_status IN ('parsed','partial','unparsed')),
  name_raw TEXT NOT NULL, method VARCHAR(20) NOT NULL,
  FOREIGN KEY (bid_ntce_no,bid_ntce_ord) REFERENCES bid_require_summary(bid_ntce_no,bid_ntce_ord) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_bqcap_bid ON bid_require_capacity (bid_ntce_no,bid_ntce_ord);
CREATE INDEX IF NOT EXISTS idx_bqcap_lic ON bid_require_capacity (license_code);

-- ⑧ 규모 (v2.4 스칼라→자식). 요구 있을 때만 1행 → 행 존재 = 요구
CREATE TABLE IF NOT EXISTS bid_require_size (
  bid_ntce_no VARCHAR(40) NOT NULL, bid_ntce_ord VARCHAR(10) NOT NULL,
  size_limit VARCHAR(20) NOT NULL CHECK (size_limit IN ('sme_only','small_only','no_large','no_conglomerate')),
  name_raw TEXT, method VARCHAR(20) NOT NULL,
  PRIMARY KEY (bid_ntce_no, bid_ntce_ord),
  FOREIGN KEY (bid_ntce_no,bid_ntce_ord) REFERENCES bid_require_summary(bid_ntce_no,bid_ntce_ord) ON DELETE CASCADE
);

-- ⑨ 신용 (v2.4 스칼라→자식). 요구 있을 때만 1행
CREATE TABLE IF NOT EXISTS bid_require_credit (
  bid_ntce_no VARCHAR(40) NOT NULL, bid_ntce_ord VARCHAR(10) NOT NULL,
  required BOOLEAN NOT NULL DEFAULT TRUE, min_grade VARCHAR(10),      -- min_grade는 v2 예약
  name_raw TEXT, method VARCHAR(20) NOT NULL,
  PRIMARY KEY (bid_ntce_no, bid_ntce_ord),
  FOREIGN KEY (bid_ntce_no,bid_ntce_ord) REFERENCES bid_require_summary(bid_ntce_no,bid_ntce_ord) ON DELETE CASCADE
);

-- ⑧⑨의 지역의무공동도급 의무지역 (지역 완화 신호의 다중값)
CREATE TABLE IF NOT EXISTS bid_require_region_duty (
  id BIGSERIAL PRIMARY KEY,
  bid_ntce_no VARCHAR(40) NOT NULL, bid_ntce_ord VARCHAR(10) NOT NULL,
  region_code VARCHAR(10) REFERENCES region_master(region_code),     -- NULL=미해석
  region_name VARCHAR(50), method VARCHAR(20) NOT NULL, name_raw TEXT NOT NULL,
  FOREIGN KEY (bid_ntce_no,bid_ntce_ord) REFERENCES bid_require_summary(bid_ntce_no,bid_ntce_ord) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_bqrd_bid ON bid_require_region_duty (bid_ntce_no,bid_ntce_ord);

-- 충족형 9축(N/M 대상): 면허·지역·인력·품목·실적·인증·시공능력·규모·신용
-- 미참여: 지명경쟁·공동수급(표시) / 지역의무(신호) / 커트라인·배점(스코어링)