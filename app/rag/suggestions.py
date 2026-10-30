"""Gợi ý câu hỏi tiếp theo dưới câu trả lời của bot: chọn theo chủ đề (không gọi mô hình nên không tốn thêm thời gian).

Mọi câu gợi ý đều là câu mà tài liệu hiện có TRẢ LỜI ĐƯỢC — chỉ thêm gợi ý mới khi wiki đã có nội dung tương ứng."""
from __future__ import annotations

from difflib import SequenceMatcher

from app.rag.lexical_store import fold

TOPICS: list[tuple[tuple[str, ...], list[str]]] = [
    (("ket hon", "dam cuoi", "hon le", "vbi", " cuoi "), ["Gia đình có tang thì được nghỉ mấy ngày?", "Hỗ trợ hôn lễ gồm những gì?", "Nghỉ phép năm được mấy ngày?", "Xin nghỉ phép gửi cho ai và báo trước bao lâu?"]),
    (("nghi tang", "tang che", "dam tang"), ["Bản thân kết hôn thì được nghỉ mấy ngày?", "Hỗ trợ tang chế là bao nhiêu?", "Xin nghỉ phép gửi cho ai và báo trước bao lâu?"]),
    (("thai san", "sinh con", " bau "), ["Chế độ thai sản gồm những gì?", "Nghỉ phép năm được mấy ngày?", "Xin nghỉ phép như thế nào?"]),
    (("thu viec",), ["Thử việc được hưởng bao nhiêu % lương?", "Thử việc có phải nộp thuế TNCN không?", "Hết thử việc thì ký hợp đồng gì?"]),
    (("nghi viec", "bao truoc", "thoi viec"), ["Quy trình nghỉ việc gồm những bước nào?", "Phép chưa dùng hết khi nghỉ việc có được hoàn tiền không?", "Hồ sơ nhân viên mới cần nộp những gì?"]),
    (("nghi phep", "ngay phep", "phep nam", "nghi le"), ["Xin nghỉ phép như thế nào?", "Nghỉ phép năm được mấy ngày?", "Nghỉ lễ năm 2026 được mấy ngày?", "Phép năm chưa dùng hết thì sao?"]),
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


def _topic_pools(text: str) -> list[list[str]]:
    """Các nhóm gợi ý có từ khoá xuất hiện trong `text`, nhóm khớp nhiều từ khoá nhất lên trước (cùng điểm giữ thứ tự khai báo)."""
    scored = [(sum(key in text for key in keys), -i, suggestions) for i, (keys, suggestions) in enumerate(TOPICS)]
    return [suggestions for hits, _, suggestions in sorted(scored, reverse=True) if hits]


def suggest_followups(question: str, answer: str = "", limit: int = 3) -> list[str]:
    """Tối đa `limit` câu hỏi gợi ý, bám chủ đề của câu hỏi; không lặp lại chính câu vừa hỏi.

    Câu trả lời chỉ dùng khi câu hỏi không khớp chủ đề nào (câu hỏi nối tiếp kiểu "còn giáo viên thì sao?"), và chỉ lấy chủ đề nổi nhất
    của nó — câu trả lời nhắc nhiều thứ (lương, phép...) nên trộn hết các chủ đề đó thì gợi ý lạc đề. Không có chủ đề nào mới dùng câu mặc định."""
    asked = fold(question)
    pools = _topic_pools(f" {asked} ")
    if not pools:
        pools = _topic_pools(f" {fold(answer)} ")[:1]
    picked: list[str] = []
    for suggestions in pools:
        for item in suggestions:
            if len(picked) < limit and item not in picked and not _same_question(item, asked):
                picked.append(item)
    if not picked:
        picked = [item for item in DEFAULT if not _same_question(item, asked)]
    return picked[:limit]
