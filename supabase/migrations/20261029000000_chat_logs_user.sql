-- Gắn người hỏi vào nhật ký chat để biết ai gặp lỗi / thống kê theo người.
-- ADDITIVE: chỉ thêm cột nullable + index, không đụng dữ liệu cũ (dòng cũ có user_email = NULL).
-- Code backend tự nhận ra cột và chỉ ghi khi cột đã có; chạy migration xong cần restart app để nó thôi nhớ "chưa có cột".
alter table public.chat_logs add column if not exists user_email text;
create index if not exists chat_logs_user_email_idx on public.chat_logs (user_email);
