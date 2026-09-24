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
    && apt-get install -y --no-install-recommends curl gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @anthropic-ai/claude-code \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY --from=frontend-builder /build/frontend/dist ./frontend/dist

ENV PYTHONUNBUFFERED=1
ENV UVICORN_WORKERS=4
# Vault Obsidian (wiki_obsidian) duoc mount vao day qua volume ben ngoai -
# container khong tu chua data vault, phai gan volume persistent trong Coolify
# tro toi thu muc nay, roi dong bo noi dung vault vao (vd qua CouchDB/LiveSync sau nay).
ENV VAULT_PATH=/data/vault
RUN mkdir -p /data/vault /app/data

# Claude CLI (--dangerously-skip-permissions, dung boi Ingest Agent qua
# permission_mode="bypassPermissions") TU CHOI chay voi quyen root vi ly do bao mat -
# BAT BUOC chay bang user thuong, khong duoc bo qua buoc nay.
RUN useradd -m -u 1000 appuser \
    && chown -R appuser:appuser /app /data/vault
USER appuser
ENV HOME=/home/appuser

VOLUME ["/data/vault"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=15s --start-period=30s --retries=5 \
    CMD curl -fsS --max-time 12 http://127.0.0.1:8000/api/health || exit 1

CMD uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS}
