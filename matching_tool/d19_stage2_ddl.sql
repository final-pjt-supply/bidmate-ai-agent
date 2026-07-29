-- ============================================================================
-- D-19 2단계 — 회원 인력에 분야 차원 추가 (제안 DDL, 2026-07-29)
--   ⚠ 스키마 소유: 백엔드(Alembic). 원칙은 제안만이나, 일정상 2026-07-29 밤 B가 직접
--     실행하기로 결정(비파괴 확인 후). 백엔드에 통보 필수 — Alembic 리비전으로 사후
--     스탬프해 마이그레이션 이력과 실 스키마의 드리프트를 막아야 한다.
--   비파괴 근거(백엔드 실코드 확인, company_profile_repository.replace_profile):
--     프로필 저장은 전체 delete-then-insert — ON CONFLICT (company_id, qual_code) 미사용
--     이라 PK → COALESCE 유니크 인덱스 교체가 쓰기 경로를 깨지 않는다.
--   ⚠ ORM 주의(백엔드 몫): CompanyPersonnel 모델의 PK가 (company_id, qual_code)로 남아
--     있는 동안 같은 자격의 분야별 다중 행을 로드하면 identity map 충돌이 난다.
--     API에 field_family 왕복을 추가할 때 모델 PK에도 field_family를 포함할 것.
--     (그 전까지는 다중 행이 생길 입력 경로 자체가 없어 안전.)
--   반영 즉시 compute_match_results.sql(v2.0) 배포 가능(분야-인지 판정).
-- ============================================================================

-- ① 컬럼 추가 (NULL = 분야 무관 — 기존 데이터·미입력 회원의 하위호환 값)
ALTER TABLE company_personnel
  ADD COLUMN IF NOT EXISTS field_family VARCHAR(12);

ALTER TABLE company_personnel DROP CONSTRAINT IF EXISTS chk_cp_field_family;
ALTER TABLE company_personnel ADD CONSTRAINT chk_cp_field_family
  CHECK (field_family IS NULL OR field_family IN (
    'CIVIL','ARCH','MECH','ELEC','COMM','LANDSCAPE','FIRE','STRUCT',
    'SAFETY','QUALITY','ENV','RAIL','SURVEY','ICT','DESIGN'));

-- ② PK 변경: (company_id, qual_code) → (company_id, qual_code, field_family)
--   같은 자격을 분야별로 나눠 등록할 수 있어야 한다(예: 중급기술자 토목 2·건축 1).
--   NULL 이 PK에 못 들어가므로 COALESCE 유니크 인덱스로 대체(분야 미지정 행은 코드당 1행 유지).
ALTER TABLE company_personnel DROP CONSTRAINT IF EXISTS company_personnel_pkey;
CREATE UNIQUE INDEX IF NOT EXISTS uq_cp_company_qual_field
  ON company_personnel (company_id, qual_code, COALESCE(field_family, '_NONE'));

-- ③ 분야 코드 ↔ 한글 라벨 (프론트 select·표시 공용. 별도 마스터 대신 주석 사전 —
--    15종 고정 enum 이라 테이블 비용이 과함. 확장 시 마스터 승격.)
--   CIVIL 토목 / ARCH 건축 / MECH 기계 / ELEC 전기 / COMM 통신 / LANDSCAPE 조경
--   FIRE 소방 / STRUCT 구조 / SAFETY 안전 / QUALITY 품질 / ENV 환경 / RAIL 철도
--   SURVEY 측량 / ICT 정보기술 / DESIGN 디자인 / (NULL) 분야 무관

-- ④ 검증
-- SELECT field_family, COUNT(*) FROM company_personnel GROUP BY 1;
