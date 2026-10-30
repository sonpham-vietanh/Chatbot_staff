# Sơ đồ luồng — Trợ lý nội bộ Trường Việt Anh (Chatbot_staff)

Tài liệu này mô tả hệ thống đang chạy thật và danh sách việc cần làm tiếp. Các sơ đồ viết bằng Mermaid, xem được trong VS Code (bản xem trước Markdown có Mermaid), GitHub và Obsidian.

Cập nhật lần đầu: 2026-10-29.

## 1. Kiến trúc tổng quan

```mermaid
flowchart LR
    subgraph Nguoi["Người dùng"]
        NV["Nhân viên, giáo viên<br/>staffbot.truongvietanh.com"]
        HR["HR, leader<br/>trang Quản lý"]
        AD["Quản trị<br/>/admin, gõ tay địa chỉ"]
        OS["Major OS<br/>SSO, widget, báo cáo"]
    end

    subgraph VPS["VPS 82.197.68.50, quản bằng Coolify"]
        PX["Traefik proxy<br/>HTTPS"]
        APP["App FastAPI + React<br/>4 worker uvicorn"]
        SYNC["Container vietanh-drive-sync<br/>rclone bisync"]
    end

    subgraph Ngoai["Dịch vụ ngoài"]
        SB[("Supabase<br/>Auth + Postgres")]
        OR["OpenRouter<br/>gpt-6-luna"]
        CL["Endpoint Claude<br/>dự phòng"]
        GD["Google Drive<br/>VietAnhVault"]
    end

    OBS["Obsidian của chủ dự án<br/>wiki_obsidian"]

    NV --> PX
    HR --> PX
    AD --> PX
    OS --> PX
    PX --> APP
    APP <--> SB
    APP --> OR
    OR -. "lỗi thì chuyển" .-> CL
    OBS <--> GD
    GD <--> SYNC
    SYNC --> APP
```

Điểm cần nhớ:
- Mọi dữ liệu người dùng (hồ sơ, WIG, lịch sử chat, báo sai) nằm trong Supabase. Wiki là nguồn tri thức, bản sao nằm trong Supabase để tìm kiếm.
- Quyết định 2026-10-29: dùng Supabase bản trả phí `bwgnjabitfrccezpppia` làm production lâu dài. Dự án thử `paebtebjgpsucthuehuo` bỏ qua, không chuyển.

## 2. Luồng một câu hỏi chat

```mermaid
flowchart TD
    A["Nhân viên gửi câu hỏi<br/>POST /api/chat-staff"] --> B{"Token Supabase<br/>hợp lệ, đúng miền email?"}
    B -- không --> B1["401, front tự refresh phiên"]
    B -- có --> C{"Câu hỏi về chính mình?<br/>hồ sơ, WIG, phép còn lại"}
    C -- có --> C1["Trả lời từ hồ sơ,<br/>danh bạ, WIG của người hỏi"]
    C -- không --> D{"Chào, cảm ơn,<br/>hỏi ngày giờ?"}
    D -- có --> D1["Trả lời tức thì<br/>không gọi LLM"]
    D -- không --> E["Viết lại câu nối tiếp<br/>thành câu đầy đủ"]
    E --> F["Thêm từ đồng nghĩa<br/>tìm BM25 trên chỉ mục wiki"]
    F --> G{"Kết quả đầu yếu?"}
    G -- có --> G1["LLM sinh thêm từ khoá,<br/>tìm lại và gộp"]
    G -- không --> H
    G1 --> H["Lọc đoạn vượt ngưỡng<br/>MIN_RELEVANCE_SCORE"]
    H --> I["Dựng prompt:<br/>quy tắc + người hỏi + đoạn wiki + lịch sử"]
    I --> J["LLM trả lời<br/>OpenRouter, lỗi thì Claude"]
    J --> K["Làm sạch định dạng,<br/>tách nguồn trích dẫn"]
    K --> L{"Bot báo thiếu tài liệu?"}
    L -- có --> L1["Tạo note nháp CẦN BỔ SUNG<br/>cho HR, có chống trùng"]
    L -- không --> M
    L1 --> M["Sinh gợi ý câu hỏi tiếp"]
    C1 --> N
    D1 --> N
    M --> N["Lưu lịch sử thread, chat_logs,<br/>usage_events"]
    N --> O["Trả về câu trả lời<br/>+ nguồn + gợi ý"]
    O --> P{"Người dùng thấy sai?"}
    P -- có --> P1["Bấm Báo sai<br/>POST /api/feedback"]
```

## 3. Luồng dữ liệu: từ tài liệu gốc đến câu trả lời

```mermaid
flowchart LR
    SRC["Tài liệu gốc HR<br/>docx, xlsx, quy định"] --> RAW["nhan-su/raw<br/>bất biến, chỉ thêm mới"]
    RAW --> ING["Agent Ingest<br/>tốn phí, tự chạy khi có file mới"]
    ING --> WIKI["nhan-su/wiki + core<br/>do LLM viết và bảo trì"]
    FB["Báo sai của HR"] --> LLMV["Người vận hành<br/>duyệt và sửa wiki"]
    LLMV --> WIKI
    WIKI <--> DRV["Google Drive<br/>VietAnhVault"]
    DRV --> RC["rclone bisync<br/>container drive-sync"]
    RC --> WAT["VaultWatcher<br/>phát hiện file đổi"]
    WAT --> SY["WikiSyncService.sync_all<br/>upsert theo vault:đường-dẫn"]
    SY --> DB[("knowledge_notes<br/>knowledge_chunks")]
    DB --> IDX["Chỉ mục BM25 trong bộ nhớ<br/>mỗi worker nạp lại sau 45 giây"]
    IDX --> BOT["Câu trả lời của bot"]
```

Lưu ý vận hành:
- Không thêm tệp vào `raw/` nếu chưa chắc, vì VaultWatcher sẽ tự chạy agent Ingest tốn tiền.
- Đồng bộ Drive đang hỏng từ 15/10 (token OAuth hết hạn). Hiện đồng bộ wiki vào bot bằng tay. Xem mục 7.
- Trang đánh dấu `chatbot: false` không đưa vào bot.

## 4. Luồng báo sai và bổ sung dữ liệu

```mermaid
flowchart TD
    A["Bot trả lời sai hoặc thiếu"] --> B["Nhân viên HR bấm Báo sai<br/>kèm ghi chú nội dung đúng"]
    B --> C[("answer_feedback<br/>status = open")]
    A2["Bot không có tài liệu"] --> D[("knowledge_notes<br/>nháp CẦN BỔ SUNG")]
    C --> E["Admin xem báo sai"]
    D --> E
    E --> F{"Ghi chú có nội dung<br/>cụ thể và đúng?"}
    F -- không --> F1["Giữ mở, hỏi lại HR"]
    F -- mâu thuẫn tài liệu gốc --> F2["Ghi vào can-xu-ly.md<br/>chờ HR chốt"]
    F -- có --> G["Đối chiếu tài liệu gốc,<br/>sửa trang wiki, ghi log.md"]
    G --> H["Đồng bộ wiki vào DB"]
    H --> I["Hỏi thử lại câu đó"]
    I --> J["Bấm Đã xử lý<br/>cộng điểm cho người báo"]
```

## 5. Luồng hồ sơ cá nhân, danh bạ và WIG

```mermaid
flowchart TD
    HR1["HR nhập danh bạ nhân sự<br/>file CSV hoặc trang Quản lý"] --> DIR[("employee_directory")]
    U1["Nhân viên đăng nhập lần đầu"] --> CS["Đồng ý điều khoản<br/>PUT /me/profile bắt buộc consent"]
    CS --> PR[("profiles")]
    DIR --> ASK["Bối cảnh người hỏi<br/>tên, phòng, sếp, phép"]
    PR --> ASK
    ASK --> BOTP["Bot cá nhân hoá câu trả lời<br/>cache 180 giây"]
    U1 --> CR["Gửi yêu cầu chỉnh sửa hồ sơ<br/>POST /me/change-requests, bắt buộc lý do"]
    CR --> CRQ[("change_requests<br/>pending")]
    CRQ --> AD1{"Admin duyệt?"}
    AD1 -- duyệt --> DIR
    AD1 -- từ chối --> CR2["Thông báo lý do cho người gửi"]
    U1 --> WG["Tạo và cập nhật WIG<br/>/me/wigs, tiến độ"]
    WG --> TEAM["Leader xem WIG của team<br/>/team/wigs"]
```

Hiện danh bạ có 0 dòng nên bot chưa cá nhân hoá được cho ai. Đây là việc ưu tiên số 1.

## 6. Luồng triển khai code (đang có người dùng thật)

```mermaid
flowchart LR
    DEV["Sửa code trên máy"] --> T["pytest + kiểm tra lặp lại<br/>trên Supabase TEST"]
    T --> RV["Người phụ trách xem lại"]
    RV --> OK{"Chủ dự án nói push?"}
    OK -- chưa --> DEV
    OK -- rồi --> PS["git push origin master"]
    PS --> CF["Coolify build và deploy<br/>chọn giờ ít người dùng"]
    CF --> HC["Health check<br/>/api/health"]
    HC --> SM["Test nhanh:<br/>đăng nhập, hỏi 3 câu mẫu"]
    SM -- lỗi --> RB["Quay lại bản trước<br/>bằng Coolify"]
```

Quy tắc: không push và không deploy khi chưa có lệnh của chủ dự án. Không để hai người cùng deploy một lúc (đã từng xảy ra tình trạng chồng deploy làm 503).

## 7. Kế hoạch việc cần làm

Trạng thái: ☐ chưa làm, ◐ đang làm trên máy (chưa push), ☑ xong.

### Nhóm 1 — Ảnh hưởng người dùng đang dùng
- ◐ Câu trả lời ổn định: nhiệt độ sinh câu trả lời về 0, quy tắc "có thông tin liên quan thì nêu, không từ chối hẳn" (`llm.py`, `prompt_builder.py`).
- ◐ Sửa nhận nhầm ý khi hỏi về sếp, quên mật khẩu email, câu hỏi hỗn hợp, WIG quá hạn (`routes.py`).
- ◐ Gợi ý câu hỏi sát chủ đề, trả lời nhanh "bạn là ai", mở rộng từ đồng nghĩa và viết tắt (`pipeline.py`, `suggestions.py`).
- ◐ Dọn dòng chỉ dẫn nội bộ lọt vào wiki, lập phiếu câu hỏi bot chưa trả lời được cho HR.
- ◐ Thông báo lỗi tiếng Việt trong form hồ sơ, tiêu đề thread khi chỉ chào, từ chối khoá lạ trong yêu cầu chỉnh sửa.
- ☐ HR gửi danh bạ nhân sự để nhập vào `employee_directory`. Có mẫu: `Mau-danh-ba-nhan-su.csv`.
- ☐ Bảo mật: đổi `ADMIN_TOKEN` (hiện 19 ký tự), xoá token API Coolify `claude-vault-setup`, đổi secret key và mật khẩu DB đã dán trong chat.

### Nhóm 2 — Tuần này
- ☐ Sửa đồng bộ Google Drive: Publish ứng dụng OAuth, chạy `rclone config reconnect gdrive:`, chép cấu hình lên VPS.
- ◐ Giám sát: đã viết `/api/health/deep`, `scripts/uptime_check.py`, mục "câu hỏi chưa trả lời được" trong trang admin. Chưa triển khai; cần đặt lịch chạy `uptime_check.py`.
- ◐ Bộ câu hỏi chuẩn (94 câu) và `scripts/eval_golden.py`: chạy sau mỗi lần sửa wiki hoặc trước khi deploy, xem `docs/quy-trinh-kiem-thu.md`.
- ☐ Tìm kiếm còn hụt 3 câu trong bộ chuẩn: khi nào xét tăng lương, giờ làm khối văn phòng, Chuyên viên C&B làm gì.
- ☐ Kiểm tra lịch sao lưu Supabase production.
- ◐ Cột người dùng cho `chat_logs`: code và file migration `20261029000000_chat_logs_user.sql` đã viết, chưa áp dụng vào DB.
- ☐ Thống nhất ai được deploy, quy ước giờ deploy.

### Nhóm 3 — Nội dung HR cần bổ sung
- ☐ Chốt năm tính phép (có 3 mốc: 1/6–31/5, 1/7–30/6, 1/8–31/7).
- ☐ Chốt việc hoàn tiền phép: nhân viên hay chỉ giáo viên.
- ☐ Mức thưởng giới thiệu học sinh, quy định riêng về thai sản, số điện thoại công ty, bảng xếp bậc khởi điểm, email các leader.
- ☐ Các chủ đề chưa có tài liệu: đi làm muộn, làm việc từ xa, intern, công tác phí, nội quy.
- ☐ Xử lý 9 báo sai của người thật đang mở.

### Nhóm 4 — Để sau
- ◐ Trả lời dạng stream: đã viết xong trên máy (`/api/chat-staff/stream`, tự rơi về cách cũ nếu lỗi); chưa thử trên trình duyệt và qua Cloudflare thật.
- ☑ Chuyển Supabase: huỷ, giữ bản trả phí hiện tại.
- ☐ Môi trường thử (staging) riêng.
- ☐ Quyết định có bắt buộc điền hồ sơ trước khi dùng không.

## 8. Tham chiếu nhanh

| Thành phần | Vị trí |
|---|---|
| Khởi động, lifespan, SSO, trang admin | `app/main.py` |
| Toàn bộ API | `app/api/routes.py` |
| Pipeline hỏi đáp | `app/rag/pipeline.py` |
| Tìm kiếm BM25 | `app/rag/lexical_store.py` |
| Prompt và quy tắc trả lời | `app/rag/prompt_builder.py` |
| Gợi ý câu hỏi tiếp | `app/rag/suggestions.py` |
| Gọi LLM và chuỗi dự phòng | `app/services/llm.py` |
| Đồng bộ wiki vào DB | `app/services/wiki_sync_service.py` |
| Theo dõi thư mục wiki | `app/services/vault_watcher.py` |
| Giao diện | `frontend/src/` |
| Cấu trúc bảng | `supabase/migrations/` |
| Đồng bộ Drive | `drive-sync/` |
| Hướng dẫn SSO Major OS | `docs/Huong_dan_SSO_Major_OS.md` |
| Wiki nguồn tri thức | `E:\Workspace\wiki_obsidian` (CLAUDE.md là hợp đồng vận hành) |

Các biến môi trường chính: `SUPABASE_*`, `OPENROUTER_*`, `LLM_*` (endpoint Claude dự phòng), `RETRIEVAL_MODE=lexical`, `ADMIN_TOKEN`, `VAULT_PATH`, `UVICORN_WORKERS`.
