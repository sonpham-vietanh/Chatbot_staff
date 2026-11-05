"""Tạo docs/Bao_cao_Chatbot_Staff_cho_HR.docx — báo cáo, mục đích và hướng dẫn sử dụng cho HR."""
from pathlib import Path

from docx import Document
from docx.shared import Pt

OUT = Path(__file__).resolve().parent.parent / "docs" / "Bao_cao_Chatbot_Staff_cho_HR.docx"

doc = Document()
doc.styles["Normal"].font.name = "Calibri"
doc.styles["Normal"].font.size = Pt(11)


def bullets(items):
    for item in items:
        doc.add_paragraph(item, style="List Bullet")


def steps(items):
    for item in items:
        doc.add_paragraph(item, style="List Number")


def table(header, rows):
    t = doc.add_table(rows=1, cols=len(header))
    t.style = "Light Grid Accent 1"
    for i, h in enumerate(header):
        t.rows[0].cells[i].text = h
    for row in rows:
        cells = t.add_row().cells
        for i, v in enumerate(row):
            cells[i].text = v
    doc.add_paragraph()


doc.add_heading("Trợ lý Việt Anh Staff Assistant", 0)
doc.add_paragraph("Báo cáo giới thiệu, mục đích và hướng dẫn sử dụng — dành cho Phòng Nhân sự (HR)")
doc.add_paragraph("Trường Việt Anh / Major Education  •  Bản nháp để xem thử")

doc.add_heading("1. Chatbot này là gì?", 1)
doc.add_paragraph(
    "Staff Assistant là trợ lý hỏi–đáp nội bộ dành cho cán bộ, nhân viên. Thay vì phải lục tìm quy chế, "
    "quy trình, biểu mẫu hay hỏi đi hỏi lại các phòng ban, nhân viên chỉ cần gõ câu hỏi bằng tiếng Việt. "
    "Trợ lý tìm trong kho tri thức nội bộ đã được duyệt và trả lời kèm nguồn trích dẫn."
)
doc.add_paragraph(
    "Điểm quan trọng: trợ lý chỉ trả lời dựa trên tài liệu nội bộ đã được phòng ban phụ trách đưa vào "
    "và duyệt. Câu hỏi nằm ngoài kho tri thức thì trợ lý sẽ không trả lời bừa."
)

doc.add_heading("2. Mục đích và lợi ích", 1)
table(
    ["Mục tiêu", "Lợi ích cho tổ chức"],
    [
        ["Tra cứu nhanh quy chế, quy trình, chính sách", "Nhân viên có câu trả lời trong vài giây, 24/7, không phụ thuộc giờ hành chính"],
        ["Giảm câu hỏi lặp lại cho HR và các phòng ban", "HR và quản lý có thêm thời gian cho việc chuyên môn"],
        ["Thống nhất một nguồn thông tin", "Mọi người nhận cùng một câu trả lời, theo đúng tài liệu mới nhất đã duyệt"],
        ["Hỗ trợ nhân viên mới hội nhập", "Người mới tự tra cứu được quy định, quy trình mà không ngại hỏi"],
        ["Phát hiện khoảng trống tri thức", "Thống kê câu hỏi và nút “Báo sai” cho thấy nội dung nào cần bổ sung hoặc sửa"],
    ],
)

doc.add_heading("3. Chatbot hoạt động như thế nào?", 1)
steps([
    "Các phòng ban (người phụ trách/leader) soạn hoặc tải tài liệu lên trang quản lý tri thức.",
    "Tài liệu được duyệt rồi mới đưa vào kho tri thức của chatbot.",
    "Nhân viên đăng nhập và đặt câu hỏi.",
    "Hệ thống tìm các đoạn tài liệu liên quan nhất, rồi trợ lý AI soạn câu trả lời từ đúng các đoạn đó.",
    "Câu trả lời hiển thị kèm nguồn trích dẫn để nhân viên đối chiếu.",
])

doc.add_heading("4. Bảo mật và phân quyền", 1)
bullets([
    "Chỉ email thuộc domain công ty và đã xác minh mới dùng được chatbot.",
    "Có thể đăng nhập một lần từ Major OS (SSO), không cần nhập lại mật khẩu.",
    "Mỗi người chỉ xem được lịch sử trò chuyện của chính mình.",
    "Quyền chỉnh sửa tri thức được cấp theo phòng ban; mỗi leader chỉ sửa nội dung thuộc phòng mình.",
    "Phòng ban của nhân viên được lấy từ danh bạ nhân sự do HR cấp, không do người dùng tự khai.",
    "Trang quản trị và báo cáo được bảo vệ bằng khoá riêng.",
])

doc.add_heading("5. Hướng dẫn sử dụng cho nhân viên", 1)
doc.add_heading("5.1 Đăng nhập", 2)
steps([
    "Mở đường dẫn chatbot do công ty cung cấp (hoặc bấm vào chatbot từ Major OS để vào thẳng).",
    "Đăng nhập bằng email công ty (ví dụ …@truongvietanh.com). Lần đầu có thể cần đăng ký và xác minh email.",
    "Email ngoài domain công ty sẽ bị từ chối.",
])
doc.add_heading("5.2 Đặt câu hỏi", 2)
steps([
    "Gõ câu hỏi vào ô chat bằng tiếng Việt tự nhiên, rồi gửi.",
    "Chờ trợ lý trả lời và xem phần nguồn trích dẫn bên dưới.",
    "Có thể hỏi tiếp trong cùng cuộc trò chuyện; trợ lý hiểu ngữ cảnh các câu trước.",
    "Mỗi cuộc trò chuyện được lưu trong danh sách bên cạnh để xem lại hoặc xoá.",
])
doc.add_heading("5.3 Mẹo để được câu trả lời tốt", 2)
bullets([
    "Hỏi cụ thể: “Quy trình xin nghỉ phép năm gồm những bước nào?” tốt hơn “nghỉ phép”.",
    "Nêu rõ đối tượng hoặc bối cảnh nếu cần (khối, phòng ban, vị trí).",
    "Nếu trợ lý nói chưa tìm thấy thông tin, hãy liên hệ phòng ban phụ trách.",
])
doc.add_heading("5.4 Khi câu trả lời sai hoặc thiếu", 2)
doc.add_paragraph(
    "Bấm nút “Báo sai” dưới câu trả lời và chọn lý do. Báo cáo được lưu kèm câu hỏi, câu trả lời và nguồn, "
    "để admin kiểm tra và người phụ trách cập nhật tài liệu."
)

doc.add_heading("6. Hướng dẫn cho người phụ trách tri thức (leader/HR)", 1)
steps([
    "Vào trang quản lý tri thức (đường dẫn /quan-ly) bằng tài khoản công ty đã được cấp quyền leader.",
    "Xem danh sách tài liệu thuộc phòng ban mình, tìm theo tiêu đề.",
    "Tạo tài liệu mới: nhập tiêu đề, chọn phòng ban, nội dung, mức truy cập và trạng thái.",
    "Sửa tài liệu khi quy định thay đổi; nội dung mới có hiệu lực với chatbot ngay khi lưu/duyệt.",
    "Theo dõi các báo cáo “Báo sai” để cập nhật nội dung kịp thời.",
])
doc.add_paragraph(
    "Lưu ý: nội dung càng rõ ràng, có tiêu đề và chia mục thì câu trả lời của chatbot càng chính xác."
)

doc.add_heading("7. Đề xuất cho HR", 1)
bullets([
    "Cung cấp và cập nhật danh bạ nhân viên (email, phòng ban) để phân quyền đúng.",
    "Chỉ định leader phụ trách tri thức cho từng phòng ban.",
    "Đưa vào kho tri thức các tài liệu thường được hỏi nhất: nội quy, nghỉ phép, lương thưởng, BHXH, quy trình onboarding.",
    "Thông báo cho toàn trường cách dùng và nút “Báo sai”.",
    "Định kỳ (hàng tháng) xem báo cáo sử dụng và các câu hỏi chưa trả lời được để bổ sung nội dung.",
])

doc.add_heading("8. Giới hạn cần lưu ý", 1)
bullets([
    "Chatbot là công cụ hỗ trợ tra cứu, không thay thế quyết định chính thức của HR hay ban lãnh đạo.",
    "Chất lượng câu trả lời phụ thuộc vào chất lượng và độ cập nhật của tài liệu nguồn.",
    "Với các trường hợp nhạy cảm hoặc cá nhân (khiếu nại, kỷ luật, lương cá nhân), nhân viên nên liên hệ trực tiếp HR.",
    "Không nhập thông tin cá nhân nhạy cảm không cần thiết vào khung chat.",
])

doc.add_heading("9. Thông tin liên hệ", 1)
doc.add_paragraph("Đường dẫn chatbot: [điền đường dẫn]\nĐầu mối hỗ trợ kỹ thuật: [điền tên / email]\nĐầu mối nội dung (HR): [điền tên / email]")

OUT.parent.mkdir(exist_ok=True)
doc.save(OUT)
print("Created:", OUT)
