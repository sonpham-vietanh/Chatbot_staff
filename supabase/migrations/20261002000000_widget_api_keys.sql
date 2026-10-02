create table if not exists public.api_keys (
    id uuid primary key default gen_random_uuid(),
    label text not null check (char_length(btrim(label)) between 1 and 100),
    key text not null unique check (key like 'vas_%'),
    allowed_origin text not null check (char_length(btrim(allowed_origin)) between 1 and 300),
    status text not null default 'active' check (status in ('active', 'revoked')),
    created_at timestamptz not null default now(),
    last_used_at timestamptz
);

create index if not exists api_keys_active_key_idx
    on public.api_keys (key)
    where status = 'active';

alter table public.api_keys enable row level security;
revoke all on table public.api_keys from anon, authenticated;
grant select, insert, update, delete on table public.api_keys to service_role;

comment on table public.api_keys is
    'Server-to-server widget keys and iframe allowlists managed by the admin API.';
comment on column public.api_keys.key is
    'Secret vas_* credential; never expose through a public client or unauthenticated endpoint.';
comment on column public.api_keys.allowed_origin is
    'Partner web origin allowed to embed the iframe or send an Origin header with widget requests.';
