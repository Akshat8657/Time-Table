-- =====================================================================
--  TimeTablePro - Migration 002 (Phase 1: data-setup screens)
--
--  New tables: academic_terms, courses, semesters, term_semesters, college_settings, school_timing,
--              subjects, elective_groups, teacher_subjects, assignments, fixed_slots,
--              calendar_entries, calendar_entry_classes, profile_photos, audit_log
--
--  Rules (same as Phase 0):
--    * Row Level Security is ON for every table, nothing for "anon".
--    * Logged-in users can only READ (their own school's rows; the admin reads everything).
--    * ALL writes go through the Python backend (which checks the role and the school).
--    * The database itself also refuses nonsense: same-school links are enforced with composite
--      foreign keys, a teacher cannot be assigned a subject he cannot teach, locked slots cannot overlap.
-- =====================================================================

create extension if not exists btree_gist with schema extensions;

-- ---------------------------------------------------------------------
-- RLS helper: "may this person see rows of school X?"  (admin: all schools; others: only their own)
-- ---------------------------------------------------------------------
create or replace function private.can_see_school(sid uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select (select private.is_admin()) or (sid is not null and sid = (select private.current_school_id()))
$$;
revoke all on function private.can_see_school(uuid) from public, anon;
grant execute on function private.can_see_school(uuid) to authenticated, service_role;

-- ---------------------------------------------------------------------
-- profiles: visiting-faculty details (Section 14 / 17)
-- ---------------------------------------------------------------------
alter table public.profiles add column if not exists visiting_days smallint[] not null default '{}';
alter table public.profiles add column if not exists max_weekly_hours smallint;
alter table public.profiles drop constraint if exists profiles_visiting_days_valid;
alter table public.profiles add constraint profiles_visiting_days_valid
  check (visiting_days <@ array[1,2,3,4,5,6]::smallint[] and cardinality(visiting_days) <= 6);
alter table public.profiles drop constraint if exists profiles_max_weekly_hours_valid;
alter table public.profiles add constraint profiles_max_weekly_hours_valid
  check (max_weekly_hours is null or max_weekly_hours between 1 and 60);
-- target for composite "same school" foreign keys
create unique index if not exists profiles_id_school_unique on public.profiles (id, school_id);

-- a normal logged-in user can never change these (the backend does it for HOD / admin)
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
       or new.visiting_days     is distinct from old.visiting_days
       or new.max_weekly_hours  is distinct from old.max_weekly_hours
       or new.must_change_password is distinct from old.must_change_password then
      raise exception 'You are not allowed to change these fields.' using errcode = '42501';
    end if;
  end if;
  return new;
end;
$$;

-- ---------------------------------------------------------------------
-- 1) academic terms (odd / even + year). Exactly one is active; the rest stay as history.
-- ---------------------------------------------------------------------
create table public.academic_terms (
  id            uuid primary key default gen_random_uuid(),
  kind          text not null check (kind in ('odd', 'even')),
  year          smallint not null check (year between 2000 and 2100),
  is_active     boolean not null default false,
  activated_at  timestamptz,
  activated_by  uuid,
  created_at    timestamptz not null default now(),
  created_by    uuid,
  unique (kind, year)
);
create unique index academic_terms_one_active on public.academic_terms ((true)) where is_active;
comment on table public.academic_terms is 'Odd 2026, Even 2027 ... only one is active at a time; old ones stay as history.';

-- ---------------------------------------------------------------------
-- 2) courses and semesters ("class" = course + semester; divisions can be added later)
-- ---------------------------------------------------------------------
create table public.courses (
  id             uuid primary key default gen_random_uuid(),
  school_id      uuid not null references public.schools (id) on delete restrict,
  name           text not null check (char_length(btrim(name)) between 1 and 120),
  code           text check (code is null or char_length(code) <= 20),
  num_semesters  smallint not null check (num_semesters between 1 and 12),
  is_active      boolean not null default true,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  unique (id, school_id)
);
create unique index courses_school_name_unique on public.courses (school_id, lower(btrim(name)));
create index courses_school_idx on public.courses (school_id);

create table public.semesters (
  id                  uuid primary key default gen_random_uuid(),
  school_id           uuid not null,
  course_id           uuid not null,
  number              smallint not null check (number between 1 and 12),
  early_release_time  time,                                  -- null = runs until the college closing time
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  foreign key (course_id, school_id) references public.courses (id, school_id) on delete cascade,
  unique (course_id, number),
  unique (id, school_id)
);
create index semesters_school_idx on public.semesters (school_id);

-- semester start / end dates for each term (Section 12)
create table public.term_semesters (
  term_id      uuid not null references public.academic_terms (id) on delete cascade,
  semester_id  uuid not null,
  school_id    uuid not null,
  start_date   date not null,
  end_date     date not null,
  updated_at   timestamptz not null default now(),
  updated_by   uuid,
  primary key (term_id, semester_id),
  check (end_date > start_date),
  foreign key (semester_id, school_id) references public.semesters (id, school_id) on delete cascade
);
create index term_semesters_school_idx on public.term_semesters (school_id);

-- ---------------------------------------------------------------------
-- 3) timing (Section 7)
-- ---------------------------------------------------------------------
create table public.college_settings (
  id               boolean primary key default true check (id),     -- exactly ONE row
  college_start    time not null default '10:00',
  college_end      time not null default '17:00',
  lecture_minutes  smallint not null default 60 check (lecture_minutes between 15 and 180),
  break_minutes    smallint not null default 5 check (break_minutes between 0 and 30),
  days_per_week    smallint not null default 5 check (days_per_week in (5, 6)),   -- 5 = Mon-Fri, 6 = Mon-Sat
  updated_at       timestamptz not null default now(),
  updated_by       uuid,
  check (college_end > college_start)
);

create table public.school_timing (
  school_id    uuid primary key references public.schools (id) on delete cascade,
  lunch_start  time,
  lunch_end    time,
  updated_at   timestamptz not null default now(),
  updated_by   uuid,
  check ((lunch_start is null and lunch_end is null) or (lunch_start is not null and lunch_end is not null and lunch_end > lunch_start))
);

-- ---------------------------------------------------------------------
-- 4) subjects, elective groups, who-can-teach-what, assignments (Sections 8, 9, 17)
-- ---------------------------------------------------------------------
create table public.subjects (
  id                 uuid primary key default gen_random_uuid(),
  school_id          uuid not null,
  semester_id        uuid not null,
  name               text not null check (char_length(btrim(name)) between 1 and 150),
  code               text not null check (char_length(btrim(code)) between 1 and 30),
  credits            smallint not null check (credits between 1 and 30),            -- = minimum lectures per week
  semester_hours     smallint not null check (semester_hours between 1 and 400),
  is_lab             boolean not null default false,
  is_elective        boolean not null default false,
  elective_group_id  uuid,
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now(),
  created_by         uuid,
  foreign key (semester_id, school_id) references public.semesters (id, school_id) on delete cascade,
  unique (id, school_id),
  unique (id, semester_id),
  check (elective_group_id is null or is_elective)
);
create unique index subjects_school_code_unique on public.subjects (school_id, lower(btrim(code)));
create index subjects_semester_idx on public.subjects (semester_id);

create table public.elective_groups (
  id           uuid primary key default gen_random_uuid(),
  school_id    uuid not null,
  semester_id  uuid not null,
  name         text not null check (char_length(btrim(name)) between 1 and 60),
  created_at   timestamptz not null default now(),
  foreign key (semester_id, school_id) references public.semesters (id, school_id) on delete cascade,
  unique (id, semester_id)
);
create unique index elective_groups_name_unique on public.elective_groups (semester_id, lower(btrim(name)));

-- an elective can only join a group of ITS OWN semester; deleting a group just un-groups its subjects
alter table public.subjects
  add constraint subjects_group_same_semester
  foreign key (elective_group_id, semester_id) references public.elective_groups (id, semester_id)
  on delete set null (elective_group_id);

create table public.teacher_subjects (          -- "subjects this teacher can teach" (Section 17)
  teacher_id  uuid not null,
  subject_id  uuid not null,
  school_id   uuid not null,
  created_at  timestamptz not null default now(),
  primary key (teacher_id, subject_id),
  foreign key (teacher_id, school_id) references public.profiles (id, school_id) on delete cascade,
  foreign key (subject_id, school_id) references public.subjects (id, school_id) on delete cascade
);
create index teacher_subjects_subject_idx on public.teacher_subjects (subject_id);

create table public.assignments (               -- teacher <-> subject (<-> class) for ONE term
  id          uuid primary key default gen_random_uuid(),
  school_id   uuid not null,
  term_id     uuid not null references public.academic_terms (id) on delete cascade,
  subject_id  uuid not null,
  teacher_id  uuid not null,
  created_at  timestamptz not null default now(),
  created_by  uuid,
  unique (term_id, subject_id),
  -- hard rule: a teacher can ONLY be assigned a subject he is allowed to teach
  foreign key (teacher_id, subject_id) references public.teacher_subjects (teacher_id, subject_id),
  foreign key (subject_id, school_id) references public.subjects (id, school_id) on delete cascade
);
create index assignments_teacher_idx on public.assignments (teacher_id);

-- ---------------------------------------------------------------------
-- 5) fixed (locked) slots - PE / SOL double blocks and anything else (Section 8)
-- ---------------------------------------------------------------------
create table public.fixed_slots (
  id            uuid primary key default gen_random_uuid(),
  school_id     uuid not null,
  term_id       uuid not null references public.academic_terms (id) on delete cascade,
  semester_id   uuid not null,
  day           smallint not null check (day between 1 and 6),              -- 1 = Monday ... 6 = Saturday
  start_period  smallint not null check (start_period between 1 and 30),
  length        smallint not null check (length between 1 and 8),
  kind          text not null check (kind in ('PE', 'SOL', 'OTHER')),
  label         text not null check (char_length(btrim(label)) between 1 and 80),
  subject_id    uuid,
  teacher_id    uuid,
  note          text check (note is null or char_length(note) <= 300),
  created_at    timestamptz not null default now(),
  created_by    uuid,
  check (kind = 'OTHER' or length = 2),                                      -- PE and SOL are ALWAYS a double block
  foreign key (semester_id, school_id) references public.semesters (id, school_id) on delete cascade,
  foreign key (subject_id, school_id) references public.subjects (id, school_id) on delete set null (subject_id),
  foreign key (teacher_id, school_id) references public.profiles (id, school_id) on delete set null (teacher_id),
  -- one class cannot have two locked things at the same time ...
  constraint fixed_slots_no_overlap_class
    exclude using gist (term_id with =, semester_id with =, day with =, int4range(start_period::int, (start_period + length)::int) with &&),
  -- ... and one teacher cannot be in two locked places at the same time
  constraint fixed_slots_no_overlap_teacher
    exclude using gist (term_id with =, teacher_id with =, day with =, int4range(start_period::int, (start_period + length)::int) with &&)
    where (teacher_id is not null)
);
create index fixed_slots_school_idx on public.fixed_slots (school_id, term_id);

-- ---------------------------------------------------------------------
-- 6) holidays and events (Section 12). school_id NULL = college-wide holiday.
-- ---------------------------------------------------------------------
create table public.calendar_entries (
  id            uuid primary key default gen_random_uuid(),
  school_id     uuid references public.schools (id) on delete cascade,
  kind          text not null check (kind in ('holiday', 'event')),
  title         text not null check (char_length(btrim(title)) between 1 and 150),
  date_from     date not null,
  date_to       date not null,
  full_day      boolean not null default true,
  start_time    time,
  end_time      time,
  note          text check (note is null or char_length(note) <= 300),
  is_preloaded  boolean not null default false,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  created_by    uuid,
  check (date_to >= date_from and date_to - date_from <= 60),
  check (full_day or (start_time is not null and end_time is not null and end_time > start_time)),
  check (school_id is not null or (kind = 'holiday' and full_day)),         -- college-wide entries are full-day holidays
  unique (id, school_id)
);
create index calendar_entries_dates_idx on public.calendar_entries (date_from, date_to);
create index calendar_entries_school_idx on public.calendar_entries (school_id);
create unique index calendar_preloaded_unique on public.calendar_entries (lower(title), date_from) where is_preloaded and school_id is null;

create table public.calendar_entry_classes (    -- which classes an event is for (none listed = every class of the school)
  entry_id     uuid not null,
  semester_id  uuid not null,
  school_id    uuid not null,
  primary key (entry_id, semester_id),
  foreign key (entry_id, school_id) references public.calendar_entries (id, school_id) on delete cascade,
  foreign key (semester_id, school_id) references public.semesters (id, school_id) on delete cascade
);

-- ---------------------------------------------------------------------
-- 7) profile photos (small JPEG kept in the database) and the audit log
-- ---------------------------------------------------------------------
create table public.profile_photos (
  user_id       uuid primary key references public.profiles (id) on delete cascade,
  content_type  text not null check (content_type = 'image/jpeg'),
  data          bytea not null check (octet_length(data) <= 300000),
  updated_at    timestamptz not null default now()
);

create table public.audit_log (
  id          bigint generated always as identity primary key,
  at          timestamptz not null default now(),
  actor_id    uuid,                         -- no foreign key on purpose: the history must survive account removal
  actor_email text,
  actor_role  text,
  school_id   uuid,
  action      text not null,                -- create / update / delete / deactivate / reactivate / reset_password / import ...
  entity      text not null,                -- teacher / subject / assignment / fixed_slot / holiday ...
  entity_id   text,
  summary     text not null,
  details     jsonb,
  via         text not null default 'ui' check (via in ('ui', 'ai', 'system'))
);
create index audit_log_at_idx on public.audit_log (at desc);
create index audit_log_school_idx on public.audit_log (school_id, at desc);

-- ---------------------------------------------------------------------
-- triggers: keep updated_at fresh
-- ---------------------------------------------------------------------
create trigger courses_set_updated_at          before update on public.courses          for each row execute function private.set_updated_at();
create trigger semesters_set_updated_at        before update on public.semesters        for each row execute function private.set_updated_at();
create trigger term_semesters_set_updated_at   before update on public.term_semesters   for each row execute function private.set_updated_at();
create trigger college_settings_set_updated_at before update on public.college_settings for each row execute function private.set_updated_at();
create trigger school_timing_set_updated_at    before update on public.school_timing    for each row execute function private.set_updated_at();
create trigger subjects_set_updated_at         before update on public.subjects         for each row execute function private.set_updated_at();
create trigger calendar_entries_set_updated_at before update on public.calendar_entries for each row execute function private.set_updated_at();

-- ---------------------------------------------------------------------
-- Row Level Security: everybody may only READ what belongs to their own school (admin: everything).
-- There are NO write policies and NO write grants: all changes go through the backend.
-- ---------------------------------------------------------------------
alter table public.academic_terms          enable row level security;
alter table public.courses                 enable row level security;
alter table public.semesters               enable row level security;
alter table public.term_semesters          enable row level security;
alter table public.college_settings        enable row level security;
alter table public.school_timing           enable row level security;
alter table public.subjects                enable row level security;
alter table public.elective_groups         enable row level security;
alter table public.teacher_subjects        enable row level security;
alter table public.assignments             enable row level security;
alter table public.fixed_slots             enable row level security;
alter table public.calendar_entries        enable row level security;
alter table public.calendar_entry_classes  enable row level security;
alter table public.profile_photos          enable row level security;
alter table public.audit_log               enable row level security;

-- college-wide information: every logged-in person may read it
create policy academic_terms_select   on public.academic_terms   for select to authenticated using (true);
create policy college_settings_select on public.college_settings for select to authenticated using (true);

-- school information: own school only (admin: all)
create policy courses_select         on public.courses                for select to authenticated using ((select private.can_see_school(school_id)));
create policy semesters_select       on public.semesters              for select to authenticated using ((select private.can_see_school(school_id)));
create policy term_semesters_select  on public.term_semesters         for select to authenticated using ((select private.can_see_school(school_id)));
create policy school_timing_select   on public.school_timing          for select to authenticated using ((select private.can_see_school(school_id)));
create policy subjects_select        on public.subjects               for select to authenticated using ((select private.can_see_school(school_id)));
create policy elective_groups_select on public.elective_groups        for select to authenticated using ((select private.can_see_school(school_id)));
create policy teacher_subjects_select on public.teacher_subjects      for select to authenticated using ((select private.can_see_school(school_id)));
create policy assignments_select     on public.assignments            for select to authenticated using ((select private.can_see_school(school_id)));
create policy fixed_slots_select     on public.fixed_slots            for select to authenticated using ((select private.can_see_school(school_id)));
create policy calendar_classes_select on public.calendar_entry_classes for select to authenticated using ((select private.can_see_school(school_id)));
-- calendar: college-wide holidays are for everybody, school entries only for that school
create policy calendar_entries_select on public.calendar_entries for select to authenticated
  using (school_id is null or (select private.can_see_school(school_id)));

-- audit log: admin only.   profile_photos: no policy at all (backend only)
create policy audit_log_select on public.audit_log for select to authenticated using ((select private.is_admin()));

-- ---------------------------------------------------------------------
-- GRANTs: authenticated = read-only. service_role = everything (it bypasses RLS anyway).
-- ---------------------------------------------------------------------
revoke all on
  public.academic_terms, public.courses, public.semesters, public.term_semesters, public.college_settings,
  public.school_timing, public.subjects, public.elective_groups, public.teacher_subjects, public.assignments,
  public.fixed_slots, public.calendar_entries, public.calendar_entry_classes, public.profile_photos, public.audit_log
from anon, authenticated;

grant select on
  public.academic_terms, public.courses, public.semesters, public.term_semesters, public.college_settings,
  public.school_timing, public.subjects, public.elective_groups, public.teacher_subjects, public.assignments,
  public.fixed_slots, public.calendar_entries, public.calendar_entry_classes, public.audit_log
to authenticated;

grant all on
  public.academic_terms, public.courses, public.semesters, public.term_semesters, public.college_settings,
  public.school_timing, public.subjects, public.elective_groups, public.teacher_subjects, public.assignments,
  public.fixed_slots, public.calendar_entries, public.calendar_entry_classes, public.profile_photos, public.audit_log
to service_role;

-- =====================================================================
-- SEED DATA
-- =====================================================================

-- School of IT: its real courses and semesters (Section 4)
insert into public.courses (school_id, name, code, num_semesters)
select s.id, v.name, v.code, v.n
from public.schools s
cross join (values ('B.Sc. IT', 'BSCIT', 7), ('M.Sc. IT', 'MSCIT', 3), ('M.Sc. AI', 'MSCAI', 3)) as v(name, code, n)
where lower(btrim(s.name)) = 'school of it'
on conflict do nothing;

insert into public.semesters (school_id, course_id, number)
select c.school_id, c.id, g.n
from public.courses c
cross join lateral generate_series(1, c.num_semesters) as g(n)
on conflict do nothing;

-- college timing defaults (the plan's example: 10:00-17:00, 1 hour lectures, 5 minute break, Mon-Fri)
insert into public.college_settings (id) values (true) on conflict do nothing;

-- School of IT starts with a normal lunch break; the HOD can change it
insert into public.school_timing (school_id, lunch_start, lunch_end)
select s.id, '13:00', '13:45' from public.schools s where lower(btrim(s.name)) = 'school of it'
on conflict do nothing;

-- Standard India holiday list = the official Central Government gazetted holidays.
--   2026: DoPT order F.No.12/2/2023-JCA dated 03-Jul-2025      2027: DoPT order dated 16-Jul-2026
-- Festival dates follow the lunar calendar, so the admin can edit / delete any of them.
insert into public.calendar_entries (school_id, kind, title, date_from, date_to, full_day, note, is_preloaded)
select null::uuid, 'holiday', v.title, v.d::date, v.d::date, true,
       'Official gazetted holiday ' || extract(year from v.d::date)::int || ' (DoPT). Festival dates follow the lunar calendar - check locally.',
       true
from (values
  ('Republic Day',                 '2026-01-26'),
  ('Holi',                         '2026-03-04'),
  ('Id-ul-Fitr',                   '2026-03-21'),
  ('Ram Navami',                   '2026-03-26'),
  ('Mahavir Jayanti',              '2026-03-31'),
  ('Good Friday',                  '2026-04-03'),
  ('Buddha Purnima',               '2026-05-01'),
  ('Id-ul-Zuha (Bakrid)',          '2026-05-27'),
  ('Muharram',                     '2026-06-26'),
  ('Independence Day',             '2026-08-15'),
  ('Milad-un-Nabi (Id-e-Milad)',   '2026-08-26'),
  ('Janmashtami',                  '2026-09-04'),
  ('Mahatma Gandhi Jayanti',       '2026-10-02'),
  ('Dussehra (Vijaya Dashami)',    '2026-10-20'),
  ('Diwali (Deepavali)',           '2026-11-08'),
  ('Guru Nanak Jayanti',           '2026-11-24'),
  ('Christmas Day',                '2026-12-25'),
  ('Republic Day',                 '2027-01-26'),
  ('Id-ul-Fitr',                   '2027-03-10'),
  ('Holi',                         '2027-03-23'),
  ('Good Friday',                  '2027-03-26'),
  ('Ram Navami',                   '2027-04-15'),
  ('Mahavir Jayanti',              '2027-04-19'),
  ('Id-ul-Zuha (Bakrid)',          '2027-05-17'),
  ('Buddha Purnima',               '2027-05-20'),
  ('Muharram',                     '2027-06-16'),
  ('Independence Day',             '2027-08-15'),
  ('Milad-un-Nabi (Id-e-Milad)',   '2027-08-15'),
  ('Janmashtami',                  '2027-08-25'),
  ('Mahatma Gandhi Jayanti',       '2027-10-02'),
  ('Dussehra (Vijaya Dashami)',    '2027-10-09'),
  ('Diwali (Deepavali)',           '2027-10-29'),
  ('Guru Nanak Jayanti',           '2027-11-14'),
  ('Christmas Day',                '2027-12-25')
) as v(title, d)
on conflict do nothing;
