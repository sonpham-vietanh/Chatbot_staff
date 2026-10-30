# Trợ lý nội bộ ↔ Major OS: việc cần làm bên Major OS

Cập nhật: 30/10/2026. Dành cho người phụ trách Major OS.

App "Trợ lý nội bộ" (chatbot nhân sự Trường Việt Anh) đã đổi tên miền, thêm tính phút sử dụng và thêm webhook đẩy sự kiện sang Major OS. Tài liệu này nói rõ bên Major OS cần làm gì.

## 1. Việc cần làm (theo thứ tự)

1. **Đổi địa chỉ app trong Major OS, làm ngay.**
   - Địa chỉ cũ `https://staffbot.vietanh.org` đã ngừng chạy (trả 503), nên trang "Kết nối app" báo lỗi "Mất kết nối" từ 09:52 ngày 27/10.
   - Địa chỉ mới: **`https://staffbot.truongvietanh.com`**. Giữ nguyên khoá API đang dùng.
   - Đổi cả ba chỗ nếu có: địa chỉ gọi `/api/report/*`, địa chỉ nút đăng nhập một lần (`/sso/os?token=...`) và địa chỉ widget (`/api/widget/chat`).
   - Sau khi đổi, bấm "Thử kết nối". Endpoint kiểm tra là `GET /api/report/kiem-tra` và phải trả `{"ok": true, ...}`.
2. **Dựng một địa chỉ HTTPS nhận webhook** và gửi cho chủ dự án để đặt vào app (xem mục 3).
3. **Tạo một chuỗi bí mật chung**, tối thiểu 32 ký tự ngẫu nhiên, dùng để ký và kiểm tra chữ ký (xem mục 3 và 4). Gửi cho chủ dự án qua kênh riêng, không dán vào chat hay email chung.
4. **Cài kiểm tra chữ ký và xử lý trùng** ở phía nhận (mục 4 có code mẫu).
5. **Hiển thị số phút sử dụng** nếu cần, lấy từ khối `phut_su_dung` của `/api/report/tong-quan` (mục 5).

Webhook chỉ bật khi app được đặt cả hai biến ở mục 3. Chưa đặt thì app hoạt động bình thường như cũ và Major OS vẫn lấy số qua API báo cáo.

## 2. API báo cáo (Major OS gọi vào app, đã có từ trước)

Xác thực bằng header `Authorization: Bearer <khoá báo cáo>`. Chỉ đọc.

| Đường dẫn | Việc |
|---|---|
| `GET /api/report/kiem-tra` | Thử kết nối, xác nhận khoá hợp lệ |
| `GET /api/report/tinh-nang` | Danh sách khoá tính năng |
| `GET /api/report/su-kien` | Nhật ký sử dụng, lọc `tu`, `den`, `tinh_nang`, phân trang bằng `con_tro` |
| `GET /api/report/gop-y` | Báo sai của người dùng |
| `GET /api/report/tong-quan` | Tổng hợp (cũng là `/api/report/canh-bao`) |

Thời gian dùng ISO 8601 có múi giờ, ví dụ `2026-10-04T00:00:00+07:00`.

## 3. Webhook: app đẩy sang Major OS

Mỗi khi app ghi một sự kiện sử dụng, app gửi sự kiện đó sang Major OS để cập nhật gần thời gian thực. Webhook chỉ là kênh bổ sung: sự kiện nào lỡ vẫn lấy bù được bằng `GET /api/report/su-kien`. Webhook chạy nền, không bao giờ làm chậm hay làm hỏng thao tác của người dùng.

**Cấu hình phía app** (chủ dự án đặt trong Coolify rồi deploy lại):

| Biến | Ý nghĩa |
|---|---|
| `OS_WEBHOOK_URL` | Địa chỉ HTTPS Major OS nhận webhook |
| `OS_WEBHOOK_SECRET` | Chuỗi bí mật dùng chung để ký |

**Yêu cầu gửi:** `POST <OS_WEBHOOK_URL>`, các header:

- `Content-Type: application/json`
- `X-Signature: sha256=<HMAC-SHA256 của body thô, khoá là OS_WEBHOOK_SECRET, dạng hex>`
- `X-Event-Id: <id sự kiện>`, dùng để chống xử lý trùng

**Body mẫu:**

```json
{
  "su_kien": {
    "id": "b1f0c0de-0000-4000-8000-000000000001",
    "tinh_nang": "hoi_dap",
    "thoi_diem": "2026-10-30T09:15:02.123456+07:00",
    "nguoi_dung": { "email": "lan@truongvietanh.com", "ten": "Nguyễn Thị Lan" },
    "so_luong": 1,
    "chi_tiet": { "tra_loi_duoc": true, "so_nguon": 2 }
  },
  "ten_app": "Trợ lý nội bộ Trường Việt Anh",
  "gio_may_chu": "2026-10-30T09:15:02+07:00"
}
```

- `id` trùng với `id` của dòng tương ứng trong `GET /api/report/su-kien`.
- `nguoi_dung` là `null` với sự kiện không có người dùng (ví dụ `hoi_dap_nhung`).
- `chi_tiet` chỉ gồm các khoá `tra_loi_duoc, so_nguon, nguon, phong_ban, ly_do, nguon_du_lieu`. Không bao giờ có nội dung câu hỏi hay tài liệu.
- Với `phut_su_dung`, app **gộp**: mỗi người chỉ gửi webhook một lần mỗi 5 phút, `so_luong` là số phút đã dồn kể từ lần gửi trước (phút đầu tiên gửi ngay). Vài phút cuối của một phiên chưa có webhook, nên muốn con số tuyệt đối thì đối chiếu qua API báo cáo.

**Quy tắc phía nhận:**

- Trả 2xx nghĩa là đã nhận.
- Mã 4xx: app không thử lại.
- Lỗi mạng hoặc 5xx: app thử lại tối đa 3 lần (chờ 1 giây, rồi 4 giây). Timeout 5 giây.
- Lỗi liên tiếp 5 lần thì app tạm ngừng gửi 5 phút và ghi một dòng cảnh báo vào log.
- Vì có thử lại và app chạy nhiều tiến trình, phía nhận phải xử lý **idempotent** theo `X-Event-Id`.

## 4. Kiểm tra chữ ký

Phải băm trên **body thô** (chưa parse JSON) và so sánh hằng thời gian.

Node.js (Express, dùng `express.raw({ type: "application/json" })`):

```js
const crypto = require("crypto");
function verify(rawBody, header, secret) {
  const expected = "sha256=" + crypto.createHmac("sha256", secret).update(rawBody).digest("hex");
  const a = Buffer.from(expected), b = Buffer.from(header || "");
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}
```

Python:

```python
import hashlib, hmac

def verify(raw_body: bytes, header: str, secret: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header or "")
```

## 5. Khoá tính năng và số phút sử dụng

| Khoá `tinh_nang` | Ý nghĩa |
|---|---|
| `hoi_dap` | Nhân viên hỏi trợ lý (1 câu = 1 lần) |
| `hoi_dap_nhung` | Câu hỏi qua widget nhúng (không có người dùng) |
| `bao_sai` | Bấm "Báo sai" dưới câu trả lời |
| `dang_nhap_os` | Đăng nhập một lần từ Major OS |
| `tao_tri_thuc`, `sua_tri_thuc`, `xoa_tri_thuc`, `khoi_phuc_tri_thuc` | Leader/admin quản lý tài liệu tri thức |
| `phut_su_dung` | 1 phút người dùng thật sự đang dùng app |
| `thu_ket_noi` | Chỉ có khi thử bằng endpoint test, không ghi vào dữ liệu |

**Cách app tính phút:** giao diện gửi một tín hiệu mỗi 60 giây, chỉ khi tab đang hiển thị và người dùng có thao tác (chuột, bàn phím, cuộn, chạm) trong 2 phút gần nhất. Mỗi người tối đa 1 phút mỗi 50 giây và 600 phút mỗi ngày.

**Khối mới trong `GET /api/report/tong-quan`:** `phut_su_dung` gồm `tong_phut`, `so_nguoi_co_hoat_dong`, `phut_trung_binh_moi_nguoi`, `nguoi_dung_nhieu_nhat` (có `so_phut`), `theo_ngay` và `bi_cat_bot`.

- Phút **không** được cộng vào `tong_so_lan`, `so_nguoi_dung` và `nguoi_dung_nhieu_nhat` cũ. Các chỉ số đó vẫn nghĩa là lượt dùng, nên số cũ không đổi.
- `theo_tinh_nang` có thêm dòng `phut_su_dung` với `so_lan` bằng tổng phút.
- Phần phút chỉ quét tối đa 20.000 dòng gần nhất. Khi `bi_cat_bot` là `true`, hãy gọi theo từng ngày bằng `tu` và `den`.

## 6. Cách thử webhook sau khi bật

Chủ dự án gọi:

```bash
curl -X POST https://staffbot.truongvietanh.com/api/admin/webhook/test -H "X-Admin-Token: <ADMIN_TOKEN>"
```

App gửi một sự kiện `thu_ket_noi` (không ghi vào dữ liệu) tới `OS_WEBHOOK_URL` và trả `{"ok": true, "ma_phan_hoi": 200}`, không lộ chuỗi bí mật. Trả 409 nếu chưa đặt đủ hai biến.

## 7. Lưu ý

- **Định dạng webhook do phía app tự đặt**, vì tài liệu kết nối Major OS hiện chưa có đặc tả webhook. Nếu Major OS cần định dạng khác (tên trường, tên header, thuật toán ký, địa chỉ theo từng sự kiện) thì báo chủ dự án. Chỉ cần sửa `app/services/webhook_service.py`.
- Mỗi người dùng sinh khoảng 60 bản ghi `phut_su_dung` mỗi giờ. Bảng nhật ký sẽ lớn dần, sẽ cần chính sách dọn dữ liệu cũ sau này.
