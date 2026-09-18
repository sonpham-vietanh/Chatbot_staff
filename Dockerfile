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

# Thư mục dữ liệu sẽ được mount volume đè lên ở Coolify; tạo sẵn để app không
# crash khi chưa gắn volume (Settings.validate_vault_path() yêu cầu tồn tại).
RUN mkdir -p /app/vault /app/data /app/Draft_Review

ENV PYTHONUNBUFFERED=1 \
    OBSIDIAN_VAULT_PATH=/app/vault \
    DRAFT_REVIEW_PATH=/app/Draft_Review \
    VECTOR_DB_PATH=/app/data/chroma \
    GRAPH_PATH=/app/data/graph_edges.json

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
