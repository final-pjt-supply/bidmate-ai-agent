# BidMate 공고 정규화 Lambda — 배포 가이드

`normalize_output_adapter.run()`을 **EventBridge 정기 스케줄**로 자동 실행하는 배치.
증분 처리(신규·재병합·버전상이 공고만)라 평상시 소량이며 람다 15분 안에 충분히 끝난다.

---

## 0. 개념 한 줄

**SAM** = 람다·스케줄·권한·VPC 설정을 `template.yaml` 하나에 적고 `sam build` → `sam deploy`
두 명령으로 배포하는 AWS 공식 서버리스 도구. 콘솔 클릭보다 재현 가능, Terraform보다 쉬움.

---

## 1. 파일 구조 (배포 전 준비)

```
lambda/
├── template.yaml            # SAM 정의 (이미 있음)
├── README_deploy.md         # 이 파일
└── src/
    ├── lambda_function.py            # 핸들러 (이미 있음)
    ├── requirements.txt             # psycopg[binary] (이미 있음)
    ├── normalize_output_adapter.py  # ★ 복사해 넣기
    └── normalizer.py                # ★ 팀 원본 복사해 넣기 (무수정)
```

> ⚠ `normalize_output_adapter.py` 와 `normalizer.py` 두 파일을 **`src/`에 복사**해야 한다.
> 핸들러가 `import normalize_output_adapter`, `import normalizer` 하기 때문. 패키지가 아니라
> 같은 폴더의 소스 파일로 둔다.

---

## 2. 사전 준비 (한 번만)

1. **AWS CLI + SAM CLI 설치**, `aws configure`로 자격 설정.
2. **Docker 설치** — `sam build --use-container`가 Lambda와 동일한 리눅스에서 psycopg
   바이너리 휠을 받기 위해 필요. (안 쓰면 로컬 OS 휠이 섞여 람다에서 import 에러가 남.)
3. **Secrets Manager에 DSN 저장** — 시크릿 값에 아래 중 하나:
   - 평문: `host=bidmate-postgres.cv8i80....rds.amazonaws.com port=5432 dbname=bidmate user=<U> password=<P>`
   - 또는 JSON: `{"DB_DSN": "host=... dbname=bidmate user=... password=..."}`
   → 시크릿 ARN을 복사해둔다.
4. **네트워킹 확인** — 람다는 RDS와 **같은 VPC의 프라이빗 서브넷**에 붙어야 한다.
   - RDS 보안그룹(SG)에 "람다 SG로부터 5432 인바운드 허용" 규칙 추가.
   - 서브넷 ID 목록, 보안그룹 ID 목록을 복사해둔다.
   - (람다가 VPC에 있으면 인터넷/Secrets Manager 접근에 VPC 엔드포인트 또는 NAT 필요.
     Secrets Manager 인터페이스 VPC 엔드포인트를 두거나, NAT가 이미 있으면 그대로.)

---

## 3. 빌드 & 배포

```bash
cd lambda
sam build --use-container

# 최초 배포 (대화형 — 파라미터 입력)
sam deploy --guided
#   Stack Name           : bidmate-normalize
#   Region               : ap-northeast-2 (서울)
#   DbSecretArn          : arn:aws:secretsmanager:...:secret:bidmate-dsn-xxxx
#   SubnetIds            : subnet-aaa,subnet-bbb   (콤마 구분)
#   SecurityGroupIds     : sg-ccc
#   Schedule             : rate(1 hour)
#   DryRun               : 1        ← 최초엔 1로! (아래 4 참고)
```

이후 재배포는 `sam deploy` (guided 없이).

---

## 4. 최초 점검 → 실가동 순서

1. **DryRun=1로 배포** → 콘솔에서 함수 수동 실행(Test, 이벤트 `{}`).
   CloudWatch 로그에서 `대상 공고 N건`, `완료: … (DRY_RUN — 미기록)` 확인.
   (아무것도 안 쓰고 계획만 로그.)
2. 대상 수·에러 이상 없으면 **DryRun=0으로 재배포**(`sam deploy` 시 파라미터만 변경) →
   이후 스케줄이 실제 적재.
3. 수동으로 한 번 더 실제 실행해 `완료: 공고 N건, 행 M개, 실패 0건` 확인.

이벤트로 1회성 점검도 가능: 콘솔 Test에 `{"dry_run": true}` → env가 0이어도 그 호출만 미기록.

---

## 5. 운영 주의

- **증분 전용.** `select_targets`가 신규·재병합·버전상이만 고르므로 평상시 소량.
- **전건 재정규화(normalizer_version 범프)는 이 람다로 금지.** 19k건은 15분 초과.
  버전 올릴 땐 로컬/EC2/일회성 스크립트로 돌릴 것. (람다는 그 뒤 증분만 담당.)
- **멱등·재개가능.** 공고별 autocommit이라 중간 실패해도 성공분은 커밋, 실패분은
  다음 스케줄에서 자동 재시도. `실패 N건` 로그가 지속되면 그 공고 원문 점검.
- **락 짧음.** 공고 단위 트랜잭션이라 장시간 락 없음.
- **백로그 대비(선택).** 스케줄을 오래 걸러 대상이 수천 건 쌓이면 15분 초과 가능.
  그럴 땐 스케줄 주기를 짧게 하거나, 어댑터 `select_targets`에 `LIMIT + 반복 호출`을
  도입(향후 과제). 현재는 정기 실행 전제라 미도입.

## 6. 모니터링(권장)

- CloudWatch 알람: 함수 Errors ≥ 1, Duration > 12분(타임아웃 임박).
- 로그 메트릭 필터: `실패 공고` 문자열 → 알람.
