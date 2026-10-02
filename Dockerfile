# ---- frontend build ----
FROM node:20-alpine AS frontend-builder
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
# npm KHONG bao loi khi tai hong 1 goi "optional" - ma binary native theo nen tang cua
# lightningcss/rolldown (Vite can de build) lai la goi optional. Ngay 2026-10-02 mot lan
# deploy hong vi npm ci am tham bo sot lightningcss-linux-x64-musl (cai 103/104 goi) roi
# "vite build" moi bao "Cannot find module ../lightningcss.linux-x64-musl.node". Nen phai
# tu kiem tra ngay sau khi cai, thieu thi cai lai 1 lan, van thieu thi dung o day voi loi ro rang.
RUN npm ci \
    && (node -e "require('lightningcss'); require('rolldown')" \
        || (echo "Thieu binary native sau npm ci - cai lai" \
            && rm -rf node_modules \
            && npm ci \
            && node -e "require('lightningcss'); require('rolldown')"))
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
# tro toi thu muc nay. Noi dung vault duoc dong bo vao day boi service "drive-sync"
# rieng (rclone bisync voi Google Drive, xem drive-sync/) - container nay chi
# doc/ghi vao thu muc da dong bo san.
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
