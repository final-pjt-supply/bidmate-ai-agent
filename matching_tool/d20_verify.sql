-- ============================================================================
-- d20_verify.sql — v2.1(면허 act_value 표시 수정) 배포 검증 (2026-07-30)
--   순서: compute_match_results.sql(v2.1) 실행 → ①② → 통과 시 ③(즉시 반영용, 선택)
-- ============================================================================

-- ① 판정 무이동 — 기대: 불일치 0 (표시층만 바뀌었으므로)
SELECT COUNT(*) AS 불일치
FROM match_results o
JOIN compute_match_results(9001) n
  ON n.bid_ntce_no = o.bid_ntce_no AND n.bid_ntce_ord = o.bid_ntce_ord
WHERE o.company_id = 9001
  AND (n.verdict <> o.verdict OR n.required <> o.required OR n.satisfied <> o.satisfied
    OR n.gate_failed <> o.gate_failed OR n.need_review <> o.need_review);

-- ② 면허 축 표본 — '우리 회사'(actual)가 요구 전문 복제가 아니라 매칭 면허만 나오는지.
--   기대: 충족 행에서 actual 이 required 보다 짧고, 회사 보유 면허명이 보인다.
--   동일잔존 = required 와 actual 이 여전히 완전히 같은 충족 행 수 — 0 이 목표지만,
--   요구가 단일 면허 1그룹뿐인 공고는 정당하게 같을 수 있다(그건 결함 아님).
SELECT
  COUNT(*) FILTER (WHERE ax->>'status' = '충족'
                     AND ax->>'required' = ax->>'actual'
                     AND (ax->>'required') LIKE '%또는%') AS 동일잔존_다중대안,
  COUNT(*) FILTER (WHERE ax->>'status' = '충족')          AS 충족행_전체
FROM compute_match_results(9001) n,
     LATERAL jsonb_array_elements(n.axes) ax
WHERE ax->>'axis' = 'license';

-- ②b 눈으로 표본 10건
SELECT n.bid_ntce_no, ax->>'status' AS status,
       left(ax->>'required', 120) AS required, left(ax->>'actual', 120) AS actual
FROM compute_match_results(9001) n,
     LATERAL jsonb_array_elements(n.axes) ax
WHERE ax->>'axis' = 'license' AND ax->>'status' = '충족'
LIMIT 10;

-- ③ (선택) 즉시 반영 — 안 돌리면 야간 전체 재계산(#80, KST 04시)이 자동 반영.
-- BEGIN;
-- DELETE FROM match_results WHERE company_id = 9001;
-- INSERT INTO match_results
--   (company_id, bid_ntce_no, bid_ntce_ord, verdict, required, satisfied,
--    gate_failed, need_review, axes, normalizer_version)
-- SELECT * FROM compute_match_results(9001);
-- COMMIT;
