# Quy trình kiểm thử bằng bộ câu hỏi chuẩn (golden set)

Bộ câu hỏi chuẩn nằm ở `tests/golden/questions.json` (hơn 90 câu: nghỉ phép, lương thưởng, thử việc, nghỉ việc, văn hoá, cơ cấu, JD, thai sản/hiếu hỉ, câu wiki chưa có, câu bảo mật). Mỗi câu có đáp án kỳ vọng đã đối chiếu với wiki `wiki_obsidian` (đến 2026-10-29). Mục đích: phát hiện hồi quy sau khi sửa wiki, sửa code hoặc deploy.

## Khi nào chạy

| Thời điểm | Chế độ | Chi phí |
|---|---|---|
| Sau mỗi lần sửa wiki (trước khi đồng bộ) | `--mode retrieval-only --wiki-dir <thư mục wiki_obsidian>` | miễn phí, offline |
| Sau khi wiki đã đồng bộ lên Supabase | `--mode retrieval-only` | miễn phí, chỉ SELECT |
| Trước mỗi lần deploy (đặc biệt khi đổi pipeline, prompt, từ đồng nghĩa) | retrieval-only, rồi `--mode pipeline --yes-spend` nếu đổi prompt/LLM | pipeline tốn tiền LLM |

```
set PYTHONUTF8=1
set PYTHONPATH=.
.venv\Scripts\python.exe scripts\eval_golden.py --mode retrieval-only
.venv\Scripts\python.exe scripts\eval_golden.py --mode retrieval-only --wiki-dir E:\Workspace\wiki_obsidian
.venv\Scripts\python.exe scripts\eval_golden.py --mode pipeline --yes-spend
```

Lọc nhanh: `--only NP01,luong-thuong` (id hoặc nhóm). Chế độ `pipeline` bị từ chối nếu thiếu `--yes-spend`; nó gọi LLM thật (3 luồng) và các câu bị từ chối có thể sinh note nháp "[CẦN BỔ SUNG] ..." thật trong Supabase production. Script chụp danh sách nháp trước/sau và chỉ xoá nháp mới có tiêu đề khớp đúng một câu golden. Đừng chạy lúc nhân viên đang dùng nhiều nếu muốn hàng đợi HR sạch.

## Đọc kết quả

Báo cáo markdown + json nằm ở `tests/golden/results/` (không commit). Mỗi câu có một trong các trạng thái:

- **Đạt**: có chuỗi đáp án kỳ vọng (retrieval-only: có trong top-5 đoạn); câu "từ chối" thì bot nói chưa có / hỏi HR và không bịa.
- **Trượt**: thiếu đáp án, chứa chuỗi cấm (`must_not_contain`, `must_not_regex`), hoặc bot từ chối trong khi wiki có. Dòng đầu báo cáo nêu rõ **câu MỚI trượt so với lần chạy trước** cùng chế độ: đây là hồi quy cần xử lý trước khi deploy.
- **Cần xem**: câu `disputed` (wiki còn mâu thuẫn, ví dụ năm tính phép, hoàn tiền phép), hoặc câu kỳ vọng từ chối nhưng không thấy dấu hiệu từ chối; đọc câu trả lời bằng mắt.
- **Bỏ qua** (chỉ retrieval-only): câu từ chối/bảo mật, chỉ chấm được khi chạy pipeline.

Trượt ở retrieval-only nghĩa là bot không được đưa đúng đoạn cho LLM (lỗi tìm kiếm hoặc thiếu nội dung trong wiki); trượt ở pipeline mà retrieval đạt nghĩa là lỗi prompt/LLM. Mã thoát 1 nếu có câu trượt.

## Thêm câu mới

1. Tìm đáp án trong wiki (grep `nhan-su/wiki`, `core`) và chép đúng con số/chuỗi; không dùng trí nhớ.
2. Thêm một phần tử vào `questions.json`: `id` duy nhất (tiền tố nhóm + số), `question`, `group`, `must_contain_any` (ít nhất một chuỗi phải có; so khớp không dấu, không phân biệt hoa thường), `must_not_contain` (số bịa dễ gặp), `source` (trang wiki). Có thể thêm `must_not_regex` (vd chặn số điện thoại, email) hoặc `retrieval_any` (từ khoá riêng cho retrieval-only).
3. Câu wiki chưa có: `"expect_refusal": true`, `must_contain_any: []`. Khi HR bổ sung tài liệu, đổi thành câu thường kèm đáp án.
4. Câu wiki còn mâu thuẫn: `"disputed": true`, ghi lý do ở `note`, không kiểm số. Khi `can-xu-ly.md` chốt, bỏ cờ và thêm đáp án.
5. Chạy `.venv\Scripts\python.exe -m pytest -q tests/golden` (kiểm cấu trúc), rồi chạy retrieval-only để xem câu mới đạt chưa.
