-- ╔══════════════════════════════════════════════════════════════════════════╗
-- ║ v2.0-field DRAFT — 배포 금지 (선행 조건 미충족 시)                        ║
-- ║ 선행 조건: d19_stage2_ddl.sql 실행 (company_personnel.field_family 컬럼)  ║
-- ║ 조건 충족 즉시 이 파일 전체 실행으로 배포 가능. 그 전까지 v1.5가 현행.   ║
-- ║ 변경점: 인력 축이 분야(family) 단위 풀로 판정 — D-19 2~3단계 완성본.      ║
-- ╚══════════════════════════════════════════════════════════════════════════╝

-- ============================================================================
-- BidMate 매칭 함수 — compute_match_results(company_id)  [RDS · DBeaver 1회 생성]
--   2026-07-24. 02 엔진을 DB 함수로 감쌈. 매칭 로직의 단일 정본.
--
-- 무엇:
--   회사 1곳 × **라이브 공고**(투찰마감 bid_clse_dt 미도래)만 10축 매칭 → verdict 행 반환.
--   RETURNS TABLE 이라 두 가지로 다 쓸 수 있음:
--     · on-read (실시간)  : SELECT * FROM compute_match_results(9001);
--     · precompute(캐시)  : INSERT INTO match_results (...) SELECT * FROM compute_match_results(9001);
--
-- 라이브 필터  [2026-07-25 개정 — 에이전트 검색(C)과 기준 통일]:
--   bid_clse_dt IS NULL OR bid_clse_dt > now(KST). 인덱스 idx_bid_table_clse 사용.
--   마감일시 공란(수의계약 등)은 '판단 불가'라 살아있는 것으로 본다.
--     └ 개정 전에는 제외했으나, 검색(C)은 포함하고 있어 해당 공고가 화면에는 뜨는데
--       자격 판정만 통째로 빠지는 누락이 있었다(실측 43/19,076건).
--
--   ★ 라이브 정의는 두 곳에 있다. 한쪽만 고치면 위 누락이 재발한다:
--       ① 여기 live_bids
--       ② agents/tools/bid_info.py 의 _OPEN_CONDITION
--     반드시 같은 식으로 함께 유지할 것.
--
-- 이 파일이 매칭 로직의 유일한 정본이다. (구 match_engine_v1.sql 은 2026-07-29 폐기 —
--   Phase 1~3 미반영 스냅샷이었고, 로직 이중 정의가 드리프트의 원인이라 삭제. git 이력 참조.)
-- ── 변경 이력 ──────────────────────────────────────────────
-- 2026-07-27  [D3] axes 페이로드에 required·actual 분리 적재.
--   10개 ax_* CTE 가 req_value·act_value 를 만들고 per_bid 가 JSON 키로 싣는다.
--   detail 은 유지(하위호환·요약용). 컬럼명을 req_value/act_value 로 둔 이유는
--   per_bid 의 출력 별칭 required 및 ax_credit 의 c.required(boolean)와 겹치기 때문.
--   ★ axis_all 이 SELECT * UNION ALL 이라 축을 추가하면 두 컬럼을 반드시 채워야 한다
--     (안 채우면 함수 생성 실패 — 이게 D3 재발 방지 장치다).
--   소비자: agents/tools/eligibility.py::_to_result (키 없으면 detail 로 폴백).
--
-- 2026-07-27  [신용 가드] credit 축 위양성 차단.
--   "신용정보 관리규약에 의한 채무불이행 또는 금융질서 문란자"는 입찰보증금 납부조건
--   또는 부정당업자 결격사유지 신용등급 요구가 아니다. 실측 3,611건 중 933건(25.8%).
--   credit_ev/credit_fp 가 extraction_evidence 근거로 판별해 '충족' 처리한다.
--   축을 지우지 않는 이유는 required 분모를 흔들지 않기 위해서. 원복은 CASE 첫 줄 삭제.
--   ※ 대증요법이다. 근본 수정은 추출 프롬프트(결격사유를 credit_rating_req 로 잡지 않기).
--
-- 2026-07-27  [TZ] CURRENT_DATE → params.today_kst 통일.
--   CURRENT_DATE 는 세션 TimeZone 의 날짜라, 세션이 UTC 면 KST 00:00~09:00 구간에서
--   하루 전이 나온다. live 조건은 AT TIME ZONE 으로 세션 무관인데 직생·실적·인증
--   3축만 CURRENT_DATE 를 써서 규약이 갈려 있었다(같은 데이터인데 세션따라 판정이 달라짐).
--   → 이제 함수 전체가 세션 tz 무관. pg_timezone 고정에 의존하지 않는다.
--
-- 2026-07-27(2차)  [D3-b] required/actual 을 '사람이 읽는 값'으로 승격.
--   1차는 계약(키 분리)만 닫았고 값은 코드·개수였다. 이번에 표시값을 채운다.
--     · region : region_code(41000) → region_master.region_name(경기도)
--     · size   : size_limit(sme_only) → '중소기업만' 등 한글 라벨
--     · N-of-M 6축(license·item·direct_prod·personnel·performance·capacity)
--                : '3개 요건/2개 충족' → 실제 항목명 나열
--   계약 의미를 통일한다:
--     req_value = 공고가 요구하는 항목 전체
--     act_value = 그중 우리가 충족한 항목 (없으면 '(없음)')
--     → 미충족 항목은 두 값의 차집합. detail 은 종전대로 개수 요약을 유지한다.
--   이름 출처는 bid_require_* 의 *_name 우선, 없으면 name_raw(원문), 그래도 없으면
--   마스터 조인. 정규화 실패(code IS NULL) 건도 원문으로는 보이게 하려는 것.
--   ★ 길이 방어: 집계 문자열에 left(...,300). respond 가 이 값을 LLM 프롬프트에
--     싣기 때문에 품목명(VARCHAR 300) 다건이면 토큰이 폭증한다.
--
-- 2026-07-27(3차)  [면허 or_group 보정] license 게이트가 과도하게 조이던 문제.
--   실측으로 드러난 결함 셋 중 SQL 로 덮을 수 있는 둘을 처리한다.
--     A. 인수분해 누락 — 원문 "A과 B / A과 C / A과 D" 조합 나열을 or_group 에 그대로
--        옮겨서 같은 면허가 여러 그룹에 반복된다(실측 건물(시설)관리용역 ×5).
--        판정(BOOL_AND)은 안 바뀌지만 분모와 표시가 망가진다 → 면허코드 집합으로 접는다.
--     C. item_field 경유를 AND 로 취급 — or_group 이 두 네임스페이스로 갈려 있다:
--          license_field → '1','2',…      item_field(route:license) → 'x1','x2',…
--        (실측 100% 대응: item_field 4,545건 전부 x 접두, method=route:license)
--        품목 경유는 "이 품목을 하려면 이 면허 중 하나"라는 유추라 의미상 OR 인데
--        x1..xN 이 각각 AND 그룹으로 세어져 live 31건이 과도하게 조여지고 있었다.
--        → item_field 전체를 단일 OR 그룹 'x' 로 병합.
--   ※ 남은 결함 B(다중 코드 유실)는 SQL 로 복구 불가 — name_raw 에만 있고 license_code
--     에 없어서 company_licenses 와 조인할 대상이 없다. live 8건이라 v2 백로그.
--     근본 수정은 어댑터(인수분해 + 다중 코드 추출) + 재정규화.
--   ※ 병합은 느슨한 쪽 오류를 택한 것이다. gate 축에서 과조임은 참여 가능한 공고를
--     숨겨 사용자가 존재조차 모르게 하지만, 느슨하면 bid_ntce_dtl_url 로 확인 가능하다.
--
-- 2026-07-28  [축0개] 판정축이 하나도 없는 공고를 '가능'으로 내던 위양성 차단.
--   verdict CASE 에 required=0 분기를 **맨 앞에** 넣는다.
--   required=0 이면 gate_failed=0·need_review=0·satisfied=0 이라
--   기존 CASE 가 전부 흘러내려 마지막 ELSE '가능' 에 걸렸다.
--   축 0개는 "요구조건이 없다"가 아니라 "공고에서 조건을 못 뽑아냈다"이다.
--   전자면 '가능'이 맞지만 후자다 — 라이브에서 근거 0줄짜리 '지원 가능' 배지가 된다.
--   실측(회사 9001, 2026-07-28 라이브 1,257건): 가능 270건 중 92건(34.1%)이 축 0개.
--     → 가능 270 → 178, 확인필요 131 → 223. 다른 verdict 는 이동 없음
--       (required=0 인 행은 정의상 전부 '가능' 쪽에만 있었다).
--   해당하는 두 경우를 한 분기가 같이 잡는다:
--     ① per_bid LEFT JOIN 미스 — bid_require_* 에 행 자체가 없음
--     ② class='info'(cert·credit) 축만 있음 — 표시용이라 판정 분모에 안 들어감
--   ※ 이건 대증요법이 아니라 판정 규칙 자체의 수정이다. 근본 원인(추출 실패)은
--     정규화 개선으로 축이 채워지면 자연히 이 분기에 안 걸리게 된다.
--
-- 2026-07-28  [신용축 강등] credit 을 supp → info 로 내린다.
--   credit_rating_req 근거 스니펫을 전수 조사한 결과(대상 3,611건):
--     · 임계 요건("BBB 이상이어야 참가 가능")을 담은 공고 = 0건
--     · 등급 토큰이 등장하는 12건은 전부 조달청 적격심사 **배점표** 인용이다
--       (회사채|기업어음|기업신용평가 3열 표 → 배점의 100/95/90/70%).
--     · 나머지는 "신용평가등급확인서 제출", "경영상태는 신용평가등급으로 평가"
--       같은 서류 안내·평가방법 문구다.
--   즉 credit 은 입찰 참가 **자격**이 아니라 낙찰자 결정 **배점 항목**이었다.
--   덧붙여 min_grade 는 전건 NULL 이라 애초에 비교할 임계값이 없다.
--   supp 로 두면 신용등급 미입력 회사에서 587건이 통째로 '미충족'이 되는
--   위양성이 생긴다 — 9001(BBB 보유)로는 전부 '충족'이라 보이지 않던 결함이다.
--   실측(회사 9001, 2026-07-28 라이브 1,323건): credit 축 보유 587건.
--     → 587건에서 required·satisfied 가 나란히 1씩 감소(대소관계 보존, verdict 불변).
--       credit 이 유일한 판정축이던 15건만 required=0 → '확인필요' 로 이동.
--   ※ 축을 지우지 않는다. axes 배열에는 info 로 남아 화면에 표시되고,
--     배점표는 D4 스코어링(낙찰 가능성 점수)에서 그대로 쓸 자산이다.
--   ※ 되돌리려면 ax_credit 의 class 리터럴만 'supp' 로 복원하면 된다.
--
-- 2026-07-29  [Phase1] 최종 마일스톤 1단계 — 정책 전가 + 축 재배치 + 판정 가드.
--   결정 기록: 프로젝트 claude/final_milestone_2026-07-29.md. 결함 번호는
--   claude/defect_register_2026-07-29.md 기준.
--   ◆ 정책 원칙 확정: 회원측 데이터 부재 → '미충족' / 공고측 데이터 미해석 → '확인필요'.
--     회원이 채울 수 있는 정보의 공백은 회원 책임으로 전가한다(D-10 은 결함 아닌 사양).
--       · ax_region      본점 미등록        확인필요 → 미충족
--       · ax_size        규모 미입력        확인필요 → 미충족
--       · ax_direct_prod 회사 품목 미등록   확인필요 → 미충족
--     ※ 존재하지 않는 company_id 호출의 구분은 함수가 하지 않는다 — 백엔드가 인증된
--        회원으로만 호출한다는 전제(백엔드 계층 책임).
--   ◆ 축 재배치: item supp → gate, direct_prod gate → supp.
--     품목 '등록'은 참가 자격이고, 직생확인서는 취득 절차가 있는 보완 가능 서류다.
--     item_rows 의 직생행 제외 필터(WHERE NOT direct_production_req)를 걷어냈다 —
--     '등록' 판정은 전 품목이 게이트(ax_item)에서, '직생확인' 판정만 supp(ax_direct_prod)
--     에서 본다. 안 걷으면 직생요구 품목(더 엄격한 요구)의 등록 여부가 더 느슨한 축으로
--     빠지는 역전이 생긴다. 부작용: 직생 placeholder(품목 미상, item_code NULL) 행이
--     게이트 확인필요로 유입 — 공고측 미해석이므로 원칙 부합.
--   ◆ [D-06] 인력·시공능력 dedup — 동일 요건 반복 적재(첨부 병합 중복)로 분모 부풀림
--     (실측 인력 503→245, 시공능력 137→84). 완전 동일 행만 접는다. 표기만 다른
--     부분 변형(라벨 상이·코드 동일 등)은 lic_dedup 같은 키 설계가 필요해 v2.
--   ◆ [D-07] min_value <= 0 가드 — 실적·시공능력 임계 0 이하는 비교식이 무조건 참이
--     되어 위양성 '충족'이 나던 것(라이브 54건). 미해석과 동일 취급 → 확인필요.
--   ◆ [D-11] ax_size detail 한글화 — 10축 중 유일하게 코드 대 코드(sme_only vs medium)
--     로 노출되던 detail 을 req_value/act_value 재사용으로 교체. 프론트는 detail 바인딩.
--   ◆ cert·credit 은 이번 단계에서 info 유지. 격상은 Phase 2(route_cert 되먹임·
--     cert_master 보강·min_grade 파싱·v1.9 재정규화) 후 해석률 실측을 보고 Phase 3 에서.
--     credit 격상 시에도 min_grade 파싱된 행만 supp(등급 미상은 축 미생성 — 배점 혼입 방지).
--
-- 2026-07-29(1.1)  [D-18] supp 3축(personnel·performance·capacity) 미해석 의미 통일.
--   BOOL_AND 이 NULL(미해석 행)을 건너뛰어 '미해석+통과 혼재'가 충족이 됐다 —
--   item·cert·direct_prod 는 미해석이 있으면 확인필요로 차단하는 것과 불일치.
--   status 를 미충족(FALSE 존재) → 확인필요(NULL 존재) → 충족 순으로 교체.
--   실측 노출(9001, 캐시 대조): '충족인데 미해석 보유' 11건 → 확인필요 이동 예상.
--   [D-19 완화] 인력 라벨에 분야(role_field) 접두 — 분야별 요건의 반복 표시를 구분.
--   평가의 분야 미반영(책임기술인 1명이 7개 분야 슬롯을 동시 충족 가능)은
--   회원 스키마(company_personnel 분야 차원)+입력 UI 확장이 필요한 v2 = D-19.
--
-- 2026-07-29(Phase3)  [격상] cert·credit 을 판정에 편입, info class 삭제.
--   근거(M1 실측, v1.9 재정규화 후): 인증 라이브 해석률 20.1% → 83.3%(미해석 171행) —
--   확인필요 유입 부담이 작아져 격상 가능 판정.
--   · ax_cert  : info → supp. 판정 의미는 종전 3-상태 그대로(회원 미보유 → 미충족 = 전가 정책,
--                공고측 미해석 → 확인필요). '취득하면 가능' 문구가 보완가능 서사와 정합.
--   · ax_credit: 전면 재설계(결정 3 — claude/final_milestone_2026-07-29.md).
--       min_grade 가 파싱된 행만 supp 축을 생성한다(M1 실측 15건, 전부 'B').
--       등급 미상(boolean-only) 요구와 credit_fp 위양성은 축 자체를 만들지 않는다 —
--       3차 실측(배점표 혼입)의 재발 방지. 비교는 grade_scale 순위(회사채 AAA~D, 작을수록 우량).
--       회원 등급 미등록 → 미충족(전가) / 회원 등급 형식 해석 불가 → 확인필요.
--   · per_bid 의 class IN ('gate','supp') 필터는 방어용으로 유지(이제 전 축이 gate/supp).
--   · [축0개] 분기의 ② 사례(info 축만 있는 공고)는 이 격상으로 소멸 — ①(행 없음)만 남는다.
--   ★ 출력 형식(RETURNS TABLE·axes 키)은 불변 — 백엔드 계약 무영향. 단 axes 의 class 값에서
--     'info' 가 사라지므로 프론트(참고 박스 렌더)에는 배포 전 필수 통보(M4).
--   ★ 되돌리려면 ax_cert 의 class 리터럴 'supp'→'info', ax_credit 블록을 이력의 1.1 판으로 복원.
--
-- 2026-07-29(v1.5)  [D-19 1단계] 인력 분야 분산 요구의 합산 비교 + placeholder 문구.
--   같은 (공고, 자격코드)의 분야별 요구를 pers_pool 로 합산 — "책임기술인 1명이
--   6개 분야 슬롯 동시 충족" 과대 충족(안 될 공고를 된다고 하는 방향) 차단.
--   미해석(qual_code NULL) 행은 종전 유지. 등급 계층 간 배정 문제는 의도적 보류 —
--   분야 매칭(2~3단계, 회원 스키마 분야 차원)은 claude/d19_personnel_roadmap.md.
--   [D-08 즉시분] '직접생산확인 요구(품목 미상)' 표시를 행동 지향 문구로 교체
--   (원문 name_raw 는 불변 — 표시 치환만. 실측: 라이브 단독결정 51건/가능 후보 32건).
-- ============================================================================

CREATE OR REPLACE FUNCTION public.compute_match_results(p_company_id bigint)
 RETURNS TABLE(company_id bigint, bid_ntce_no character varying, bid_ntce_ord character varying, verdict text, required integer, satisfied integer, gate_failed integer, need_review integer, axes jsonb, normalizer_version character varying)
 LANGUAGE sql
 STABLE
AS $function$
WITH params AS (
  SELECT p_company_id AS company_id,
         -- [TZ] 기준일. CURRENT_DATE 는 세션 TimeZone 의 날짜라 세션이 UTC 면 KST 00:00~09:00
         --      구간에서 하루 전이 나온다. live 조건은 AT TIME ZONE 으로 세션 무관인데
         --      직생·실적·인증 3축만 CURRENT_DATE 를 써서 규약이 갈려 있었다.
         --      → 같은 데이터·같은 회사인데 세션 tz 에 따라 판정이 달라진다. 여기서 통일한다.
         (now() AT TIME ZONE 'Asia/Seoul')::date AS today_kst
),
-- 라이브 공고 = 투찰마감 미도래. summary 전체 컬럼 보존(region_limit_type·normalizer_version 등).
live_bids AS (
  SELECT s.*
  FROM bid_require_summary s
  JOIN bid_table bt USING (bid_ntce_no, bid_ntce_ord)
  -- ↓ bid_info._OPEN_CONDITION 과 동일한 식 (동시 유지 대상 — 상단 주석 참조)
  WHERE (bt.bid_clse_dt IS NULL
      OR bt.bid_clse_dt > (now() AT TIME ZONE 'Asia/Seoul'))
),

-- ═══════════════ ① 면허 (gate) ═══════════════
lic_group AS (
  SELECT r.bid_ntce_no, r.bid_ntce_ord,
         -- [3차] or_group 이 두 네임스페이스로 갈려 있다(실측 100% 대응):
         --   license_field → '1','2',…   item_field(route:license) → 'x1','x2',…
         --   품목 경유는 "이 품목을 하려면 이 면허 중 하나"라는 유추라 의미상 OR 인데,
         --   x1..xN 이 각각 별도 AND 그룹으로 세어져 게이트를 과하게 조이고 있었다.
         --   → 품목 경유 전체를 단일 OR 그룹 'x' 로 접는다.
         CASE WHEN r.source = 'item_field' THEN 'x' ELSE r.or_group END AS or_group,
         BOOL_OR(cl.license_code IS NOT NULL) AS grp_match,
         BOOL_OR(r.license_code IS NULL)      AS grp_unresolved,
         -- [D3-b] OR 그룹 내부는 '또는'으로 묶는다. 표준명 없으면 원문(name_raw).
         COALESCE(STRING_AGG(DISTINCT COALESCE(r.license_name, r.name_raw), ' 또는 '),
                  '(미해석)')                  AS grp_label,
         -- [3차] 중복 판별 키. 라벨이 아니라 '면허코드 집합'으로 접어야 한다.
         --   같은 면허가 표준명/원문 두 표기로 들어오는 사례가 있어서
         --   (학술.연구용역 vs 학술연구용역업[업종코드1169],
         --    정보통신공사업 vs 정보통신공사업 면허증) 라벨 기준으로는 안 접힌다.
         --   미해석(code NULL)은 같다고 단정할 수 없으므로 원문을 키에 넣어 구분 유지.
         array_to_string(
           array_agg(DISTINCT COALESCE(r.license_code::text, 'RAW:' || r.name_raw)
                     ORDER BY COALESCE(r.license_code::text, 'RAW:' || r.name_raw)),
           '|')                               AS grp_key
  FROM bid_require_licenses r
  JOIN live_bids lb ON lb.bid_ntce_no = r.bid_ntce_no AND lb.bid_ntce_ord = r.bid_ntce_ord
  CROSS JOIN params p
  LEFT JOIN company_licenses cl ON cl.company_id = p.company_id AND cl.license_code = r.license_code
  GROUP BY 1,2,3
),
-- [3차] 코드 집합이 같은 그룹을 하나로 접는다(실측 건물(시설)관리용역 ×5 등).
--   grp_match/grp_unresolved 는 코드 집합이 같으면 반드시 같으므로 어느 행을 남겨도 된다.
--   라벨은 짧은 쪽을 남긴다 — 표준명이 원문보다 짧은 경향이 있다.
lic_dedup AS (
  SELECT DISTINCT ON (bid_ntce_no, bid_ntce_ord, grp_key)
         bid_ntce_no, bid_ntce_ord, grp_match, grp_unresolved, grp_label
  FROM lic_group
  ORDER BY bid_ntce_no, bid_ntce_ord, grp_key, length(grp_label), grp_label
),
ax_license AS (
  SELECT bid_ntce_no, bid_ntce_ord, 'license' AS axis, 'gate' AS class,
         CASE WHEN BOOL_AND(grp_match)                           THEN '충족'
              WHEN BOOL_OR(NOT grp_match AND NOT grp_unresolved) THEN '미충족'
              ELSE '확인필요' END AS status,
         COUNT(*) FILTER (WHERE grp_match) || '/' || COUNT(*) || ' 그룹 충족'          AS detail,
         left(STRING_AGG(grp_label, ', ' ORDER BY grp_label), 300)                    AS req_value,
         COALESCE(left(STRING_AGG(grp_label, ', ' ORDER BY grp_label)
                       FILTER (WHERE grp_match), 300), '(없음)')                      AS act_value
  FROM lic_dedup GROUP BY 1,2
),

-- ═══════════════ ② 지역 (gate) ═══════════════
hq AS (
  SELECT cr.region_code, rm.region_name          -- [D3-b] 표시용 이름 동반
  FROM company_regions cr
  CROSS JOIN params p
  LEFT JOIN region_master rm ON rm.region_code = cr.region_code
  WHERE cr.company_id = p.company_id AND cr.region_type = 'hq'
),
reg_rows AS (
  SELECT r.bid_ntce_no, r.bid_ntce_ord,
         BOOL_OR(r.flag = 'nationwide') AS any_nationwide,
         BOOL_OR(r.region_code IS NOT NULL AND EXISTS (
             SELECT 1 FROM hq
             WHERE hq.region_code = r.region_code
                OR hq.region_code LIKE r.region_code || '%')) AS hq_match,
         BOOL_OR(r.region_code IS NULL OR r.flag = 'site_ref') AS any_unresolved,
         -- [D3-b] region_code → region_name. 전국(nationwide) 행은 목록에서 뺀다.
         STRING_AGG(DISTINCT COALESCE(r.region_name, r.name_raw), ', ')
           FILTER (WHERE COALESCE(r.flag,'') <> 'nationwide')  AS req_regions
  FROM bid_require_regions r
  JOIN live_bids lb ON lb.bid_ntce_no = r.bid_ntce_no AND lb.bid_ntce_ord = r.bid_ntce_ord
  GROUP BY 1,2
),
ax_region AS (
  SELECT s.bid_ntce_no, s.bid_ntce_ord, 'region' AS axis, 'gate' AS class,
         CASE WHEN rr.any_nationwide OR rr.hq_match THEN '충족'
              -- [Phase1 정책] 본점 미등록은 종전 확인필요 → 미충족. 회원측 부재는 회원 책임.
              WHEN NOT EXISTS (SELECT 1 FROM hq)    THEN '미충족'
              WHEN rr.any_unresolved                THEN '확인필요'
              ELSE '미충족' END AS status,
         CASE WHEN rr.any_nationwide THEN '전국 (제한없음)'
              WHEN rr.hq_match       THEN '본점 소재지 충족'
              WHEN NOT EXISTS (SELECT 1 FROM hq) THEN '본점 소재지 미등록'
              ELSE '요구 지역 불일치/미해석' END AS detail,
         CASE WHEN rr.any_nationwide THEN '전국 (제한없음)'
              ELSE COALESCE(left(rr.req_regions, 300), '(미해석)') END               AS req_value,
         COALESCE((SELECT left(STRING_AGG(COALESCE(region_name, region_code), ', '), 300)
                     FROM hq), '(본점 미등록)')                                       AS act_value
  FROM live_bids s
  JOIN reg_rows rr USING (bid_ntce_no, bid_ntce_ord)
  WHERE s.region_limit_type = 'hq_location'
),

-- ═══════════════ ③ 규모 (gate) ═══════════════
comp_qual AS (
  SELECT q.* FROM company_qualifications q CROSS JOIN params p WHERE q.company_id = p.company_id
),
ax_size AS (
  -- [D-11/Phase1] detail 이 코드 대 코드(sme_only vs medium)로 노출되던 것을
  --   req_value/act_value(한글 라벨) 재사용으로 교체. 프론트는 detail 에 바인딩한다.
  SELECT bid_ntce_no, bid_ntce_ord, axis, class, status,
         req_value || ' 요구 · 우리 회사 ' || act_value AS detail,
         req_value, act_value
  FROM (
    SELECT z.bid_ntce_no, z.bid_ntce_ord, 'size' AS axis, 'gate' AS class,
           -- [Phase1 정책] 규모 미입력(company_qualifications 행 없음)은 종전 확인필요 → 미충족.
           CASE WHEN cq.company_id IS NULL THEN '미충족'
                WHEN z.size_limit = 'sme_only'   AND cq.company_size IN ('small','medium') THEN '충족'
                WHEN z.size_limit = 'small_only' AND cq.company_size = 'small'             THEN '충족'
                WHEN z.size_limit IN ('no_large','no_conglomerate')
                     AND cq.company_size <> 'conglomerate' THEN '충족'
                ELSE '미충족' END AS status,
           -- [D3-b] 코드 → 한글 라벨. CHECK 제약값과 1:1 (bid_require.sql / company_info.sql).
           CASE z.size_limit
             WHEN 'sme_only'        THEN '중소기업만'
             WHEN 'small_only'      THEN '소기업만'
             WHEN 'no_large'        THEN '대기업 제외'
             WHEN 'no_conglomerate' THEN '대기업집단 제외'
             ELSE COALESCE(z.size_limit, '(미해석)') END                               AS req_value,
           CASE cq.company_size
             WHEN 'small'        THEN '소기업'
             WHEN 'medium'       THEN '중기업'
             WHEN 'mid_large'    THEN '중견기업'
             WHEN 'conglomerate' THEN '대기업집단'
             ELSE COALESCE(cq.company_size, '(미등록)') END                            AS act_value
    FROM bid_require_size z
    JOIN live_bids lb ON lb.bid_ntce_no = z.bid_ntce_no AND lb.bid_ntce_ord = z.bid_ntce_ord
    LEFT JOIN comp_qual cq ON TRUE
  ) t
),

-- ═══════════════ ④a 직생 (supp — Phase1 격하) ═══════════════
--   직접생산확인증명서는 취득 절차가 있는 보완 가능 서류라 gate 가 아니라 supp 다.
--   '품목 등록' 자체의 판정은 ④b ax_item(gate)이 전 품목에 대해 수행한다.
dp_rows AS (
  SELECT i.bid_ntce_no, i.bid_ntce_ord,
         COUNT(*) AS n_req,
         COUNT(*) FILTER (WHERE i.item_code IS NULL) AS n_unres,
         -- [Phase1 정책] 공고 품목은 해석됐는데 회원 company_items 에 그 품목 행이 없는 경우.
         --   종전엔 '판단 불가 → 확인필요'로 뒀으나(구 D4-guard), 회원측 부재는 회원
         --   책임으로 전가한다(2026-07-29 결정) → 미충족. supp 라 verdict 는 '보완가능' 쪽.
         COUNT(*) FILTER (WHERE i.item_code IS NOT NULL AND ci.item_code IS NULL) AS n_nocomp,
         COUNT(*) FILTER (WHERE ci.item_code IS NOT NULL AND ci.has_direct_production
                            AND (ci.direct_prod_valid_until IS NULL
                                 OR ci.direct_prod_valid_until >= p.today_kst)) AS n_ok,
         -- [D3-b] 품목명 나열 ([D-08] placeholder 표시 치환 포함)
         STRING_AGG(DISTINCT REPLACE(COALESCE(i.item_name, i.name_raw),
             '직접생산확인 요구(품목 미상)',
             '직접생산확인 요구 — 대상 품목은 공고 원문 확인 필요'), ', ')             AS req_names,
         STRING_AGG(DISTINCT COALESCE(i.item_name, i.name_raw), ', ')
           FILTER (WHERE ci.item_code IS NOT NULL AND ci.has_direct_production
                     AND (ci.direct_prod_valid_until IS NULL
                          OR ci.direct_prod_valid_until >= p.today_kst))              AS ok_names
  FROM bid_require_items i
  JOIN live_bids lb ON lb.bid_ntce_no = i.bid_ntce_no AND lb.bid_ntce_ord = i.bid_ntce_ord
  CROSS JOIN params p
  LEFT JOIN company_items ci ON ci.company_id = p.company_id AND ci.item_code = i.item_code
  WHERE i.direct_production_req
  GROUP BY 1,2
),
ax_direct_prod AS (
  SELECT bid_ntce_no, bid_ntce_ord, 'direct_prod' AS axis, 'supp' AS class,
         -- [Phase1] 확인필요는 이제 공고측 미해석(n_unres)만. 회원 품목 미등록(n_nocomp)은
         --          미충족으로 흘러내린다(정책 전가). 미해석+미등록 혼재 시에도 미충족 —
         --          회원측 결격이 확정돼 있으면 나머지가 미해석이어도 결론은 같다.
         CASE WHEN n_ok = n_req                THEN '충족'
              WHEN n_ok + n_unres = n_req      THEN '확인필요'
              ELSE '미충족' END AS status,
         '직생확인 ' || n_ok || '/' || n_req
           || CASE WHEN n_nocomp > 0 THEN ' · 회사 품목 미등록 ' || n_nocomp ELSE '' END
           || CASE WHEN n_unres  > 0 THEN ' · 공고 품목 미해석 ' || n_unres  ELSE '' END AS detail,
         COALESCE(left(req_names, 300), '(미해석)')                                    AS req_value,
         COALESCE(left(ok_names, 300),
                  CASE WHEN n_nocomp > 0 THEN '(회사 품목 미등록)' ELSE '(없음)' END)     AS act_value
  FROM dp_rows
),

-- ═══════════════ ④b 품목 등록 (gate — Phase1 격상) ═══════════════
--   [Phase1] 직생행 제외 필터(WHERE NOT direct_production_req)를 걷어냈다.
--   '등록' 판정은 직생요구 여부와 무관하게 전 품목이 여기(게이트)를 지나고,
--   직생요구 품목의 '직생확인' 판정만 ④a(supp)가 담당한다. 필터를 남기면
--   더 엄격한 요구(직생요구 품목)의 등록 여부가 더 느슨한 축으로 빠지는 역전이 생긴다.
--   직생 placeholder(품목 미상, item_code NULL)는 n_unres → 게이트 확인필요로 유입.
item_rows AS (
  SELECT i.bid_ntce_no, i.bid_ntce_ord,
         COUNT(*) AS n_req,
         COUNT(*) FILTER (WHERE i.item_code IS NULL)      AS n_unres,
         COUNT(*) FILTER (WHERE ci.item_code IS NOT NULL) AS n_ok,
         -- [D-08] placeholder 원문을 행동 지향 문구로 치환(표시만 — 데이터 불변)
         STRING_AGG(DISTINCT REPLACE(COALESCE(i.item_name, i.name_raw),
             '직접생산확인 요구(품목 미상)',
             '직접생산확인 요구 — 대상 품목은 공고 원문 확인 필요'), ', ')             AS req_names,
         STRING_AGG(DISTINCT COALESCE(i.item_name, i.name_raw), ', ')
           FILTER (WHERE ci.item_code IS NOT NULL)                                    AS ok_names
  FROM bid_require_items i
  JOIN live_bids lb ON lb.bid_ntce_no = i.bid_ntce_no AND lb.bid_ntce_ord = i.bid_ntce_ord
  CROSS JOIN params p
  LEFT JOIN company_items ci ON ci.company_id = p.company_id AND ci.item_code = i.item_code
  GROUP BY 1,2
),
ax_item AS (
  SELECT bid_ntce_no, bid_ntce_ord, 'item' AS axis, 'gate' AS class,
         CASE WHEN n_ok = n_req           THEN '충족'
              WHEN n_ok + n_unres = n_req THEN '확인필요'
              ELSE '미충족' END AS status,
         '품목 등록 ' || n_ok || '/' || n_req AS detail,
         COALESCE(left(req_names, 300), '(미해석)')                                    AS req_value,
         COALESCE(left(ok_names, 300), '(없음)')                                       AS act_value
  FROM item_rows
),

-- ═══════════════ ⑤ 인력 (supp) — M1 rank 반영 ═══════════════
comp_person_total AS (
  SELECT COALESCE(SUM(cp.headcount),0) AS total
  FROM company_personnel cp CROSS JOIN params p WHERE cp.company_id = p.company_id
),
-- [D-06] 동일 인력 요건의 반복 적재(첨부 병합 중복)가 분모를 부풀린다(실측 503→245).
--   요건 정의 필드가 완전히 같은 행만 접는다. name_raw 는 키에서 뺀다 — 같은 요건이
--   표기만 다르게 반복되는 사례를 접기 위해서다.
pers_req AS (
  SELECT DISTINCT r.bid_ntce_no, r.bid_ntce_ord,
         r.qual_code, r.qual_name, r.role_field, r.grade_raw, r.headcount, r.method
  FROM bid_require_personnel r
  JOIN live_bids lb ON lb.bid_ntce_no = r.bid_ntce_no AND lb.bid_ntce_ord = r.bid_ntce_ord
  WHERE COALESCE(r.method,'') <> 'ignored'
),
-- [v1.5/D-19 1단계] 같은 자격코드의 분야 분산 요구를 '총 N명'으로 합산한다.
--   공고가 "책임기술인: 조경 1·소방 1·토목 1"로 요구하면 종전에는 행마다 "보유 ≥ 1?"을
--   물어 1명 보유로 3개 슬롯이 전부 충족됐다(과대 충족 — 안 될 공고를 된다고 하는 방향).
--   합산하면 "책임기술인 계 3명 이상?"이 된다 — 분야 구분은 못 해도 인원 규모는 맞는다.
--   · 대상: qual_code 해석된 요구만. 미해석(NULL)은 종전 행 단위 유지(확인필요 표면화).
--   · 의도적 단순화: 등급 계층 간 풀 겹침(중급 2명+고급 1명 배정 문제)은 건드리지 않는다.
--     분야 매칭·배정은 2~3단계(회원 스키마에 분야 차원) = claude/d19_personnel_roadmap.md.
--   · 오류 방향: 겸직 허용 공고에선 과소 판정이 되지만 supp 축이라 '보완가능'에서 멈춘다.
-- [v2.0/D-19 2~3단계] 분야 계열 정규화 — 요구측 role_field 원문 880종 트리아지(2026-07-29)
--   기반. 순서 = 우선순위(구체적 계열 먼저). 어느 계열에도 안 걸리면 NULL = 분야 무관
--   (현장대리인·감리원·PM 등 역할성 표기 — 전체 인력 풀과 비교).
--   회원측 company_personnel.field_family 도 같은 코드 체계(select 입력이라 정규화 불요).
field_norm AS (
  SELECT DISTINCT role_field,
    CASE
      WHEN role_field ~ '철도|궤도|열차|기관사'                                        THEN 'RAIL'
      WHEN role_field ~ '구조'                                                          THEN 'STRUCT'
      WHEN role_field ~ '토목|토질|지질|도로|공항|항만|해안|교량|터널|상하수도|수자원|수문' THEN 'CIVIL'
      WHEN role_field ~ '건축'                                                          THEN 'ARCH'
      WHEN role_field ~ '기계'                                                          THEN 'MECH'
      WHEN role_field ~ '전기|전력'                                                     THEN 'ELEC'
      WHEN role_field ~ '통신'                                                          THEN 'COMM'
      WHEN role_field ~ '조경'                                                          THEN 'LANDSCAPE'
      WHEN role_field ~ '소방|방재'                                                     THEN 'FIRE'
      WHEN role_field ~ '안전|보건'                                                     THEN 'SAFETY'
      WHEN role_field ~ '품질'                                                          THEN 'QUALITY'
      WHEN role_field ~ '환경|토양|수질|대기|소음|진동|폐기물|생태'                     THEN 'ENV'
      WHEN role_field ~ '측량|지형공간'                                                 THEN 'SURVEY'
      WHEN role_field ~ 'SW|소프트웨어|개발|데이터|DB|클라우드|정보보호|보안|네트워크|정보처리|시스템|IT|AI|UI/UX' THEN 'ICT'
      WHEN role_field ~ '디자인'                                                        THEN 'DESIGN'
      ELSE NULL
    END AS ffam
  FROM pers_req WHERE NULLIF(btrim(role_field),'') IS NOT NULL
),
pers_pool AS (
  -- [v2.0] 풀 단위 = (공고, 자격코드, 분야계열). 같은 계열 안에서만 인원 합산·비교.
  SELECT p.bid_ntce_no, p.bid_ntce_ord, p.qual_code, fn.ffam,
         MIN(p.qual_name) AS qual_name,
         SUM(p.headcount) AS headcount,
         COUNT(*)         AS n_fields,
         left(STRING_AGG(DISTINCT NULLIF(p.role_field,''), '·'), 60) AS fields_label
  FROM pers_req p
  LEFT JOIN field_norm fn ON fn.role_field = p.role_field
  WHERE p.qual_code IS NOT NULL
  GROUP BY 1, 2, 3, 4
),
pers_eval AS (
  -- ① 해석된 요구 — (공고, 자격코드, 분야계열) 풀 비교
  --   회원측 매칭 규칙: 요구 계열 NULL → 전체 풀 / 회원 field_family NULL(기존 데이터)
  --   → 분야 무관 인력으로 간주해 모든 계열 풀에 계상(관대한 하위호환 — 같은 인원이
  --   여러 계열에 중복 계상될 수 있는 한계는 문서화, 입력 UI 정착 후 조임).
  SELECT p.bid_ntce_no, p.bid_ntce_ord,
    left(COALESCE(m.qual_name, p.qual_name, '인력')
         || COALESCE('(' || NULLIF(p.fields_label, '') || ')', ''), 40)
      || ' 계 ' || p.headcount || '명' AS label,
    CASE
      WHEN m.qual_type = 'grade' AND m.grade_rank IS NOT NULL THEN
        (SELECT COALESCE(SUM(cp.headcount),0)
           FROM company_personnel cp
           JOIN personnel_grade_master gm ON gm.qual_code = cp.qual_code
           CROSS JOIN params pa
          WHERE cp.company_id = pa.company_id
            AND gm.qual_type = 'grade' AND gm.field = m.field
            AND gm.grade_rank >= m.grade_rank
            AND (p.ffam IS NULL OR cp.field_family IS NULL OR cp.field_family = p.ffam)) >= p.headcount
      ELSE
        (SELECT COALESCE(SUM(cp.headcount),0)
           FROM company_personnel cp CROSS JOIN params pa
          WHERE cp.company_id = pa.company_id AND cp.qual_code = p.qual_code
            AND (p.ffam IS NULL OR cp.field_family IS NULL OR cp.field_family = p.ffam)) >= p.headcount
    END AS met
  FROM pers_pool p
  LEFT JOIN personnel_grade_master m ON m.qual_code = p.qual_code
  UNION ALL
  -- ② 미해석 요구 — 종전 행 단위 (method='none' → 확인필요 / 그 외 → 총원 근사)
  SELECT r.bid_ntce_no, r.bid_ntce_ord,
    left(COALESCE(NULLIF(r.role_field,'') || ' ', '')
         || COALESCE(NULLIF(r.grade_raw,''), r.qual_name, '인력'), 40)
      || ' ' || r.headcount || '명' AS label,
    CASE
      WHEN r.method = 'none' THEN NULL
      ELSE (SELECT total FROM comp_person_total) >= r.headcount
    END AS met
  FROM pers_req r
  WHERE r.qual_code IS NULL
),
ax_personnel AS (
  SELECT bid_ntce_no, bid_ntce_ord, 'personnel' AS axis, 'supp' AS class,
         -- [Phase1.1/D-18] 의미론 통일: 미해석 행(NULL)은 충족을 차단한다(item·cert·direct_prod 와 동일).
         --   종전 BOOL_AND 은 NULL 을 건너뛰어 '미해석+통과 혼재'가 충족이 됐다.
         CASE WHEN BOOL_OR(met IS FALSE) THEN '미충족'
              WHEN BOOL_OR(met IS NULL)  THEN '확인필요'
              ELSE '충족' END AS status,
         '인력 요건 ' || COUNT(*) FILTER (WHERE met) || '/' || COUNT(*) AS detail,
         COALESCE(left(STRING_AGG(label, ', '), 300), '(미해석)')                      AS req_value,
         COALESCE(left(STRING_AGG(label, ', ') FILTER (WHERE met), 300), '(없음)')     AS act_value
  FROM pers_eval GROUP BY 1,2
),

-- ═══════════════ ⑥ 실적 (supp) ═══════════════
perf_rows AS (
  SELECT r.id, r.bid_ntce_no, r.bid_ntce_ord,
         -- [D3-b] 이름 컬럼이 없어 필드에서 조립한다. category_raw(분야) + 기간 + 금액/건수.
         -- [D-07] min_value <= 0 은 정규화 실패값 — 비교식이 무조건 참이 되므로 미해석 취급.
         CASE
           WHEN r.min_value IS NULL OR r.min_value <= 0 OR r.unit IS NULL OR r.parse_status = 'unparsed'
             THEN COALESCE(left(NULLIF(r.category_raw,''), 40), '(미해석 실적요건)')
           ELSE COALESCE(left(NULLIF(r.category_raw,''), 40) || ' ', '')
                || '최근 ' || r.period_years || '년 '
                || CASE WHEN r.unit = '원' AND r.min_value >= 100000000
                          THEN trim(to_char(r.min_value / 100000000.0, 'FM9999990.9')) || '억원'
                        WHEN r.unit = '원'
                          THEN to_char(r.min_value::numeric, 'FM999,999,999,999') || '원'
                        ELSE to_char(r.min_value::numeric, 'FM999999999') || '건' END
                || CASE WHEN r.agg_type = 'sum' THEN ' (합산)' ELSE '' END
         END AS label,
         CASE
           WHEN r.min_value IS NULL OR r.min_value <= 0 OR r.unit IS NULL OR r.parse_status = 'unparsed' THEN NULL
           WHEN r.unit = '원' AND COALESCE(r.agg_type,'single') = 'single' THEN
             (SELECT COALESCE(MAX(pr.contract_amt),0) FROM company_performance_records pr, params p
               WHERE pr.company_id = p.company_id
                 AND pr.end_date >= p.today_kst - (r.period_years * INTERVAL '1 year')
                 AND (r.field_code IS NULL OR pr.field_code = r.field_code)) >= r.min_value
           WHEN r.unit = '원' THEN
             (SELECT COALESCE(SUM(pr.contract_amt),0) FROM company_performance_records pr, params p
               WHERE pr.company_id = p.company_id
                 AND pr.end_date >= p.today_kst - (r.period_years * INTERVAL '1 year')
                 AND (r.field_code IS NULL OR pr.field_code = r.field_code)) >= r.min_value
           WHEN r.unit = '건' THEN
             (SELECT COUNT(*) FROM company_performance_records pr, params p
               WHERE pr.company_id = p.company_id
                 AND pr.end_date >= p.today_kst - (r.period_years * INTERVAL '1 year')
                 AND (r.field_code IS NULL OR pr.field_code = r.field_code)) >= r.min_value
         END AS met
  FROM bid_require_performances r
  JOIN live_bids lb ON lb.bid_ntce_no = r.bid_ntce_no AND lb.bid_ntce_ord = r.bid_ntce_ord
),
ax_performance AS (
  SELECT bid_ntce_no, bid_ntce_ord, 'performance' AS axis, 'supp' AS class,
         -- [Phase1.1/D-18] 의미론 통일: 미해석 행(NULL)은 충족을 차단한다(item·cert·direct_prod 와 동일).
         --   종전 BOOL_AND 은 NULL 을 건너뛰어 '미해석+통과 혼재'가 충족이 됐다.
         CASE WHEN BOOL_OR(met IS FALSE) THEN '미충족'
              WHEN BOOL_OR(met IS NULL)  THEN '확인필요'
              ELSE '충족' END AS status,
         '실적 요건 ' || COUNT(*) FILTER (WHERE met) || '/' || COUNT(*) AS detail,
         COALESCE(left(STRING_AGG(label, ', '), 300), '(미해석)')                      AS req_value,
         COALESCE(left(STRING_AGG(label, ', ') FILTER (WHERE met), 300), '(없음)')     AS act_value
  FROM perf_rows GROUP BY 1,2
),

-- ═══════════════ ⑦ 시공능력 (supp) ═══════════════
-- [D-06] 동일 시공능력 요건의 반복 적재 접기(실측 137→84). (업종, 임계값, 파싱상태)가
--   완전히 같은 행만 접는다. 미해석 다건이 1건으로 접히는 건 감수 — met 이 전부 NULL
--   이라 판정 불변, 분모 표시만 보수적으로 준다.
cap_req AS (
  SELECT DISTINCT r.bid_ntce_no, r.bid_ntce_ord, r.license_code, r.min_value, r.parse_status
  FROM bid_require_capacity r
  JOIN live_bids lb ON lb.bid_ntce_no = r.bid_ntce_no AND lb.bid_ntce_ord = r.bid_ntce_ord
),
cap_rows AS (
  SELECT r.bid_ntce_no, r.bid_ntce_ord,
         -- [D3-b] 업종(license_master) + 금액. license_code NULL 이면 총액 요건.
         -- [D-07] min_value <= 0 은 정규화 실패값 → 미해석 취급.
         COALESCE(lm.license_name, '총액') || ' '
           || CASE WHEN r.min_value IS NULL OR r.min_value <= 0 THEN '(미해석)'
                   WHEN r.min_value >= 100000000
                     THEN trim(to_char(r.min_value / 100000000.0, 'FM9999990.9')) || '억원'
                   ELSE to_char(r.min_value::numeric, 'FM999,999,999,999') || '원' END AS label,
         CASE
           WHEN r.min_value IS NULL OR r.min_value <= 0 OR r.parse_status = 'unparsed' THEN NULL
           WHEN r.license_code IS NOT NULL THEN
             COALESCE((SELECT ce.eval_amount FROM company_capacity_evals ce, params p
                        WHERE ce.company_id = p.company_id AND ce.license_code = r.license_code),0) >= r.min_value
           ELSE
             (SELECT COALESCE(SUM(ce.eval_amount),0) FROM company_capacity_evals ce, params p
               WHERE ce.company_id = p.company_id) >= r.min_value
         END AS met
  FROM cap_req r
  LEFT JOIN license_master lm ON lm.license_code = r.license_code
),
ax_capacity AS (
  SELECT bid_ntce_no, bid_ntce_ord, 'capacity' AS axis, 'supp' AS class,
         -- [Phase1.1/D-18] 의미론 통일: 미해석 행(NULL)은 충족을 차단한다(item·cert·direct_prod 와 동일).
         --   종전 BOOL_AND 은 NULL 을 건너뛰어 '미해석+통과 혼재'가 충족이 됐다.
         CASE WHEN BOOL_OR(met IS FALSE) THEN '미충족'
              WHEN BOOL_OR(met IS NULL)  THEN '확인필요'
              ELSE '충족' END AS status,
         '시공능력 ' || COUNT(*) FILTER (WHERE met) || '/' || COUNT(*) AS detail,
         COALESCE(left(STRING_AGG(label, ', '), 300), '(미해석)')                      AS req_value,
         COALESCE(left(STRING_AGG(label, ', ') FILTER (WHERE met), 300), '(없음)')     AS act_value
  FROM cap_rows GROUP BY 1,2
),

-- ═══════════════ ⑧ 신용 (supp — Phase3 격상, min_grade 有 행만) ═══════════════
-- credit_rating_req 근거 스니펫만 뽑는다. extraction_evidence 는 두 세대로 shape 가 다르다:
--   구(백필) = [{page, field, snippet}, ...]        · 신(일일) = {field: [{page, snippet, ...}]}
credit_ev AS (
  SELECT lb.bid_ntce_no, lb.bid_ntce_ord, e->>'snippet' AS snip
  FROM live_bids lb
  JOIN bid_table bt ON bt.bid_ntce_no = lb.bid_ntce_no AND bt.bid_ntce_ord = lb.bid_ntce_ord,
       LATERAL jsonb_array_elements(bt.extraction_evidence) AS e
  WHERE jsonb_typeof(bt.extraction_evidence) = 'array'
    AND e->>'field' = 'credit_rating_req'
  UNION ALL
  SELECT lb.bid_ntce_no, lb.bid_ntce_ord, e->>'snippet'
  FROM live_bids lb
  JOIN bid_table bt ON bt.bid_ntce_no = lb.bid_ntce_no AND bt.bid_ntce_ord = lb.bid_ntce_ord,
       LATERAL jsonb_array_elements(bt.extraction_evidence -> 'credit_rating_req') AS e
  WHERE jsonb_typeof(bt.extraction_evidence) = 'object'
    AND jsonb_typeof(bt.extraction_evidence -> 'credit_rating_req') = 'array'
),
-- 근거가 전부 결격사유/입찰보증금 조항인 공고 = 신용등급 요구가 아님(위양성)
credit_fp AS (
  SELECT bid_ntce_no, bid_ntce_ord
  FROM credit_ev
  GROUP BY 1,2
  HAVING BOOL_AND(snip ~ '(채무불이행|금융질서\s*문란|신용정보\s*관리규약)')
),
-- [Phase3] 신용등급 순위표 — 회사채 계열(AAA~D, ± 수식). 작을수록 우량. CP(A1~) 체계는 v2.
--   min_grade 는 어댑터 _GRADE_RE 가 이 집합의 부분집합만 생산하므로 JOIN 이 곧 유효성 필터다.
grade_scale(g_txt, g_rank) AS (
  VALUES ('AAA',1),('AA+',2),('AA',3),('AA-',4),('A+',5),('A',6),('A-',7),
         ('BBB+',8),('BBB',9),('BBB-',10),('BB+',11),('BB',12),('BB-',13),
         ('B+',14),('B',15),('B-',16),('CCC+',17),('CCC',18),('CCC-',19),
         ('CC',20),('C',21),('D',22)
),
ax_credit AS (
  -- [Phase3/결정3] min_grade 파싱행만 supp 축 생성. 등급 미상(boolean-only)·credit_fp 위양성은
  --   축 미생성 — 적격심사 배점표를 참가자격으로 오인한 건이 판정에 못 들어오게 하는 구조적 차단.
  --   (JOIN grade_scale rr 이 min_grade IS NOT NULL + 유효 등급 필터를 겸한다)
  SELECT c.bid_ntce_no, c.bid_ntce_ord, 'credit' AS axis, 'supp' AS class,
         CASE WHEN cq.company_id IS NULL OR cq.credit_rating IS NULL THEN '미충족'  -- 회원측 부재(전가)
              WHEN ar.g_rank IS NULL THEN '확인필요'          -- 회원 등급값 형식 해석 불가
              WHEN ar.g_rank <= rr.g_rank THEN '충족'          -- 우량(순위 작음)할수록 충족
              ELSE '미충족' END AS status,
         '요구 ' || c.min_grade || ' 이상 · 우리 회사 '
           || COALESCE(cq.credit_rating, '(미등록)') AS detail,
         c.min_grade || ' 이상'                                                        AS req_value,
         COALESCE(cq.credit_rating, '(미등록)')                                        AS act_value
  FROM bid_require_credit c
  JOIN live_bids lb ON lb.bid_ntce_no = c.bid_ntce_no AND lb.bid_ntce_ord = c.bid_ntce_ord
  JOIN grade_scale rr ON rr.g_txt = c.min_grade
  LEFT JOIN credit_fp fp ON fp.bid_ntce_no = c.bid_ntce_no AND fp.bid_ntce_ord = c.bid_ntce_ord
  LEFT JOIN comp_qual cq ON TRUE
  LEFT JOIN grade_scale ar ON ar.g_txt = upper(btrim(cq.credit_rating))
  WHERE c.required AND fp.bid_ntce_no IS NULL
),

-- ═══════════════ ⑨ 인증 (supp — Phase3 격상) ═══════════════
cert_rows AS (
  SELECT r.bid_ntce_no, r.bid_ntce_ord,
         COUNT(*) AS n_req,
         COUNT(*) FILTER (WHERE r.cert_code IS NULL) AS n_unres,
         COUNT(*) FILTER (WHERE cc.cert_code IS NOT NULL
                            AND (cc.valid_until IS NULL OR cc.valid_until >= p.today_kst)) AS n_ok,
         STRING_AGG(DISTINCT r.cert_name, ', ')
           FILTER (WHERE r.cert_code IS NOT NULL AND cc.cert_code IS NULL) AS missing_names,
         STRING_AGG(DISTINCT COALESCE(r.cert_name, r.name_raw), ', ')                 AS req_names,
         STRING_AGG(DISTINCT COALESCE(r.cert_name, r.name_raw), ', ')
           FILTER (WHERE cc.cert_code IS NOT NULL
                     AND (cc.valid_until IS NULL OR cc.valid_until >= p.today_kst))   AS ok_names
  FROM bid_require_certs r
  JOIN live_bids lb ON lb.bid_ntce_no = r.bid_ntce_no AND lb.bid_ntce_ord = r.bid_ntce_ord
  CROSS JOIN params p
  LEFT JOIN company_certs cc ON cc.company_id = p.company_id AND cc.cert_code = r.cert_code
  GROUP BY 1,2
),
ax_cert AS (
  -- [Phase3] info → supp. 판정 의미는 종전 그대로 — 회원 미보유 → 미충족(전가),
  --   공고측 미해석(n_unres) → 확인필요. M1 실측 라이브 미해석 171행이라 유입 부담 소.
  SELECT bid_ntce_no, bid_ntce_ord, 'cert' AS axis, 'supp' AS class,
         CASE WHEN n_ok = n_req           THEN '충족'
              WHEN n_ok + n_unres = n_req THEN '확인필요'
              ELSE '미충족' END AS status,
         '인증 ' || n_ok || '/' || n_req
           || COALESCE(' · 취득하면 가능: ' || missing_names, '') AS detail,
         COALESCE(left(req_names, 300), '(미해석)')                                    AS req_value,
         COALESCE(left(ok_names, 300), '(없음)')                                       AS act_value
  FROM cert_rows
),

-- ═══════════════ 축 통합 → verdict ═══════════════
axis_all AS (
  SELECT * FROM ax_license     UNION ALL
  SELECT * FROM ax_region      UNION ALL
  SELECT * FROM ax_size        UNION ALL
  SELECT * FROM ax_direct_prod UNION ALL
  SELECT * FROM ax_item        UNION ALL
  SELECT * FROM ax_personnel   UNION ALL
  SELECT * FROM ax_performance UNION ALL
  SELECT * FROM ax_capacity    UNION ALL
  SELECT * FROM ax_credit      UNION ALL
  SELECT * FROM ax_cert
),
per_bid AS (
  SELECT bid_ntce_no, bid_ntce_ord,
         COUNT(*) FILTER (WHERE class IN ('gate','supp'))                        AS required,
         COUNT(*) FILTER (WHERE class IN ('gate','supp') AND status = '충족')     AS satisfied,
         COUNT(*) FILTER (WHERE class = 'gate'           AND status = '미충족')   AS gate_failed,
         COUNT(*) FILTER (WHERE class IN ('gate','supp') AND status = '확인필요') AS need_review,
         JSONB_AGG(JSONB_BUILD_OBJECT(
             'axis', axis, 'class', class, 'status', status, 'detail', detail,
             'required', req_value, 'actual', act_value)
           ORDER BY CASE class WHEN 'gate' THEN 1 WHEN 'supp' THEN 2 ELSE 3 END, axis) AS axes
  FROM axis_all GROUP BY 1,2
)
SELECT p.company_id,
       s.bid_ntce_no, s.bid_ntce_ord,
       CASE WHEN COALESCE(b.required,0) = 0    THEN '확인필요'   -- [축0개] 아래 주석
            WHEN COALESCE(b.gate_failed,0) > 0 THEN '불가'
            WHEN COALESCE(b.need_review,0) > 0 THEN '확인필요'
            WHEN COALESCE(b.satisfied,0) < COALESCE(b.required,0) THEN '보완가능'
            ELSE '가능' END::TEXT,
       COALESCE(b.required,0)::INT,
       COALESCE(b.satisfied,0)::INT,
       COALESCE(b.gate_failed,0)::INT,
       COALESCE(b.need_review,0)::INT,
       COALESCE(b.axes, '[]'::jsonb),
       s.normalizer_version
FROM live_bids s
CROSS JOIN params p
LEFT JOIN per_bid b USING (bid_ntce_no, bid_ntce_ord);
$function$
;

-- ── 사용 ────────────────────────────────────────────────────
-- on-read (백엔드가 로그인/새로고침 때 호출):
--   SELECT verdict, required, satisfied, axes FROM compute_match_results(9001)
--   ORDER BY (verdict='가능') DESC;                       -- 가능부터
--
-- precompute (match_results에 캐시하려면):
--   DELETE FROM match_results WHERE company_id = 9001;
--   INSERT INTO match_results
--     (company_id, bid_ntce_no, bid_ntce_ord, verdict, required, satisfied,
--      gate_failed, need_review, axes, normalizer_version)
--   SELECT * FROM compute_match_results(9001);
--
-- 검증(라이브 한정 분포):
--   SELECT verdict, count(*) FROM compute_match_results(9001) GROUP BY 1 ORDER BY 2 DESC;
