# Mô tả API báo cáo — Trợ lý nội bộ Trường Việt Anh

*Dành cho team AI của Major OS · viết theo tài liệu "Kết nối app với Major OS v2" · mọi dữ liệu mẫu bên dưới là dữ liệu giả*

## 1. App làm gì, ai dùng

- **Trợ lý nội bộ** là chatbot hỏi–đáp quy định, quy trình, chính sách của Trường Việt Anh. Câu trả lời lấy từ kho tri thức
  đã được duyệt và luôn kèm nguồn.
- **Người dùng: chỉ nhân viên** (email `@truongvietanh.com`). App **không** có học sinh hay phụ huynh nên không có mã học
  sinh, họ tên học sinh hay số điện thoại phụ huynh trong bất kỳ báo cáo nào.
- Ngoài hỏi–đáp, leader/admin có trang **Quản lý tri thức** để thêm/sửa/xoá tài liệu của phòng ban mình.
- App có hiển thị cho người dùng câu: "Ứng dụng ghi nhận thời gian và tính năng sử dụng để cải thiện sản phẩm."

## 2. Kết nối

| | |
|---|---|
| Địa chỉ gốc | `{{BASE_URL}}` |
| Giao thức | HTTPS, `GET`, trả JSON UTF-8 |
| Xác thực | Header `Authorization: Bearer <key>` (chỉ cách này; không nhận key trên URL) |
| Key | Key riêng cho Major OS, **chỉ đọc**, thu hồi được. Admin tạo ở trang `/admin` → **Kết nối Major OS**. Key hiện đúng **một lần** lúc tạo; máy chủ chỉ giữ bản băm |
| Giới hạn gọi | khoảng **{{RATE_LIMIT}} lần/phút/key** (lấy mỗi 15 phút thì dư rất xa). Vượt thì nhận `429` kèm header `Retry-After` (giây). Gọi sai key quá 30 lần/phút từ cùng một IP cũng bị `429` |
| Múi giờ | Mọi mốc thời gian trả ra theo **giờ Việt Nam (+07:00)**. Tham số `tu`, `den` nhận ISO 8601 **có múi giờ** (`+07:00`, `Z`...); thiếu múi giờ bị từ chối `422` |
| Lỗi | JSON `{"detail": "..."}` với mã `401` (key sai/thu hồi), `422` (tham số sai), `429` (quá giới hạn), `503` (tạm thời lỗi, thử lại sau) |
| Người liên hệ kỹ thuật | [điền tên/email người phụ trách khi gửi cho Major OS] |

Lọc theo thời gian dùng khoảng **nửa mở `[tu, den)`**: `tu` tính, `den` không tính (`tu` = `den` cho kết quả rỗng; `tu` muộn hơn `den` bị `422`).

**Cách lấy phần mới khuyến nghị:** lần sau lấy `tu` = (`luc` lớn nhất đã nhận) **trừ 2 phút**, rồi **bỏ các dòng đã có theo `id`**. Lùi 2 phút là để
không sót những sự kiện được ghi vào hệ thống chậm vài giây; `id` cố định nên dòng lấy lại không bị đếm đôi.

## 3. Các endpoint

Tất cả nằm dưới `{{BASE_URL}}/api/report/`.

### 3.1 `GET /api/report/kiem-tra` — thử kết nối

Không tham số. Chỉ xác nhận key hợp lệ, không trả dữ liệu người dùng.

```json
{ "ok": true, "ten_app": "Trợ lý nội bộ Trường Việt Anh", "phien_ban": "0.2.0",
  "gio_may_chu": "2026-10-06T09:00:00+07:00", "nhan_key": "Major OS" }
```

### 3.2 `GET /api/report/tinh-nang` — danh mục tính năng

Khoá → tên hiển thị (xem mục 4). Khoá cố định, không đổi nghĩa.

### 3.3 `GET /api/report/su-kien` — nhật ký sử dụng (dùng cho bảng xếp hạng)

| Tham số | Ý nghĩa |
|---|---|
| `tu`, `den` | Khoảng thời gian `[tu, den)`, ISO 8601 có múi giờ. Bỏ trống = không giới hạn đầu/cuối |
| `tinh_nang` | (tuỳ chọn) chỉ lấy 1 khoá tính năng |
| `gioi_han` | Số dòng mỗi trang, 1–500, mặc định 500 (lớn hơn 500 bị `422`) |
| `con_tro` | Giá trị `trang_sau` của trang trước; bỏ trống ở trang đầu |

Dữ liệu sắp theo thời gian tăng dần. Hết dữ liệu thì `trang_sau` là `null`. Lấy lại cùng khoảng nhiều lần cho kết quả
giống nhau; mỗi dòng có `id` cố định nên không sinh bản trùng.

```json
{
  "du_lieu": [
    {
      "id": "evt_6f1c0a8e2b3d4c5e9a7b1d2c3e4f5a6b",
      "luc": "2026-10-06T08:15:00.123456+07:00",
      "nguoi": { "email": "lan.nguyen@truongvietanh.com", "ten": "Nguyễn Thị Lan" },
      "tinh_nang": "hoi_dap",
      "so_lan": 1,
      "chi_tiet": { "tra_loi_duoc": true, "so_nguon": 2 }
    },
    {
      "id": "evt_0a1b2c3d4e5f60718293a4b5c6d7e8f9",
      "luc": "2026-10-06T08:20:10+07:00",
      "nguoi": null,
      "tinh_nang": "hoi_dap_nhung",
      "so_lan": 1,
      "chi_tiet": { "tra_loi_duoc": false, "so_nguon": 0, "nguon": "Major OS" }
    }
  ],
  "trang_sau": "WyIyMDI2LTEwLTA1VDAxOjE1OjAwLjEyMzQ1NiswMDowMCIsImV2dF82ZjFjIl0",
  "gio_may_chu": "2026-10-06T09:00:00+07:00"
}
```

| Trường | Ý nghĩa |
|---|---|
| `id` | Mã sự kiện cố định, duy nhất |
| `luc` | Thời điểm xảy ra, giờ VN (+07:00) |
| `nguoi` | `{email, ten}` của nhân viên; `null` khi sự kiện không gắn người (chỉ `hoi_dap_nhung`). `ten` có thể `null` nếu app chưa biết họ tên |
| `tinh_nang` | Khoá tính năng (mục 4) |
| `so_lan` | Số lần (đơn vị: lần). Mỗi sự kiện hiện là 1 lần |
| `chi_tiet` | Thông tin phụ tuỳ tính năng (không bắt buộc đọc), chỉ gồm các khoá: `tra_loi_duoc` (true = câu trả lời có dẫn nguồn tài liệu; không có khoá này với dữ liệu nạp lại từ lịch sử cũ), `so_nguon` (số tài liệu trích dẫn), `nguon` (tên key nhúng, chỉ `hoi_dap_nhung`), `phong_ban` (phòng của tài liệu bị sửa), `ly_do` (lý do báo sai). **Không bao giờ có tiêu đề/nội dung tài liệu hay hội thoại** |

**App không đo số phút sử dụng** nên không có trường `so_phut`; bảng xếp hạng dùng `so_lan`.

### 3.4 `GET /api/report/gop-y` — góp ý của người dùng ("Báo sai")

Cùng tham số `tu`, `den`, `gioi_han`, `con_tro` như 3.3 (lọc theo **thời điểm gửi**).

> **Trạng thái xử lý thay đổi sau khi báo cáo được tạo**, nhưng lọc `tu`/`den` theo thời điểm gửi. Vì vậy mỗi lần lấy nên **đọc lại 30 ngày gần nhất** rồi cập nhật theo `id`
> (dữ liệu nhỏ). Số báo cáo đang chờ luôn có sẵn ở `canh-bao`.

```json
{
  "du_lieu": [
    {
      "id": "b8f0c2de-1a2b-4c3d-9e8f-001122334455",
      "luc": "2026-10-06T10:02:00+07:00",
      "nguoi": { "email": "lan.nguyen@truongvietanh.com", "ten": null },
      "ly_do": "thong_tin_sai",
      "ghi_chu": "Số ngày phép không đúng với quy định mới",
      "cau_hoi": "Tôi được nghỉ phép bao nhiêu ngày?",
      "trang_thai": "chua_xu_ly",
      "xu_ly_luc": null
    }
  ],
  "trang_sau": null,
  "gio_may_chu": "2026-10-06T10:30:00+07:00"
}
```

- `ly_do`: `thong_tin_sai`, `thieu_thong_tin`, `lac_de`, `khac`.
- `trang_thai`: `chua_xu_ly`, `da_sua`, `bo_qua`.
- `ghi_chu`: lời nhắn tự do của người báo (tối đa 2000 ký tự, có thể `null`). `cau_hoi` cắt tối đa 300 ký tự.
- `nguoi`: `null` nếu báo cáo không có email; `ten` lấy từ nhật ký sử dụng, `null` nếu app chưa biết họ tên.

### 3.5 `GET /api/report/canh-bao` — cảnh báo nghiệp vụ hiện tại

Ảnh chụp **trạng thái ngay lúc gọi** (không có `tu`/`den`, không phân trang; mỗi loại đếm tối đa 1000 mục). `id` chính là loại cảnh báo, lấy lại nhiều lần
không sinh trùng; cảnh báo hết nguyên nhân thì biến mất khỏi danh sách.

```json
{
  "du_lieu": [
    {
      "id": "bao_sai_chua_xu_ly",
      "loai": "bao_sai_chua_xu_ly",
      "muc": "canh_bao",
      "tieu_de": "4 báo cáo câu trả lời sai chưa xử lý",
      "mo_ta": "Nhân viên báo trợ lý trả lời sai/thiếu; admin cần xem và sửa tài liệu.",
      "so_luong": 4,
      "tu_luc": "2026-10-01T09:12:00+07:00",
      "duong_dan": "/admin"
    }
  ],
  "gio_may_chu": "2026-10-06T10:30:00+07:00"
}
```

| `loai` | Khi nào xuất hiện | `muc` |
|---|---|---|
| `bao_sai_chua_xu_ly` | Có báo cáo "Báo sai" chưa xử lý; `tu_luc` = báo cáo cũ nhất | `canh_bao` nếu cũ hơn 3 ngày, ngược lại `thong_tin` |
| `cau_hoi_chua_tra_loi` | Có câu hỏi trợ lý chưa trả lời được (đang chờ bổ sung tài liệu) | `thong_tin` |
| `tai_lieu_cho_duyet` | Có tài liệu nháp chờ duyệt | `thong_tin` |
| `ty_le_tra_loi_duoc_thap` | 7 ngày gần nhất có từ 30 câu hỏi trở lên và dưới 50% có dẫn nguồn | `canh_bao` |

`duong_dan` là đường dẫn trên máy chủ của app (`{{BASE_URL}}` + đường dẫn) để người có quyền bấm vào xử lý.

### 3.6 `GET /api/report/tong-quan` — số liệu tổng hợp sẵn

Tham số `tu`, `den` như 3.3; **bỏ trống `tu` thì mặc định 30 ngày gần nhất**. Tiện để vẽ nhanh; số chi tiết nên lấy từ `su-kien`.

```json
{
  "tong_so_lan": 412,
  "so_nguoi_dung": 57,
  "ty_le_tra_loi_duoc": 0.78,
  "theo_tinh_nang": [
    { "tinh_nang": "hoi_dap", "ten": "Hỏi trợ lý nội bộ", "so_lan": 380, "so_nguoi": 55 },
    { "tinh_nang": "khoi_phuc_tri_thuc", "ten": "Khôi phục tài liệu tri thức", "so_lan": 0, "so_nguoi": 0 }
  ],
  "nguoi_dung_nhieu_nhat": [ { "email": "lan.nguyen@truongvietanh.com", "ten": "Nguyễn Thị Lan", "so_lan": 41 } ],
  "bi_cat_bot": false,
  "gio_may_chu": "2026-10-06T10:30:00+07:00"
}
```

Tính năng chưa có lượt nào vẫn xuất hiện với `so_lan: 0` để thấy "tính năng nào ít ai dùng". `bi_cat_bot = true` nghĩa là
khoảng thời gian quá lớn, số liệu chỉ tính trên 20 000 sự kiện đầu — hãy thu hẹp `tu`/`den`.

## 4. Danh sách tính năng (khoá → tên hiển thị)

| Khoá | Tên hiển thị | Ý nghĩa |
|---|---|---|
{{FEATURES_TABLE}}

## 5. Người dùng

| Loại | App gửi |
|---|---|
| Nhân viên | `email` (chữ thường) + `ten` nếu có |
| Học sinh / phụ huynh | **Không có** trong app này |

Dữ liệu sử dụng từ trước ngày bật API này (câu hỏi và báo sai cũ) đã được nạp lại một lần, đánh dấu
`chi_tiet.nguon_du_lieu = "nap_lai_tu_lich_su"`; cuộc trò chuyện người dùng đã xoá trước đó thì không còn để nạp lại.

## 6. Quyền riêng tư

- API **không** trả nội dung câu hỏi hay câu trả lời của nhân viên ở `su-kien`; chỉ `gop-y` có `cau_hoi` (≤ 300 ký tự) vì
  người dùng chủ động gửi để báo sai.
- Key chỉ đọc, không thể ghi/sửa bất kỳ dữ liệu nào của app. Thu hồi key ở `/admin` → **Kết nối Major OS** có hiệu lực ngay.
