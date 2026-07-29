-- ============================================================================
-- cert_master 시드 ② — Phase 2 미해석 트리아지 반영 (2026-07-29, 검수 완료판 v2)
--   근거: bid_require_certs method='none' 5,778행 + g01 load 버킷 빈도 실측.
--   검수(2026-07-29 14:32 마스터 전량 45종 대조):
--     · 당초 신규 후보 12종 중 9종이 기존 마스터에 존재 → INSERT 제거, 별칭만 유지
--       (SEC_FUNC·HACCP·Q_MARK·KC·RRA_CONFORM·HIGH_EFF·GR·QA_PROC 기존재,
--        단체표준은 GROUP_STD 가 아니라 기존 SPS 로 별칭 재지향 — 이중 실체 방지)
--     · 신규 INSERT 는 3종만: CSAP / CSA_STAR / TTA_VERIFIED
--   전략(등록부 D-09): 실인증 적재 + 별칭 접기 / 비인증 증빙은 어댑터 IGNORE / 등급·교육형 보류.
--   멱등: ON CONFLICT DO NOTHING.
--   canon_key 특성: 공백·ㆍ·.·, 제거 → 띄어쓰기 변형은 별칭 1건으로 전 변형 커버. 하이픈 보존.
-- ============================================================================

-- ── ① 신규 인증 (3종) ────────────────────────────────────────────────────
INSERT INTO cert_master (cert_code, cert_name, category, issuer, law_basis) VALUES
 ('CSAP',         '클라우드컴퓨팅서비스 보안인증(CSAP)', 'infosec', '한국인터넷진흥원(KISA)',    '클라우드컴퓨팅법'),
 ('CSA_STAR',     'CSA STAR 인증',                       'infosec', 'Cloud Security Alliance',   NULL),
 ('TTA_VERIFIED', 'TTA인증(정보통신 시험인증)',          'software','한국정보통신기술협회(TTA)', NULL)
ON CONFLICT (cert_code) DO NOTHING;

-- ── ② 별칭 — 정식명과 실측 표기의 canon 불일치 해소 ──────────────────────
--   해석률 20.1%의 주범은 마스터 부재가 아니라 표기 불일치였다(마스터는 45종으로 충분히 풍부).
INSERT INTO master_alias (entity_type, alias_text, canonical_code) VALUES
 -- 기존 마스터의 실측 축약·변형 표기
 ('cert', 'KS인증',                                   'KS'),
 ('cert', 'KS',                                       'KS'),
 ('cert', 'K마크',                                    'K_MARK'),
 ('cert', 'GS인증',                                   'GS'),
 ('cert', '품질인증(Good Software)',                  'GS'),           -- 실측 110건 최다 표기
 ('cert', 'CC인증',                                   'CC'),
 ('cert', 'CE',                                       'CE'),
 ('cert', 'UL',                                       'UL'),
 ('cert', 'ISMS-P 인증',                              'ISMS_P'),
 ('cert', 'ISMS-P',                                   'ISMS_P'),
 ('cert', 'ISMS 인증',                                'ISMS'),
 ('cert', '성능인증',                                 'EPC'),
 ('cert', '환경표지인증',                             'ECO_LABEL'),
 ('cert', '환경표지인증서',                           'ECO_LABEL'),
 ('cert', '환경마크',                                 'ECO_LABEL'),
 ('cert', '안전보건경영시스템(ISO-45001) 인증',       'ISO_45001'),    -- 실측 50건 표기
 ('cert', 'ISO45001',                                 'ISO_45001'),
 ('cert', '식품안전관리인증기준(HACCP)',              'HACCP'),        -- 실측 49건 표기
 ('cert', 'Q마크',                                    'Q_MARK'),
 ('cert', 'KC인증',                                   'KC'),
 ('cert', 'KC',                                       'KC'),
 ('cert', '단체표준인증',                             'SPS'),          -- ★ GROUP_STD 아님 — 기존 SPS
 ('cert', '적합성평가(적합인증, 적합등록, 잠정인증)', 'RRA_CONFORM'),  -- 실측 47건 표기
 ('cert', '방송통신기자재등의 적합등록 필증',         'RRA_CONFORM'),
 ('cert', '적합등록필증',                             'RRA_CONFORM'),
 ('cert', '우수재활용제품(GR) 인증',                  'GR'),
 ('cert', 'GR인증',                                   'GR'),
 ('cert', '품질보증조달물품',                         'QA_PROC'),
 -- 신규 3종의 실측 표기
 ('cert', 'CSAP 인증',                                'CSAP'),
 ('cert', 'CSAP 보안인증서',                          'CSAP'),
 ('cert', 'CSAP IaaS·SaaS',                           'CSAP'),
 ('cert', '클라우드 보안인증',                        'CSAP'),
 ('cert', 'CSA STAR Gold',                            'CSA_STAR'),
 ('cert', 'TTA인증',                                  'TTA_VERIFIED')
ON CONFLICT DO NOTHING;

-- ── ③ 가족(family) 규칙용 신규 인증 10종 (v1.9 어댑터 _CERT_FAMILY 대응) ──
--   미해석 전수 트리아지(5,023행, 2026-07-29)에서 빈도·라이브 근거로 선정.
--   어댑터 가족 규칙이 이 코드로 매핑하므로 v1.9 재정규화 전에 반드시 선적재.
--   (미적재 시 _fk 가드가 NULL 강등 → 미해석 적재로 안전 강등되지만 매핑 효과 소실)
INSERT INTO cert_master (cert_code, cert_name, category, issuer, law_basis) VALUES
 ('BF',            '장애물 없는 생활환경(BF) 인증',        'building',    '한국장애인개발원 등 지정기관', '장애인·노인 등 편의증진법'),
 ('MIN_GREEN',     '공공조달 최소녹색기준제품',            'procurement', '조달청',                     '조달사업법'),
 ('STANDBY_POWER', '대기전력저감우수제품',                 'environment', '한국에너지공단',             '에너지이용 합리화법'),
 ('INNO_PROD',     '혁신제품 지정',                        'procurement', '조달청(혁신장터)',           '조달사업법'),
 ('NRE_CERT',      '신재생에너지설비 인증',                'environment', '한국에너지공단',             '신재생에너지법'),
 ('SP_CERT',       '소프트웨어프로세스 품질인증(SP인증)',  'software',    '정보통신산업진흥원(NIPA)',   '소프트웨어 진흥법'),
 ('SEC_SVC',       '정보보호 전문서비스 기업 지정',        'infosec',     '과학기술정보통신부',         '정보통신기반 보호법'),
 ('EDU_SAFE',      '교육시설안전 인증',                    'building',    '한국교육시설안전원',         '교육시설법'),
 ('GD_MARK',       '우수디자인(GD) 인증',                  'quality',     '한국디자인진흥원',           '산업디자인진흥법'),
 ('TYPE_APPROVAL', '형식승인(법정 형식승인 총칭)',         'product',     '소관 법정 기관(KFI·KTC 등)', '소방시설법·계량법 등')
ON CONFLICT (cert_code) DO NOTHING;

-- ── ④ 적재 후 재해석률 추정 (재정규화 전 미리보기) ────────────────────────
WITH unres AS (
  SELECT name_raw, COUNT(*) AS n FROM bid_require_certs WHERE cert_code IS NULL GROUP BY 1
), al AS (
  SELECT regexp_replace(alias_text, '[\s·ㆍ.,]', '', 'g') AS k FROM master_alias WHERE entity_type = 'cert'
  UNION SELECT regexp_replace(cert_name, '[\s·ㆍ.,]', '', 'g') FROM cert_master
)
SELECT SUM(n) AS 미해석_전체,
       SUM(n) FILTER (WHERE EXISTS (
         SELECT 1 FROM al WHERE al.k = regexp_replace(unres.name_raw, '[\s·ㆍ.,]', '', 'g'))) AS 해석전환_예상
  FROM unres;

-- 보류(의도적 미적재 — 미해석 유지로 표면화):
--   '안전인증' 단독 86건 — SAFETY_KCS(산업용 KCs)일 가능성이 크나 KC·어린이제품 안전인증과
--     동명이라 단독 표기만으로 확정 불가 → 오매핑 방지 위해 보류. 문맥 해석은 v2.
--   안전관리 계속교육 수료증(99)·스마트 건설기술교육 수료증 → 인력 교육 이수(축 없음)
--   안전보건수준평가 B등급 이상(36)·안전관리능력평가(33) → 등급형 평가(축 없음)
--   특허(28)·형식승인(21)·적격조합확인서(15)·정보보호시스템 유형별 사전인증요건(14)
--   FedRAMP·FIPS 140-2·NIST 800-171·PCI-DSS·GDPR·HIPAA(각 1건) — 빈도컷 미달 외산 프레임워크
