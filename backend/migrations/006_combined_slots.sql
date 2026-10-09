-- =====================================================================
--  TimeTablePro - Migration 006
--  Master plan V9 (Section 8): a fixed slot may cover SEVERAL classes together (COMBINED session),
--  e.g. SOL for M.Sc. IT Sem 1 + M.Sc. AI Sem 1 with one teacher.
--
--  Model: one row per covered class (so every class stays occupied and every class screen keeps working);
--  the rows of ONE combined session share `combined_group_id`. A combined session is ONE session:
--  its rows never clash with each other, but they clash with everything else exactly like a normal slot.
-- =====================================================================
alter table public.fixed_slots add column if not exists combined_group_id uuid;
comment on column public.fixed_slots.combined_group_id is
  'rows with the same value are ONE combined session covering several classes (null = a normal single-class slot)';
create index if not exists fixed_slots_group_idx on public.fixed_slots (combined_group_id) where combined_group_id is not null;

-- a teacher cannot be in two different places at once - but the rows of the same combined session are ONE place
alter table public.fixed_slots drop constraint if exists fixed_slots_no_overlap_teacher;
alter table public.fixed_slots add constraint fixed_slots_no_overlap_teacher
  exclude using gist (
    term_id with =, teacher_id with =, day with =,
    int4range(start_period::int, (start_period + length)::int) with &&,
    (coalesce(combined_group_id, id)) with <>
  ) where (teacher_id is not null);
