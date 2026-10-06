-- Leader tự quản lý tri thức phòng mình trên web (trang "Quản lý tri thức"):
--   * knowledge_leaders: ai là leader của phòng nào (admin quản lý trong /admin).
--   * knowledge_note_versions: lịch sử mọi thay đổi của note (ai sửa, lúc nào, bản cũ) để khôi phục.
-- Chỉ backend (service_role) đọc/ghi; không client nào được truy cập trực tiếp.
create table if not exists public.knowledge_leaders (
    email text primary key check (email = lower(btrim(email))),
    display_name text not null default '',
    departments text[] not null default '{}',
    active boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.knowledge_note_versions (
    id uuid primary key default gen_random_uuid(),
    note_id text not null,
    version_no integer not null,
    change_kind text not null check (change_kind in ('create', 'update', 'delete', 'restore', 'sync')),
    changed_by text not null default '',
    title text not null,
    department text not null,
    access_level text not null default 'staff',
    status text not null default 'draft',
    content text not null,
    created_at timestamptz not null default now(),
    unique (note_id, version_no)
);

create index if not exists knowledge_note_versions_note_idx
    on public.knowledge_note_versions (note_id, version_no desc);

alter table public.knowledge_leaders enable row level security;
alter table public.knowledge_note_versions enable row level security;
revoke all on table public.knowledge_leaders from anon, authenticated;
revoke all on table public.knowledge_note_versions from anon, authenticated;
grant select, insert, update, delete on table public.knowledge_leaders to service_role;
grant select, insert, update, delete on table public.knowledge_note_versions to service_role;

comment on table public.knowledge_leaders is
    'Leaders allowed to add/edit/delete knowledge notes of their own departments via /quan-ly.';
comment on table public.knowledge_note_versions is
    'Append-only history of knowledge note changes (kept after the note is deleted so it can be restored).';
