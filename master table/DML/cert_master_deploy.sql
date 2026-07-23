-- ============================================================================
-- cert_master 독립 배포 (v2.4 전체 배포 전 선행 실행)
--   · cert_master 테이블 생성 + 인증 시드 + master_alias(cert) 별칭 시드
--   · 멱등: CREATE IF NOT EXISTS / INSERT ... ON CONFLICT DO NOTHING
--   · 선행조건: master_alias 테이블이 이미 존재해야 함(기존 마스터군의 별칭사전).
--   · 테이블 정의는 bid_require.sql의 cert_master와 100% 동일 → 이후 v2.4 전체 배포 시
--     CREATE IF NOT EXISTS는 no-op, 시드는 ON CONFLICT로 중복 무시(안전).
--   기준일 2026-07-23. 시드 = 기존 32종 베이스라인 + bid_table.required_certs 채굴 상위 반영.
-- ============================================================================

-- ── 테이블 ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cert_master (
  cert_code   VARCHAR(30) PRIMARY KEY,       -- 공식 코드 부재 → 자체 시맨틱 코드
  cert_name   VARCHAR(100) NOT NULL,
  category    VARCHAR(30)  NOT NULL,
  issuer      VARCHAR(100),
  law_basis   VARCHAR(150),
  is_active   BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE INDEX IF NOT EXISTS idx_cert_category ON cert_master (category);

-- ── 시드 ① 베이스라인 32종 (설계 시 확정) ────────────────────────────────
INSERT INTO cert_master (cert_code, cert_name, category, issuer, law_basis) VALUES
 ('ISO_9001','ISO 9001 품질경영시스템','quality','인증기관(KAB 인정)','ISO 9001'),
 ('ISO_13485','ISO 13485 의료기기 품질경영시스템','quality','인증기관','ISO 13485'),
 ('KS','KS인증(한국산업표준)','quality','국가기술표준원·지정인증기관','산업표준화법'),
 ('K_MARK','K마크(성능·품질인증)','quality','한국산업기술시험원(KTL)',NULL),
 ('ISO_14001','ISO 14001 환경경영시스템','environment','인증기관(KAB 인정)','ISO 14001'),
 ('ISO_50001','ISO 50001 에너지경영시스템','environment','인증기관','ISO 50001'),
 ('ECO_LABEL','환경표지(환경마크)','environment','한국환경산업기술원','환경기술 및 환경산업 지원법'),
 ('EPD','환경성적표지','environment','한국환경산업기술원','환경기술 및 환경산업 지원법'),
 ('LOW_CARBON','저탄소제품 인증','environment','한국환경산업기술원',NULL),
 ('GREEN_TECH','녹색기술인증','environment','한국산업기술진흥원(KIAT)','저탄소 녹색성장 기본법'),
 ('ISO_45001','ISO 45001 안전보건경영시스템','safety','인증기관(KAB 인정)','ISO 45001'),
 ('ISO_27001','ISO/IEC 27001 정보보안경영시스템','infosec','인증기관','ISO/IEC 27001'),
 ('ISO_22301','ISO 22301 업무연속성경영시스템','infosec','인증기관','ISO 22301'),
 ('ISMS','정보보호 관리체계 인증(ISMS)','infosec','한국인터넷진흥원(KISA)','정보통신망법'),
 ('ISMS_P','정보보호 및 개인정보보호 관리체계 인증(ISMS-P)','infosec','한국인터넷진흥원(KISA)','정보통신망법·개인정보보호법'),
 ('CC','CC인증(공통평가기준)','infosec','IT보안인증사무국',NULL),
 ('GS','GS인증(Good Software 품질인증)','software','한국정보통신기술협회(TTA)','소프트웨어 진흥법'),
 ('NEP','신제품(NEP) 인증','innovation','국가기술표준원','산업기술혁신 촉진법'),
 ('NET','신기술(NET) 인증','innovation','산업통상자원부(국가기술표준원)','산업기술혁신 촉진법'),
 ('EPC','성능인증(중소기업기술개발제품)','innovation','중소벤처기업부·한국중소벤처기업유통원','중소기업제품 구매촉진 및 판로지원법 제15조'),
 ('EXCELLENT_PROC','우수조달물품 지정','procurement','조달청','조달사업법'),
 ('JOINT_BRAND','우수조달 공동상표물품','procurement','조달청','조달사업법'),
 ('INNOBIZ','이노비즈(기술혁신형 중소기업)','company','중소벤처기업부·기술보증기금',NULL),
 ('MAINBIZ','메인비즈(경영혁신형 중소기업)','company','중소벤처기업부',NULL),
 ('VENTURE','벤처기업 확인','company','벤처기업확인기관','벤처기업육성에 관한 특별법'),
 ('RND_LAB','기업부설연구소·연구개발전담부서 인정','company','한국산업기술진흥협회(KOITA)','기초연구진흥 및 기술개발지원에 관한 법률'),
 ('ROOT','뿌리기업 확인','company','국가뿌리산업진흥센터','뿌리산업 진흥과 첨단화에 관한 법률'),
 ('KC','KC인증(국가통합인증마크)','product','제품안전 인증기관','전기용품 및 생활용품 안전관리법 등'),
 ('GR','GR인증(우수재활용제품)','recycle','국가기술표준원·자원순환산업인증원','자원의 절약과 재활용촉진법'),
 ('SPS','단체표준 인증(SPS)','standard','한국표준협회 등','산업표준화법'),
 ('HACCP','HACCP(식품안전관리인증)','food','한국식품안전관리인증원','식품위생법'),
 ('ISO_22000','ISO 22000 식품안전경영시스템','food','인증기관','ISO 22000')
ON CONFLICT (cert_code) DO NOTHING;

-- ── 시드 ② 실데이터 채굴 상위 반영(신규 인증) ────────────────────────────
--   bid_table.required_certs 빈도 상위에서 베이스라인에 없던 실인증만 선별 추가.
INSERT INTO cert_master (cert_code, cert_name, category, issuer, law_basis) VALUES
 ('Q_MARK','Q마크(품질보증)','quality','지정 품질보증기관',NULL),
 ('HIGH_EFF','고효율에너지기자재 인증','environment','한국에너지공단','에너지이용 합리화법'),
 ('RRA_CONFORM','방송통신기자재 적합성평가(적합인증·적합등록)','telecom','국립전파연구원(RRA)','전파법'),
 ('SEC_FUNC','보안기능확인서','infosec','IT보안인증사무국','국가정보보안기본지침'),
 ('ZEB','제로에너지건축물 인증','building','국토교통부·한국에너지공단','녹색건축물 조성 지원법'),
 ('GREEN_BUILDING','녹색건축 인증(G-SEED)','building','국토교통부·환경부','녹색건축물 조성 지원법'),
 ('SAFETY_KCS','안전인증(산업용 KCs)','safety','한국산업안전보건공단','산업안전보건법'),
 ('WATER_FIT','수도용자재 위생안전기준 적합인증','product','한국물기술인증원','수도법'),
 ('CE','CE 마킹(EU 적합성)','foreign','EU 인증기관',NULL),
 ('UL','UL 인증(미국 안전규격)','foreign','UL',NULL),
 ('JIS','JIS(일본공업규격)','foreign','일본규격협회(JSA)',NULL)
ON CONFLICT (cert_code) DO NOTHING;

-- ── 시드 ③ 별칭사전 (canon_key 대칭 매핑) ─────────────────────────────────
--   (a) 베이스라인 24 + (b) 실데이터 표기 변형. 어댑터 route_cert가 canon으로 해소.
INSERT INTO master_alias (entity_type, alias_text, canonical_code, source) VALUES
 -- (a) 베이스라인
 ('cert','ISO9001','ISO_9001','manual'),('cert','ISO 9001:2015','ISO_9001','manual'),
 ('cert','품질경영시스템','ISO_9001','manual'),('cert','ISO14001','ISO_14001','manual'),
 ('cert','환경경영시스템','ISO_14001','manual'),('cert','ISO45001','ISO_45001','manual'),
 ('cert','OHSAS18001','ISO_45001','manual'),('cert','OHSAS 18001','ISO_45001','manual'),
 ('cert','ISO27001','ISO_27001','manual'),('cert','ISO/IEC 27001','ISO_27001','manual'),
 ('cert','ISMS-P','ISMS_P','manual'),('cert','GS 인증','GS','manual'),('cert','굿소프트웨어','GS','manual'),
 ('cert','신제품인증','NEP','manual'),('cert','신기술인증','NET','manual'),('cert','성능인증','EPC','manual'),
 ('cert','우수조달제품','EXCELLENT_PROC','manual'),('cert','우수조달물품','EXCELLENT_PROC','manual'),
 ('cert','환경마크','ECO_LABEL','manual'),('cert','녹색기술인증','GREEN_TECH','manual'),
 ('cert','기술혁신형중소기업','INNOBIZ','manual'),('cert','경영혁신형중소기업','MAINBIZ','manual'),
 ('cert','벤처확인','VENTURE','manual'),('cert','벤처기업인증','VENTURE','manual'),
 -- (b) 실데이터 표기 변형 (bid_table 채굴)
 ('cert','KC인증','KC','mining'),('cert','KC마크','KC','mining'),
 ('cert','전기용품안전인증','KC','mining'),('cert','전기용품안전관리법에 의한 전기용품안전인증서','KC','mining'),
 ('cert','KS인증','KS','mining'),('cert','KS인증서','KS','mining'),('cert','KS제품인증서','KS','mining'),
 ('cert','GS인증','GS','mining'),('cert','TTA인증','GS','mining'),
 ('cert','품질인증(Good Software)','GS','mining'),('cert','GS인증 1등급','GS','mining'),
 ('cert','환경표지인증','ECO_LABEL','mining'),('cert','환경표지인증서','ECO_LABEL','mining'),
 ('cert','환경마크(환경표지인증)','ECO_LABEL','mining'),
 ('cert','HACCP인증','HACCP','mining'),('cert','식품안전관리인증기준(HACCP)','HACCP','mining'),
 ('cert','안전보건경영시스템(ISO-45001) 인증','ISO_45001','mining'),
 ('cert','단체표준인증','SPS','mining'),('cert','단체표준표시인증서','SPS','mining'),
 ('cert','우수재활용제품(GR) 인증','GR','mining'),
 ('cert','CC인증','CC','mining'),('cert','K마크','K_MARK','mining'),('cert','Q마크','Q_MARK','mining'),
 ('cert','고효율에너지기자재인증','HIGH_EFF','mining'),
 ('cert','방송통신기자재등의적합등록필증','RRA_CONFORM','mining'),
 ('cert','전파법에 의한 전자파 적합인증서','RRA_CONFORM','mining'),
 ('cert','EMC 인증','RRA_CONFORM','mining'),('cert','적합등록필증','RRA_CONFORM','mining'),
 ('cert','보안기능확인서','SEC_FUNC','mining'),
 ('cert','제로에너지건축물인증','ZEB','mining'),('cert','녹색건축인증','GREEN_BUILDING','mining'),
 ('cert','수도용적합인증','WATER_FIT','mining')
ON CONFLICT (entity_type, alias_text) DO NOTHING;

-- ── 확인 ─────────────────────────────────────────────────────────────────
SELECT 'cert_master'  AS t, count(*) AS n FROM cert_master
UNION ALL
SELECT 'alias(cert)'  AS t, count(*)      FROM master_alias WHERE entity_type='cert';