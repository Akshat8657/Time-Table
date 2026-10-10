-- =====================================================================================================================
-- Phase 8 - penetration test finding: an OLD refresh token (the login cookie) kept working after it had been swapped for a
-- new one (Supabase lets the immediate predecessor be used again). A stolen cookie could therefore keep a session alive.
-- We now remember a fingerprint (SHA-256, never the token itself) of the newest and the previous refresh token of every
-- login: the newest works, the previous one only for 10 seconds (two tabs refreshing at the same moment), anything else
-- ends the session at once.
-- =====================================================================================================================
alter table public.app_sessions add column if not exists refresh_hash text;
alter table public.app_sessions add column if not exists prev_refresh_hash text;
alter table public.app_sessions add column if not exists rotated_at timestamptz;
