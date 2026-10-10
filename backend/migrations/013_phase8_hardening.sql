-- =====================================================================================================================
-- Phase 8 - security hardening (penetration test findings)
--
-- 1) NO browser writes anywhere. The browser only ever talks to our Python API, which validates every change and writes
--    the audit log. Phase 0 had left three direct-write doors open on the Supabase data API (PostgREST):
--      * a signed-in person could PATCH their own profile (name / phone / photo / specialization) - an HOD even the
--        profiles of his school - without validation and WITHOUT an audit line;
--      * the admin could INSERT / UPDATE schools the same way.
--    They are closed here: the write policies are dropped and the browser roles keep SELECT only (RLS still decides
--    which rows they may read).
-- 2) Future tables / functions / sequences created by migrations do not silently become writable / callable by the
--    browser roles (Supabase's defaults would grant them).
-- =====================================================================================================================

drop policy if exists profiles_update on public.profiles;
drop policy if exists schools_admin_insert on public.schools;
drop policy if exists schools_admin_update on public.schools;

-- table-level revoke also removes the column-level grants (profiles: full_name, phone, photo_url, specialization)
revoke insert, update, delete, truncate, references, trigger on all tables in schema public from anon, authenticated;
revoke all on all sequences in schema public from anon, authenticated;
revoke execute on all functions in schema public from anon, authenticated;

alter default privileges in schema public revoke insert, update, delete, truncate, references, trigger on tables from anon, authenticated;
alter default privileges in schema public revoke all on sequences from anon, authenticated;
alter default privileges in schema public revoke execute on functions from anon, authenticated, public;
