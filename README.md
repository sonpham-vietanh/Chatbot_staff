# Viet Anh Staff Assistant

RAG nội bộ cho staff Trường Việt Anh / Major Education. Tri thức, vector embedding, tài khoản người dùng, lịch sử hội thoại và log — lưu trên **Supabase (Postgres + pgvector + Auth)**. Nguồn tri thức đến từ 2 đường: (1) admin tạo/sửa/duyệt trực tiếp qua `/admin`, (2) tự động qua vault Obsidian (`wiki_obsidian`, đồng bộ về server bằng Google Drive) + Ingest Agent.

## Kiến trúc

```text
Vault Obsidian (may nguoi dung) --Google Drive (drive-sync sidecar, rclone)--> /data/vault (server)
        |
        v
VaultWatcher (poll raw/ moi domain) -> phat hien nguon moi
        |
        v
Ingest Agent (Claude Agent SDK, theo CLAUDE.md cua vault) -> ghi wiki/ trong vault
        |
        v
WikiSyncService -> AdminService: chunk theo heading + OpenRouter embedding -> Supabase

                          (song song)
Admin tao/sua/duyet note qua /admin (Postgres: knowledge_notes)
        |
        v
Chunk theo heading (markdown, tu cat nho doan qua dai) + OpenRouter embedding (batch)
        |
        v
Ghi vao knowledge_chunks (pgvector) NGAY khi luu - khong co buoc "reindex" nen rieng
        |
        v
Chat: query -> RPC match_knowledge_chunks (cosine) -> keyword-boost rerank -> LLM
        |
        v
FastAPI: /api/chat-staff (yeu cau dang nhap, tu log chat_logs + chat_threads),
         /api/auth/*, /api/chat/threads*, /api/debug/search, /api/admin/*
```

Vì mỗi lần tạo/sửa/duyệt note đều đồng bộ chunk+embedding ngay trong cùng request, hệ thống không cần khoá reindex riêng, không có "cửa sổ collection rỗng". Nhánh vault/Google Drive/Ingest Agent là đường nạp tri thức tự động (leader chỉ cần thả file vào đúng `<domain>/raw/` trong Obsidian, không cần vào `/admin`); `VaultWatcher` dùng file-lock (`fcntl`) để chỉ 1 trong N uvicorn worker chạy vòng poll.

## Cấu trúc

```text
app/
  main.py                    # FastAPI app, khoi dong VaultWatcher (lifespan), serve "/" (React) + "/admin"
  config.py                  # Settings (SUPABASE_URL, SUPABASE_SERVICE_KEY, VAULT_PATH, ...)
  api/routes.py               # /api/auth/*, /api/chat/threads*, /api/chat-staff, /api/admin/*, /api/health, /api/debug/search
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
    auth_service.py               # Proxy tới Supabase Auth (GoTrue): signup/login/refresh/me
    chat_history_service.py       # Lưu chat_threads/chat_messages theo từng user
    analytics_service.py          # Tổng hợp chat_logs cho /api/admin/analytics
    api_key_service.py            # CRUD API key nhúng widget chat (embed)
    ingest_agent.py                # Chạy Ingest Agent (Claude Agent SDK) ghi vault theo CLAUDE.md
    document_reader.py             # MCP tool cho Ingest Agent đọc .docx/.xlsx
    vault_watcher.py                # Poll vault, gọi Ingest Agent khi có nguồn mới, cảnh báo sửa tay ngoài luồng
    wiki_sync_service.py            # Đồng bộ toàn bộ wiki/ trong vault vào Supabase
  static/
    index.html                   # Demo UI tĩnh (fallback khi chưa build React)
    admin.html                   # Trang quản trị: tạo/sửa/duyệt/upload note
frontend/                        # React + Vite + Tailwind (giao diện chat chính)
drive-sync/                       # Sidecar rclone bisync, đồng bộ 2 chiều vault Obsidian với Google Drive
tests/
requirements.txt
.env.example
```

Xem `wiki_obsidian/CLAUDE.md` để biết quy ước cấu trúc vault (mỗi phòng ban 1 thư mục gốc + `core/` dùng chung). Đồng bộ vault dùng Google Drive (1 tài khoản Drive riêng "sở hữu" vault, share từng thư mục con cho đúng người) + `drive-sync/` (rclone bisync) — không còn tự host server đồng bộ riêng (đã thử Seafile tự host, bỏ vì chi phí vận hành quá cao so với lợi ích, xem `wiki_obsidian/log.md` 2026-09-28).

## Schema Supabase

- `knowledge_notes`: `id, title, department, owner, status (draft/approved/rejected), version, access_level, content, source_file, created_by, created_at, updated_at, reviewed_at, reviewed_by`
- `knowledge_chunks`: `id, note_id (fk), chunk_index, heading, text, embedding vector(1536), title, department, status, version, access_level` — index HNSW cosine
- `chat_logs`: `id, session_id, question, answer, grounded, citations (jsonb), created_at` — log mọi lượt hỏi-đáp, best-effort (không làm hỏng response nếu insert lỗi)
- `chat_threads` / `chat_messages`: lịch sử hội thoại theo từng user đã đăng nhập (`/api/chat/threads*`)
- Hàm RPC: `match_knowledge_chunks(query_embedding, match_count, filter_department)` — tìm kiếm cosine, chỉ trả note `status = 'approved'`, lọc đúng bằng `department = filter_department` nếu có truyền; `sync_knowledge_chunks(p_note_id, p_chunks)` — xoá + ghi lại toàn bộ chunk của 1 note trong 1 transaction.

RLS đã bật trên các bảng, không có policy nào (default-deny) — backend dùng `service_role` key nên tự bypass RLS; không có client nào khác được cấp quyền truy cập trực tiếp.

### Danh sách nhân viên và đăng nhập Google

- Migration `supabase/migrations/20261001000000_employee_directory.sql` tạo bảng hồ sơ HR tùy chọn. Không cần nạp roster để cho phép đăng nhập; nhân viên chưa có trong bảng vẫn được vào bằng email công ty đã xác minh.
- Bảng chỉ giữ thông tin tối thiểu phục vụ hồ sơ cá nhân: email, họ tên, phòng ban, chức vụ, ngày bắt đầu làm việc, trạng thái hồ sơ. Không đưa ngày sinh, bậc lương hoặc hoàn cảnh gia đình vào bảng này hay RAG.
- Bảng không có policy cho client; backend dùng `SUPABASE_SERVICE_KEY` để tra cứu. Nếu có hồ sơ khớp email, `/api/auth/me` trả về hồ sơ của chính tài khoản đó; thiếu hồ sơ không chặn đăng nhập.
- `ALLOWED_EMAIL_DOMAINS` là allowlist bắt buộc, ví dụ `truongvietanh.com,mamnon-vietanh.com`. Google/password login, token refresh và API đã đăng nhập đều yêu cầu email đã xác minh thuộc một trong các domain này. Nếu biến chưa cấu hình, backend từ chối đăng nhập (fail closed).
- Thêm `SUPABASE_ANON_KEY` vào `.env`/biến môi trường runtime. Đây là publishable key, khác với `SUPABASE_SERVICE_KEY`; chỉ key anon được trả về qua `/api/auth/config`.
- Trong Google Cloud Console tạo OAuth Client ID kiểu Web, thêm origin của local và production, và đặt callback URL chính xác do Supabase hiển thị (thường `https://<project-ref>.supabase.co/auth/v1/callback`). Bật Google provider trong Supabase Auth.
- Trong Supabase Auth > URL Configuration thêm redirect URLs `http://localhost:5173` và origin production của ứng dụng. Local Vite proxy chuyển `/api/auth/config` về backend; production dùng cùng origin.
- Google OAuth có thể tự tạo tài khoản Supabase lần đầu; quyền vào chatbot dựa trên email đã xác minh và domain công ty, không dựa trên roster HR. Đăng ký email/mật khẩu cũng chỉ chấp nhận domain được cấu hình.

⚠️ **Biết trước khi mở rộng nhiều phòng ban**: `filter_department` so khớp tuyệt đối, và giá trị `department` gửi lên hiện do **client tự khai** trong request (`user_department`), chưa gắn với tài khoản đã đăng nhập. Nội dung dùng chung (`core/` trong vault) sẽ không xuất hiện trong câu trả lời có lọc phòng ban cụ thể trừ khi sửa lại logic truy vấn/gán quyền trước — xem `wiki_obsidian/can-xu-ly.md`.

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

Xem `.env.example` cho danh sách đầy đủ. Tối thiểu để chạy chat:

```env
SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_SERVICE_KEY=<service_role secret key, lấy từ Supabase Dashboard > Project Settings > API Keys>
EMBEDDING_PROVIDER=openrouter
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=<key của mày>
OPENROUTER_MODEL=openai/gpt-6-luna
OPENROUTER_EMBEDDING_MODEL=openai/text-embedding-3-small
ADMIN_TOKEN=<token tự đặt cho /admin>
```

Muốn bật nhánh Ingest Agent (vault Obsidian tự nạp tri thức) thì thêm `ANTHROPIC_API_KEY`, `VAULT_PATH` (và trên Windows: `CLAUDE_CLI_PATH` trỏ tới binary `claude` native). Không set `VAULT_PATH`/`ANTHROPIC_API_KEY` thì `VaultWatcher` không khởi động, hệ thống vẫn chạy bình thường qua `/admin`.

`SUPABASE_SERVICE_KEY` là secret — không commit, không dán vào chat công khai. Không ghi API key vào source code.

### Dùng thử

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
Invoke-RestMethod "http://127.0.0.1:8000/api/debug/search?q=nghỉ phép"
```

`/api/chat-staff` yêu cầu đăng nhập (`Authorization: Bearer <access_token>` từ `/api/auth/login`) vì có lưu lịch sử hội thoại theo user.

Nếu câu hỏi không có context phù hợp, chatbot từ chối an toàn và tự tạo 1 note `status: draft` (tiêu đề `[CẦN BỔ SUNG] ...`) trong `knowledge_notes` để HR biết câu nào chưa có dữ liệu.

## Quản trị dữ liệu (`/admin`)

Có 2 đường ghi dữ liệu vào wiki:

- **Qua `/admin`** (web): tạo note trực tiếp, upload file (`.md .txt .csv .json .pdf .docx`, tối đa 10MB), sửa/duyệt/từ chối/xoá — mỗi thao tác tự chunk lại + tính embedding mới trong cùng request. Xác thực bằng `ADMIN_TOKEN` (1 token dùng chung, MVP — chưa phải tài khoản riêng từng người).
- **Qua vault Obsidian**: leader/nhân viên bỏ file nguồn vào đúng `<domain>/raw/` trong vault (đồng bộ bằng Google Drive) → `VaultWatcher` phát hiện → chạy Ingest Agent ghi lại thành trang wiki đúng quy ước → `WikiSyncService` đẩy vào Supabase tự động. Khung chat không có tính năng upload (đã gỡ vì lý do bảo mật: bản cũ chỉ tin theo lựa chọn phòng ban/quyền do client tự khai, không xác thực thật).

## API chính

- `GET /api/health` — trạng thái + số note/note đã duyệt
- `POST /api/auth/signup|login|refresh`, `GET /api/auth/me` — Supabase Auth; email phải xác minh và thuộc `ALLOWED_EMAIL_DOMAINS`
- `GET/DELETE /api/chat/threads*` — lịch sử hội thoại của user đang đăng nhập
- `POST /api/chat-staff` — hỏi đáp grounded, có citation, cần đăng nhập, tự log vào `chat_logs` + lưu thread
- `GET /api/debug/search?q=` — xem chunk và score được retrieve
- `GET|POST|PUT|DELETE /api/admin/notes*`, `POST /api/admin/upload` — quản trị note (cần `X-Admin-Token`)
- `GET|POST|DELETE /api/admin/api-keys*` — quản lý API key cho widget chat nhúng ngoài
- `POST /api/widget/chat` — endpoint server-to-server cho giao diện đối tác; gửi `X-Widget-Key`, không cần đăng nhập Supabase lần hai
- `GET /api/admin/analytics` — thống kê câu hỏi/nguồn được trích dẫn nhiều nhất

### Tích hợp chatbot vào giao diện khác

Trong `/admin` → **API nhúng**, tạo một key với label và domain của ứng dụng đối tác. Key này có thể dùng theo hai cách:

- **Server-to-server** (khuyến nghị): backend của ứng dụng đối tác gọi `POST /api/widget/chat` với header `X-Widget-Key`. Cách này dùng phiên đăng nhập Google của ứng dụng đối tác, nên người dùng không phải đăng nhập chatbot lần hai. Endpoint này không biết người dùng cuối là ai — backend đối tác phải tự kiểm tra đăng nhập trước khi chuyển câu hỏi sang. `history` chỉ cần 6 tin gần nhất; server tự giữ 6 tin cuối và cắt mỗi `content` còn 2000 ký tự, không trả lỗi. `answer` trả về là **văn bản thuần** (đã bỏ mọi ký hiệu Markdown ở backend — xem `app/rag/answer_format.py`; nhiều ý thì xuống dòng, liệt kê bằng dòng bắt đầu `- `), vì giao diện đối tác hiển thị nguyên văn; chỉ `/api/chat-staff` mới giữ `**in đậm**` cho giao diện React của mình tự render.
- **Iframe** (phương án phụ): nhúng `<origin chatbot>/?embed_key=<key>`; người dùng trong iframe vẫn cần đăng nhập chatbot. ⚠️ **Chưa nên dùng**: hệ thống chưa tách loại key, nên key đặt trong URL iframe (ai xem mã nguồn trang cũng đọc được) vẫn gọi được `/api/widget/chat` mà không cần đăng nhập. Cần thêm loại key "chỉ iframe" (bị `/api/widget/chat` từ chối) trước khi bật phương án này.

Hướng dẫn chi tiết gửi cho bên tích hợp Major OS: `docs/Huong_dan_tich_hop_Mini_Chat_Major_OS.docx`.

Ví dụ request server-to-server:

```http
POST https://staffbot.vietanh.org/api/widget/chat
X-Widget-Key: vas_...
Content-Type: application/json

{"question":"Tôi cần xin nghỉ phép trước bao lâu?","history":[]}
```

Không đặt `X-Widget-Key` trong JavaScript chạy ở trình duyệt. Key phải nằm trong biến môi trường/backend của ứng dụng đối tác; frontend chỉ nhận lại `answer` và `citations` từ backend đó.

## Deploy (Docker / Coolify)

`Dockerfile` build sẵn frontend rồi gộp vào image Python — 1 container phục vụ cả API lẫn giao diện, cài kèm Claude CLI (cho Ingest Agent). Cần 1 volume persistent mount vào `/data/vault` (Coolify), và service `drive-sync/` chạy song song để đồng bộ vault thật xuống đó với Google Drive bằng rclone (xem `docker-compose.yml` — file này chỉ dùng test local, biến môi trường thật set trực tiếp trong Coolify). Biến môi trường bắt buộc: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `SUPABASE_ANON_KEY`, `ALLOWED_EMAIL_DOMAINS`, `OPENROUTER_API_KEY`, `ADMIN_TOKEN`; thêm `ANTHROPIC_API_KEY`+`VAULT_PATH`+3 biến `DRIVE_*` nếu muốn bật nhánh Ingest Agent.

## Checklist trước pilot

- [ ] Thêm authentication/SSO thật cho `/admin` thay vì 1 token dùng chung
- [ ] Gán `department` theo tài khoản đã xác thực thay vì client tự khai (`user_department`) — cần trước khi mở thêm phòng ban thứ 2, xem cảnh báo ở mục Schema Supabase
- [ ] Không đưa dữ liệu lương, hợp đồng hoặc PII vào context staff (kiểm tra lại từng note)
- [ ] Thêm rate limit, observability
- [ ] Backup định kỳ Supabase (Point-in-time recovery hoặc export)
- [ ] Bổ sung test retrieval, prompt injection, regression dataset, và test cho vault_watcher/ingest_agent/wiki_sync_service (hiện chưa có)
- [ ] Tạo tài khoản Google Drive riêng "sở hữu" vault + chạy `rclone config` lấy `DRIVE_RCLONE_CONFIG_B64` cho `drive-sync`
