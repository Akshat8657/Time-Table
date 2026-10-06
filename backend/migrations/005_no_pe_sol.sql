-- =====================================================================
--  TimeTablePro - Migration 005
--  COLLEGE_SEED_DATA.md V2: some classes (B.Sc. Sem 5 and Sem 7) have NO PE and NO SOL at all.
--  The PE/SOL checklist must not nag about them: a class can be marked "no PE / SOL".
-- =====================================================================
alter table public.semesters add column if not exists no_pe_sol boolean not null default false;
comment on column public.semesters.no_pe_sol is 'true = this class has no PE and no SOL (the Fixed Slots checklist skips it)';
