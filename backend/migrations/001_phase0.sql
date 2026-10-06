-- =====================================================================
--  TimeTablePro - Migration 001 (Phase 0)
--  Tables : schools, profiles, app_sessions
--  Rules  : Row Level Security (RLS) is ON for every table.
--           Nothing is readable/writable by the "anon" role.
--           Normal logged-in users get only the minimum they need.
--           All privileged work (creating accounts, etc.) is done by the
--           Python backend with the service/postgres role.
-- =====================================================================

-- Future tables created in "public" must NOT be open to the public API by default.
-- (Each new table will get explicit, minimal GRANTs + RLS policies.)
alter default privileges for role postgres in schema public revoke all on tables from anon, authenticated;
alter default privileges for role postgres in schema public revoke all on sequences from anon, authenticated;

-- Private schema: helper functions live here so they are NOT exposed by the public Data API.
create schema if not exists private;
revoke all on schema private from public;
grant usage on schema private to authenticated, service_role;

-- ---------------------------------------------------------------------
-- Small trigger helper: keep updated_at fresh
-- ---------------------------------------------------------------------
create or replace function private.set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

-- ---------------------------------------------------------------------
-- 1) schools
-- ---------------------------------------------------------------------
create table if not exists public.schools (
  id          uuid primary key default gen_random_uuid(),
  name        text not null,
  is_active   boolean not null default true,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  constraint schools_name_valid check (char_length(btrim(name)) between 1 and 120)
);
create unique index if not exists schools_name_unique on public.schools (lower(btrim(name)));
comment on table public.schools is 'Schools (departments) of the college, e.g. School of IT.';

-- ---------------------------------------------------------------------
-- 2) profiles  (one row per login account, linked to Supabase Auth)
-- ---------------------------------------------------------------------
create table if not exists public.profiles (
  id                    uuid primary key references auth.users (id) on delete cascade,
  full_name             text not null,
  email                 text not null,
  role                  text not null,
  school_id             uuid references public.schools (id) on delete restrict,
  specialization        text,
  phone                 text,
  photo_url             text,
  is_visiting           boolean not null default false,
  is_active             boolean not null default true,
  must_change_password  boolean not null default true,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),
  constraint profiles_role_valid        check (role in ('admin', 'hod', 'teacher')),
  constraint profiles_full_name_valid   check (char_length(btrim(full_name)) between 1 and 120),
  constraint profiles_email_lowercase   check (email = lower(email) and char_length(email) <= 254),
  constraint profiles_school_matches_role check (
    (role = 'admin' and school_id is null) or (role in ('hod', 'teacher') and school_id is not null)
  ),
  constraint profiles_specialization_len check (specialization is null or char_length(specialization) <= 300),
  constraint profiles_phone_len          check (phone is null or char_length(phone) <= 30),
  constraint profiles_photo_url_len      check (photo_url is null or char_length(photo_url) <= 500)
);
create unique index if not exists profiles_email_unique on public.profiles (email);
create index if not exists profiles_school_id_idx on public.profiles (school_id);
create index if not exists profiles_role_idx on public.profiles (role);
comment on table public.profiles is 'Login accounts (admin / hod / teacher) linked 1:1 to Supabase Auth users.';

-- ---------------------------------------------------------------------
-- 3) app_sessions  (server-side session record: idle timeout, remember-me, revocation)
--    Only the backend can touch this table.
-- ---------------------------------------------------------------------
create table if not exists public.app_sessions (
  id                    uuid primary key default gen_random_uuid(),
  supabase_session_id   uuid not null unique,
  user_id               uuid not null references auth.users (id) on delete cascade,
  remember_me           boolean not null default false,
  created_at            timestamptz not null default now(),
  last_activity_at      timestamptz not null default now(),
  absolute_expires_at   timestamptz not null,
  revoked_at            timestamptz,
  revoked_reason        text,
  ip                    text,
  user_agent            text
);
create index if not exists app_sessions_user_idx on public.app_sessions (user_id);
create index if not exists app_sessions_open_idx on public.app_sessions (last_activity_at) where revoked_at is null;
comment on table public.app_sessions is 'Backend-only: tracks inactivity, remember-me and revocation of login sessions.';

-- ---------------------------------------------------------------------
-- Triggers
-- ---------------------------------------------------------------------
drop trigger if exists schools_set_updated_at on public.schools;
create trigger schools_set_updated_at before update on public.schools
  for each row execute function private.set_updated_at();

drop trigger if exists profiles_set_updated_at on public.profiles;
create trigger profiles_set_updated_at before update on public.profiles
  for each row execute function private.set_updated_at();

-- Safety net: a normal logged-in user can never change role / school / status / flags,
-- even if somebody widens the column GRANTs by mistake later.
create or replace function private.protect_profile_columns()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if current_user in ('anon', 'authenticated') then
    if new.id                   is distinct from old.id
       or new.email             is distinct from old.email
       or new.role              is distinct from old.role
       or new.school_id         is distinct from old.school_id
       or new.is_active         is distinct from old.is_active
       or new.is_visiting       is distinct from old.is_visiting
       or new.must_change_password is distinct from old.must_change_password then
      raise exception 'You are not allowed to change these fields.' using errcode = '42501';
    end if;
  end if;
  return new;
end;
$$;

drop trigger if exists profiles_protect_columns on public.profiles;
create trigger profiles_protect_columns before update on public.profiles
  for each row execute function private.protect_profile_columns();

-- ---------------------------------------------------------------------
-- RLS helper functions (SECURITY DEFINER so they can read profiles without recursion)
-- ---------------------------------------------------------------------
create or replace function private.current_app_role()
returns text
language sql
stable
security definer
set search_path = ''
as $$
  select p.role from public.profiles p where p.id = (select auth.uid()) and p.is_active
$$;

create or replace function private.current_school_id()
returns uuid
language sql
stable
security definer
set search_path = ''
as $$
  select p.school_id from public.profiles p where p.id = (select auth.uid()) and p.is_active
$$;

create or replace function private.is_admin()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select coalesce((select p.role = 'admin' from public.profiles p where p.id = (select auth.uid()) and p.is_active), false)
$$;

revoke all on function private.current_app_role(), private.current_school_id(), private.is_admin() from public, anon;
grant execute on function private.current_app_role(), private.current_school_id(), private.is_admin() to authenticated, service_role;

-- ---------------------------------------------------------------------
-- Row Level Security
-- ---------------------------------------------------------------------
alter table public.schools      enable row level security;
alter table public.profiles     enable row level security;
alter table public.app_sessions enable row level security;

-- schools: admin sees all; HOD / teacher see only their own school; only admin may add/edit.
drop policy if exists schools_select on public.schools;
create policy schools_select on public.schools for select to authenticated
  using ( (select private.is_admin()) or id = (select private.current_school_id()) );

drop policy if exists schools_admin_insert on public.schools;
create policy schools_admin_insert on public.schools for insert to authenticated
  with check ( (select private.is_admin()) );

drop policy if exists schools_admin_update on public.schools;
create policy schools_admin_update on public.schools for update to authenticated
  using ( (select private.is_admin()) ) with check ( (select private.is_admin()) );

-- profiles: everyone sees their own row; HOD sees his school; admin sees all.
drop policy if exists profiles_select on public.profiles;
create policy profiles_select on public.profiles for select to authenticated
  using (
    id = (select auth.uid())
    or (select private.is_admin())
    or ( (select private.current_app_role()) = 'hod' and school_id = (select private.current_school_id()) )
  );

-- profiles: same row scope for edits (and only the harmless columns, see GRANTs below).
drop policy if exists profiles_update on public.profiles;
create policy profiles_update on public.profiles for update to authenticated
  using (
    id = (select auth.uid())
    or (select private.is_admin())
    or ( (select private.current_app_role()) = 'hod' and school_id = (select private.current_school_id()) )
  )
  with check (
    id = (select auth.uid())
    or (select private.is_admin())
    or ( (select private.current_app_role()) = 'hod' and school_id = (select private.current_school_id()) )
  );

-- app_sessions: NO policies at all -> invisible to anon / authenticated.

-- ---------------------------------------------------------------------
-- GRANTs (least privilege). service_role keeps full access (it bypasses RLS).
-- ---------------------------------------------------------------------
revoke all on public.schools, public.profiles, public.app_sessions from anon, authenticated;

grant select, insert, update on public.schools to authenticated;                    -- RLS limits to admin for writes
grant select on public.profiles to authenticated;
grant update (full_name, phone, photo_url, specialization) on public.profiles to authenticated;  -- harmless columns only

grant all on public.schools, public.profiles, public.app_sessions to service_role;

-- ---------------------------------------------------------------------
-- Seed data
-- ---------------------------------------------------------------------
insert into public.schools (name) values ('School of IT')
on conflict (lower(btrim(name))) do nothing;
