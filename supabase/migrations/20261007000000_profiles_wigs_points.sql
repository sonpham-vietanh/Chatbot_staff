-- Hồ sơ nhân viên, WIG (mục tiêu trọng yếu) và sổ điểm đóng góp.
-- Chỉ backend (service_role) đọc/ghi; người dùng đi qua API, API tự kiểm tra "ai được xem WIG của ai".
-- Chạy lại nhiều lần an toàn (if not exists).

-- 1) Thông tin do HR quản lý: thêm quản lý trực tiếp / cơ sở / số điện thoại công việc (đều không bắt buộc).
alter table public.employee_directory add column if not exists manager_email text
    check (manager_email is null or manager_email = lower(btrim(manager_email)));
alter table public.employee_directory add column if not exists campus text;
alter table public.employee_directory add column if not exists work_phone text;

-- 2) Phần nhân viên tự cập nhật (tách khỏi bảng HR để không ghi đè dữ liệu HR).
create table if not exists public.staff_profiles (
    email text primary key check (email = lower(btrim(email))),
    phone text check (phone is null or char_length(phone) <= 30),
    bio text check (bio is null or char_length(bio) <= 500),
    updated_at timestamptz not null default now()
);

-- 3) WIG: mỗi người tự nhập, chỉ số đo tự do (tên chỉ số + đơn vị + giá trị đầu/mục tiêu/hiện tại).
create table if not exists public.wigs (
    id uuid primary key default gen_random_uuid(),
    owner_email text not null check (owner_email = lower(btrim(owner_email))),
    period_label text not null check (char_length(period_label) between 1 and 40),
    title text not null check (char_length(title) between 1 and 200),
    description text check (description is null or char_length(description) <= 2000),
    metric_name text not null check (char_length(metric_name) between 1 and 120),
    unit text check (unit is null or char_length(unit) <= 30),
    start_value numeric not null default 0,
    target_value numeric not null,
    current_value numeric not null default 0,
    due_date date,
    status text not null default 'active' check (status in ('active', 'done', 'dropped')),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
create index if not exists wigs_owner_idx on public.wigs (owner_email, status, created_at desc);

-- 4) Lịch sử cập nhật tiến độ (chỉ thêm, không sửa đè) để biết WIG đi tới đâu theo thời gian.
create table if not exists public.wig_updates (
    id uuid primary key default gen_random_uuid(),
    wig_id uuid not null references public.wigs (id) on delete cascade,
    value numeric not null,
    note text check (note is null or char_length(note) <= 1000),
    created_by text not null,
    created_at timestamptz not null default now()
);
create index if not exists wig_updates_wig_idx on public.wig_updates (wig_id, created_at desc);

-- 5) Sổ điểm đóng góp (chỉ cộng dồn, không xoá). Mỗi (người, lý do, nguồn) chỉ được ghi một lần.
create table if not exists public.user_points (
    id uuid primary key default gen_random_uuid(),
    email text not null check (email = lower(btrim(email))),
    points integer not null,
    reason text not null check (char_length(reason) between 1 and 60),
    ref_type text not null default '',
    ref_id text not null default '',
    created_at timestamptz not null default now(),
    unique (email, reason, ref_type, ref_id)
);
create index if not exists user_points_email_idx on public.user_points (email, created_at desc);

alter table public.staff_profiles enable row level security;
alter table public.wigs enable row level security;
alter table public.wig_updates enable row level security;
alter table public.user_points enable row level security;
revoke all on table public.staff_profiles, public.wigs, public.wig_updates, public.user_points from anon, authenticated;
grant select, insert, update, delete on table public.staff_profiles, public.wigs, public.wig_updates, public.user_points to service_role;
-- API cần đọc thêm 3 cột HR mới
grant select on table public.employee_directory to service_role;

comment on table public.wigs is 'WIG do nhân viên tự nhập; xem được bởi chính chủ, quản lý trực tiếp (employee_directory.manager_email) và admin. Không ingest vào RAG.';
comment on table public.user_points is 'Sổ điểm đóng góp (vd. báo sai được xác nhận). Chỉ ghi nhận, chưa dùng để xếp hạng/đổi thưởng.';
