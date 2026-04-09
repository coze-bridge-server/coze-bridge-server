# ============================================
# Coze Bridge Server — Docker 이미지
# Python 3.11 slim 기반 / FastAPI + Uvicorn
# ============================================

FROM python:3.11-slim

# curl 설치 (docker-compose healthcheck용)
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# 비루트 사용자 생성 (보안 강화)
RUN useradd --create-home appuser

WORKDIR /app

# 의존성 먼저 설치 (레이어 캐시 활용)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 소스코드 복사
COPY . .

# 소유권 변경
RUN chown -R appuser:appuser /app

# 비루트 사용자로 전환
USER appuser

# exec form 사용 — PID 1이 uvicorn이 되어 signal 정상 전달
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
