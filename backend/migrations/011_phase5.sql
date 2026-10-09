-- =====================================================================
--  TimeTablePro - Migration 011  (PHASE 5: absence / leave / substitution / one-day adjustments)
--
--  leave_requests        a teacher asks for leave (date or range) -> HOD approves/rejects
--  teacher_absences      one teacher is absent on one date (self / HOD / admin / an approved leave / AI)
--  substitutions         one need: one absent teacher's one time-slot on one date that must be covered
--  substitution_offers   auto popup offers to candidate teachers; FIRST to accept wins
--  day_adjustments       move / cancel / swap ONE class's lecture for ONE date (the weekly base stays unchanged)
--
--  Everything is written by the backend only; browsers may only READ what belongs to them.
-- =====================================================================

-- ---------------------------------------------------------------------
-- leave requests
-- ---------------------------------------------------------------------
create table public.leave_requests (
  id             uuid primary key default gen_random_uuid(),
  school_id      uuid not null references public.schools (id) on delete cascade,
  term_id        uuid references public.academic_terms (id) on delete set null,
  teacher_id     uuid not null references public.profiles (id) on delete cascade,
  date_from      date not null,
  date_to        date not null,
  reason         text check (reason is null or char_length(reason) <= 300),
  status         text not null default 'pending' check (status in ('pending', 'approved', 'rejected', 'cancelled')),
  decided_by     uuid,
  decided_at     timestamptz,
  decision_note  text check (decision_note is null or char_length(decision_note) <= 300),
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  check (date_to >= date_from and date_to - date_from <= 120)
);
create index leave_requests_school_idx on public.leave_requests (school_id, status);
create index leave_requests_teacher_idx on public.leave_requests (teacher_id, date_from);
create trigger leave_requests_updated_at before update on public.leave_requests
  for each row execute function private.set_updated_at();

-- ---------------------------------------------------------------------
-- a teacher is absent on a specific date
-- ---------------------------------------------------------------------
create table public.teacher_absences (
  id            uuid primary key default gen_random_uuid(),
  school_id     uuid not null references public.schools (id) on delete cascade,
  term_id       uuid not null references public.academic_terms (id) on delete cascade,
  teacher_id    uuid not null references public.profiles (id) on delete cascade,
  absence_date  date not null,
  reason        text check (reason is null or char_length(reason) <= 300),
  source        text not null default 'self' check (source in ('self', 'hod', 'admin', 'leave', 'ai')),
  leave_id      uuid references public.leave_requests (id) on delete set null,
  created_by    uuid,
  created_at    timestamptz not null default now(),
  unique (teacher_id, absence_date)
);
create index teacher_absences_school_idx on public.teacher_absences (school_id, absence_date);

-- ---------------------------------------------------------------------
-- one substitution need = one absent teacher's one time-slot on one date
-- ---------------------------------------------------------------------
create table public.substitutions (
  id                 uuid primary key default gen_random_uuid(),
  school_id          uuid not null references public.schools (id) on delete cascade,
  term_id            uuid not null references public.academic_terms (id) on delete cascade,
  absence_id         uuid references public.teacher_absences (id) on delete cascade,
  lecture_date       date not null,
  weekday            smallint not null check (weekday between 1 and 7),
  start_period       smallint not null check (start_period between 1 and 30),
  length             smallint not null check (length between 1 and 8),
  semester_ids       uuid[] not null default '{}',          -- classes attending (several for a combined session)
  subject_ids        uuid[] not null default '{}',          -- the absent teacher's subject(s) at that slot
  parallel_key       uuid,
  label              text not null check (char_length(label) between 1 and 200),
  absent_teacher_id  uuid not null references public.profiles (id) on delete cascade,
  status             text not null default 'open' check (status in ('open', 'offering', 'filled', 'unfilled', 'cancelled')),
  mode               text check (mode is null or mode in ('manual', 'auto')),
  substitute_id      uuid references public.profiles (id) on delete set null,
  filled_at          timestamptz,
  filled_via         text check (filled_via is null or filled_via in ('manual', 'offer')),
  offer_deadline     timestamptz,                           -- when the current auto round ends
  note               text check (note is null or char_length(note) <= 300),
  created_by         uuid,
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now(),
  unique (absence_id, start_period)                          -- at most one sub per absent slot
);
create index substitutions_school_date_idx on public.substitutions (school_id, lecture_date);
create index substitutions_open_idx on public.substitutions (status) where status in ('open', 'offering');
create index substitutions_substitute_idx on public.substitutions (substitute_id) where substitute_id is not null;
create index substitutions_absent_idx on public.substitutions (absent_teacher_id);
create trigger substitutions_updated_at before update on public.substitutions
  for each row execute function private.set_updated_at();

-- ---------------------------------------------------------------------
-- auto-offers: the top few free teachers are asked; the FIRST to accept wins
-- ---------------------------------------------------------------------
create table public.substitution_offers (
  id               uuid primary key default gen_random_uuid(),
  substitution_id  uuid not null references public.substitutions (id) on delete cascade,
  school_id        uuid not null references public.schools (id) on delete cascade,
  teacher_id       uuid not null references public.profiles (id) on delete cascade,
  rank             smallint not null default 1,
  score            numeric(6, 2) not null default 0,
  reasons          jsonb not null default '[]'::jsonb,
  status           text not null default 'pending' check (status in ('pending', 'accepted', 'declined', 'expired', 'superseded')),
  responded_at     timestamptz,
  expires_at       timestamptz not null,
  created_at       timestamptz not null default now(),
  unique (substitution_id, teacher_id)
);
create index substitution_offers_teacher_idx on public.substitution_offers (teacher_id, status);
create index substitution_offers_sub_idx on public.substitution_offers (substitution_id);

-- ---------------------------------------------------------------------
-- one-day adjustments: move / cancel ONE class's lecture for ONE date (base week unchanged).
-- A SWAP is stored as two 'move' rows that share swap_group.
-- ---------------------------------------------------------------------
create table public.day_adjustments (
  id             uuid primary key default gen_random_uuid(),
  school_id      uuid not null references public.schools (id) on delete cascade,
  term_id        uuid not null references public.academic_terms (id) on delete cascade,
  lecture_date   date not null,
  kind           text not null check (kind in ('move', 'cancel')),
  semester_ids   uuid[] not null default '{}',
  orig_start     smallint not null check (orig_start between 1 and 30),
  length         smallint not null check (length between 1 and 8),
  new_start      smallint check (new_start is null or new_start between 1 and 30),
  subject_ids    uuid[] not null default '{}',
  parallel_key   uuid,
  label          text not null check (char_length(label) between 1 and 200),
  teacher_id     uuid,
  co_teacher_id  uuid,
  swap_group     uuid,
  reason         text check (reason is null or char_length(reason) <= 300),
  created_by     uuid,
  created_at     timestamptz not null default now(),
  check (kind = 'cancel' or new_start is not null)
);
create index day_adjustments_school_date_idx on public.day_adjustments (school_id, lecture_date);

-- ---------------------------------------------------------------------
-- Row Level Security.  admin = everything, HOD = own school, teacher = only what is theirs. Writes are backend-only.
-- ---------------------------------------------------------------------
alter table public.leave_requests        enable row level security;
alter table public.teacher_absences      enable row level security;
alter table public.substitutions         enable row level security;
alter table public.substitution_offers   enable row level security;
alter table public.day_adjustments       enable row level security;

create policy leave_requests_select on public.leave_requests for select to authenticated
  using (
    (select private.is_admin())
    or ((select private.current_app_role()) = 'hod' and school_id = (select private.current_school_id()))
    or teacher_id = (select auth.uid())
  );
create policy teacher_absences_select on public.teacher_absences for select to authenticated
  using (
    (select private.is_admin())
    or ((select private.current_app_role()) = 'hod' and school_id = (select private.current_school_id()))
    or teacher_id = (select auth.uid())
  );
create policy substitutions_select on public.substitutions for select to authenticated
  using (
    (select private.is_admin())
    or ((select private.current_app_role()) = 'hod' and school_id = (select private.current_school_id()))
    or absent_teacher_id = (select auth.uid()) or substitute_id = (select auth.uid())
  );
create policy substitution_offers_select on public.substitution_offers for select to authenticated
  using (
    (select private.is_admin())
    or ((select private.current_app_role()) = 'hod' and school_id = (select private.current_school_id()))
    or teacher_id = (select auth.uid())
  );
create policy day_adjustments_select on public.day_adjustments for select to authenticated
  using (
    (select private.is_admin())
    or ((select private.current_app_role()) = 'hod' and school_id = (select private.current_school_id()))
    or teacher_id = (select auth.uid()) or co_teacher_id = (select auth.uid())
  );

revoke all on public.leave_requests, public.teacher_absences, public.substitutions, public.substitution_offers,
              public.day_adjustments from anon, authenticated;
grant select on public.leave_requests, public.teacher_absences, public.substitutions, public.substitution_offers,
                public.day_adjustments to authenticated;
grant all on public.leave_requests, public.teacher_absences, public.substitutions, public.substitution_offers,
             public.day_adjustments to service_role;
