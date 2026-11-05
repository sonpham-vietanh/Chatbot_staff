# Bot trả lời nhóm app lương

Ngày 05/11/2026. Trả lời `yeu-cau-cho-staffbot.md`. Số liệu lấy từ Supabase thật và mã nguồn bot hôm nay.

## 1. Tình trạng 6 việc bên bot

| # | Việc | Tình trạng |
|---|---|---|
| 1 | Đặt `PAYROLL_INBOUND_SECRET` ở bot, deploy lại | **Chưa làm.** Chủ dự án đặt trong Coolify. Khi chưa đặt, bot trả 503. |
| 2 | Gửi khoá cho quản trị app lương | **Chưa làm**, chờ việc 1. Gửi qua kênh riêng. |
| 3 | Địa chỉ gọi vào | `https://staffbot.truongvietanh.com`. |
| 4 | Danh bạ đủ email người dùng app lương | **Chưa đủ chắc chắn**, xem mục 2. |
| 5 | Thông điệp 403 và 429 | Là tiếng Việt thân thiện, xem mục 3. |
| 6 | Chạy `ping` cùng nhau | Làm được ngay khi có khoá. |

## 2. Danh bạ bot so với 219 hồ sơ của app lương

Bot hiện có **212 nhân viên đang hoạt động**, tất cả có ngày vào làm. Theo đơn vị:

| Đơn vị | Số người | Có quản lý trực tiếp |
|---|---|---|
| VAGV | 75 | 75 |
| HQ | 44 | 43 |
| VABT | 29 | 29 |
| MNVA | 21 | 21 |
| DUC_NHAN | 21 | 21 |
| SCHOOLFEAST | 18 | 18 |
| MNTS | 4 | 4 |

Lệch cần xử lý:

- Bot **không có đơn vị MGIS**. Nếu app lương có nhân viên MGIS thì họ nhận 403 khi hỏi.
- Hai người bên bot chưa nhập được vì thiếu email: Võ Tá Vinh (NV-0212) và Nhan Thanh Mai (NV-0213). Hai người này bên bot cũng chưa có.
- 212 so với 219: chênh 7 hồ sơ. Chưa biết 7 người đó là ai.

Cách đối chiếu chính xác: sau khi có khoá, app lương gọi `GET /api/partner/payroll/directory` (phân trang) và so theo email với bảng của mình. Danh sách email có ở app lương mà không có ở bot là danh sách HR cần thêm vào bot. Nếu anh gửi tôi danh sách email của 219 hồ sơ, tôi so ngay.

## 3. Thông điệp lỗi bot trả (để app lương hiện nguyên văn)

| Mã | `detail` |
|---|---|
| 403 | "Nhân viên này không có trong danh bạ hoặc đã ngừng hoạt động." |
| 429 | "Bạn hỏi hơi nhanh, vui lòng thử lại sau ít giây." (kèm header `Retry-After: 10`) |

Hai câu này có thể hiện trực tiếp cho nhân viên. Bạn có thể thêm câu "Liên hệ HR để được thêm vào" ở phía app lương cho trường hợp 403.

## 4. Trả lời 4 câu hỏi

### Câu 1: bot có trả lời đúng "sếp trực tiếp", "phép theo thâm niên" cho mọi đơn vị không?

Về dữ liệu thì có, với người đã có trong danh bạ: 211/212 người có quản lý trực tiếp (chỉ thiếu 1 người ở HQ), 212/212 có ngày vào làm. Hai lưu ý về phép:

- Cột "hình thức" (cơ hữu, thỉnh giảng, hợp đồng) **đang trống với cả 212 người**. Bot coi người chưa có hình thức là cơ hữu nên tính phép theo thâm niên cho tất cả, kể cả người thỉnh giảng hay hợp đồng thời vụ. Nên bổ sung cột này (HR nhập, hoặc app lương gửi ngược qua `POST /employees`, trường `employment_type`).
- Bot chỉ biết phép **theo thâm niên**, không biết ngày đã nghỉ. Nó không trả lời được "tôi còn bao nhiêu ngày phép". Nếu app lương có số ngày đã nghỉ, đó là hướng phát triển sau, chưa có API.

### Câu 2: kho tri thức có bài nhạy cảm nằm chung không?

**Có, và bot hiện chưa chặn.** Kho có 103 bài đã duyệt, trong đó **36 bài mang nhãn bí mật (`manager`)**: thang lương và bậc lương Career Pathway (bản đầy đủ), quy chế lương thưởng HR01, mô tả công việc (JD) các phòng, mẫu hợp đồng lao động, quy định học bổng nội bộ. Tôi đã thử tìm kiếm thật: hỏi "nhóm lương bậc lương Career Pathway" trả về chính các bài `manager` đó. Mã tìm kiếm của bot không lọc theo nhãn này, và cũng không lọc theo phòng ban. Nghĩa là **bất kỳ nhân viên nào hỏi bot, qua bong bóng hay qua chatbot nội bộ, đều có thể nhận nội dung các bài này**.

Điều này có từ trước khi có bong bóng, nhưng bong bóng làm bot đến tay nhiều người hơn. Bot chưa sửa. Hai hướng, cần chủ dự án (và nhân sự) quyết trước khi mở bong bóng cho toàn công ty:

1. **Bot lọc bài `manager`** khỏi câu trả lời cho nhân viên thường, chỉ cho leader và admin xem. Cần xác định ai là "leader" cho bong bóng (bot đã có bảng leader và danh sách admin).
2. **Giữ nguyên** nếu công ty coi thang lương và quy chế lương thưởng là thông tin ai cũng được biết.

Việc đẩy bài hướng dẫn dùng app lương (`PUT /knowledge`) không bị ảnh hưởng: các bài đó mang nhãn công khai. Chỉ cần đảm bảo không có số liệu từng người.

### Câu 3: giới hạn 20 câu mỗi phút

Bot chạy **4 tiến trình**. Bộ đếm tính riêng từng tiến trình nên mức tối đa thực tế là 20 × 4 = 80 câu mỗi phút mỗi người. Nút thắt thật nằm ở mô hình AI: toàn app chỉ chịu khoảng 6 đến 9 lời gọi cùng lúc, nên khi nhiều người hỏi đồng thời, câu trả lời chậm hơn trước khi chạm giới hạn đếm. App lương nên đặt thêm giới hạn riêng nhẹ hơn (ví dụ 10 câu mỗi phút) để người dùng không đẩy bot vào hàng chờ.

### Câu 4: bot có ghi nội dung câu hỏi không?

- **Có ghi nội dung.** Mỗi lượt hỏi lưu câu hỏi và câu trả lời vào bảng nhật ký chat để thống kê "câu hỏi hay gặp" và "câu chưa trả lời được". Không có chính sách tự xoá: nhật ký cũ nhất hiện là 18/09/2026.
- **Hiện chưa gắn với email người hỏi.** Cột email chưa được tạo ở cơ sở dữ liệu thật (thiếu một bản nâng cấp đã viết sẵn), nên nhật ký chỉ có nội dung, không biết ai hỏi. Nếu chạy bản nâng cấp đó thì từ lúc đó nhật ký sẽ ghi kèm email.
- Có thêm bảng sử dụng chỉ ghi email, loại tính năng và thời điểm, **không** ghi nội dung câu hỏi.
- Người xem được nhật ký: quản trị bot (có `ADMIN_TOKEN`). Nhân viên thường không xem được.

Khi nhắc nhân viên, nên nói đúng: "Câu hỏi và câu trả lời được lưu để cải thiện trợ lý, quản trị bot xem được. Đừng nhập số tài khoản hay lương của người khác." Giữ nguyên lời nhắc đó của app lương là phù hợp.

## 5. Điều cần quyết trước khi mở cho toàn công ty

1. Nhãn `manager` (câu 2): lọc hay giữ nguyên.
2. MGIS và 7 hồ sơ lệch: HR bổ sung vào bot.
3. Cột hình thức nhân sự (cơ hữu, thỉnh giảng, hợp đồng): bổ sung để phép tính đúng.
4. Có chạy bản nâng cấp ghi email vào nhật ký chat hay không (câu 4).
