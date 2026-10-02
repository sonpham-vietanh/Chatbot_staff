"""Làm sạch định dạng câu trả lời của LLM ở BACKEND, không trông chờ từng giao diện tự xử lý.

Prompt chỉ cho phép 2 kiểu định dạng (**in đậm** và gạch đầu dòng "- "), nhưng model vẫn
thỉnh thoảng chèn ký hiệu khác (`code`, # heading, *nghiêng*, bảng...). Có 2 mức:

- clean_markdown(): giữ đúng 2 kiểu được phép, bỏ mọi ký hiệu khác — cho giao diện chat của
  mình (React tự render **đậm** và bullet).
- to_plain_text(): bỏ luôn cả ** — cho bên tích hợp qua API (/api/widget/chat), nơi câu trả
  lời được hiển thị nguyên văn nên mọi ký hiệu Markdown đều lộ ra thành ký tự thừa.

Nguyên tắc khi chọn luật: thà để sót 1 ký hiệu hiếm gặp còn hơn làm hỏng nội dung thật
(công thức lương có dấu *, tên file có dấu _, số điện thoại bắt đầu bằng +...).
"""
import re

# Các mẫu "cả dòng" bên dưới được so khớp trên dòng ĐÃ strip() bằng fullmatch — không đặt
# \s* ở 2 đầu mẫu, để 1 dòng toàn khoảng trắng dài không gây lùi (backtracking) bậc hai.
_FENCE_LINE = re.compile(r"(?:```|~~~)[\w+-]*")
# Đường kẻ ngang: "---" (từ 3 gạch) hoặc đúng "***". KHÔNG coi "______" (chỗ điền trong biểu
# mẫu) hay "********" (mật khẩu bị che) là đường kẻ — đó là nội dung.
_RULE_LINE = re.compile(r"(?:-[ \t]*){3,}|\*[ \t]*\*[ \t]*\*")
_TABLE_SEPARATOR_CELL = re.compile(r":?-{2,}:?")
_HEADING = re.compile(r"^(\s{0,3})#{1,6}[ \t]+(?=\S)")
# Không có luật cho blockquote ("> ..."): trong câu trả lời nhân sự, dòng bắt đầu bằng ">"
# gần như luôn là phép so sánh ("> 5 năm: 14 ngày phép"), gỡ đi là sai nội dung.
# Không coi "+" đầu dòng là bullet: "+ Phụ cấp" trong công thức lương sẽ thành "- Phụ cấp".
_BULLET = re.compile(r"^(\s*)[*•][ \t]+")
_BULLET_LINE = re.compile(r"^\s*-[ \t]+\S")
_EMPTY_BULLET = re.compile(r"^\s*-\s*$")
# Riêng "+" THỤT LỀ ngay dưới 1 gạch đầu dòng là ý con (cách viết rất phổ biến trong văn bản
# tiếng Việt: "- Mục A:" rồi "  + ý 1").
_NESTED_PLUS = re.compile(r"^(\s+)\+([ \t]+\S)")
_CONTINUATION = re.compile(r"(?!\d+[.)]\s)[^\W\d_]")
_LINK = re.compile(r"!?\[([^\]\n]{1,300})\]\((https?://[^\s)]{1,1000})\)")
_HTML_INLINE = re.compile(r"</?(?:b|strong|i|em|u)\s*/?>|</li\s*>", re.IGNORECASE)
_HTML_LIST_ITEM = re.compile(r"<li\s*>", re.IGNORECASE)
_HTML_BLOCK = re.compile(r"</?(?:p|ul|ol|br)\s*/?>", re.IGNORECASE)
# Dòng chỉ gồm dấu sao (từ 4 dấu) là nội dung bị che ("Mật khẩu: ********"), không phải in đậm rỗng.
_MASK_LINE = re.compile(r"\*{4,}")
_STRIKE = re.compile(r"~~(?=[^\s~])([^~\n]{1,300}?)(?<=\S)~~")

# Ký hiệu nhấn mạnh chỉ được coi là Markdown khi đứng ở RANH GIỚI TỪ: trước dấu mở là đầu
# dòng/khoảng trắng/dấu mở ngoặc, sau dấu đóng là cuối dòng/khoảng trắng/dấu câu. Nhờ vậy
# "(Lương cơ bản)*(Hệ số)", "10%*(lương)", "bao_cao_thang_9.xlsx", "__init__.py" không bị đụng.
# \x00 là ký tự giữ chỗ tạm cho ** (xem _clean_line).
_OPEN = r"(?<![^\s(\[“\"'‘\x00])"
_PUNCT = r"[.,;:!?)\]”\"'’\x00]"
# Dấu câu ngay sau dấu đóng phải là dấu câu thật (theo sau là hết dòng/khoảng trắng/dấu câu
# khác), không phải dấu chấm của phần mở rộng tên file như "__init__.py".
_CLOSE = rf"(?=$|\s|{_PUNCT}(?:$|\s|{_PUNCT}))"
# Nội dung không được chứa "__": 2 tên kiểu "__init__.py ... __name__" trên 1 dòng không
# được ghép thành 1 cụm đậm.
_UNDERSCORE_BOLD = re.compile(_OPEN + r"__(?=[^\W_])((?:(?!__).)+?)(?<![\s_])__" + _CLOSE)
_UNDERSCORE_ITALIC = re.compile(_OPEN + r"_(?=[^\W_])([^_\n]+?)(?<![\s_])_" + _CLOSE)
_ITALIC = re.compile(_OPEN + r"\*(?=\w)([^*\n]+?)(?<![\s*])\*" + _CLOSE)

_MAX_PASSES = 3


def _balance_bold(line: str) -> str:
    """Ghép cặp ** theo thứ tự trong 1 dòng (giao diện cũng ghép theo từng dòng): bỏ cặp
    rỗng và bỏ dấu ** lẻ cuối cùng (model quên đóng, hoặc câu bị cắt giữa chừng do giới hạn
    token). KHÔNG dùng regex kiểu `\\*\\*\\s*\\*\\*` để tìm cặp rỗng — nó khớp nhầm dấu đóng
    của cụm trước với dấu mở của cụm sau ("**20/11:** **300.000đ**") và làm dính chữ."""
    parts = line.split("**")
    out = [parts[0]]
    index = 1
    while index < len(parts):
        if index + 1 < len(parts):
            content = parts[index]
            out.append(f"**{content}**" if content.strip() else content)
            out.append(parts[index + 1])
            index += 2
        else:
            out.append(parts[index])
            index += 1
    return "".join(out)


def _strip_heading(line: str) -> str:
    while match := _HEADING.match(line):  # lặp cho "# # Tiêu đề"
        body = line[match.end():].rstrip()
        without_closing = body.rstrip("#")
        # "## Mục 1 ##" có dãy # đóng (cách chữ bằng khoảng trắng) thì bỏ; "## Ngôn ngữ C#" thì # là chữ.
        if without_closing != body and without_closing.endswith((" ", "\t")):
            body = without_closing.rstrip()
        line = match.group(1) + body
    return line


def _is_noise_line(line: str) -> bool:
    """Dòng chỉ mang ký hiệu Markdown, không mang nội dung: hàng rào code, đường kẻ ngang,
    hàng phân cách của bảng ("|---|---|")."""
    stripped = line.strip()
    if not stripped:
        return False
    if _FENCE_LINE.fullmatch(stripped) or _RULE_LINE.fullmatch(stripped):
        return True
    return "|" in stripped and all(
        _TABLE_SEPARATOR_CELL.fullmatch(cell.strip()) for cell in stripped.strip("|").split("|")
    )


def _clean_line(line: str) -> str:
    line = _strip_heading(line)
    line = _BULLET.sub(r"\1- ", line)
    stripped = line.strip()
    if len(stripped) > 2 and stripped.startswith("|") and stripped.endswith("|"):
        # Dòng của bảng Markdown: bỏ 2 dấu | ngoài cùng, giữ | giữa các ô cho dễ đọc.
        line = " | ".join(cell.strip() for cell in stripped[1:-1].split("|"))
    line = line.replace("`", "")
    line = _HTML_INLINE.sub("", line)
    line = _LINK.sub(lambda m: m.group(2) if m.group(1) == m.group(2) else f"{m.group(1)} ({m.group(2)})", line)
    line = _STRIKE.sub(r"\1", line)
    line = _UNDERSCORE_BOLD.sub(r"**\1**", line)
    line = _balance_bold(line)
    # Tạm giấu ** để luật chữ nghiêng không ăn nhầm dấu sao của in đậm.
    line = _ITALIC.sub(r"\1", line.replace("**", "\x00")).replace("\x00", "**")
    line = _UNDERSCORE_ITALIC.sub(r"\1", line)
    return line.rstrip()


def _clean_once(answer: str) -> str:
    # Thẻ HTML mức khối tách dòng ("a<br>b", "<li>a</li><li>b</li>") phải thành xuống dòng
    # thật trước khi xử lý theo dòng — xoá trơn thì 2 chữ ở 2 bên dính vào nhau.
    answer = _HTML_BLOCK.sub("\n", _HTML_LIST_ITEM.sub("\n- ", answer))
    lines: list[str] = []
    for raw_line in answer.splitlines():
        if _is_noise_line(raw_line):
            continue
        if _MASK_LINE.fullmatch(raw_line.strip()):
            lines.append(raw_line.strip())
            continue
        line = _clean_line(raw_line)
        if _EMPTY_BULLET.match(line):
            continue
        lines.append(line)

    cleaned: list[str] = []
    for line in lines:
        if not line:
            if cleaned and cleaned[-1]:  # gộp nhiều dòng trống liên tiếp, bỏ dòng trống đầu
                cleaned.append("")
            continue
        previous = cleaned[-1] if cleaned else ""
        if previous and _BULLET_LINE.match(previous):
            if _NESTED_PLUS.match(line):
                line = _NESTED_PLUS.sub(r"\1-\2", line)
            elif line[0] in " \t" and _CONTINUATION.match(line.strip()):
                # Dòng thụt lề ngay dưới 1 gạch đầu dòng, bắt đầu bằng chữ, là phần nối tiếp
                # của chính ý đó. Dòng đánh số ("1. bước một") hay bắt đầu bằng ký hiệu
                # ("> 5 năm") là ý riêng, giữ nguyên dòng.
                cleaned[-1] = f"{previous} {line.strip()}"
                continue
        if previous and bool(_BULLET_LINE.match(line)) != bool(_BULLET_LINE.match(previous)):
            # Giao diện chỉ dựng danh sách khi CẢ khối (ngăn bởi dòng trống) đều là bullet.
            # Câu dẫn dính liền danh sách ("Gồm:\n- a\n- b") sẽ hiện thành đoạn văn có dấu
            # "-" thô, nên luôn tách khối bullet khỏi dòng thường bằng 1 dòng trống.
            cleaned.append("")
        cleaned.append(line)
    return "\n".join(cleaned).strip()


def clean_markdown(answer: str) -> str:
    """Chỉ giữ **in đậm** và gạch đầu dòng '- '; bỏ code/heading/nghiêng/bảng và ** lẻ cặp."""
    for _ in range(_MAX_PASSES):
        # Bỏ 1 ký hiệu có thể làm lộ ký hiệu khác ở đầu dòng ("# # Tiêu đề") — lặp tới khi
        # ổn định để kết quả không đổi nếu bị làm sạch thêm lần nữa (widget làm sạch 2 lần).
        cleaned = _clean_once(answer)
        if cleaned == answer:
            break
        answer = cleaned
    return answer


def to_plain_text(answer: str) -> str:
    """Văn bản thuần: không còn ký hiệu Markdown nào; danh sách vẫn là các dòng '- ...'."""
    def drop_bold(text: str) -> str:
        return "\n".join(
            line if _MASK_LINE.fullmatch(line.strip()) else line.replace("**", "").rstrip()
            for line in text.splitlines()
        ).strip()

    plain = drop_bold(clean_markdown(answer))
    # Bỏ ** có thể làm lộ ký hiệu đầu dòng từng bị bọc đậm ("**# Tiêu đề**") — làm sạch lại.
    return drop_bold(clean_markdown(plain))
