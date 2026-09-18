# Viet Anh Staff Assistant

RAG nội bộ cho staff Trường Việt Anh / Major Education. Toàn bộ dữ liệu — tri thức nội bộ, vector embedding và log hội thoại — lưu trên **Supabase (Postgres + pgvector)**. Không còn phụ thuộc file `.md` trên đĩa hay volume trên VPS.

## Kiến trúc

```text
Admin tạo/sửa/duyệt note qua /admin (Postgres: knowledge_notes)
        |
        v
Chunk theo heading (markdown, tự cắt nhỏ đoạn quá dài) + OpenRouter embedding (batch)
        |
        v
Ghi vào knowledge_chunks (pgvector) NGAY khi lưu — không có bước "reindex" nền riêng
        |
        v
Chat: query -> RPC match_knowledge_chunks (cosine) -> keyword-boost rerank -> LLM
        |
        v
FastAPI: /api/chat-staff (tự log vào chat_logs), /api/debug/search, /api/admin/*
```

Vì mỗi lần tạo/sửa/duyệt note đều đồng bộ chunk+embedding ngay trong cùng request, hệ thống không cần vault watcher, không cần khoá reindex, không có "cửa sổ collection rỗng" — toàn bộ lớp vấn đề đó (từng gặp nhiều lần với ChromaDB + file Obsidian) không còn tồn tại.

## Cấu trúc

```text
app/
  main.py                    # FastAPI app, serve "/" (React) + "/admin"
  config.py                  # Settings (SUPABASE_URL, SUPABASE_SERVICE_KEY, ...)
  api/routes.py               # /api/chat-staff, /api/admin/*, /api/health, /api/debug/search
  models/schemas.py
  rag/
    chunking.py                # Cắt markdown theo heading (# tới ####), tự chia nhỏ đoạn quá dài
    embeddings.py               # Mock / Gemini / OpenRouter embedding (có embed_batch)
    vector_store.py             # Gọi RPC match_knowledge_chunks + keyword-boost rerank
    pipeline.py                 # Retrieve -> LLM -> parse citation -> log
    prompt_builder.py           # System prompt (phân loại câu hỏi, format, suy luận)
  services/
    supabase_client.py          # Wrapper REST/RPC Supabase (không cần psycopg)
    admin_service.py             # CRUD note + đồng bộ chunk/embedding tại thời điểm ghi
    knowledge_ingest.py          # Trích text từ file upload (md/txt/csv/json/pdf/docx)
    llm.py                       # Mock / Gemini / OpenRouter LLM (nhớ hội thoại, suy luận)
  static/
    index.html                   # Demo UI tĩnh (fallback khi chưa build React)
    admin.html                   # Trang quản trị: tạo/sửa/duyệt/upload note
frontend/                        # React + Vite + Tailwind (giao diện chat chính)
tests/
requirements.txt
.env.example
```

## Schema Supabase

- `knowledge_notes`: `id, title, department, owner, status (draft/approved/rejected), version, access_level, content, source_file, created_by, created_at, updated_at, reviewed_at, reviewed_by`
- `knowledge_chunks`: `id, note_id (fk), chunk_index, heading, text, embedding vector(1536), title, department, status, version, access_level` — index HNSW cosine
- `chat_logs`: `id, session_id, question, answer, grounded, citations (jsonb), created_at` — log mọi lượt hỏi-đáp, best-effort (không làm hỏng response nếu insert lỗi)
- Hàm RPC: `match_knowledge_chunks(query_embedding, match_count, filter_department)` — tìm kiếm cosine, chỉ trả note `status = 'approved'`; `sync_knowledge_chunks(p_note_id, p_chunks)` — xoá + ghi lại toàn bộ chunk của 1 note trong 1 transaction.

RLS đã bật trên cả 3 bảng, không có policy nào (default-deny) — backend dùng `service_role` key nên tự bypass RLS; không có client nào khác được cấp quyền truy cập trực tiếp.

## Chạy local

### Giao diện React

```powershell
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload --port 8000
```

Terminal 2:

```powershell
Set-Location frontend
npm install
npm run dev
```

Mở giao diện tại <http://localhost:5173>. API docs ở <http://localhost:8000/docs>. Trang quản trị ở <http://localhost:8000/admin> (cần `ADMIN_TOKEN`).

### Cấu hình `.env`

```env
SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_SERVICE_KEY=<service_role secret key, lấy từ Supabase Dashboard > Project Settings > API Keys>
EMBEDDING_PROVIDER=openrouter
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=<key của mày>
OPENROUTER_MODEL=google/gemini-2.5-flash
OPENROUTER_EMBEDDING_MODEL=openai/text-embedding-3-small
ADMIN_TOKEN=<token tự đặt cho /admin>
```

`SUPABASE_SERVICE_KEY` là secret — không commit, không dán vào chat công khai. Không ghi API key vào source code.

### Dùng thử

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
Invoke-RestMethod "http://127.0.0.1:8000/api/debug/search?q=nghỉ phép"
$body = @{ question = "Tôi cần xin nghỉ phép trước bao lâu?" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/chat-staff -ContentType "application/json" -Body $body
```

Nếu câu hỏi không có context phù hợp, chatbot từ chối an toàn và tự tạo 1 note `status: draft` (tiêu đề `[CẦN BỔ SUNG] ...`) trong `knowledge_notes` để HR biết câu nào chưa có dữ liệu.

## Quản trị dữ liệu (`/admin`)

Đây là **nơi duy nhất** được ghi dữ liệu — khung chat không có tính năng upload (đã gỡ vì lý do bảo mật: bản cũ chỉ tin theo lựa chọn phòng ban/quyền do client tự khai, không xác thực thật).

- **Tạo note mới**: nhập tiêu đề + nội dung markdown trực tiếp trên web, chọn lưu draft hoặc duyệt luôn.
- **Upload file**: kéo thả `.md .txt .csv .json .pdf .docx` (tối đa 10MB), tự trích nội dung, tạo draft chờ duyệt.
- **Sửa/Duyệt/Từ chối/Xoá**: mỗi thao tác tự động chunk lại + tính embedding mới + ghi vào Supabase trong cùng request.

Xác thực bằng `ADMIN_TOKEN` (1 token dùng chung, MVP — chưa phải tài khoản riêng từng người).

## API chính

- `GET /api/health` — trạng thái + số note/note đã duyệt
- `POST /api/chat-staff` — hỏi đáp grounded, có citation, tự log vào `chat_logs`
- `GET /api/debug/search?q=` — xem chunk và score được retrieve
- `GET|POST|PUT|DELETE /api/admin/notes*`, `POST /api/admin/upload` — quản trị (cần `X-Admin-Token`)

## Deploy (Docker / Coolify)

`Dockerfile` build sẵn frontend rồi gộp vào image Python — 1 container phục vụ cả API lẫn giao diện. Không còn cần persistent volume (dữ liệu nằm hết ở Supabase) — chỉ cần set biến môi trường `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `OPENROUTER_API_KEY`, `ADMIN_TOKEN` trong Coolify rồi Deploy.

## Checklist trước pilot

- [ ] Thêm authentication/SSO thật cho `/admin` thay vì 1 token dùng chung
- [ ] Không đưa dữ liệu lương, hợp đồng hoặc PII vào context staff (kiểm tra lại từng note)
- [ ] Thêm rate limit, observability
- [ ] Backup định kỳ Supabase (Point-in-time recovery hoặc export)
- [ ] Bổ sung test retrieval, prompt injection, regression dataset
