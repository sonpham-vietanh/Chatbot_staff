# Viet Anh Staff Assistant

MVP RAG nội bộ cho staff Trường Việt Anh / Major Education. Hệ thống đọc các file Markdown trong `Obsidian_Vault/`, chỉ index note có `status: approved`, lưu embedding vào ChromaDB local và trả lời có citation.

## Kiến trúc

```text
Obsidian_Vault/*.md
        |
        v
Frontmatter + markdown-aware chunking + wikilink graph
        |
        v
Mock embeddings -> ChromaDB local -> top-k retrieval -> reranker hook -> OpenRouter LLM
        |
        v
FastAPI: /api/chat-staff, /api/debug/search, /api/reindex
```

MVP có các provider:

- `MockEmbeddingProvider`: vector deterministic, dùng để demo flow. TODO: thay bằng Gemini embedding hoặc provider được duyệt.
- `GeminiEmbeddingProvider`: dùng `gemini-embedding-001` để semantic retrieval.
- `MockLLMProvider`: trả excerpt từ chunk đứng đầu khi chưa có API key.
- `GeminiLLMProvider`: dùng Gemini với prompt guardrails trong `app/rag/prompt_builder.py`.
- `OpenRouterLLMProvider`: gọi model OpenRouter qua endpoint OpenAI-compatible; cấu hình hiện tại dùng `google/gemini-2.5-flash`.
- `NoOpReranker`: interface để cắm cross-encoder/reranker.
- `app/rag/hybrid.py`: interface lexical/BM25 và hàm Reciprocal Rank Fusion để mở rộng hybrid retrieval.
- `GraphService`: lấy note liên kết trực tiếp từ wikilink, dedup và giới hạn context theo `GRAPH_MAX_*`.
- ChromaDB là vector database local tại `data/chroma/`.

## Cấu trúc

```text
app/
  main.py
  config.py
  api/routes.py
  models/schemas.py
  rag/embeddings.py
  rag/prompt_builder.py
  rag/reranker.py
  rag/vector_store.py
  services/obsidian.py
  services/drafts.py
  services/llm.py
  services/rag_service.py
Obsidian_Vault/
Draft_Review/
data/
tests/
requirements.txt
.env.example
```

## Chạy local

### Giao diện React

Frontend demo nằm trong `frontend/`, dùng React + Vite + Tailwind CSS và proxy `/api` tới FastAPI port `8000`.

Terminal 1, tại thư mục project:

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

Mở giao diện tại <http://localhost:5173>. API docs vẫn ở <http://localhost:8000/docs>.

### Cấu hình OpenRouter

Không ghi API key vào source code hoặc commit. Vì key đã từng bị lộ trong cuộc trò chuyện, hãy revoke key cũ và tạo key mới trong OpenRouter. Sau đó mở `.env` local và đặt:

```env
EMBEDDING_PROVIDER=mock
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=điền_key_mới_của_mày_vào_đây
OPENROUTER_MODEL=google/gemini-2.5-flash
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
```

Embedding hiện dùng mock deterministic để chạy local ổn định, còn OpenRouter đảm nhiệm generation. Khi đổi embedding provider/model, chạy lại `POST /api/reindex`; lệnh này reset collection trước khi upsert để tránh lỗi khác dimension.

### Kết nối Obsidian Vault trên Windows

`Obsidian.exe` chỉ là chương trình mở Obsidian, không phải dữ liệu tri thức. Backend cần đường dẫn tới thư mục Vault, nơi chứa các file Markdown `.md`.

Trên máy hiện tại, Obsidian đang đăng ký Vault tại:

```text
C:\Users\08888\OneDrive\Documents\Obsidian Vault
```

Đặt đường dẫn đó trong file `.env`:

```env
OBSIDIAN_VAULT_PATH=C:\Users\08888\OneDrive\Documents\Obsidian Vault
```

Nếu máy khác hoặc Vault khác, mở Obsidian, chọn **Settings > About > Open vault folder**, rồi lấy đúng đường dẫn thư mục được mở. Không đặt `C:\Users\08888\AppData\Local\Programs\Obsidian\Obsidian.exe` vào biến này.

Backend đọc đệ quy tất cả file `.md` trong Vault. Chỉ note có `status: approved` trong YAML frontmatter mới được index.

PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn app.main:app --reload
```

Mở tài liệu API tại <http://127.0.0.1:8000/docs>.

Trong terminal khác, reindex dữ liệu mẫu:

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/reindex
```

Kiểm tra backend đã nhìn thấy Vault:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
```

Watcher realtime đã được bật mặc định bằng `watchdog`: khi file `.md` được tạo, sửa, xóa hoặc rename trong Vault, hệ thống chờ `VAULT_WATCHER_DEBOUNCE_SECONDS` rồi tự reindex. Không cần bấm Reindex thủ công sau mỗi lần lưu note. Endpoint `POST /api/reindex` vẫn có thể dùng để ép đồng bộ ngay lập tức.

Thử debug retrieval:

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/api/debug/search?q=nghỉ phép"
```

Thử chatbot:

```powershell
$body = @{ question = "Tôi cần xin nghỉ phép trước bao lâu?" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/chat-staff -ContentType "application/json" -Body $body
```

Nếu câu hỏi không có context, chatbot chỉ từ chối an toàn. Chatbot không tự tạo draft; chỉ thao tác upload chủ động của HR/admin mới ghi dữ liệu vào Obsidian.

## Frontmatter bắt buộc

```yaml
---
title: Quy trình nghỉ phép
department: HR
owner: hr@vietanh.edu.vn
status: approved
version: "1.0"
effective_date: 2026-10-01
access_level: staff
---
```

Chỉ `status: approved` được index. Request dùng `user_department`, `user_access_level` và `version` để filter metadata. `staff` chỉ đọc note `staff`, `manager` đọc `staff` và `manager`, còn `admin` đọc cả ba mức.

## Graph-RAG chuẩn bị sẵn

Wikilink dạng `[[Ten_file]]`, `[[Ten_file#Heading]]` hoặc `[[Ten_file|Nhãn]]` được thu thập trong lúc reindex. Edges được ghi vào `data/graph_edges.json` và được `GraphService` dùng để mở rộng context sau vector retrieval.

## API chính

- `GET /api/health`: health check.
- `POST /api/reindex`: đọc vault, tạo chunks, graph edges và upsert Chroma.
- `POST /api/chat-staff`: hỏi đáp grounded, citation, draft khi thiếu dữ liệu.
- `POST /api/knowledge/upload`: HR/admin upload Markdown, text, CSV, JSON, PDF, DOCX hoặc hình ảnh vào Vault dưới dạng draft.
- `GET /api/debug/search?q=`: xem chunk và score được retrieve.

Upload từ giao diện chat dùng nút kẹp file. Chọn phòng ban `HR` và quyền `Staff` hoặc `Admin` để upload; backend sẽ trả `403` cho user không có quyền. File text/PDF/DOCX được trích nội dung vào note Markdown, còn ảnh được lưu trong `.staff_uploads/` và nhúng bằng wikilink. Mọi upload luôn bắt đầu ở `status: draft`; HR cần mở note trong Obsidian, kiểm tra nội dung, bổ sung metadata nếu cần và đổi thành `status: approved`. Sau khi lưu, watcher realtime tự index note vào chatbot.

## Checklist trước pilot

- [ ] Thay mock embedding bằng model đã được duyệt và kiểm tra dữ liệu gửi ra ngoài.
- [ ] Thay mock LLM bằng Gemini/provider nội bộ, giữ nguyên prompt guardrails.
- [ ] Thêm authentication/SSO và phân quyền theo access level thật.
- [ ] Không đưa dữ liệu lương, hợp đồng hoặc PII vào context staff.
- [ ] Thêm audit log, rate limit, observability và backup Chroma.
- [ ] Bổ sung test retrieval, prompt injection, access control và regression dataset.
