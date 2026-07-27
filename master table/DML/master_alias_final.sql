-- ============================================================================
-- master_alias 별칭 등록 — 확정본
-- 2026-07-27 · 코드는 전부 license_master 실조회로 확인함
--
-- 배경: 면허 미해석 2,203행 / live 요구공고 849건 중 232건(27.3%).
--       verdict '확인필요' 163건의 사실상 단독 원인.
--
-- 확인 결과 license_master 커버리지는 양호했다. 결손 코드는 0529 하나(2건)뿐이고
-- 나머지는 전부 '이름이 달라서' 못 찾은 것 — 즉 시드가 아니라 별칭 문제였다.
--
-- ★ normalizer v1.8 의 canon_key 가 공백·가운뎃점·마침표를 제거하므로
--   대표형 1건만 등록하면 표기 변형은 함께 잡힌다. 아래는 그걸 감안해 최소로 추렸다.
-- ============================================================================

INSERT INTO master_alias (entity_type, alias_text, canonical_code, source) VALUES

-- ── ① 방송영상독립제작사 (57건) — 마스터는 '제작자', 원문은 '제작사'. 한 글자 차이 ──
  ('license', '방송영상독립제작사', '3230', 'manual'),

-- ── ② 정보통신 엔지니어링·기술사 (56건) ──
--    원문이 "정보통신부문의 정보통신분야에 …" 로 길게 풀어 쓴다.
--    v1.8 접미사 규칙이 "~로 신고한 자"·"~를 등록한 자"를 벗기므로 어간만 등록하면 된다.
  ('license', '정보통신부문의 정보통신분야에 엔지니어링사업자로 신고', '3572', 'manual'),
  ('license', '정보통신부문의 정보통신분야에 엔지니어링사업자',       '3572', 'manual'),
  ('license', '정보통신분야에 엔지니어링사업자',                     '3572', 'manual'),
  ('license', '정보통신부문의 정보통신분야에 기술사사무소를 등록',     '1320', 'manual'),
  ('license', '정보통신부문의 정보통신분야에 기술사사무소',           '1320', 'manual'),
  ('license', '정보통신분야에 기술사사무소를 등록',                  '1320', 'manual'),
  ('license', '정보통신분야에 기술사사무소',                        '1320', 'manual'),

-- ── ③ 전력시설물 설계 (43건) — 마스터는 '설계업(종합설계)/(전문설계1종)' ──
  ('license', '전력시설물의 종합설계업',    '1105', 'manual'),
  ('license', '전력시설물 종합설계업',      '1105', 'manual'),
  ('license', '종합설계업',                '1105', 'manual'),
  ('license', '전력시설물의 전문설계업(1종)', '1106', 'manual'),
  ('license', '전력시설물 전문설계업(1종)',   '1106', 'manual'),
  ('license', '전문설계업(1종)',            '1106', 'manual'),

-- ── ④ 건축사 (23건) — 접미사 패턴으로 안 벗겨지는 서술형 ──
  ('license', '건축사',                                               '4817', 'manual'),
  ('license', '건축사사무소를 개설하고 국토교통부장관에게 신고를 필한 자', '4817', 'manual'),
  ('license', '건축사면허를 소지하고 건축사사무소 개설 및 신고를 필한 자', '4817', 'manual'),
  ('license', '건축사사무소 개설 및 국토교통부장관 신고필',              '4817', 'manual'),
  ('license', '건축사사무소 개설 및 신고',                             '4817', 'manual'),
  ('license', '건축사사무소 개설 및 국토교통부장관에게 신고',            '4817', 'manual'),

-- ── ⑤ 부문별 엔지니어링사업자·기술사사무소 (16건) ──
--    원문 "건설부문(토질·지질)에 대한 엔지니어링사업자 신고" 형식.
  ('license', '건설부문(토질·지질)에 대한 엔지니어링사업자', '3588', 'manual'),
  ('license', '건설부문(구조)에 대한 엔지니어링사업자',      '3585', 'manual'),
  ('license', '환경부문(수질관리 분야) 엔지니어링사업자',    '3593', 'manual'),
  ('license', '설비부문(설비)에 대한 엔지니어링사업자',      '3591', 'manual'),
  ('license', '환경부문(수질관리 분야) 기술사사무소',        '1360', 'manual'),

-- ── ⑥ 여객자동차 (14건) — 마스터 '운수사업', 원문 '운송사업' ──
  ('license', '여객자동차운송사업(구역여객자동차운송사업-전세버스)', '5805', 'manual'),
  ('license', '여객자동차운수사업(전세버스운송사업자)',             '5805', 'manual'),
  ('license', '전세버스운송사업',                                 '5805', 'manual'),

-- ── ⑦ '~자' 접미 (21건) — '소프트웨어사업자'를 깨뜨릴 위험이 있어 규칙화 대신 별칭 ──
  ('license', '정보통신공사업자',     '0036', 'manual'),
  ('license', '정보통신공사 등록업체', '0036', 'manual'),
  ('license', '기간통신사업자',       '1458', 'manual'),
  ('license', '국내 기간통신사업자',   '1458', 'manual'),

-- ── ⑧ 의약품 (7건) ──
  ('license', '의약품수입업',   '5304', 'manual'),
  ('license', '의약품등 수입업', '5304', 'manual'),

-- ── ⑨ 총칭·표기 변형 ──
  ('license', '기타자유업종(행사대행업)',                  '9901', 'manual'),
  ('license', '소프트웨어사업자(컴퓨터관련서비스업)',        '1468', 'manual'),
  ('license', '소프트웨어사업자(컴퓨터관련서비스분야)',      '1468', 'manual'),
  ('license', '소프트웨어사업자(디지털콘텐츠개발서비스분야)', '1469', 'manual')

ON CONFLICT (entity_type, alias_text) DO UPDATE
  SET canonical_code = EXCLUDED.canonical_code, source = EXCLUDED.source;


-- ═══ 검증 — 고아 별칭 0건이어야 한다 ═══
-- 존재하지 않는 코드를 가리키면 조용히 매칭 실패한다.
SELECT a.alias_text, a.canonical_code
FROM master_alias a
LEFT JOIN license_master l ON l.license_code = a.canonical_code
WHERE a.entity_type = 'license' AND l.license_code IS NULL;


-- ═══ 참고 — 오타성 차이 추가 발굴 (pg_trgm 필요) ═══
-- '방송영상독립제작사' vs '제작자' 처럼 한두 글자 차이가 더 있을 수 있다.
-- 확장이 없으면 이 블록은 건너뛰어도 된다.
/*
CREATE EXTENSION IF NOT EXISTS pg_trgm;

WITH un AS (
  SELECT name_raw, count(*) AS n
  FROM bid_require_licenses
  WHERE license_code IS NULL AND source = 'license_field'
  GROUP BY 1 HAVING count(*) >= 3
)
SELECT u.n AS 건수, u.name_raw AS 원문, m.license_code, m.license_name,
       round(similarity(u.name_raw, m.license_name)::numeric, 3) AS 유사도
FROM un u
JOIN license_master m ON similarity(u.name_raw, m.license_name) > 0.6
ORDER BY u.n DESC, 유사도 DESC;
*/


-- ═══ 결손 코드 (참고) ═══
-- [B] 확인 결과 license_master 에 없는 코드는 0529 하나, 2건뿐이었다.
--   원문: '측량업(기타-수치지도제작업)(0529)'
-- 넣으려면 아래. 규모가 작아 생략해도 무방하다.
/*
INSERT INTO license_master (license_code, license_name, category)
VALUES ('0529', '측량업(기타-수치지도제작업)', '측량')
ON CONFLICT (license_code) DO NOTHING;
*/