"""조달청 물품목록정보서비스 전수 수집 → item_code_master 시드 SQL 생성.

사용법:
    G2B_SERVICE_KEY=<Decoding 서비스키> python fetch_item_code_master.py

수집 대상 (ThngListInfoService02):
    - getPrdctClsfcNoUnit8Info02  : 물품분류 8단위 (품명)      → parent 없음
    - getPrdctClsfcNoUnit10Info02 : 물품분류 10단위 (세부품명) → parent = 앞 8자리

출력:
    item_codes_raw_unit8.json / item_codes_raw_unit10.json  — API 원본 (감사/재현용)
    007_seed_item_code_master.sql — DDL(IF NOT EXISTS) + 전수 INSERT (1,000행 단위 분할)

비고:
    - is_sme_product(중기간 경쟁제품)는 중기부 고시 파일로 별도 갱신 (이 스크립트 범위 밖)
    - 이름 별칭은 생성하지 않음 — 물품 매칭은 코드 기반이라 별칭 사전 불필요,
      프로필 UI 검색은 item_code_master.item_name을 직접 검색
"""

import json
import os
import sys
import time
import requests

BASE_URL = "http://apis.data.go.kr/1230000/ao/ThngListInfoService02"
PAGE_SIZE = 500
CHUNK = 1000  # INSERT 문 분할 단위 (거대 단일 문장 방지)

OPERATIONS = [
    # (오퍼레이션, 코드 필드, 이름 필드, 원본저장 파일, 레벨)
    ("getPrdctClsfcNoUnit8Info02",  "prdctClsfcNo",     "prdctClsfcNoNm",     "item_codes_raw_unit8.json",  8),
    ("getPrdctClsfcNoUnit10Info02", "dtilPrdctClsfcNo", "dtilPrdctClsfcNoNm", "item_codes_raw_unit10.json", 10),
]

def fetch_all(service_key: str, operation: str) -> list[dict]:
    items, page = [], 1
    while True:
        params = {"serviceKey": service_key, "numOfRows": PAGE_SIZE, "pageNo": page, "type": "json"}
        r = requests.get(f"{BASE_URL}/{operation}", params=params, timeout=60)
        r.raise_for_status()
        data = r.json()
        if "response" not in data:  # 나라장터: 에러도 HTTP 200
            sys.exit(f"[에러] 비정상 응답 ({operation} page={page}): {str(data)[:300]}")
        body = data["response"]["body"]
        total = int(body["totalCount"])
        batch = body.get("items") or []
        if isinstance(batch, dict):
            batch = [batch]
        items.extend(batch)
        print(f"  {operation} page {page}: +{len(batch)} (누적 {len(items)}/{total})")
        if len(items) >= total or not batch:
            return items
        page += 1
        time.sleep(0.15)


def esc(s) -> str:
    return str(s).replace("'", "''")


def main():
    key = os.environ.get("G2B_SERVICE_KEY")
    if not key:
        sys.exit("G2B_SERVICE_KEY 환경변수를 설정하세요 (Decoding 키)")

    all_rows = []  # (code, name, parent, active)
    counts = {}
    for op, code_f, name_f, raw_file, level in OPERATIONS:
        print(f"전수 조회: {op} ...")
        items = fetch_all(key, op)
        with open(raw_file, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False)
        n = 0
        for it in items:
            code = str(it.get(code_f, "")).strip()
            name = str(it.get(name_f, "")).strip()
            if not code or not name:
                continue
            parent = code[:8] if level == 10 else None
            active = "TRUE" if str(it.get("useYn", "Y")).strip() != "N" else "FALSE"
            all_rows.append((code, name, parent, active))
            n += 1
        counts[level] = n
        print(f"  → {n}건 (원본: {raw_file})")

    L = []
    L.append("-- ============================================================================")
    L.append("-- 007: item_code_master 전수 시드 (조달청 물품목록정보서비스)")
    L.append(f"-- 8단위(품명) {counts.get(8, 0)}건 + 10단위(세부품명) {counts.get(10, 0)}건")
    L.append("-- is_sme_product는 중기부 고시 파일로 별도 갱신. 이름 별칭 미생성(코드 기반 매칭).")
    L.append("-- ============================================================================\n")
    L.append("CREATE TABLE IF NOT EXISTS item_code_master (")
    L.append("  item_code      VARCHAR(10) PRIMARY KEY,   -- 8자리=품명 / 10자리=세부품명")
    L.append("  item_name      VARCHAR(300) NOT NULL,")
    L.append("  parent_code    VARCHAR(10),               -- 10자리 → 앞 8자리")
    L.append("  is_active      BOOLEAN DEFAULT TRUE,      -- useYn")
    L.append("  is_sme_product BOOLEAN DEFAULT FALSE      -- 중기간 경쟁제품 (별도 갱신)")
    L.append(");\n")

    for i in range(0, len(all_rows), CHUNK):
        chunk = all_rows[i:i + CHUNK]
        L.append("INSERT INTO item_code_master (item_code, item_name, parent_code, is_active) VALUES")
        vals = []
        for code, name, parent, active in chunk:
            p = f"'{parent}'" if parent else "NULL"
            vals.append(f"('{esc(code)}', '{esc(name)}', {p}, {active})")
        L.append(",\n".join(vals))
        L.append("ON CONFLICT (item_code) DO NOTHING;\n")

    L.append("""-- ─── 검수 쿼리: bid_table item_codes 커버리지 (distinct 3,517 기준) ───
-- SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE im.item_code IS NOT NULL) / COUNT(*), 1)
--          AS coverage_pct,
--        COUNT(*) AS distinct_codes,
--        COUNT(*) FILTER (WHERE im.item_code IS NULL) AS unmatched
-- FROM (SELECT DISTINCT ic->>'code' AS code
--       FROM bid_table, LATERAL jsonb_array_elements(item_codes) AS ic
--       WHERE jsonb_typeof(item_codes) = 'array') t
-- LEFT JOIN item_code_master im ON im.item_code = t.code;
--
-- 미매칭 실물 확인 (4자리 업종코드 혼입 여부 등):
-- SELECT t.code, LENGTH(t.code) AS len FROM (위와 동일 서브쿼리) t
-- LEFT JOIN item_code_master im ON im.item_code = t.code
-- WHERE im.item_code IS NULL LIMIT 30;""")

    out = "007_seed_item_code_master.sql"
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print(f"시드 생성: {out} (총 {len(all_rows)}행, {CHUNK}행 단위 INSERT 분할)")


if __name__ == "__main__":
    main()