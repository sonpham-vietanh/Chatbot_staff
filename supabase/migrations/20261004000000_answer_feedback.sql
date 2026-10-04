-- Nút "Báo sai" trên giao diện chat: nhân viên báo một câu trả lời của trợ lý là sai,
-- admin xem và xử lý trong /admin. Lưu nguyên văn câu hỏi/câu trả lời/nguồn tại thời
-- điểm báo, để báo cáo vẫn đủ ngữ cảnh kể cả khi người dùng đã xoá cuộc trò chuyện.
create table if not exists public.answer_feedback (
    id uuid primary key default gen_random_uuid(),
    user_id uuid,
    user_email text,
    thread_id uuid,
    question text not null check (char_length(question) between 1 and 4000),
    answer text not null check (char_length(answer) between 1 and 20000),
    citations jsonb not null default '[]'::jsonb,
    reason text not null check (reason in ('wrong_info', 'missing_info', 'off_topic', 'other')),
    note text check (note is null or char_length(note) <= 2000),
    status text not null default 'open' check (status in ('open', 'resolved', 'dismissed')),
    created_at timestamptz not null default now(),
    handled_at timestamptz
);

create index if not exists answer_feedback_status_created_idx
    on public.answer_feedback (status, created_at desc);

alter table public.answer_feedback enable row level security;
revoke all on table public.answer_feedback from anon, authenticated;
grant select, insert, update, delete on table public.answer_feedback to service_role;

comment on table public.answer_feedback is
    'Staff reports of wrong chatbot answers ("Báo sai"); read and triaged by admins only.';
