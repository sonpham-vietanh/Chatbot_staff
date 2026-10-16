"""Gợi ý câu hỏi tiếp theo dưới câu trả lời của bot: chọn theo chủ đề (không gọi mô hình nên không tốn thêm thời gian).

Mọi câu gợi ý đều là câu mà tài liệu hiện có TRẢ LỜI ĐƯỢC — chỉ thêm gợi ý mới khi wiki đã có nội dung tương ứng."""
from __future__ import annotations

from difflib import SequenceMatcher

from app.rag.lexical_store import fold

TOPICS: list[tuple[tuple[str, ...], list[str]]] = [
    (("thu viec",), ["Thử việc được hưởng bao nhiêu % lương?", "Thử việc có phải nộp thuế TNCN không?", "Hết thử việc thì ký hợp đồng gì?"]),
    (("nghi viec", "bao truoc", "thoi viec"), ["Quy trình nghỉ việc gồm những bước nào?", "Phép chưa dùng hết khi nghỉ việc có được hoàn tiền không?", "Hồ sơ nhân viên mới cần nộp những gì?"]),
    (("nghi phep", "ngay phep", "phep nam", "nghi le"), ["Xin nghỉ phép như thế nào?", "Nghỉ lễ năm 2026 được mấy ngày?", "Phép năm chưa dùng hết thì sao?"]),
    (("overtime", "lam them", "tang ca", " ot "), ["Overtime ngày lễ tính hệ số mấy?", "Khi nào phải gửi bảng overtime cho HR?", "Phụ cấp trực trưa là bao nhiêu?"]),
    (("hoc bong",), ["Học bổng cho con nhân viên có điều kiện gì?", "Học bổng Learning Teacher là gì?", "Đăng ký học bổng nội bộ gửi cho ai?"]),
    (("thuong", "luong", "thang 13"), ["Thưởng Tết tối thiểu là bao nhiêu?", "Lương được trả vào ngày mấy?", "Chia lãi cho Leaders tính thế nào?"]),
    (("cong tac",), ["Công tác phí một ngày là bao nhiêu?", "Công tác phí đi trong ngày tính thế nào?"]),
    (("hop dong", "hdld"), ["Hợp đồng không xác định thời hạn báo trước bao lâu khi nghỉ?", "Thử việc bao lâu?"]),
    (("tuyen dung",), ["Quy trình tuyển dụng gồm mấy giai đoạn?", "Ai duyệt đề xuất nhu cầu tuyển?"]),
    (("van hoa", "smac", "gia tri cot loi"), ["5 giá trị cốt lõi của công ty là gì?", "Công thức SMAC gồm những nguyên tắc nào?"]),
    (("an trua", "ho tro an", "bua an"), ["Giáo viên chủ nhiệm được hỗ trợ ăn thế nào?", "Phụ cấp trực trưa là bao nhiêu?"]),
]
DEFAULT = ["Nghỉ phép năm được mấy ngày?", "Quy trình nghỉ việc gồm những bước nào?", "Thưởng Tết tối thiểu là bao nhiêu?"]


def _same_question(suggestion: str, asked_folded: str) -> bool:
    """Gợi ý trùng hoặc gần giống câu người dùng vừa hỏi thì bỏ (vd 'Xin nghỉ phép thế nào?' với 'Xin nghỉ phép như thế nào?')."""
    folded = fold(suggestion)
    return folded in asked_folded or asked_folded in folded or SequenceMatcher(None, folded, asked_folded).ratio() > 0.8


def suggest_followups(question: str, answer: str = "", limit: int = 3) -> list[str]:
    """Tối đa `limit` câu hỏi gợi ý, theo chủ đề của câu hỏi (rồi đến câu trả lời); không lặp lại chính câu vừa hỏi."""
    asked = fold(question)
    haystacks = (f" {asked} ", f" {fold(answer)} ")
    picked: list[str] = []
    for text in haystacks:
        for keys, suggestions in TOPICS:
            if any(key in text for key in keys):
                for item in suggestions:
                    if item not in picked and not _same_question(item, asked):
                        picked.append(item)
        if len(picked) >= limit:
            break
    for item in DEFAULT:
        if len(picked) >= limit:
            break
        if item not in picked and not _same_question(item, asked):
            picked.append(item)
    return picked[:limit]
