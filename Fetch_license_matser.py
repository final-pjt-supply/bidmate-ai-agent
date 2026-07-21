"""나라장터 업종 및 근거법규서비스 전수 수집 → license_master 시드 SQL 생성.

사용법:
    G2B_SERVICE_KEY=<Decoding 서비스키> python fetch_license_master.py

출력:
    industry_codes_raw.json     — API 원본 전체 (감사/재현용)
    seed_license_master_v2.sql  — license_master 전수 + 별칭 INSERT (공식 업종코드 기준)

주의:
    - 기존 자체 코드(CNST_G_* 등) 26종을 공식 코드로 교체하는 정리 SQL이 파일 하단에
      주석으로 포함됨 — 팀 확인 후 주석 해제하여 실행.
    - 에러 응답이 HTTP 200으로 오는 나라장터 특성 반영('response' 키 확인).
"""

import json
import os
import sys
import time
import requests

BASE_URL = "http://apis.data.go.kr/1230000/ao/IndstrytyBaseLawrgltInfoService"
OPERATION = "getIndstrytyBaseLawrgltInfoList"
PAGE_SIZE = 500

# ── 기존 seed_license_master.sql(v0.1)의 별칭 자산 재사용 ──
# 표준명 → 표기 변형 목록 (canonical_code는 공식 업종코드로 자동 재매핑됨)
VARIANTS = {
    "토목공사업": ["토목공사업 등록", "토목공사업을 등록한 자", "토목공사업을 등록한 업체",
                "종합건설업(토목공사업)", "종합건설업 중 토목공사업"],
    "건축공사업": ["건축공사업 등록", "건축공사업을 등록한 자", "건축공사업을 등록한 업체"],
    "토목건축공사업": ["토목건축공사업 등록", "토목건축공사업을 등록한 자"],
    "산업·환경설비공사업": ["산업·환경설비공사업 등록", "산업.환경설비공사업",
                      "산업.환경설비공사업 등록", "산업·환경설비공사업을 등록한 자"],
    "조경공사업": [],
    "전기공사업": ["전기공사업 등록", "전기공사업을 등록한 자"],
    "정보통신공사업": ["정보통신공사업 등록", "정보통신공사업을 등록한 자"],
    "전문소방시설공사업": ["전문소방시설공사업 등록"],
    "전문소방시설설계업": ["전문소방시설설계업 등록"],
    "실내건축공사업": ["실내건축공사업 등록", "전문건설업 중 실내건축공사업"],
    "철도·궤도공사업": ["철도･궤도공사업"],  # 특수 가운뎃점 변형
}


def fetch_all(service_key: str) -> list[dict]:
    items, page = [], 1
    while True:
        params = {
            "serviceKey": service_key,
            "numOfRows": PAGE_SIZE,
            "pageNo": page,
            "type": "json",
        }
        r = requests.get(f"{BASE_URL}/{OPERATION}", params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        if "response" not in data:  # 나라장터: 에러도 HTTP 200
            sys.exit(f"[에러] 비정상 응답 (page={page}): {str(data)[:300]}")
        body = data["response"]["body"]
        total = int(body["totalCount"])
        batch = body.get("items") or []
        if isinstance(batch, dict):  # 단건일 때 dict로 오는 API 관행 방어
            batch = [batch]
        items.extend(batch)
        print(f"  page {page}: +{len(batch)} (누적 {len(items)}/{total})")
        if len(items) >= total or not batch:
            return items
        page += 1
        time.sleep(0.2)  # 초당 30tps 제한의 1/6 수준으로 여유

def esc(s) -> str:
    return str(s).replace("'", "''")


def build_sql(items: list[dict]) -> str:
    L = []
    L.append("-- ============================================================================")
    L.append("-- BidMate license_master 전수 시드 v2 (공식 나라장터 업종코드 기준)")
    L.append(f"-- 원천: 업종및근거법규서비스 getIndstrytyBaseLawrgltInfoList — {len(items)}건")
    L.append("-- license_code = indstrytyCd (나라장터 입찰참가자격등록규정 업종DB 코드)")
    L.append("-- ============================================================================\n")

    L.append("INSERT INTO license_master (license_code, license_name, category, law_basis, is_active) VALUES")
    vals, name_to_code = [], {}
    for it in items:
        code = str(it.get("indstrytyCd", "")).strip()
        name = str(it.get("indstrytyNm", "")).strip()
        if not code or not name:
            continue
        cat = str(it.get("indstrytyClsfcNm", "") or "기타").strip()
        law = " ".join(filter(None, [
            str(it.get("baseLawordNm", "") or "").strip(),
            str(it.get("baseLawordArtclClauseNm", "") or "").strip(),
        ]))[:100]
        active = "TRUE" if str(it.get("indstrytyUseYn", "Y")).strip() != "N" else "FALSE"
        vals.append(f"('{esc(code)}', '{esc(name)}', '{esc(cat)}', '{esc(law)}', {active})")
        name_to_code.setdefault(name, code)
    L.append(",\n".join(vals))
    L.append("ON CONFLICT (license_code) DO NOTHING;\n")

    # 별칭: ① 정식명 identity 전수 ② 기존 v0.1 변형 자산 재매핑
    L.append("INSERT INTO master_alias (entity_type, alias_text, canonical_code, source) VALUES")
    avals, seen = [], set()
    for name, code in name_to_code.items():
        if name not in seen:
            seen.add(name)
            avals.append(f"('license', '{esc(name)}', '{esc(code)}', 'rule')")
    unmatched = []
    for std_name, variants in VARIANTS.items():
        code = name_to_code.get(std_name)
        if code is None:
            unmatched.append(std_name)
            continue
        for v in variants:
            if v not in seen:
                seen.add(v)
                avals.append(f"('license', '{esc(v)}', '{esc(code)}', 'manual')")
    L.append(",\n".join(avals))
    L.append("ON CONFLICT (entity_type, alias_text) DO NOTHING;\n")

    if unmatched:
        L.append(f"-- ⚠️ 공식 목록에서 동명 표준을 못 찾은 v0.1 표준명 (수동 매핑 필요): {unmatched}\n")

    L.append("""-- ─── 기존 자체 코드(v0.1) 정리 — 팀 확인 후 주석 해제 실행 ───
-- DELETE FROM master_alias  WHERE entity_type = 'license'
--   AND canonical_code IN (SELECT license_code FROM license_master WHERE license_code ~ '^[A-Z]');
-- DELETE FROM license_master WHERE license_code ~ '^[A-Z]';  -- 공식 코드는 숫자 계열

-- ─── 검수 쿼리: name_raw 커버리지 (완전일치 기준) ───
-- SELECT lic->>'name_raw' AS name_raw, COUNT(*) AS cnt,
--        (ma.canonical_code IS NOT NULL) AS matched
-- FROM bid_table, LATERAL jsonb_array_elements(required_licenses) AS lic
-- LEFT JOIN master_alias ma
--   ON ma.entity_type = 'license' AND ma.alias_text = lic->>'name_raw'
-- WHERE jsonb_typeof(required_licenses) = 'array'
-- GROUP BY 1, 3 ORDER BY cnt DESC LIMIT 50;""")
    return "\n".join(L)


def main():
    key = os.environ.get("G2B_SERVICE_KEY")
    if not key:
        sys.exit("G2B_SERVICE_KEY 환경변수를 설정하세요 (Decoding 키)")
    print("전수 조회 시작...")
    items = fetch_all(key)
    with open("industry_codes_raw.json", "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    print(f"원본 저장: industry_codes_raw.json ({len(items)}건)")
    sql = build_sql(items)
    with open("seed_license_master_v2.sql", "w", encoding="utf-8") as f:
        f.write(sql)
    print("시드 생성: seed_license_master_v2.sql")


if __name__ == "__main__":
    main()