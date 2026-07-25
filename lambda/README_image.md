# BidMate 정규화 Lambda — 컨테이너 이미지 배포 (팀 표준 방식)

기존 12개 함수와 동일한 **Image 패키지** 방식. 상류(`realtime-merge-dev`)가 이미
bid_table을 적재하므로, 이 함수는 그 **다음 단계**로 붙어 신규·재병합 공고를 정규화한다.

> SAM(Zip) 버전(`template.yaml`)도 폴더에 있지만, 팀 관례가 이미지라 **이 문서 기준**으로 간다.

---

## 1. 준비

```
src/
  lambda_function.py            (있음)
  normalize_output_adapter.py   (있음)
  normalizer.py                 ← ★ 팀 원본 복사 (무수정)
  requirements.txt              (있음)
Dockerfile                      (있음)
```

`src/normalizer.py`만 채우면 빌드 가능.

## 2. VPC·권한·DB env = `realtime-merge-dev` 미러링 (핵심, 실측값)

merge 함수 구성에서 확인된 실제 값. 새 함수에 **동일하게** 넣는다.

**VPC / 서브넷 / 보안그룹** (구성 > VPC):
- VPC: `vpc-06fa7caa42e106c66` (bidmate-vpc)
- 서브넷: `subnet-0348f5a655ef2f9b5`(2a) · `subnet-0097fa998d43ebf09`(2c) — 둘 다 프라이빗
- 보안그룹: `sg-08ae3328fa7784b6f` (realtime-merge-sg)
  → 이 SG가 RDS의 `bidmate-rds-sg`에 5432 인바운드 허용됨(RDS 데이터베이스 탭에서 확인됨).

**실행 역할** (구성 > 권한):
- `realtime-merge-function-role` **그대로 재사용 가능.** 가진 권한 = `AWSLambdaVPCAccessExecutionRole`
  + `AWSLambdaBasicExecutionRole`뿐(= VPC ENI + CloudWatch 로그). **Secrets Manager 권한 불필요**
  (아래 DB를 env로 넣으므로). 같은 역할을 쓰거나, 같은 관리형 정책 2개로 새 역할 생성.

**DB 접속 = 환경변수** (merge와 동일 방식 — Secrets Manager 안 씀):
merge의 `MERGE_DB_*` 값을 우리 함수의 `DB_*`로 복사한다.

| merge(기존)         | 우리 함수(신규)   |
|---------------------|-------------------|
| MERGE_DB_HOST       | DB_HOST           |
| MERGE_DB_NAME       | DB_NAME           |
| MERGE_DB_USER       | DB_USER           |
| MERGE_DB_PASSWORD   | DB_PASSWORD       |
| (없음)              | DRY_RUN = 1 → 후 0 |

> ⚠ 보안 메모: merge처럼 **비밀번호가 평문 env**로 들어간다(팀 기존 방식이라 일관성 위해 따름).
> 나중에 팀 차원에서 Secrets Manager로 옮기면 더 안전 — 그때 핸들러는 `DB_SECRET_ARN`만
> 지원하므로 코드 변경 없이 전환됨. (지금은 범위 밖.)

## 3. 빌드 & 푸시 (ECR)

```bash
ACCT=890608337282           # 스크린샷 계정 ID
REGION=ap-northeast-2
REPO=realtime-normalize     # 기존 realtime-* 네이밍에 맞춤

# ECR 리포 (최초 1회)
aws ecr create-repository --repository-name $REPO --region $REGION || true

# 로그인
aws ecr get-login-password --region $REGION \
  | docker login --username AWS --password-stdin $ACCT.dkr.ecr.$REGION.amazonaws.com

# 빌드 → 태그 → 푸시  (Apple Silicon이면 --platform linux/amd64 필수)
docker build --platform linux/amd64 -t $REPO .
docker tag $REPO:latest $ACCT.dkr.ecr.$REGION.amazonaws.com/$REPO:latest
docker push $ACCT.dkr.ecr.$REGION.amazonaws.com/$REPO:latest
```

## 4. 함수 생성

**콘솔**: 함수 생성 > 컨테이너 이미지 > 이미지 URI = 위 `:latest` >
함수명 `realtime-normalize-dev` > 구성에서 VPC·역할·env를 2절대로.

**또는 CLI**:
```bash
aws lambda create-function \
  --function-name realtime-normalize-dev \
  --package-type Image \
  --code ImageUri=$ACCT.dkr.ecr.$REGION.amazonaws.com/$REPO:latest \
  --role arn:aws:iam::$ACCT:role/realtime-merge-function-role \
  --timeout 900 --memory-size 512 \
  --vpc-config SubnetIds=subnet-0348f5a655ef2f9b5,subnet-0097fa998d43ebf09,SecurityGroupIds=sg-08ae3328fa7784b6f \
  --environment "Variables={DB_HOST=bidmate-postgres.cv8i80gskzwz.ap-northeast-2.rds.amazonaws.com,DB_NAME=bidmate,DB_USER=bidmaster,DB_PASSWORD=<merge의 값>,DRY_RUN=1}" \
  --region $REGION
# ↑ DB_PASSWORD는 merge의 MERGE_DB_PASSWORD 값을 넣는다(여기 문서에 비밀번호는 안 적음).
#   최초엔 DRY_RUN=1. 점검 후 update-function-configuration으로 0.
```

코드 갱신(재배포): 이미지 다시 push 후
`aws lambda update-function-code --function-name realtime-normalize-dev --image-uri <...>:latest`

## 5. 트리거 — EventBridge 스케줄 (merge와 동일 패턴)

merge는 Step Functions가 아니라 **자기 EventBridge 스케줄**로 돈다
(`realtime-dev-RealtimeMergeFunctionMergeSchedule`). 그러니 normalize도 같은 패턴:

- **A. 독립 EventBridge 스케줄 (권장·간단).** normalize에 트리거 추가 > EventBridge >
  새 규칙 > 스케줄 식. merge 스케줄 주기를 확인해(merge 트리거 > 세부정보) **그보다 조금 뒤**로.
  예: merge가 `rate(1 hour)`면 normalize도 `rate(1 hour)`(증분·멱등이라 정확히 직후가 아니어도
  다음 회차에 잡음). 정밀히 하려면 cron으로 merge+10분 offset.
- **B. merge onSuccess 목적지 체이닝 (더 정확, merge 수정 필요).** merge는 이미 destinations를
  쓴다(onFailure→SNS `realtime-pipeline-alerts`). merge 구성 > 대상 > **성공 시(onSuccess)**로
  `realtime-normalize-dev`를 추가하면 merge 성공 직후 자동 호출. 단 merge 함수 설정을 건드리고,
  merge가 CFN 스택(realtime-dev)으로 관리되면 콘솔 변경이 IaC와 드리프트할 수 있음 → 팀 합의 후.

→ 우선 **A(독립 스케줄)** 로 띄우고, 나중에 더 정밀한 체이닝이 필요하면 B로.

## 6. 최초 점검 → 실가동

1. `DRY_RUN=1`로 생성 → 콘솔 Test(이벤트 `{}`) → CloudWatch 로그
   `대상 공고 N건`, `완료: … (DRY_RUN — 미기록)` 확인.
2. 이상 없으면 `DRY_RUN=0`으로 변경 → 실제 적재. 수동 1회 실행해
   `완료: 공고 N건, 행 M개, 실패 0건` 확인.
3. 트리거(A/B/C) 연결.

## 7. 운영 주의 (SAM README와 동일)

- **증분 전용.** 전건 재정규화(normalizer_version 범프)는 이 람다 금지 — 15분 초과.
  버전 올릴 땐 로컬/EC2 일회성 스크립트로. 람다는 그 뒤 증분만.
- **멱등·재개가능.** 공고별 커밋 → 실패분은 다음 실행에서 자동 재시도.
- 백로그로 대상이 수천 건 쌓이면 타임아웃 위험 → 스케줄 주기 단축 또는 `select_targets`
  LIMIT+반복(향후 과제).
