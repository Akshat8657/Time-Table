-- =====================================================================
--  TimeTablePro - Migration 004 (master plan V8)
--
--   1) Real college timing as the default: 10:00-16:50 (6 periods of 1 hour, 5 min breaks), School of IT lunch 13:10-13:40
--   2) DAY-WISE schedule per semester: each weekday has its own end time AND can be fully OFF (Saturday off, OJT days).
--      This replaces the single semesters.early_release_time.
--   3) Assignments: a primary teacher + an optional CO-TEACHER (both appear on the timetable, clash checks use both)
--   4) Fixed slots: optional co-teacher too
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1) real defaults (only touch rows that still hold the old Phase 1 defaults - never overwrite a value somebody chose)
-- ---------------------------------------------------------------------
alter table public.college_settings alter column college_end set default '16:50';
update public.college_settings set college_end = '16:50' where college_start = '10:00' and college_end = '17:00';

update public.school_timing st
   set lunch_start = '13:10', lunch_end = '13:40'
  from public.schools s
 where s.id = st.school_id and lower(btrim(s.name)) = 'school of it'
   and st.lunch_start = '13:00' and st.lunch_end = '13:45';

-- ---------------------------------------------------------------------
-- 2) day-wise schedule per semester. A missing row = the normal full day (until the college closing time).
-- ---------------------------------------------------------------------
create table public.semester_days (
  semester_id  uuid not null,
  school_id    uuid not null,
  day          smallint not null check (day between 1 and 6),            -- 1 = Monday ... 6 = Saturday
  is_off       boolean not null default false,                           -- no lectures at all on this day
  end_time     time,                                                     -- null = until the college closing time
  updated_at   timestamptz not null default now(),
  updated_by   uuid,
  primary key (semester_id, day),
  foreign key (semester_id, school_id) references public.semesters (id, school_id) on delete cascade,
  check (not is_off or end_time is null)
);
create index semester_days_school_idx on public.semester_days (school_id);
create trigger semester_days_set_updated_at before update on public.semester_days
  for each row execute function private.set_updated_at();

-- keep what was already entered with the old single "early release" time: same end time on every day
insert into public.semester_days (semester_id, school_id, day, end_time)
select sm.id, sm.school_id, d, sm.early_release_time
from public.semesters sm
cross join generate_series(1, 6) d
where sm.early_release_time is not null;

alter table public.semesters drop column early_release_time;

alter table public.semester_days enable row level security;
create policy semester_days_select on public.semester_days for select to authenticated
  using ((select private.can_see_school(school_id)));
revoke all on public.semester_days from anon, authenticated;
grant select on public.semester_days to authenticated;
grant all on public.semester_days to service_role;

-- ---------------------------------------------------------------------
-- 3) co-teacher on assignments (a second teacher who is present for the same lectures)
-- ---------------------------------------------------------------------
alter table public.assignments add column co_teacher_id uuid;
alter table public.assignments
  add constraint assignments_co_teacher_differs check (co_teacher_id is null or co_teacher_id <> teacher_id);
-- the co-teacher must ALSO be allowed to teach that subject
alter table public.assignments
  add constraint assignments_co_teacher_can_teach
  foreign key (co_teacher_id, subject_id) references public.teacher_subjects (teacher_id, subject_id);
create index assignments_co_teacher_idx on public.assignments (co_teacher_id) where co_teacher_id is not null;

-- ---------------------------------------------------------------------
-- 4) co-teacher on locked slots
-- ---------------------------------------------------------------------
alter table public.fixed_slots add column co_teacher_id uuid;
alter table public.fixed_slots
  add constraint fixed_slots_co_teacher_differs check (co_teacher_id is null or co_teacher_id <> teacher_id);
alter table public.fixed_slots
  add constraint fixed_slots_co_teacher_fk
  foreign key (co_teacher_id, school_id) references public.profiles (id, school_id) on delete set null (co_teacher_id);
