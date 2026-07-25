# BidMate 정규화 Lambda — 컨테이너 이미지 (팀 표준: 기존 12개 함수와 동일 Image 방식)
# AWS Lambda Python 베이스(Amazon Linux) — psycopg[binary] manylinux 휠이 그대로 설치됨.
FROM public.ecr.aws/lambda/python:3.12

# 1) 의존성
COPY src/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 2) 소스: 핸들러 + 어댑터 + 팀 원본 normalizer (무수정)
#    ★ 빌드 전에 src/normalizer.py 를 반드시 넣어둘 것.
COPY src/lambda_function.py            ${LAMBDA_TASK_ROOT}/
COPY src/normalize_output_adapter.py  ${LAMBDA_TASK_ROOT}/
COPY src/normalizer.py                ${LAMBDA_TASK_ROOT}/

# 3) 핸들러 지정
CMD ["lambda_function.handler"]
