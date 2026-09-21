# ---- frontend build ----
FROM node:20-alpine AS frontend-builder
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- backend + bundled frontend ----
FROM python:3.11-slim AS runtime
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY --from=frontend-builder /build/frontend/dist ./frontend/dist

ENV PYTHONUNBUFFERED=1
ENV UVICORN_WORKERS=4

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=15s --start-period=30s --retries=5 \
    CMD curl -fsS --max-time 12 http://127.0.0.1:8000/api/health || exit 1

CMD uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS}
