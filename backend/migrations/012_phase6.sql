-- =====================================================================
--  TimeTablePro - Migration 012  (PHASE 6: teacher preferences, preference window, reports, audit-log screen)
--
--  teacher_preferences   one row per teacher: prefer first lecture / avoid last lecture / max lectures per day
--  teacher_pref_slots    the weekly grid: only the slots that are NOT plain "available"  (preferred | unavailable)
--  preference_settings   per school: how many slots a teacher may mark "not available" + when the reminder goes out
--  preference_windows    the HOD opens a window with a deadline; teachers can only edit while it is open
--  preference_responses  who has filled (submitted) in which window  -> the live "filled / not filled" list
--  workload_norms        UGC / AICTE weekly teaching-hours norm (college default row + optional per-school override)
--
--  + indexes so the Audit Log screen filters stay fast.
--  Everything is written by the backend only; browsers may only READ what belongs to them.
-- =====================================================================

-- ---------------------------------------------------------------------
-- the teacher's own choices
-- ---------------------------------------------------------------------
create table public.teacher_preferences (
  teacher_id    uuid primary key references public.profiles (id) on delete cascade,
  school_id     uuid not null references public.schools (id) on delete cascade,
  prefer_first  boolean not null default false,
  avoid_last    boolean not null default false,
  max_per_day   smallint check (max_per_day is null or max_per_day between 1 and 12),
  updated_at    timestamptz not null default now(),
  updated_by    uuid
);
create index teacher_preferences_school_idx on public.teacher_preferences (school_id);

create table public.teacher_pref_slots (
  teacher_id  uuid not null references public.profiles (id) on delete cascade,
  school_id   uuid not null references public.schools (id) on delete cascade,
  day         smallint not null check (day between 1 and 6),
  period      smallint not null check (period between 1 and 20),
  kind        text not null check (kind in ('preferred', 'unavailable')),
  primary key (teacher_id, day, period)
);
create index teacher_pref_slots_school_idx on public.teacher_pref_slots (school_id);

-- ---------------------------------------------------------------------
-- school-level rules for preferences
-- ---------------------------------------------------------------------
create table public.preference_settings (
  school_id        uuid primary key references public.schools (id) on delete cascade,
  max_unavailable  smallint not null default 8 check (max_unavailable between 0 and 72),
  reminder_hours   smallint not null default 24 check (reminder_hours between 1 and 168),
  updated_at       timestamptz not null default now(),
  updated_by       uuid
);

-- ---------------------------------------------------------------------
-- the preference window
-- ---------------------------------------------------------------------
create table public.preference_windows (
  id                uuid primary key default gen_random_uuid(),
  school_id         uuid not null references public.schools (id) on delete cascade,
  status            text not null default 'open' check (status in ('open', 'closed')),
  opened_at         timestamptz not null default now(),
  deadline          timestamptz not null,
  closed_at         timestamptz,
  closed_reason     text check (closed_reason is null or closed_reason in ('manual', 'deadline')),
  reminder_sent_at  timestamptz,
  reopened_count    smallint not null default 0,
  note              text check (note is null or char_length(note) <= 300),
  opened_by         uuid,
  closed_by         uuid
);
create index preference_windows_school_idx on public.preference_windows (school_id, opened_at desc);
-- at most ONE open window per school at any time
create unique index preference_windows_one_open on public.preference_windows (school_id) where status = 'open';

create table public.preference_responses (
  window_id     uuid not null references public.preference_windows (id) on delete cascade,
  teacher_id    uuid not null references public.profiles (id) on delete cascade,
  school_id     uuid not null references public.schools (id) on delete cascade,
  submitted_at  timestamptz not null default now(),
  primary key (window_id, teacher_id)
);
create index preference_responses_teacher_idx on public.preference_responses (teacher_id);

-- ---------------------------------------------------------------------
-- workload norm (UGC / AICTE weekly teaching hours)
-- ---------------------------------------------------------------------
create table public.workload_norms (
  id           uuid primary key default gen_random_uuid(),
  school_id    uuid references public.schools (id) on delete cascade,   -- null = the college-wide default
  norm_hours   smallint not null default 16 check (norm_hours between 1 and 60),
  min_hours    smallint not null default 12 check (min_hours between 0 and 60),
  label        text check (label is null or char_length(label) <= 80),
  updated_at   timestamptz not null default now(),
  updated_by   uuid,
  check (min_hours <= norm_hours)
);
create unique index workload_norms_scope on public.workload_norms ((coalesce(school_id, '00000000-0000-0000-0000-000000000000'::uuid)));

-- ---------------------------------------------------------------------
-- audit log: the viewing screen filters by person, action, entity, school and date
-- ---------------------------------------------------------------------
create index if not exists audit_log_actor_idx  on public.audit_log (actor_id, at desc);
create index if not exists audit_log_action_idx on public.audit_log (action, at desc);
create index if not exists audit_log_entity_idx on public.audit_log (entity, at desc);

-- ---------------------------------------------------------------------
-- Row Level Security.  admin = everything, HOD = own school, teacher = only what is theirs. Writes are backend-only.
-- ---------------------------------------------------------------------
alter table public.teacher_preferences  enable row level security;
alter table public.teacher_pref_slots   enable row level security;
alter table public.preference_settings  enable row level security;
alter table public.preference_windows   enable row level security;
alter table public.preference_responses enable row level security;
alter table public.workload_norms       enable row level security;

create policy teacher_preferences_select on public.teacher_preferences for select to authenticated
  using (
    (select private.is_admin())
    or ((select private.current_app_role()) = 'hod' and school_id = (select private.current_school_id()))
    or teacher_id = (select auth.uid())
  );
create policy teacher_pref_slots_select on public.teacher_pref_slots for select to authenticated
  using (
    (select private.is_admin())
    or ((select private.current_app_role()) = 'hod' and school_id = (select private.current_school_id()))
    or teacher_id = (select auth.uid())
  );
create policy preference_settings_select on public.preference_settings for select to authenticated
  using (
    (select private.is_admin())
    or school_id = (select private.current_school_id())
  );
create policy preference_windows_select on public.preference_windows for select to authenticated
  using (
    (select private.is_admin())
    or school_id = (select private.current_school_id())
  );
create policy preference_responses_select on public.preference_responses for select to authenticated
  using (
    (select private.is_admin())
    or ((select private.current_app_role()) = 'hod' and school_id = (select private.current_school_id()))
    or teacher_id = (select auth.uid())
  );
create policy workload_norms_select on public.workload_norms for select to authenticated
  using (
    (select private.is_admin())
    or ((select private.current_app_role()) = 'hod' and (school_id is null or school_id = (select private.current_school_id())))
  );

revoke all on public.teacher_preferences, public.teacher_pref_slots, public.preference_settings, public.preference_windows,
              public.preference_responses, public.workload_norms from anon, authenticated;
grant select on public.teacher_preferences, public.teacher_pref_slots, public.preference_settings, public.preference_windows,
                public.preference_responses, public.workload_norms to authenticated;
grant all on public.teacher_preferences, public.teacher_pref_slots, public.preference_settings, public.preference_windows,
             public.preference_responses, public.workload_norms to service_role;
