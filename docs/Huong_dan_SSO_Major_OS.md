# Đăng nhập 1 lần từ Major OS sang Trợ lý nội bộ

Mục tiêu: nhân viên đã đăng nhập Major OS (Google @truongvietanh.com) bấm nút "Trợ lý nội bộ" thì vào thẳng `https://staffbot.vietanh.org`, không phải đăng nhập lần hai.

## Phía Major OS cần làm

1. **Lưu khoá bí mật dùng chung** `OS_SSO_SECRET` (chuỗi ngẫu nhiên ≥ 32 ký tự, quản trị chatbot gửi riêng) vào biến môi trường **backend** của Major OS. Không bao giờ đưa khoá này xuống trình duyệt.
2. **Thêm một endpoint ở backend Major OS**, ví dụ `GET /go/staffbot`, chỉ cho người đã đăng nhập. Endpoint này:
   - Lấy email công ty của người dùng đang đăng nhập.
   - Ký một JWT **HS256** bằng `OS_SSO_SECRET` với các trường bên dưới.
   - Trả về redirect (302) tới `https://staffbot.vietanh.org/sso/os?token=<jwt>`.
3. **Nút "Trợ lý nội bộ"** trên giao diện Major OS trỏ tới endpoint ở bước 2 (không tự ký token ở frontend).

### Nội dung JWT

| Trường | Giá trị |
|---|---|
| `iss` | `"major-os"` (cố định) |
| `email` | email công ty của người dùng, ví dụ `ten@truongvietanh.com` |
| `iat` | thời điểm ký (giây Unix) |
| `exp` | `iat + 60` (tối đa 300 giây) |
| `jti` | chuỗi ngẫu nhiên ≥ 16 ký tự, mỗi lần bấm một chuỗi mới (ví dụ UUID) |

Header: `{"alg": "HS256", "typ": "JWT"}`. Mỗi token chỉ dùng được **một lần** và hết hạn sau tối đa 5 phút, nên luôn ký token mới ngay lúc người dùng bấm.

### Ví dụ (Node.js, thư viện `jsonwebtoken`)

```js
const jwt = require('jsonwebtoken')
const crypto = require('crypto')

app.get('/go/staffbot', requireLogin, (req, res) => {
  const token = jwt.sign(
    { iss: 'major-os', email: req.user.email, jti: crypto.randomUUID() },
    process.env.OS_SSO_SECRET,
    { algorithm: 'HS256', expiresIn: 60 },   // tự thêm iat và exp
  )
  res.redirect(`https://staffbot.vietanh.org/sso/os?token=${encodeURIComponent(token)}`)
})
```

### Ví dụ (Python, thư viện `PyJWT`)

```python
import os, time, uuid, jwt

def go_staffbot(user_email: str) -> str:
    now = int(time.time())
    token = jwt.encode(
        {"iss": "major-os", "email": user_email, "iat": now, "exp": now + 60, "jti": uuid.uuid4().hex},
        os.environ["OS_SSO_SECRET"], algorithm="HS256",
    )
    return f"https://staffbot.vietanh.org/sso/os?token={token}"   # trả redirect 302 tới URL này
```

## Phía chatbot

- Kiểm tra chữ ký, `iss`, hạn dùng, email thuộc domain công ty, `jti` chưa dùng.
- Tạo phiên đăng nhập Supabase cho đúng email đó (tự tạo tài khoản lần đầu, không gửi email).
- Trả phiên về trình duyệt qua phần `#...` của URL (không lên server, không vào log) rồi mở giao diện trò chuyện.
- Lỗi (token sai, hết hạn, dùng lại, sai domain) → về màn đăng nhập kèm thông báo; người dùng vẫn đăng nhập Google như bình thường.

## Cấu hình

- Chatbot: đặt `OS_SSO_SECRET` trong biến môi trường Coolify của app chatbot rồi redeploy.
- Major OS: đặt cùng giá trị `OS_SSO_SECRET` trong backend.
- Tạo khoá (chạy một lần, gửi riêng cho đội Major OS qua kênh an toàn):
  `python -c "import secrets; print(secrets.token_urlsafe(48))"`
