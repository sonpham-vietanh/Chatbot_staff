"""Tạo docs/Chatbot_Staff_HR_1trang.docx — bản tóm tắt 1 trang cho HR."""
from pathlib import Path

from docx import Document
from docx.shared import Cm, Pt

OUT = Path(__file__).resolve().parent.parent / "docs" / "Chatbot_Staff_HR_1trang.docx"

doc = Document()
section = doc.sections[0]
section.top_margin = section.bottom_margin = Cm(1.5)
section.left_margin = section.right_margin = Cm(1.8)
normal = doc.styles["Normal"]
normal.font.name = "Calibri"
normal.font.size = Pt(10)
normal.paragraph_format.space_after = Pt(2)


def heading(text):
    p = doc.add_paragraph()
    run = p.add_run(text)
    run.bold = True
    run.font.size = Pt(11.5)
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after = Pt(2)


def bullets(items):
    for item in items:
        p = doc.add_paragraph(item, style="List Bullet")
        p.paragraph_format.space_after = Pt(0)


title = doc.add_paragraph()
r = title.add_run("TRỢ LÝ VIỆT ANH STAFF ASSISTANT")
r.bold = True
r.font.size = Pt(16)
sub = doc.add_paragraph("Tóm tắt mục đích và hướng dẫn sử dụng — dành cho Phòng Nhân sự")
sub.runs[0].italic = True

heading("1. Chatbot là gì & để làm gì")
doc.add_paragraph(
    "Trợ lý hỏi–đáp nội bộ: nhân viên gõ câu hỏi bằng tiếng Việt, trợ lý tìm trong kho tài liệu nội bộ đã được "
    "duyệt (quy chế, quy trình, chính sách…) và trả lời kèm nguồn trích dẫn. Không có trong tài liệu thì không trả lời bừa."
)
bullets([
    "Tra cứu nhanh 24/7, giảm câu hỏi lặp lại cho HR và các phòng ban.",
    "Một nguồn thông tin thống nhất, đúng tài liệu mới nhất đã duyệt; hỗ trợ nhân viên mới hội nhập.",
    "Nút “Báo sai” và thống kê giúp phát hiện nội dung cần bổ sung hoặc sửa.",
])

heading("2. Hướng dẫn cho nhân viên")
bullets([
    "Đăng nhập bằng email công ty (…@truongvietanh.com) hoặc bấm từ Major OS để vào thẳng.",
    "Gõ câu hỏi cụ thể, ví dụ “Quy trình xin nghỉ phép năm gồm những bước nào?”, rồi xem nguồn trích dẫn bên dưới.",
    "Hỏi tiếp trong cùng cuộc trò chuyện; lịch sử lưu ở danh sách bên cạnh (chỉ mình bạn xem được).",
    "Câu trả lời sai/thiếu: bấm “Báo sai” và chọn lý do để người phụ trách cập nhật.",
])

heading("3. Hướng dẫn cho leader / HR phụ trách tri thức")
bullets([
    "Vào trang quản lý tri thức (/quan-ly) bằng tài khoản được cấp quyền, chỉ sửa được tài liệu của phòng mình.",
    "Tạo hoặc sửa tài liệu (tiêu đề, phòng ban, nội dung, mức truy cập); lưu/duyệt xong chatbot dùng ngay.",
    "Viết rõ ràng, chia tiêu đề mục để câu trả lời chính xác hơn; xem các báo cáo “Báo sai” để cập nhật.",
])

heading("4. Bảo mật")
bullets([
    "Chỉ email công ty đã xác minh được dùng; phòng ban lấy từ danh bạ nhân sự do HR cấp, không do người dùng tự khai.",
    "Quyền chỉnh sửa theo phòng ban; mỗi người chỉ xem lịch sử của chính mình.",
])

heading("5. Đề xuất cho HR")
bullets([
    "Cập nhật danh bạ nhân viên (email, phòng ban) và chỉ định leader tri thức cho từng phòng ban.",
    "Ưu tiên đưa vào: nội quy, nghỉ phép, lương thưởng, BHXH, onboarding; thông báo cách dùng cho toàn trường.",
    "Hằng tháng xem báo cáo sử dụng và các câu hỏi chưa trả lời được để bổ sung nội dung.",
])

heading("6. Lưu ý")
doc.add_paragraph(
    "Chatbot chỉ hỗ trợ tra cứu, không thay quyết định chính thức của HR/ban lãnh đạo; chất lượng phụ thuộc tài liệu nguồn. "
    "Vấn đề nhạy cảm hoặc cá nhân (khiếu nại, kỷ luật, lương cá nhân) cần liên hệ trực tiếp HR."
)
doc.add_paragraph("Đường dẫn: [điền]   |   Hỗ trợ kỹ thuật: [điền]   |   Đầu mối HR: [điền]")

OUT.parent.mkdir(exist_ok=True)
doc.save(OUT)
print("Created:", OUT)
