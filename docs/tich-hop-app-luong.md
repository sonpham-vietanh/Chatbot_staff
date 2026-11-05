# Tích hợp bot nhân sự với app lương

Dành cho người phát triển app lương. Hai app dùng chung hồ sơ nhân sự, **bot là nguồn gốc**. App lương có ba việc: nhận thay đổi danh bạ từ bot, sửa ngược vài trường được phép, và nhúng bong bóng chat hỏi bot về cách dùng app.

```
HR sửa danh bạ ──► bot ──(webhook ký HMAC)──► app lương      nhận thay đổi nhân sự
                   bot ◄──(API ký HMAC)────── app lương      sửa vài trường, đẩy bài hướng dẫn, hỏi chat
Trình duyệt ─► backend app lương ─(ký HMAC + email)─► bot     bong bóng chat
```

Khoá bí mật **không bao giờ** đưa xuống trình duyệt. Trình duyệt chỉ nói chuyện với backend app lương; backend ký và gọi sang bot.

## 0. Việc cần làm bên app lương

Địa chỉ bot: `https://staffbot.truongvietanh.com`. Các mục chi tiết (chữ ký, định dạng, code mẫu) nằm ở mục 1 đến 6 bên dưới; mục này chỉ là danh sách việc và cách nghiệm thu. Không cần sửa gì bên bot, chủ dự án đặt ba biến ở mục 1 và gửi khoá riêng cho bên làm app lương.

### Việc phải làm

1. **Biến môi trường của app lương:** `BOT_BASE_URL`, `BOT_INBOUND_SECRET` (cùng giá trị `PAYROLL_INBOUND_SECRET` ở bot), `BOT_WEBHOOK_SECRET` (cùng giá trị `PAYROLL_WEBHOOK_SECRET` ở bot). Không đưa ba biến này xuống frontend, không ghi vào log.
2. **Bảng nhân viên dùng chung:** khoá chính là `email` (chữ thường). Các cột lấy từ bot: `display_name`, `department`, `job_title`, `employment_start_date`, `employment_end_date`, `active`, `manager_email`, `campus`, `employee_code`, `level_name`, `contract_type`, `employment_type`, `team_name`. Dữ liệu lương riêng của app (lương cơ bản, phụ cấp, bảng lương...) nằm ở bảng khác, **không** nằm trong các cột này và không bao giờ gửi sang bot.
3. **Endpoint nhận webhook** (HTTPS công khai, ví dụ `POST /webhooks/bot`): so chữ ký trên body thô (mục 4), bỏ qua sự kiện đã xử lý theo `X-Event-Id`, upsert từng nhân viên trong `su_kien.chi_tiet.nhan_vien` theo `email`, rồi trả 2xx **ngay** (xử lý nặng đẩy sang hàng đợi, timeout của bot là 5 giây). Chữ ký sai trả 401. Gửi địa chỉ này cho chủ dự án để đặt vào `PAYROLL_WEBHOOK_URL`.
4. **Xử lý người nghỉ việc:** `active: false` thì không đưa vào kỳ lương sau ngày `employment_end_date`, nhưng **giữ** bản ghi và lịch sử lương, không xoá.
5. **Đồng bộ bù:** khi app khởi động và mỗi vài giờ, gọi `GET /api/partner/payroll/directory?offset=&limit=100` lặp đến hết (`tong`), upsert tất cả. Lần chạy đầu tiên chính là lần nạp danh bạ ban đầu, chạy việc này **trước** khi bật webhook.
6. **Client gọi bot:** một hàm `callBot(method, path, payload, query)` ký theo mục 2, dùng chung cho các việc dưới. Có timeout (60 giây cho chat, 15 giây cho còn lại).
7. **Bong bóng chat:** endpoint `POST /api/bot-chat` ở backend (bắt buộc đã đăng nhập, lấy `email` từ phiên đăng nhập, không lấy từ body), gọi `/api/partner/payroll/chat`; frontend dán bong bóng mẫu ở mục 6. Hiển thị `detail` khi bot trả lỗi (429 hỏi nhanh, 403 chưa có trong danh bạ).
8. **Bài hướng dẫn cho bot:** viết các bài hướng dẫn dùng app (xem phiếu lương, cách tính từng khoản, câu hỏi thường gặp, ai liên hệ khi sai) dưới dạng file markdown trong repo của app lương, và một lệnh `sync-bot-knowledge` đọc các file đó rồi gọi `PUT /knowledge` (mục 5). Mã bài (`id`) lấy từ tên file, ổn định. Chạy lệnh này mỗi lần deploy; bài đã xoá khỏi repo thì đưa vào `delete_ids`.
9. **(Tuỳ chọn) Sửa ngược:** nếu app lương là nơi nhập ngày vào làm, loại hợp đồng, hình thức hoặc bậc, gọi `POST /employees` (mục 4) khi các giá trị đó đổi. Chỉ 4 trường đó, và người phải có sẵn trong danh bạ bot.

### Nghiệm thu

Làm lần lượt, mỗi bước có kết quả mong đợi:

| Bước | Làm | Kết quả đúng |
|---|---|---|
| 1 | Gọi `GET /api/partner/payroll/ping` có ký | `{"ok": true}`. 401 là sai khoá hoặc đồng hồ máy lệch hơn 5 phút. |
| 2 | Chạy đồng bộ bù (việc 5) | Số nhân viên trong app lương bằng `tong` bot trả về, người nghỉ có `active = false`. |
| 3 | Chủ dự án gọi `POST /api/admin/payroll-webhook/test` | Endpoint webhook nhận sự kiện `thu_ket_noi`, chữ ký hợp lệ, bot nhận `{"ok": true, "ma_phan_hoi": 200}`. |
| 4 | HR đổi chức danh một người trên bot | Trong vài giây, `job_title` của người đó đổi ở app lương. Gửi lại cùng `X-Event-Id` không tạo bản ghi mới. |
| 5 | Chạy `sync-bot-knowledge` hai lần liên tiếp | Lần 1 `created = n`, lần 2 `unchanged = n`. Sau khoảng 45 giây, hỏi bong bóng đúng nội dung một bài thì câu trả lời dùng bài đó. |
| 6 | Đăng nhập là nhân viên A, hỏi "sếp của tôi là ai?" | Trả đúng quản lý của A. Đổi `email` trong request ở devtools thì backend vẫn dùng email của phiên (không đổi được người). |
| 7 | Hỏi 21 câu trong một phút | Câu thứ 21 trả 429. |
| 8 | Sửa một byte trong body đã ký rồi gửi | 401. |

### Không được làm

- Không gửi lương, phụ cấp, thuế, số tài khoản hay bất kỳ số liệu riêng của từng người sang bot (kênh `knowledge` và `chat` đều không phải nơi cho việc đó).
- Không ký ở trình duyệt, không để khoá trong mã frontend.
- Không tạo hay xoá nhân viên từ app lương; việc đó làm ở bot (HR), app lương nhận qua webhook.
- Không bỏ qua bước so chữ ký webhook, dù chỉ ở môi trường thử.

## 1. Cấu hình

Phía bot (Coolify, rồi deploy lại). Để trống = tắt.

| Biến | Việc |
|---|---|
| `PAYROLL_INBOUND_SECRET` | Khoá ký các yêu cầu app lương gọi vào bot. Tối thiểu 32 ký tự ngẫu nhiên. |
| `PAYROLL_WEBHOOK_URL` | Địa chỉ HTTPS của app lương nhận webhook từ bot. |
| `PAYROLL_WEBHOOK_SECRET` | Khoá bot dùng ký webhook gửi sang app lương. Khác khoá trên. |

Tạo khoá: `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Gửi sang app lương qua kênh riêng, không dán vào chat chung.

## 2. Ký yêu cầu gọi vào bot

Mọi endpoint `/api/partner/payroll/*` cần hai header:

- `X-Timestamp`: giây Unix hiện tại. Lệch quá 5 phút bị từ chối.
- `X-Signature`: `sha256=` + HMAC-SHA256 dạng hex, khoá là `PAYROLL_INBOUND_SECRET`, trên chuỗi:

```
<X-Timestamp>\n<METHOD viết hoa>\n<đường dẫn, không có query>\n<body thô>
```

Chữ ký gắn với thời điểm, phương thức và đường dẫn nên không phát lại được và không dùng chéo endpoint. Với GET, body là rỗng. Đường dẫn là `/api/partner/payroll/chat`, không kèm `?offset=...`.

Python:

```python
import hashlib, hmac, json, time, httpx

BOT = "https://staffbot.truongvietanh.com"
SECRET = "<PAYROLL_INBOUND_SECRET>"

def call(method, path, payload=None, params=None):
    body = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else b""
    ts = str(int(time.time()))
    message = f"{ts}\n{method}\n{path}\n".encode() + body
    signature = "sha256=" + hmac.new(SECRET.encode(), message, hashlib.sha256).hexdigest()
    return httpx.request(method, BOT + path, content=body, params=params, timeout=60,
                         headers={"X-Timestamp": ts, "X-Signature": signature, "Content-Type": "application/json"})
```

Node.js:

```js
const crypto = require("crypto");
async function callBot(method, path, payload, query = "") {
  const body = payload === undefined ? "" : JSON.stringify(payload);
  const ts = String(Math.floor(Date.now() / 1000));
  const signature = "sha256=" + crypto.createHmac("sha256", process.env.PAYROLL_INBOUND_SECRET)
    .update(`${ts}\n${method}\n${path}\n${body}`).digest("hex");
  return fetch(`https://staffbot.truongvietanh.com${path}${query}`, {
    method, body: body || undefined,
    headers: { "X-Timestamp": ts, "X-Signature": signature, "Content-Type": "application/json" },
  });
}
```

Body phải là đúng chuỗi byte đã ký. Đừng ký một chuỗi rồi gửi bản JSON đã định dạng lại.

## 3. Các endpoint

| Endpoint | Việc |
|---|---|
| `GET /api/partner/payroll/ping` | Thử khoá và đồng hồ. Trả `{"ok": true}`. |
| `POST /api/partner/payroll/chat` | Hỏi bot thay cho một nhân viên (mục 6). |
| `PUT /api/partner/payroll/knowledge` | Đẩy hoặc xoá bài hướng dẫn dùng app (mục 5). |
| `POST /api/partner/payroll/employees` | Sửa ngược vài trường hồ sơ (mục 4). |
| `GET /api/partner/payroll/directory?offset=0&limit=100` | Toàn bộ danh bạ theo trang, gồm cả người đã nghỉ (có cờ `active`). |

Mã lỗi: 401 chữ ký hoặc đồng hồ sai, 403 nhân viên không có trong danh bạ hoặc đã nghỉ, 422 dữ liệu sai, 429 hỏi quá nhanh, 503 bot chưa đặt `PAYROLL_INBOUND_SECRET`.

## 4. Đồng bộ hồ sơ nhân sự

### Bot → app lương (webhook)

Mỗi khi HR tạo, sửa, cho nghỉ, kích hoạt lại, nhập hàng loạt, hoặc duyệt yêu cầu chỉnh sửa của nhân viên, bot gửi:

`POST <PAYROLL_WEBHOOK_URL>`, header `Content-Type: application/json`, `X-Event-Id: <id>`, `X-Signature: sha256=<HMAC-SHA256 của body thô, khoá PAYROLL_WEBHOOK_SECRET, hex>`. Lưu ý: chữ ký webhook chỉ băm **body**, khác chữ ký yêu cầu gọi vào bot ở mục 2.

```json
{
  "su_kien": {
    "id": "b1f0c0de-0000-4000-8000-000000000001",
    "tinh_nang": "nhan_vien_cap_nhat",
    "thoi_diem": "2026-11-05T10:15:02+07:00",
    "nguoi_dung": null,
    "so_luong": 2,
    "chi_tiet": {
      "nhan_vien": [
        {"email": "lan@truongvietanh.com", "display_name": "Nguyễn Thị Lan", "department": "HR&OP", "job_title": "Chuyên viên",
         "employment_start_date": "2024-01-15", "employment_end_date": null, "active": true, "manager_email": "mgr@truongvietanh.com",
         "campus": "Phan Huy Ích", "employee_code": "NV-0012", "level_name": null, "contract_type": "HĐLĐ không xác định thời hạn",
         "employment_type": "co_huu", "team_name": null}
      ]
    }
  },
  "ten_app": "Trợ lý nội bộ Trường Việt Anh",
  "gio_may_chu": "2026-11-05T10:15:02+07:00"
}
```

- Mỗi webhook chứa tối đa 100 nhân viên. `active: false` nghĩa là đã nghỉ.
- Mỗi nhân viên là bản ghi **đầy đủ** các trường trên (không phải phần thay đổi): app lương upsert theo `email`.
- Không có số điện thoại, môn dạy, kiêm nhiệm.
- Trả 2xx là đã nhận. 4xx bot không thử lại. Lỗi mạng hoặc 5xx thử lại tối đa 3 lần (chờ 1 giây, 4 giây), timeout 5 giây. Lỗi liên tiếp 5 lần thì tạm ngừng gửi 5 phút.
- Xử lý **idempotent** theo `X-Event-Id` và so chữ ký bằng hàm so sánh hằng thời gian.
- Webhook có thể lỡ (app lương tắt, bot ngắt mạch). Cứ vài giờ, hoặc mỗi lần khởi động, gọi `GET /api/partner/payroll/directory` để đồng bộ bù. Lần nạp đầu tiên cũng làm như vậy.

Xác minh webhook (Python):

```python
def verify(raw_body: bytes, header: str, secret: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header or "")
```

Thử từ phía bot: `curl -X POST https://staffbot.truongvietanh.com/api/admin/payroll-webhook/test -H "X-Admin-Token: <ADMIN_TOKEN>"` gửi sự kiện `thu_ket_noi` và trả `{"ok": true, "ma_phan_hoi": 200}`.

### App lương → bot (sửa ngược)

`POST /api/partner/payroll/employees`

```json
{"employees": [{"email": "lan@truongvietanh.com", "employment_start_date": "2024-01-15", "contract_type": "HĐLĐ 12 tháng", "level_name": "Bậc 3"}]}
```

- Chỉ các trường `employment_start_date` (dạng `YYYY-MM-DD`), `contract_type`, `employment_type` (`co_huu`, `thinh_giang`, `hop_dong`, `khac`), `level_name` được sửa. Trường khác bị bỏ qua.
- Không tạo người mới. Thêm hoặc cho nghỉ nhân viên làm ở bot (HR), rồi webhook báo sang app lương.
- Tối đa 200 người mỗi lần. Kết quả: `{"updated": n, "unchanged": n, "errors": [{"row", "email", "message"}]}`. Lỗi một dòng không ảnh hưởng dòng khác.
- Sửa từ app lương được ghi vào nhật ký danh bạ với người thực hiện `payroll-app` và **không** đẩy ngược lại app lương, nên không có vòng lặp.

## 5. Kiến thức của app lương cho bong bóng chat

Bot trả lời dựa trên bài đã duyệt trong kho tri thức. App lương đẩy các bài hướng dẫn của mình vào đó:

`PUT /api/partner/payroll/knowledge`

```json
{
  "articles": [
    {"id": "cach-xem-phieu-luong", "title": "Cách xem phiếu lương hằng tháng", "content": "Vào mục Phiếu lương, chọn tháng cần xem..."}
  ],
  "delete_ids": ["bai-da-bo"]
}
```

- `id` do app lương đặt, ổn định (chữ, số, `.`, `_`, `-`, tối đa 64 ký tự). Gửi lại cùng `id` là cập nhật. Tối đa 50 bài và 50 mã xoá mỗi lần; tiêu đề tối đa 200 ký tự, nội dung tối đa 20.000 ký tự.
- Bài có hiệu lực ngay, dùng chung cho mọi nhân viên, và chỉ sửa hoặc xoá được bằng đúng `id` này. Bot không đụng vào bài của phòng ban khác.
- Viết như tài liệu hướng dẫn: bước làm, ý nghĩa từng khoản, câu hỏi thường gặp. Mỗi bài một chủ đề, có tiêu đề rõ, vì bot tìm theo từ khoá.
- **Không gửi số liệu lương của từng người.** Kênh này công khai với mọi nhân viên hỏi bot.
- Bài mới thường tìm được sau khoảng 45 giây (chỉ mục tìm kiếm làm mới định kỳ).
- Kết quả: `{"created", "updated", "unchanged", "deleted", "errors"}`.

## 6. Bong bóng chat

`POST /api/partner/payroll/chat`

```json
{"email": "lan@truongvietanh.com", "question": "Phiếu lương tháng này xem ở đâu?", "history": [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]}
```

Trả `{"answer": "...", "grounded": true, "citations": [...], "suggestions": [...]}`. `answer` là văn bản thuần, không có ký hiệu markdown.

- `email` là email của người đang đăng nhập app lương. **Lấy từ phiên đăng nhập ở backend, không lấy từ dữ liệu trình duyệt gửi lên.**
- Người đó phải đang hoạt động trong danh bạ của bot, nếu không bot trả 403.
- Bot biết họ tên, phòng ban, chức danh, thâm niên của người hỏi nên trả lời được "sếp tôi là ai", "tôi có bao nhiêu ngày phép theo thâm niên".
- `history` là các tin gần nhất của cuộc trò chuyện (bot dùng 6 tin cuối). Bot không lưu hội thoại của bong bóng; app lương giữ lịch sử nếu cần.
- Mỗi người tối đa 20 câu mỗi phút.
- Lượt hỏi được tính vào báo cáo sử dụng (`hoi_dap`, nguồn `app-luong`).

Backend app lương (Node/Express), dùng hàm `callBot` ở mục 2:

```js
app.post("/api/bot-chat", requireLogin, express.json(), async (req, res) => {
  const r = await callBot("POST", "/api/partner/payroll/chat", {
    email: req.user.email,                       // lấy từ phiên đăng nhập
    question: String(req.body.question || ""),
    history: Array.isArray(req.body.history) ? req.body.history : [],
  });
  res.status(r.status).json(await r.json());
});
```

Bong bóng (trình duyệt, gọi backend của chính app lương):

```html
<button id="bot-fab" aria-label="Hỏi trợ lý" style="position:fixed;right:20px;bottom:20px;width:56px;height:56px;border-radius:50%;border:0;background:#14153a;color:#f9dd0e;font-size:24px;cursor:pointer">?</button>
<div id="bot-box" hidden style="position:fixed;right:20px;bottom:88px;width:340px;max-width:calc(100vw - 40px);height:460px;background:#fff;border:1px solid #e7e5da;border-radius:10px;box-shadow:0 12px 32px rgba(20,21,58,.16);display:flex;flex-direction:column">
  <div id="bot-log" style="flex:1;overflow:auto;padding:12px;font:14px/1.5 system-ui"></div>
  <form id="bot-form" style="display:flex;gap:8px;padding:8px;border-top:1px solid #e7e5da">
    <input id="bot-q" autocomplete="off" placeholder="Hỏi về app lương..." style="flex:1;padding:8px;border:1px solid #e7e5da;border-radius:6px">
    <button style="padding:8px 12px;border:0;background:#f9dd0e;color:#14153a;font-weight:700;border-radius:0">Gửi</button>
  </form>
</div>
<script>
const box = document.getElementById("bot-box"), log = document.getElementById("bot-log"), history = [];
document.getElementById("bot-fab").onclick = () => { box.hidden = !box.hidden; };
function add(who, text) { const p = document.createElement("p"); p.textContent = text; p.style.margin = "0 0 8px"; if (who === "me") p.style.textAlign = "right"; log.append(p); log.scrollTop = log.scrollHeight; }
document.getElementById("bot-form").onsubmit = async (e) => {
  e.preventDefault();
  const input = document.getElementById("bot-q"), question = input.value.trim();
  if (!question) return;
  input.value = ""; add("me", question);
  try {
    const r = await fetch("/api/bot-chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question, history: history.slice(-6) }) });
    const data = await r.json();
    const answer = r.ok ? data.answer : (data.detail || "Trợ lý đang bận, thử lại sau nhé.");
    add("bot", answer);
    if (r.ok) history.push({ role: "user", content: question }, { role: "assistant", content: answer });
  } catch { add("bot", "Không kết nối được trợ lý."); }
};
</script>
```

Dùng `textContent` (không `innerHTML`) để câu trả lời không chèn được mã vào trang.

## 7. Thứ tự triển khai gợi ý

1. Đặt ba biến ở mục 1 ở bot, deploy lại. Đặt hai khoá tương ứng ở app lương.
2. Gọi `ping` để chắc khoá và đồng hồ khớp.
3. App lương dựng endpoint nhận webhook, gọi `GET /directory` nạp danh bạ lần đầu, rồi dùng `POST /api/admin/payroll-webhook/test` thử đường webhook.
4. Đẩy các bài hướng dẫn (`PUT /knowledge`), hỏi thử qua `/chat`.
5. Gắn bong bóng vào app lương.

## 8. Lưu ý

- Danh bạ trên bot là nguồn gốc. Nếu app lương cần thêm trường nữa (hoặc cho sửa thêm trường), thêm vào `OUTBOUND_FIELDS` hoặc `INBOUND_EMPLOYEE_FIELDS` trong `app/services/payroll_partner_service.py`.
- Chat qua bong bóng dùng chung kho tri thức với chatbot nội bộ, nên bot cũng trả lời được cả chính sách nhân sự. Hiện chatbot chưa giới hạn nội dung theo phòng ban (mọi nhân viên thấy mọi bài đã duyệt), nên đừng đưa vào kho thứ gì chưa muốn mọi nhân viên đọc.
- Bot chạy nhiều tiến trình; giới hạn 20 câu/phút/người tính riêng theo từng tiến trình nên mức thực tế có thể cao hơn một chút.
