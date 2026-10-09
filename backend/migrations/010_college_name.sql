-- =====================================================================
--  TimeTablePro - Migration 010 (PHASE 4): the college's name, printed on every PDF / Excel export header.
--  Admin edits it on the Timing Setup screen. The first value comes from the seed file ("Real AURO Data").
-- =====================================================================
alter table public.college_settings add column if not exists college_name text;
alter table public.college_settings drop constraint if exists college_settings_name_valid;
alter table public.college_settings add constraint college_settings_name_valid
  check (college_name is null or char_length(btrim(college_name)) between 1 and 120);
update public.college_settings set college_name = 'AURO University' where college_name is null;
