# 마스터 테이블 설계

> 노션 의사결정사항 > 마스터 테이블 설계 페이지 스냅샷 (2026-07-21 기준, 상태: 결정 중)
> 원본: https://app.notion.com/p/3a30fa75d86b8007936ecb7f19d6a257

---

# 이유

### 1. 자격조건 매칭 성립 조건

SQL 매칭은 공고(LLM 추출값)와 회사 프로필이 **같은 표준 어휘를 참조할 때만** 성립
→ **양쪽이 공통으로 바라보는 마스터 테이블이 필요**하다.

### 2. LLM 추출값의 표준화(canonicalization) 필요성

1. RDS 실측 결과 `code` 전부 null
2. OR 로직이 name_raw 문자열에 내장
3. 동일 면허의 표기 변형 다수

→ **자유 표기를 표준 코드로 변환하는 기준 테이블 필요.**

---

## RDS 진단 결과 (2026-07-14)

- `required_licenses`는 `[{code, or_group, name_raw}]` 구조인데 **code 전부 null**, OR 로직이 name_raw 문자열에 내장됨 ("토목(또는 토목건축)공사업 등록") → 이 상태로는 SQL 자격 매칭 불가
- 같은 면허의 표기 변형 다수: "전기공사업 등록" / "전기공사업을 등록한 자", "산업·환경설비" / "산업.환경설비"
- 지역제한은 대체로 깨끗하나 계층 혼입("전주", "천안" 등 기초단위) + 변형("경기도 관할구역", "부산")
- enum 필드(company_size_limit 등)는 깨끗함 → 프롬프트 enum 강제가 작동한다는 증거
- SQL 매칭은 양쪽 어휘가 일치해야 성립: **공고 측은 정규화 배치로, 프로필 측은 입력 UI에서 마스터 참조로 강제**

## 18개 자격요건 필드 → 참조 방식 분류

기준: 값이 **열린 어휘**(표기가 다양한 개체명)면 마스터 참조, **닫힌 어휘**(enum)나 숫자면 자체 처리.

| 자격요건 필드 | 설명 | 값 성격 | 참조 대상 |
|---|---|---|---|
| required_licenses | 요구 면허·등록 (예: 전기공사업) | 열린 어휘 | **license_master** |
| region_limit_names | 지역제한 대상 지역명 | 열린 어휘(계층) | **region_master** |
| item_codes | 대상 물품/업종 코드 — 구조 [{type, code}] | 코드 체계 | **item_code_master** |
| direct_production_req | 직접생산확인증명 요구 여부 | bool | **item_code_master** (품목 연계) |
| required_certs | 요구 인증 (ISO, KS 등) | 열린 어휘 | **cert_master** |
| personnel_reqs | 기술인력 요건 (자격·등급·인원수) | 등급 체계 | **personnel_grade_master** |
| credit_rating_req | 신용평가등급 요구 여부 (⚠️ 확정 스키마상 BOOLEAN — 등급값 아님) | bool | 마스터 불필요 (등급 서열 비교는 v2) |
| company_size_limit | 기업규모 제한 — enum 5종: sme_only/small_only/no_large/no_conglomerate/none | 닫힌 enum | 자체 enum |
| region_limit_type | 지역제한 방식 (본점 소재지 기준 등) | 닫힌 enum | 자체 enum |
| award_cutline_type | 낙찰자 결정 방식 (점수/비율/최저가) | 닫힌 enum | 자체 enum |
| award_cutline_value | 낙찰 커트라인 값 (예: 적격심사 85점) | 숫자 | 마스터 불필요 |
| tech_weight | 기술평가 배점 비중 | 숫자 | 마스터 불필요 |
| price_weight | 가격평가 배점 비중 | 숫자 | 마스터 불필요 |
| performance_reqs | 실적 요건 (금액·건수·기간) | 숫자/구조 | 마스터 불필요 |
| capacity_reqs | 능력 요건 (시공능력평가액 등) | 숫자/구조 | 마스터 불필요 |
| joint_venture_allowed | 공동수급 허용 여부 | bool | 마스터 불필요 |
| subcontract_allowed | 하도급 허용 여부 | bool | 마스터 불필요 |
| region_basis | 지역제한 근거 (원문 조항) | 텍스트 | 마스터 불필요 |

**결론: 마스터 테이블 6종 + 통합 별칭 테이블 1종**

## DDL

```sql
-- ① 면허/등록 마스터 (핵심)
CREATE TABLE license_master (
  license_code   VARCHAR(20) PRIMARY KEY,   -- 예: 'CNST_G_TOB' (토목공사업)
  license_name   VARCHAR(100) NOT NULL,     -- 표준명 (법령 별표 기준)
  category       VARCHAR(30) NOT NULL,      -- 종합건설/전문건설/전기/정보통신/소방/용역업/기타
  law_basis      VARCHAR(100),              -- 근거법 (건설산업기본법 등)
  parent_code    VARCHAR(20) REFERENCES license_master,  -- 계층 (종합건설업>토목공사업)
  is_active      BOOLEAN DEFAULT TRUE       -- 업종 개편 대응 (구 업종 보존)
);

-- ② 행정구역 마스터 (계층 매칭의 핵심)
CREATE TABLE region_master (
  region_code    VARCHAR(10) PRIMARY KEY,   -- 행정표준코드 (시도 2자리/시군구 5자리)
  region_name    VARCHAR(50) NOT NULL,      -- '경기도', '성남시'
  region_level   SMALLINT NOT NULL,         -- 1=시도, 2=시군구
  parent_code    VARCHAR(10) REFERENCES region_master  -- 성남시→경기도
);

-- ③ 물품분류 마스터
CREATE TABLE item_code_master (
  item_code      VARCHAR(10) PRIMARY KEY,   -- 조달청 물품분류번호(8자리)/세부품명(10자리)
  item_name      VARCHAR(200) NOT NULL,
  parent_code    VARCHAR(10),
  is_sme_product BOOLEAN DEFAULT FALSE      -- 중기간 경쟁제품 여부 (연 1회 고시 갱신)
);

-- ④ 인증 마스터
CREATE TABLE cert_master (
  cert_code  VARCHAR(20) PRIMARY KEY,
  cert_name  VARCHAR(100) NOT NULL,         -- ISO9001, KS, GS, 이노비즈 ...
  issuer     VARCHAR(100)
);

-- ⑤ 기술인력 등급 마스터
CREATE TABLE personnel_grade_master (
  qual_code  VARCHAR(20) PRIMARY KEY,
  qual_name  VARCHAR(100) NOT NULL,         -- '토목기사', '특급기술자'
  grade      VARCHAR(20),                   -- 초급/중급/고급/특급, 기사/산업기사/기술사
  field      VARCHAR(50)                    -- 토목/건축/전기/정보통신 ...
);

-- ⑥ 신용등급 서열 (⚠️ v2 보류 — bid_table.credit_rating_req가 BOOLEAN이라 현 스키마에선 등급 비교 불가)
CREATE TABLE credit_rating_scale (
  rating_code VARCHAR(10) PRIMARY KEY,      -- 'BBB+', 'B0' ...
  rank_order  SMALLINT NOT NULL UNIQUE      -- 낮을수록 우량 → 'B+ 이상' = rank_order <= N
);

-- ⑦ 통합 별칭 테이블 — 정규화 배치와 프로필 검색 UI가 공유
CREATE TABLE master_alias (
  entity_type    VARCHAR(20) NOT NULL,      -- 'license'/'region'/'cert'/'item'/'personnel'
  alias_text     VARCHAR(200) NOT NULL,     -- '토목공사업을 등록한 자', '경기도 관할구역'
  canonical_code VARCHAR(20) NOT NULL,
  source         VARCHAR(20) DEFAULT 'manual',  -- manual / llm / rule
  PRIMARY KEY (entity_type, alias_text)
);
```

## 양쪽 참조 구조

```
                        ┌── license_master ──┐
공고 (LLM 추출 정규화 배치) │   region_master    │  회사 프로필 (가입 입력 UI)
bid_table.required_      │   item_code_master │  선택형 입력(드롭다운/검색)만 허용
licenses[].code ────────▶│   cert_master      │◀── 자유 텍스트 금지
region_limit_names       │   personnel_grade  │
→ region_code 정규화      │   credit_scale     │
                        └────────────────────┘
```

### 회사 프로필 스키마 (마스터 FK 강제)

```sql
CREATE TABLE company_profile (
  company_id     UUID PRIMARY KEY,
  company_name   VARCHAR(100) NOT NULL,
  hq_region_code VARCHAR(10) REFERENCES region_master,
  company_size   VARCHAR(20),               -- small/medium/mid_large/conglomerate
  credit_rating  VARCHAR(10) REFERENCES credit_rating_scale
);

CREATE TABLE company_license (
  company_id UUID, license_code VARCHAR(20) REFERENCES license_master,
  PRIMARY KEY (company_id, license_code));

CREATE TABLE company_item (
  company_id UUID, item_code VARCHAR(10) REFERENCES item_code_master,
  direct_production_yn BOOLEAN,
  PRIMARY KEY (company_id, item_code));

CREATE TABLE company_cert (
  company_id UUID, cert_code VARCHAR(20) REFERENCES cert_master, valid_until DATE,
  PRIMARY KEY (company_id, cert_code));

CREATE TABLE company_personnel (
  company_id UUID, qual_code VARCHAR(20) REFERENCES personnel_grade_master,
  headcount SMALLINT,
  PRIMARY KEY (company_id, qual_code));
```

> ⚠️ **실적 스키마 v1.1 변경 예정 (7/20 논의)**: 집계값 방식(single_max_amt, sum_5yr_amt)은 공고의 다양한 요구 형태(단일/누계/건수 × 기간 3·5·10년)에 대응 불가 → **건별 실적 대장 `company_performance_record`**(계약 1건=1행: contract_name, field_code, contract_amt, end_date, quantity_value/unit)로 교체하고 집계는 매칭 시점에 SQL로 계산. 유사실적 판정은 정량(SQL) + 유사성(LLM 참고 의견, 확정 금지) 2단 분리.

### 매칭 시맨틱 (자격 매칭 도구의 비교 규칙)

| 필드 | 비교 규칙 |
|---|---|
| 면허 | or_group별 EXISTS — 그룹 내 하나라도 보유하면 통과, 그룹 간은 AND |
| 지역 | region_code **계층 포함** — 공고가 '경기도'면 프로필이 성남시(자식)여도 parent 경유 통과 |
| 신용등급 | 현 스키마는 bool 일치만 가능 (공고 측이 요구 여부만 보유). rank_order 서열 비교는 v2 |
| 실적 | 금액/건수 부등호 (기간 필터 후 MAX/SUM/COUNT 계산) |
| 인력 | 등급별 headcount 부등호 |
| 규모/직생/공동수급 | enum·bool 일치 |

## 속성별 수집 전략

원칙: **표준이 존재하면 시드 1회 적재, 표준이 없으면 실데이터 주도(demand-driven).** 전수 수집 대신 RDS name_raw distinct 목록(= 실수요 명세서)부터 채운다.

| 마스터 | 출처 | 수집 방법 | 규모 | 갱신 | 공수 |
|---|---|---|---|---|---|
| region_master | 행정표준코드관리시스템(code.go.kr) 법정동코드 | 파일 다운로드 → 시도 17 + 시군구 필터 적재 | ~250행 | 거의 없음 | **반나절** |
| license_master | 국가법령정보센터 — 건설산업기본법 시행령 별표(종합 5·전문 14업종), 전기공사업법, 정보통신공사업법, 소방시설공사업법, 건설기술진흥법, SW진흥법 | 법령 별표 기반 **수동 시드** → RDS name_raw 상위 빈도와 대조해 누락 보완 | ~100행 | 법 개정 시(드묾) | **1~2일** |
| item_code_master | 공공데이터포털 '조달청 물품목록정보서비스' OpenAPI | 전수(수만 건) 대신 **온디맨드 캐시** — 공고 등장 코드만 API로 resolve해 적재 | 등장분만 | 수시(자동) | 반나절 |
| └ 중기간 경쟁제품 | 중소벤처기업부 고시 / 공공구매종합정보(SMPP) | 고시 목록 적재 → is_sme_product 갱신 | ~600품목 | 연 1회 | 반나절 |
| personnel_grade_master | 국가기술자격 종목(큐넷) + 건설기술인 등급체계 | 등급 체계 수동 시드 (종목 전수 불필요, 공고 등장분만) | ~50행 | 거의 없음 | 반나절 |
| credit_rating_scale | 신평사 공통 등급체계(AAA~D) | 수동 시드 | ~22행 | 없음 | **v2 보류** (7/20 스키마 대조: bid 측 BOOLEAN) |
| cert_master | 표준 전체 목록 부재 | **실데이터 주도** — evidence 등장 인증 + 대표 인증 30~50개 시드 | ~50행 | 등장 시 추가 | **보류 권고** (7/20: 직생·규모 서류 혼입 확인, 진짜 채움률 18.2%) |
| master_alias | 자체 RDS | name_raw distinct → 규칙 분해 → 소형 LLM 매핑 제안 → **사람 승인 후 적재** | 등장분만 | 지속 누적 | 정규화 파이프라인과 함께 |

### 우선순위 제안

```
region (반나절) → personnel (반나절) → license (1~2일) → item_code (반나절)
= 2~3일 내 시드 완료 가능.  cert·credit은 보류(v2).
```

region을 먼저 하는 이유: 공수 대비 효과 최대(계층 매칭 즉시 성립), 외부 의존 없음, 지역 데이터가 이미 비교적 깨끗해서 별칭 몇 개만으로 완결.

## 팀 협의 결정 사항

1. **마스터 6종 + master_alias 채택 여부** — 대안: 필드별 개별 alias 테이블 (권고: 통합 1종이 관리 단순)
2. **수집 담당 배분** — 위 표의 공수 기준으로 분배
3. **별칭 매핑 사람 승인 절차** — LLM은 매핑 *제안*까지만, master_alias 확정 등록은 사람 승인. 면허 오매칭 = 무효입찰 리스크가 있는 유일한 지점이므로 자동 확정 금지 권고
4. **license_code 명명 규칙** — 'CNST_G_TOB' 방식 vs 법정 코드 존재 시 차용
5. **정규화 배치의 위치** — 병합 배치 후단에 붙일지, 병합 배치 내부 단계로 넣을지
6. **JSON null 정리** — ✅ **7/21 완료.** 원인: 7/15 09:32 일회성 벌크 실행(9초간 14,639행 — 정기 배치 db.py 아님, 현행 코드는 정상이라 수정 불필요). 7개 필드 총 92,741건 SQL NULL로 정리, 정리 후 JSON null 0건 확인. **정리 후 진짜 채움률**: licenses 73.3% / cutline_type 70.1% / item_codes 57.4% / size 41.6% / region 16.7% / certs 19.0% / performance 9.6% / personnel 7.2% / capacity 3.1%. 후속 규칙 제안: **bid_table 쓰기는 db.py 경유만** (벌크 필요 시 db.py에 벌크 함수 추가, 우회 스크립트 금지)
7. **credit_rating_scale v2 보류 (7/20 확정 스키마 대조 결과)** — bid_table.credit_rating_req는 BOOLEAN(요구 여부)이므로 등급 서열 매칭 불가. 등급 매칭이 필요하면 LLM 추출 스키마에 등급값 필드 추가가 선행 조건
