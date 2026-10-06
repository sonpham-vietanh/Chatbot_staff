-- API báo cáo cho Major OS (docs: "Kết nối app với Major OS v2").
-- 1) usage_events: nhật ký sử dụng chỉ-thêm (ai, lúc nào, tính năng gì). Tách riêng khỏi chat_messages vì
--    người dùng xoá cuộc trò chuyện thì tin nhắn mất, nhưng số liệu sử dụng phải còn nguyên.
-- 2) report_keys: key chỉ-đọc dành riêng cho Major OS, chỉ lưu băm SHA-256 (không ai xem lại được key gốc).

create table if not exists public.usage_events (
    id text primary key default ('evt_' || replace(gen_random_uuid()::text, '-', '')),
    occurred_at timestamptz not null default now(),
    user_id uuid,
    user_email text,
    user_name text,
    feature text not null check (char_length(feature) between 1 and 60),
    quantity integer not null default 1 check (quantity >= 1),
    meta jsonb not null default '{}'::jsonb
);

create index if not exists usage_events_time_idx on public.usage_events (occurred_at, id);
create index if not exists usage_events_user_idx on public.usage_events (user_email, occurred_at);

alter table public.usage_events enable row level security;
revoke all on table public.usage_events from anon, authenticated;
grant select, insert, update, delete on table public.usage_events to service_role;

comment on table public.usage_events is
    'Append-only usage log exposed read-only to Major OS through /api/report/su-kien.';

create table if not exists public.report_keys (
    id uuid primary key default gen_random_uuid(),
    label text not null check (char_length(btrim(label)) between 1 and 100),
    key_hash text not null unique,
    key_hint text not null,
    status text not null default 'active' check (status in ('active', 'revoked')),
    created_at timestamptz not null default now(),
    last_used_at timestamptz,
    revoked_at timestamptz
);

alter table public.report_keys enable row level security;
revoke all on table public.report_keys from anon, authenticated;
grant select, insert, update, delete on table public.report_keys to service_role;

comment on table public.report_keys is
    'Read-only API keys for Major OS reporting; only the SHA-256 hash is stored.';

-- Nạp lại lịch sử đã có để số liệu không bắt đầu từ 0. Chỉ nạp khi chưa có sự kiện thật nào (id 'evt_...'),
-- nên chạy lại file này sau khi app đã ghi sự kiện cũng không làm đếm đôi.
do $backfill$
begin
if not exists (select 1 from public.usage_events where id like 'evt\_%') then
insert into public.usage_events (id, occurred_at, user_id, user_email, user_name, feature, quantity, meta)
select 'msg_' || m.id::text,
       m.created_at,
       t.user_id,
       lower(u.email),
       coalesce(nullif(u.raw_user_meta_data ->> 'display_name', ''), nullif(u.raw_user_meta_data ->> 'full_name', ''),
                nullif(u.raw_user_meta_data ->> 'name', '')),
       'hoi_dap',
       1,
       '{"nguon_du_lieu": "nap_lai_tu_lich_su"}'::jsonb
from public.chat_messages m
join public.chat_threads t on t.id = m.thread_id
left join auth.users u on u.id = t.user_id
where m.role = 'user'
on conflict (id) do nothing;

insert into public.usage_events (id, occurred_at, user_id, user_email, user_name, feature, quantity, meta)
select 'fb_' || f.id::text, f.created_at, f.user_id, lower(f.user_email), null, 'bao_sai', 1,
       '{"nguon_du_lieu": "nap_lai_tu_lich_su"}'::jsonb
from public.answer_feedback f
on conflict (id) do nothing;
end if;
end
$backfill$;
