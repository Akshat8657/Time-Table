-- =====================================================================
--  TimeTablePro - Migration 003
--  Plan Section 8: PE and SOL are special - their weekly lectures come ONLY from the manually fixed double
--  blocks, never from credits. A subject can now say "I am PE" / "I am SOL" so later phases know.
-- =====================================================================
alter table public.subjects add column if not exists special_kind text;
alter table public.subjects drop constraint if exists subjects_special_kind_valid;
alter table public.subjects add constraint subjects_special_kind_valid
  check (special_kind is null or (special_kind in ('PE', 'SOL') and not is_elective and not is_lab));
