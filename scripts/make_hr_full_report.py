"""Tạo docs/Bao_cao_Chatbot_Staff_HR_day_du.docx — báo cáo đầy đủ + hướng dẫn sử dụng cho HR."""
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

OUT = Path(__file__).resolve().parent.parent / "docs" / "Bao_cao_Chatbot_Staff_HR_day_du.docx"
NAVY = RGBColor(0x1B, 0x2A, 0x5C)

doc = Document()
sec = doc.sections[0]
sec.left_margin = sec.right_margin = Cm(2.2)
sec.top_margin = sec.bottom_margin = Cm(2)
doc.styles["Normal"].font.name = "Calibri"
doc.styles["Normal"].font.size = Pt(11)
for name, size in (("Heading 1", 16), ("Heading 2", 13)):
    st = doc.styles[name]
    st.font.name = "Calibri"
    st.font.size = Pt(size)
    st.font.color.rgb = NAVY


def para(text, bold=False, italic=False, size=None, align=None):
    p = doc.add_paragraph()
    r = p.add_run(text)
    r.bold, r.italic = bold, italic
    if size:
        r.font.size = Pt(size)
    if align:
        p.alignment = align
    return p


def bullets(items):
    for item in items:
        doc.add_paragraph(item, style="List Bullet")


def steps(items):
    for item in items:
        doc.add_paragraph(item, style="List Number")


def shade(cell, color):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), color)
    tcPr.append(shd)


def table(header, rows, widths=None):
    t = doc.add_table(rows=1, cols=len(header))
    t.style = "Table Grid"
    for i, h in enumerate(header):
        c = t.rows[0].cells[i]
        c.text = ""
        run = c.paragraphs[0].add_run(h)
        run.bold = True
        run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        shade(c, "1B2A5C")
    for row in rows:
        cells = t.add_row().cells
        for i, v in enumerate(row):
            cells[i].text = v
    if widths:
        for row in t.rows:
            for i, w in enumerate(widths):
                row.cells[i].width = Cm(w)
    doc.add_paragraph()


def note(text):
    t = doc.add_table(rows=1, cols=1)
    t.style = "Table Grid"
    c = t.rows[0].cells[0]
    c.text = text
    shade(c, "FFF4D6")
    doc.add_paragraph()


# ---------- Trang bìa ----------
for _ in range(5):
    doc.add_paragraph()
para("TRƯỜNG VIỆT ANH / MAJOR EDUCATION", bold=True, size=13, align=WD_ALIGN_PARAGRAPH.CENTER)
para("TRỢ LÝ NỘI BỘ", bold=True, size=30, align=WD_ALIGN_PARAGRAPH.CENTER).runs[0].font.color.rgb = NAVY
para("Viet Anh Staff Assistant", bold=True, size=20, align=WD_ALIGN_PARAGRAPH.CENTER)
doc.add_paragraph()
para("BÁO CÁO GIỚI THIỆU, MỤC ĐÍCH VÀ HƯỚNG DẪN SỬ DỤNG", bold=True, size=14, align=WD_ALIGN_PARAGRAPH.CENTER)
para("Dành cho Phòng Nhân sự (HR)", italic=True, size=12, align=WD_ALIGN_PARAGRAPH.CENTER)
for _ in range(6):
    doc.add_paragraph()
para("Bản nháp để xem thử  •  Phiên bản 0.1", italic=True, align=WD_ALIGN_PARAGRAPH.CENTER)
doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

# ---------- Mục lục ----------
doc.add_heading("Nội dung", 1)
for line in [
    "1. Tóm tắt dành cho lãnh đạo HR",
    "2. Bối cảnh và mục đích",
    "3. Chatbot làm được gì — và không làm gì",
    "4. Cách hoạt động (giải thích không cần kỹ thuật)",
    "5. Hướng dẫn sử dụng cho nhân viên",
    "6. Hướng dẫn cho người phụ trách tri thức (leader / HR)",
    "7. Phân quyền và bảo mật",
    "8. Vai trò và trách nhiệm đề xuất",
    "9. Kế hoạch triển khai đề xuất",
    "10. Chỉ số theo dõi hiệu quả",
    "11. Rủi ro và cách giảm thiểu",
    "12. Câu hỏi thường gặp (FAQ)",
    "13. Thuật ngữ",
    "Phụ lục: Danh mục tài liệu nên nạp đầu tiên",
]:
    doc.add_paragraph(line)
doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

# ---------- 1 ----------
doc.add_heading("1. Tóm tắt dành cho lãnh đạo HR", 1)
para(
    "Staff Assistant là trợ lý hỏi–đáp bằng tiếng Việt cho cán bộ, giáo viên, nhân viên. Người dùng đặt câu hỏi, "
    "trợ lý tìm trong kho tri thức nội bộ đã được duyệt rồi trả lời kèm nguồn trích dẫn."
)
table(
    ["Nội dung", "Tóm tắt"],
    [
        ["Mục đích", "Tra cứu nhanh quy chế, quy trình, chính sách; giảm câu hỏi lặp lại cho HR và các phòng ban"],
        ["Người dùng", "Cán bộ, giáo viên, nhân viên có email công ty (@truongvietanh.com)"],
        ["Nguồn trả lời", "Chỉ tài liệu nội bộ đã được phòng ban phụ trách đưa vào và duyệt"],
        ["Đảm bảo độ tin cậy", "Mỗi câu trả lời có trích dẫn nguồn; câu hỏi ngoài kho tri thức bị từ chối an toàn thay vì trả lời bừa"],
        ["Cải tiến liên tục", "Nút “Báo sai” và danh sách “[CẦN BỔ SUNG]” cho thấy tri thức nào còn thiếu hoặc sai"],
        ["Cần HR làm gì", "Cung cấp danh bạ nhân sự, cử leader tri thức, đưa tài liệu thường được hỏi vào kho, truyền thông cách dùng"],
    ],
    widths=[4, 12.5],
)

# ---------- 2 ----------
doc.add_heading("2. Bối cảnh và mục đích", 1)
doc.add_heading("2.1 Vấn đề hiện tại", 2)
bullets([
    "Nhân viên mất thời gian tìm quy chế, biểu mẫu, quy trình nằm rải rác ở nhiều nơi.",
    "HR và các phòng ban liên tục trả lời những câu hỏi giống nhau (nghỉ phép, chế độ, thủ tục…).",
    "Cùng một câu hỏi có thể nhận các câu trả lời khác nhau tuỳ người trả lời hoặc phiên bản tài liệu.",
    "Nhân viên mới ngại hỏi, hội nhập chậm.",
])
doc.add_heading("2.2 Mục đích của chatbot", 2)
table(
    ["Mục tiêu", "Lợi ích mang lại"],
    [
        ["Tra cứu nhanh, mọi lúc", "Có câu trả lời trong vài giây, kể cả ngoài giờ hành chính"],
        ["Giảm tải cho HR/phòng ban", "Dành thời gian cho công việc chuyên môn thay vì trả lời lặp lại"],
        ["Một nguồn sự thật duy nhất", "Mọi người nhận cùng một câu trả lời theo tài liệu mới nhất đã duyệt"],
        ["Hỗ trợ hội nhập", "Nhân viên mới tự tra cứu nội quy, quy trình mà không ngại hỏi"],
        ["Phát hiện khoảng trống tri thức", "Biết câu hỏi nào chưa có tài liệu hoặc câu trả lời nào chưa đúng để bổ sung"],
    ],
    widths=[5, 11.5],
)

# ---------- 3 ----------
doc.add_heading("3. Chatbot làm được gì — và không làm gì", 1)
doc.add_heading("3.1 Làm được", 2)
bullets([
    "Trả lời câu hỏi về quy định, quy trình, chính sách có trong kho tri thức, bằng tiếng Việt tự nhiên.",
    "Hiển thị nguồn trích dẫn để người dùng đối chiếu với tài liệu gốc.",
    "Hiểu ngữ cảnh khi hỏi tiếp trong cùng cuộc trò chuyện; lưu lịch sử riêng cho từng người.",
    "Ưu tiên tri thức của đúng phòng ban của người hỏi (phòng ban lấy từ danh bạ do HR cấp).",
    "Cho phép nhân viên báo câu trả lời sai kèm ghi chú.",
    "Đăng nhập một lần từ Major OS, hoặc đăng nhập bằng tài khoản Google/email công ty.",
])
doc.add_heading("3.2 Không làm", 2)
bullets([
    "Không thay thế quyết định chính thức của HR hoặc ban lãnh đạo.",
    "Không tự bịa câu trả lời khi tài liệu không có — sẽ báo chưa tìm thấy thông tin.",
    "Không xử lý hồ sơ cá nhân: khiếu nại, kỷ luật, lương cá nhân… cần liên hệ trực tiếp HR.",
    "Không cho nhân viên tự tải tài liệu lên khung chat (chỉ leader/admin mới nạp tri thức, vì lý do bảo mật).",
])

# ---------- 4 ----------
doc.add_heading("4. Cách hoạt động", 1)
steps([
    "NẠP TRI THỨC: leader các phòng ban soạn hoặc tải tài liệu lên trang quản lý tri thức (hoặc thả file vào thư mục chung của phòng, hệ thống tự đọc).",
    "DUYỆT: chỉ nội dung ở trạng thái “đã duyệt” mới được chatbot sử dụng.",
    "ĐẶT CÂU HỎI: nhân viên đăng nhập và gõ câu hỏi.",
    "TÌM KIẾM: hệ thống tìm các đoạn tài liệu liên quan nhất, ưu tiên đúng phòng ban của người hỏi.",
    "SOẠN TRẢ LỜI: trợ lý AI chỉ dựa trên các đoạn tìm được để viết câu trả lời, kèm nguồn.",
    "PHẢN HỒI: nếu không đủ thông tin, trợ lý từ chối an toàn và ghi nhận câu hỏi thành mục “[CẦN BỔ SUNG]” để HR/leader biết cần bổ sung.",
])
note(
    "Điểm mấu chốt: AI chỉ “diễn đạt lại” những gì có trong tài liệu đã duyệt. Chất lượng câu trả lời vì thế phụ thuộc "
    "trực tiếp vào chất lượng và độ cập nhật của tài liệu."
)

# ---------- 5 ----------
doc.add_heading("5. Hướng dẫn sử dụng cho nhân viên", 1)
doc.add_heading("5.1 Đăng nhập", 2)
steps([
    "Mở đường dẫn chatbot do công ty cung cấp, hoặc bấm vào chatbot từ Major OS để vào thẳng không cần đăng nhập lại.",
    "Trên màn hình đăng nhập, chọn đăng nhập bằng tài khoản Google @truongvietanh.com, hoặc nhập email, mật khẩu (tối thiểu 6 ký tự).",
    "Lần đầu dùng email/mật khẩu: nhập họ tên, email công ty, mật khẩu để đăng ký, sau đó xác minh email.",
    "Email ngoài domain công ty hoặc chưa xác minh sẽ không vào được.",
])
doc.add_heading("5.2 Đặt câu hỏi", 2)
steps([
    "Gõ câu hỏi vào ô chat (gợi ý hiển thị: “Hỏi về quy định nội bộ…”) rồi gửi.",
    "Khi thấy “Đang tra cứu tài liệu nội bộ…”, chờ vài giây.",
    "Đọc câu trả lời và xem phần nguồn trích dẫn để đối chiếu.",
    "Hỏi tiếp trong cùng cuộc trò chuyện nếu cần làm rõ thêm.",
])
doc.add_heading("5.3 Quản lý lịch sử và tài khoản", 2)
bullets([
    "Mục “Lịch sử” ở thanh bên trái liệt kê các cuộc trò chuyện của riêng bạn; bấm để xem lại hoặc xoá.",
    "“Đổi tài khoản” để chuyển sang tài khoản Google khác; “Đăng xuất” để thoát.",
    "Chỉ những người được cấp quyền mới thấy nút “Quản lý tri thức”.",
])
doc.add_heading("5.4 Ví dụ câu hỏi tốt", 2)
table(
    ["Nên hỏi", "Ít hiệu quả"],
    [
        ["Quy trình xin nghỉ phép năm gồm những bước nào?", "nghỉ phép"],
        ["Nhân viên mới thử việc được hưởng chế độ gì?", "chế độ"],
        ["Hồ sơ đăng ký BHXH cần những giấy tờ nào?", "bảo hiểm"],
        ["Quy định về giờ làm việc của giáo viên khối tiểu học?", "giờ làm"],
    ],
    widths=[10, 6.5],
)
bullets([
    "Hỏi một ý một lần, nêu rõ bối cảnh (khối, phòng ban, vị trí) nếu cần.",
    "Nếu trợ lý báo chưa tìm thấy thông tin, hãy hỏi lại bằng từ khoá khác hoặc liên hệ phòng ban phụ trách.",
])
doc.add_heading("5.5 Khi câu trả lời sai hoặc thiếu", 2)
steps([
    "Bấm nút báo sai dưới câu trả lời (“Câu trả lời này sai ở đâu?”).",
    "Chọn lý do và có thể ghi chú thêm, ví dụ “thông tin đã cũ”.",
    "Gửi. Báo cáo được lưu kèm câu hỏi, câu trả lời và nguồn để admin kiểm tra và leader cập nhật.",
])
doc.add_heading("5.6 Gặp sự cố", 2)
table(
    ["Hiện tượng", "Cách xử lý"],
    [
        ["Không đăng nhập được", "Kiểm tra dùng đúng email @truongvietanh.com và đã xác minh email; thử “Đổi tài khoản”"],
        ["Báo lỗi “Phiên đăng nhập đã hết hạn”", "Đăng nhập lại"],
        ["Báo quá tải / thử lại sau", "Chờ vài giây rồi gửi lại; nếu kéo dài, báo đầu mối kỹ thuật"],
        ["Không thấy nút Quản lý tri thức", "Bạn chưa được cấp quyền leader — liên hệ HR/admin"],
    ],
    widths=[6, 10.5],
)

# ---------- 6 ----------
doc.add_heading("6. Hướng dẫn cho người phụ trách tri thức (leader / HR)", 1)
doc.add_heading("6.1 Truy cập", 2)
para(
    "Đăng nhập chatbot bằng tài khoản được cấp quyền leader, bấm “Quản lý tri thức” ở thanh bên (đường dẫn /quan-ly). "
    "Mỗi leader chỉ xem và sửa được tài liệu của phòng ban mình."
)
doc.add_heading("6.2 Thao tác chính", 2)
steps([
    "Tìm tài liệu: gõ tiêu đề hoặc từ khoá vào ô tìm kiếm, chọn tài liệu trong danh sách để xem hoặc sửa.",
    "Tạo tài liệu mới: nhập tiêu đề, phòng ban, nội dung, mức truy cập và trạng thái.",
    "Soạn nội dung bằng định dạng markdown; dùng [[tên-trang]] để liên kết tới tài liệu khác.",
    "Lưu: khi thấy nhãn “Chưa lưu” thì bấm lưu; nội dung được cập nhật cho chatbot ngay.",
    "Xem “Liên kết đi” và “Được liên kết từ” để kiểm tra mạng lưới tài liệu, phát hiện trang còn thiếu (hiển thị màu cảnh báo).",
    "Xem lịch sử các lần sửa để biết ai sửa gì, khi nào.",
])
doc.add_heading("6.3 Cách viết tài liệu để chatbot trả lời tốt", 2)
bullets([
    "Mỗi tài liệu một chủ đề; dùng tiêu đề mục rõ ràng (ví dụ “Điều kiện”, “Hồ sơ cần chuẩn bị”, “Thời hạn”).",
    "Viết câu đầy đủ, nêu rõ đối tượng áp dụng và thời điểm hiệu lực.",
    "Ghi số liệu, mốc thời gian cụ thể thay vì “theo quy định”.",
    "Gỡ hoặc cập nhật tài liệu khi quy định thay đổi để tránh trả lời theo bản cũ.",
])
doc.add_heading("6.4 Việc nên làm định kỳ", 2)
bullets([
    "Hằng tuần: xử lý các báo cáo “Báo sai” và các mục “[CẦN BỔ SUNG]”.",
    "Hằng tháng: rà soát tài liệu hết hiệu lực; xem báo cáo sử dụng (câu hỏi và nguồn được trích nhiều nhất).",
    "Khi ban hành quy định mới: cập nhật tài liệu cùng ngày ban hành.",
])
doc.add_heading("6.5 Tải tài liệu có sẵn", 2)
para(
    "Quản trị viên có thể tải trực tiếp các file .md, .txt, .csv, .json, .pdf, .docx (tối đa 10 MB mỗi file) ở trang quản trị. "
    "Hệ thống tự chia nhỏ và đưa vào kho tri thức ngay sau khi lưu."
)

# ---------- 7 ----------
doc.add_heading("7. Phân quyền và bảo mật", 1)
table(
    ["Vai trò", "Được làm", "Không được làm"],
    [
        ["Nhân viên", "Hỏi đáp, xem/xoá lịch sử của mình, báo sai", "Xem lịch sử người khác; sửa tri thức"],
        ["Leader phòng ban", "Tạo, sửa tài liệu thuộc phòng mình; xem lịch sử sửa", "Sửa tài liệu của phòng khác"],
        ["Admin", "Quản lý mọi tài liệu, API key tích hợp, báo cáo sử dụng và phản hồi", "—"],
    ],
    widths=[3.5, 7, 6],
)
bullets([
    "Chỉ email thuộc domain công ty và đã xác minh mới sử dụng được chatbot.",
    "Phòng ban của người dùng lấy từ danh bạ nhân sự do HR cấp, không dựa vào thông tin người dùng tự khai.",
    "Có thể đăng nhập một lần từ Major OS bằng mã ngắn hạn (hết hạn sau tối đa 5 phút, dùng một lần).",
    "Trang quản trị và báo cáo được bảo vệ bằng khoá riêng; khoá không bao giờ đưa xuống trình duyệt.",
    "Câu hỏi, câu trả lời và nguồn được ghi nhật ký để phục vụ kiểm tra chất lượng — cần thông báo rõ cho nhân viên.",
])

# ---------- 8 ----------
doc.add_heading("8. Vai trò và trách nhiệm đề xuất", 1)
table(
    ["Vai trò", "Trách nhiệm"],
    [
        ["HR (chủ sở hữu nghiệp vụ)", "Cung cấp và cập nhật danh bạ nhân sự; chỉ định leader; chịu trách nhiệm nội dung HR; theo dõi chỉ số"],
        ["Leader phòng ban", "Nạp, duyệt, cập nhật tri thức của phòng; xử lý báo sai thuộc phòng mình"],
        ["Quản trị kỹ thuật", "Vận hành hệ thống, cấp tài khoản/quyền, sao lưu, xử lý sự cố"],
        ["Ban lãnh đạo", "Phê duyệt phạm vi và chính sách sử dụng; hỗ trợ truyền thông"],
    ],
    widths=[5, 11.5],
)

# ---------- 9 ----------
doc.add_heading("9. Kế hoạch triển khai đề xuất", 1)
table(
    ["Giai đoạn", "Công việc", "Kết quả"],
    [
        ["1. Chuẩn bị (1–2 tuần)", "Chốt danh bạ nhân sự; cử leader từng phòng; chọn bộ tài liệu ưu tiên", "Danh sách leader, danh mục tài liệu"],
        ["2. Nạp tri thức (2 tuần)", "Leader soạn/tải tài liệu, HR duyệt; kiểm thử bằng bộ câu hỏi mẫu", "Kho tri thức đợt đầu, báo cáo kiểm thử"],
        ["3. Thí điểm (2–4 tuần)", "Mở cho một nhóm nhỏ (ví dụ khối văn phòng); thu phản hồi qua nút Báo sai", "Danh sách lỗi và nội dung cần bổ sung"],
        ["4. Triển khai toàn trường", "Truyền thông, hướng dẫn, mở cho toàn bộ nhân viên", "Chatbot hoạt động chính thức"],
        ["5. Vận hành", "Rà soát hằng tuần/tháng; cập nhật tài liệu; báo cáo cho lãnh đạo", "Chu trình cải tiến liên tục"],
    ],
    widths=[4, 8, 4.5],
)
note("Mốc thời gian chỉ là đề xuất tham khảo, cần HR và ban lãnh đạo điều chỉnh theo thực tế.")

# ---------- 10 ----------
doc.add_heading("10. Chỉ số theo dõi hiệu quả", 1)
para("Hệ thống ghi nhận sẵn lượt sử dụng, câu hỏi, nguồn được trích dẫn và báo cáo sai. HR nên theo dõi:")
table(
    ["Chỉ số", "Ý nghĩa", "Cách dùng"],
    [
        ["Số lượt hỏi / số người dùng", "Mức độ chấp nhận", "So sánh theo tuần, theo phòng ban"],
        ["Tỷ lệ câu hỏi trả lời được (có nguồn)", "Độ phủ của kho tri thức", "Thấp = cần bổ sung tài liệu"],
        ["Số báo cáo “Báo sai”", "Chất lượng nội dung", "Tăng bất thường = rà lại tài liệu"],
        ["Số mục “[CẦN BỔ SUNG]”", "Khoảng trống tri thức", "Xếp ưu tiên nội dung cần viết"],
        ["Chủ đề hỏi nhiều nhất", "Nhu cầu thực của nhân viên", "Làm nội dung truyền thông, đào tạo"],
    ],
    widths=[5.5, 5, 6],
)

# ---------- 11 ----------
doc.add_heading("11. Rủi ro và cách giảm thiểu", 1)
table(
    ["Rủi ro", "Cách giảm thiểu"],
    [
        ["Tài liệu cũ dẫn đến trả lời sai", "Quy trình rà soát định kỳ; cập nhật cùng ngày ban hành; theo dõi báo sai"],
        ["Nhân viên tin tuyệt đối vào chatbot", "Hiển thị nguồn; truyền thông rằng chatbot chỉ hỗ trợ tra cứu"],
        ["Lộ thông tin nội bộ", "Chỉ email công ty đã xác minh; phân quyền theo phòng ban; không nạp tài liệu mật vào kho chung"],
        ["Hỏi về chuyện cá nhân, nhạy cảm", "Hướng dẫn liên hệ trực tiếp HR; không nạp dữ liệu cá nhân vào kho tri thức"],
        ["Phụ thuộc dịch vụ AI bên ngoài", "Có mô hình dự phòng và thông báo lỗi rõ ràng; theo dõi chi phí sử dụng"],
        ["Leader không cập nhật tri thức", "Gắn trách nhiệm trong phân vai; HR xem báo cáo định kỳ"],
    ],
    widths=[6, 10.5],
)

# ---------- 12 ----------
doc.add_heading("12. Câu hỏi thường gặp (FAQ)", 1)
faq = [
    ("Chatbot có biết thông tin cá nhân của tôi (lương, ngày phép còn lại) không?",
     "Không. Chatbot chỉ trả lời từ tài liệu nội bộ đã duyệt, không truy cập hồ sơ cá nhân."),
    ("Ai xem được câu hỏi của tôi?",
     "Lịch sử cuộc trò chuyện chỉ hiển thị cho riêng bạn. Câu hỏi và câu trả lời được ghi nhật ký để quản trị viên kiểm tra chất lượng và thống kê."),
    ("Vì sao chatbot nói không tìm thấy thông tin?",
     "Tài liệu về vấn đề đó chưa có trong kho tri thức hoặc chưa được duyệt. Câu hỏi sẽ được ghi nhận để bổ sung."),
    ("Câu trả lời khác với những gì tôi biết, nên tin ai?",
     "Hãy đối chiếu nguồn trích dẫn và bấm “Báo sai”. Với vấn đề quan trọng, luôn xác nhận lại với HR."),
    ("Nội dung mới có hiệu lực khi nào?",
     "Ngay sau khi leader lưu/duyệt tài liệu."),
    ("Có dùng trên điện thoại được không?",
     "Có thể truy cập bằng trình duyệt trên điện thoại hoặc từ Major OS."),
    ("Có thể nhúng chatbot vào hệ thống khác không?",
     "Có, thông qua API nhúng dành cho ứng dụng nội bộ như Major OS; admin tạo khoá cho từng ứng dụng."),
]
for q, a in faq:
    p = doc.add_paragraph()
    p.add_run("Hỏi: " + q).bold = True
    doc.add_paragraph("Đáp: " + a)

# ---------- 13 ----------
doc.add_heading("13. Thuật ngữ", 1)
table(
    ["Thuật ngữ", "Giải thích"],
    [
        ["Kho tri thức", "Tập hợp các tài liệu nội bộ đã duyệt mà chatbot dùng để trả lời"],
        ["Nguồn trích dẫn", "Tài liệu và đoạn mà câu trả lời dựa vào"],
        ["Leader tri thức", "Người được cấp quyền soạn, sửa tài liệu của phòng ban"],
        ["Báo sai", "Nút để nhân viên báo câu trả lời chưa đúng"],
        ["[CẦN BỔ SUNG]", "Mục tự động tạo khi câu hỏi chưa có tài liệu phù hợp"],
        ["SSO", "Đăng nhập một lần: đã đăng nhập Major OS thì vào thẳng chatbot"],
        ["Markdown", "Cách soạn văn bản đơn giản bằng ký hiệu (tiêu đề #, danh sách -, liên kết [[…]])"],
    ],
    widths=[4, 12.5],
)

# ---------- Phụ lục ----------
doc.add_heading("Phụ lục: Danh mục tài liệu nên nạp đầu tiên", 1)
table(
    ["Nhóm", "Ví dụ tài liệu", "Phòng phụ trách"],
    [
        ["Nội quy – quy chế", "Nội quy lao động, quy tắc ứng xử, quy chế làm việc", "HR"],
        ["Chế độ – phúc lợi", "Nghỉ phép, nghỉ lễ, thai sản, BHXH/BHYT, phụ cấp, thưởng", "HR / Kế toán"],
        ["Quy trình nhân sự", "Tuyển dụng, thử việc, hợp đồng, chấm dứt hợp đồng, onboarding", "HR"],
        ["Hành chính", "Quy trình xin cấp văn phòng phẩm, đặt phòng, công tác phí", "Hành chính"],
        ["Chuyên môn", "Quy định chuyên môn, lịch năm học, quy trình đánh giá", "Các khối/phòng chuyên môn"],
        ["CNTT", "Hướng dẫn tài khoản, email, Major OS, xử lý sự cố thường gặp", "CNTT"],
    ],
    widths=[3.5, 9, 4],
)
para("Thông tin liên hệ", bold=True)
doc.add_paragraph("Đường dẫn chatbot: [điền]")
doc.add_paragraph("Đầu mối hỗ trợ kỹ thuật: [điền tên / email]")
doc.add_paragraph("Đầu mối nội dung (HR): [điền tên / email]")

OUT.parent.mkdir(exist_ok=True)
doc.save(OUT)
print("Created:", OUT)
