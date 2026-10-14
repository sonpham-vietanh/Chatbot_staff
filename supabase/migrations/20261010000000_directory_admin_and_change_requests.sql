-- Danh bạ nhân sự do HR quản lý (thêm cột), yêu cầu chỉnh sửa hồ sơ của nhân viên, nhật ký thay đổi và mốc đồng ý thông báo dữ liệu.
-- Chỉ backend (service_role) đọc/ghi. Chạy lại nhiều lần an toàn (if not exists).

-- 1) Thêm cột cho employee_directory (đều không bắt buộc).
alter table public.employee_directory add column if not exists employment_end_date date;
alter table public.employee_directory add column if not exists employment_type text;
alter table public.employee_directory add column if not exists team_name text;
alter table public.employee_directory add column if not exists extra_roles text;
alter table public.employee_directory add column if not exists subjects text;
alter table public.employee_directory add column if not exists grade_levels text;

-- Ràng buộc đặt tên riêng để chạy lại không lỗi trùng.
do $$
begin
    if not exists (select 1 from pg_constraint where conname = 'employee_directory_employment_type_chk') then
        alter table public.employee_directory add constraint employee_directory_employment_type_chk
            check (employment_type is null or employment_type in ('co_huu', 'thinh_giang', 'hop_dong', 'khac'));
    end if;
    if not exists (select 1 from pg_constraint where conname = 'employee_directory_team_name_len_chk') then
        alter table public.employee_directory add constraint employee_directory_team_name_len_chk
            check (team_name is null or char_length(team_name) <= 100);
    end if;
    if not exists (select 1 from pg_constraint where conname = 'employee_directory_extra_roles_len_chk') then
        alter table public.employee_directory add constraint employee_directory_extra_roles_len_chk
            check (extra_roles is null or char_length(extra_roles) <= 200);
    end if;
    if not exists (select 1 from pg_constraint where conname = 'employee_directory_subjects_len_chk') then
        alter table public.employee_directory add constraint employee_directory_subjects_len_chk
            check (subjects is null or char_length(subjects) <= 200);
    end if;
    if not exists (select 1 from pg_constraint where conname = 'employee_directory_grade_levels_len_chk') then
        alter table public.employee_directory add constraint employee_directory_grade_levels_len_chk
            check (grade_levels is null or char_length(grade_levels) <= 100);
    end if;
end $$;

-- HR sửa danh bạ qua API: cần INSERT/UPDATE (không DELETE: nghỉ việc dùng active = false).
grant select, insert, update on table public.employee_directory to service_role;

comment on column public.employee_directory.employment_type is 'co_huu | thinh_giang | hop_dong | khac';

-- 2) Mốc nhân viên xác nhận đã đọc thông báo mục đích sử dụng dữ liệu.
alter table public.staff_profiles add column if not exists consent_at timestamptz;

-- 3) Yêu cầu chỉnh sửa thông tin HR do nhân viên gửi, HR duyệt hoặc từ chối.
create table if not exists public.profile_change_requests (
    id uuid primary key default gen_random_uuid(),
    email text not null check (email = lower(btrim(email))),
    field text not null,
    old_value text,
    new_value text not null check (char_length(new_value) <= 500),
    reason text check (reason is null or char_length(reason) <= 500),
    status text not null default 'pending' check (status in ('pending', 'approved', 'rejected', 'cancelled')),
    reviewed_by text,
    review_note text check (review_note is null or char_length(review_note) <= 500),
    created_at timestamptz not null default now(),
    reviewed_at timestamptz
);
-- Mỗi người chỉ có 1 yêu cầu đang chờ cho mỗi trường.
create unique index if not exists profile_change_requests_pending_uidx
    on public.profile_change_requests (email, field) where status = 'pending';
create index if not exists profile_change_requests_status_idx
    on public.profile_change_requests (status, created_at desc);
create index if not exists profile_change_requests_email_idx
    on public.profile_change_requests (email, created_at desc);

-- 4) Nhật ký thay đổi hồ sơ (chỉ thêm, không sửa/xoá).
create table if not exists public.profile_audit_log (
    id uuid primary key default gen_random_uuid(),
    email text not null,
    actor text not null,
    action text not null check (action in ('self_update', 'hr_update', 'create', 'deactivate', 'reactivate', 'import', 'request_approved', 'request_rejected')),
    changes jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);
create index if not exists profile_audit_log_email_idx on public.profile_audit_log (email, created_at desc);

alter table public.profile_change_requests enable row level security;
alter table public.profile_audit_log enable row level security;
revoke all on table public.profile_change_requests, public.profile_audit_log from anon, authenticated;
grant select, insert, update on table public.profile_change_requests to service_role;
grant select, insert on table public.profile_audit_log to service_role;

comment on table public.profile_change_requests is 'Yêu cầu chỉnh sửa thông tin HR do nhân viên gửi; HR duyệt thì giá trị mới được áp vào employee_directory.';
comment on table public.profile_audit_log is 'Nhật ký thay đổi hồ sơ. Với tự cập nhật của nhân viên chỉ ghi tên trường, không ghi giá trị.';
