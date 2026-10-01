create table if not exists public.employee_directory (
    email text primary key check (email = lower(btrim(email))),
    display_name text not null,
    department text not null,
    job_title text not null,
    employment_start_date date,
    active boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

alter table public.employee_directory enable row level security;
revoke all on table public.employee_directory from anon, authenticated;
grant select on table public.employee_directory to service_role;

comment on table public.employee_directory is
    'Optional HR-managed self-service profile; not an authentication allowlist and never ingested into chatbot RAG.';
comment on column public.employee_directory.email is
    'Verified, normalized company email used to match Supabase Auth identities.';
comment on column public.employee_directory.active is
    'Set false to hide this optional HR profile without revoking domain-authorized chatbot login.';