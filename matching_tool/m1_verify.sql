-- ============================================================================
-- M1 — v1.9 전건 재정규화 직후 검증 (2026-07-29)
--   전제: 재정규화 완료(워커 6개 '완료' 합산 19,342·실패 0) + 람다 동시성 복원.
--   전부 읽기 전용. ⑥ 캐시 재계산만 쓰기 — 반드시 ①~⑤ 확인 후 마지막에.
-- ============================================================================

-- ─────────────────────────────────────────────────────────────
-- ① 버전 전환 확인 — 전건이 v1.9 인가
-- ─────────────────────────────────────────────────────────────
SELECT normalizer_version, COUNT(*) AS 공고수
  FROM bid_require_summary GROUP BY 1 ORDER BY 2 DESC;

-- ─────────────────────────────────────────────────────────────
-- ② 인증 해석률 — 핵심 지표
--    기대: family 최대 세력, none ~1,600 (재정규화 전 5,778).
-- ─────────────────────────────────────────────────────────────
SELECT method, COUNT(*) AS 행수,
       COUNT(*) FILTER (WHERE cert_code IS NULL) AS code_null
  FROM bid_require_certs GROUP BY 1 ORDER BY 2 DESC;

-- ②-2 라이브 한정 해석률 (Phase 3 격상 판단 지표. 재정규화 전 20.1%)
SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE r.cert_code IS NOT NULL) / NULLIF(COUNT(*),0), 1) AS 라이브_해석률_pct,
       COUNT(*) FILTER (WHERE r.cert_code IS NULL) AS 라이브_미해석행
  FROM bid_require_certs r
  JOIN bid_table b USING (bid_ntce_no, bid_ntce_ord)
 WHERE b.bid_clse_dt IS NULL OR b.bid_clse_dt > (now() AT TIME ZONE 'Asia/Seoul');

-- ②-3 잔여 미해석 top 20 (다음 트리아지 라운드 후보 — 가족 규칙 누수 확인)
SELECT name_raw, COUNT(*) AS 건수
  FROM bid_require_certs WHERE cert_code IS NULL
 GROUP BY 1 ORDER BY 2 DESC LIMIT 20;

-- ─────────────────────────────────────────────────────────────
-- ③ 되먹임(cert_feedback) 흡수량 — D-02/D-04/D-05 실측
-- ─────────────────────────────────────────────────────────────
SELECT '규모 보강(route:cert)' AS 항목, COUNT(*) AS 건수
  FROM bid_require_size WHERE method = 'route:cert'
UNION ALL
SELECT '신용 보강(route:cert)', COUNT(*)
  FROM bid_require_credit WHERE method = 'route:cert'
UNION ALL
SELECT '신용 min_grade 파싱됨', COUNT(*)
  FROM bid_require_credit WHERE min_grade IS NOT NULL
UNION ALL
SELECT '직생 품목코드 회수(route:cert)', COUNT(*)
  FROM bid_require_items WHERE method = 'route:cert'
UNION ALL
SELECT '직생 placeholder 잔여(재정규화 전 632)', COUNT(*)
  FROM bid_require_items WHERE name_raw = '직접생산확인 요구(품목 미상)';

-- ③-2 min_grade 분포 (Phase 3 credit supp 축 생성 대상 규모)
SELECT min_grade, COUNT(*) AS 건수
  FROM bid_require_credit WHERE min_grade IS NOT NULL
 GROUP BY 1 ORDER BY 2 DESC;

-- ─────────────────────────────────────────────────────────────
-- ④ G-01 치유 확인 — 7-28 13시 윈도우 모순 표본 10건에 certs 행이 생겼는가
--    기대: 전건 has_certs = true (요구 문자열이 전부 라우팅 대상이면 size/credit 행으로 확인)
-- ─────────────────────────────────────────────────────────────
SELECT v.bid_ntce_no, v.bid_ntce_ord,
       s.normalizer_version,
       EXISTS (SELECT 1 FROM bid_require_certs c
                WHERE c.bid_ntce_no = v.bid_ntce_no AND c.bid_ntce_ord = v.bid_ntce_ord) AS has_certs
  FROM (VALUES
        ('R26BK01641081','001'), ('R26BK01647926','000'), ('R26BK01650362','000'),
        ('R26BK01650366','000'), ('R26BK01650372','000'), ('R26BK01650384','000'),
        ('R26BK01650563','000'), ('R26BK01650900','000'), ('R26BK01651990','000'),
        ('R26BK01652069','000')) AS v(bid_ntce_no, bid_ntce_ord)
  LEFT JOIN bid_require_summary s
         ON s.bid_ntce_no = v.bid_ntce_no AND s.bid_ntce_ord = v.bid_ntce_ord;

-- ─────────────────────────────────────────────────────────────
-- ⑤ verdict 이동 미리보기 — 캐시(재정규화 전) vs 신규 계산 (9001, 읽기 전용)
--    기대 방향: 규모·직생 보강분이 축을 만들어 '확인필요(축0개)' 감소.
-- ─────────────────────────────────────────────────────────────
WITH now_res AS (SELECT * FROM compute_match_results(9001))
SELECT COALESCE(c.verdict, '(신규)') AS 이전_캐시,
       COALESCE(n.verdict, '(마감 이탈)') AS 이후,
       COUNT(*) AS 건수
  FROM match_results c
  FULL JOIN now_res n USING (bid_ntce_no, bid_ntce_ord)
 WHERE COALESCE(c.company_id, 9001) = 9001
 GROUP BY 1, 2 ORDER BY 3 DESC;

-- ─────────────────────────────────────────────────────────────
-- ⑥ 【⑤ 확인 후 마지막】 캐시 재계산 — 회원별 반복
-- ─────────────────────────────────────────────────────────────
-- DELETE FROM match_results WHERE company_id = 9001;
-- INSERT INTO match_results
--   (company_id, bid_ntce_no, bid_ntce_ord, verdict, required, satisfied,
--    gate_failed, need_review, axes, normalizer_version)
-- SELECT * FROM compute_match_results(9001);
-- SELECT count(*) FROM match_results WHERE company_id = 9001;
