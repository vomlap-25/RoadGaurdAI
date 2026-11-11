# Multi-architecture base image; build/run on linux/arm64 (Graviton) or linux/amd64.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_ENV=production \
    ROADGUARD_STORAGE_BACKEND=sqlite \
    ROADGUARD_DATABASE_PATH=/data/roadguard.sqlite3 \
    ROADGUARD_UPLOAD_DIR=/data/uploads \
    ROADGUARD_EVIDENCE_DIR=/data/evidence

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
      libglib2.0-0 libgomp1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./requirements.txt
RUN python -m pip install --upgrade pip && pip install -r requirements.txt
COPY app ./app
RUN mkdir -p /data/uploads /data/evidence /data && \
    useradd --system --uid 10001 --create-home roadguard && \
    chown -R roadguard:roadguard /app /data
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)" || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
