-- Mở rộng hồ sơ nhân viên: thêm trường HR quản lý và trường nhân viên tự cập nhật. Chạy lại nhiều lần an toàn.
-- Không chứa dữ liệu nhạy cảm (CCCD, lương, ngân hàng) — những thứ đó không đưa vào chatbot.

-- Do HR quản lý (bảng employee_directory): nhân viên chỉ xem.
alter table public.employee_directory add column if not exists employee_code text check (employee_code is null or char_length(employee_code) <= 30);
alter table public.employee_directory add column if not exists level_name text check (level_name is null or char_length(level_name) <= 60);
alter table public.employee_directory add column if not exists contract_type text check (contract_type is null or char_length(contract_type) <= 60);

-- Nhân viên tự cập nhật (bảng staff_profiles): chỉ chính chủ và admin xem.
alter table public.staff_profiles add column if not exists zalo text check (zalo is null or char_length(zalo) <= 40);
alter table public.staff_profiles add column if not exists birth_date date;
alter table public.staff_profiles add column if not exists gender text check (gender is null or gender in ('nam', 'nu', 'khac'));
alter table public.staff_profiles add column if not exists address text check (address is null or char_length(address) <= 300);
alter table public.staff_profiles add column if not exists education text check (education is null or char_length(education) <= 200);
alter table public.staff_profiles add column if not exists expertise text check (expertise is null or char_length(expertise) <= 500);
alter table public.staff_profiles add column if not exists emergency_name text check (emergency_name is null or char_length(emergency_name) <= 100);
alter table public.staff_profiles add column if not exists emergency_relation text check (emergency_relation is null or char_length(emergency_relation) <= 60);
alter table public.staff_profiles add column if not exists emergency_phone text check (emergency_phone is null or char_length(emergency_phone) <= 30);

comment on column public.staff_profiles.birth_date is 'Do nhân viên tự nhập; chỉ chính chủ và admin xem, không dùng cho chatbot RAG.';
comment on column public.staff_profiles.emergency_phone is 'Liên hệ khẩn cấp do nhân viên tự nhập; chỉ chính chủ và admin xem.';
