-- =====================================================================
--  TimeTablePro - Migration 009  (PHASE 4: publish, views, notifications, semester hours, compensation)
--
--  0) subjects.weekly_lectures     optional override of "required lectures a week" (empty = the credits)
--  1) publications / published_entries / publish_events
--                                   the LIVE timetable: a snapshot of every class's primary draft (+ the locked slots and
--                                   the teachers at that moment). Teachers read ONLY these tables, never the drafts.
--  2) notifications / push_subscriptions
--                                   in-app bell (one row per person per event) + browser Web Push endpoints
--  3) extra_lectures                one-off compensation lectures on specific dates (holiday compensation)
-- =====================================================================

-- ---------------------------------------------------------------------
-- 0) weekly lectures override
-- ---------------------------------------------------------------------
alter table public.subjects add column if not exists weekly_lectures smallint;
alter table public.subjects drop constraint if exists subjects_weekly_lectures_valid;
alter table public.subjects add constraint subjects_weekly_lectures_valid
  check (weekly_lectures is null or weekly_lectures between 1 and 30);
comment on column public.subjects.weekly_lectures is
  'Optional: lectures required per week. NULL = same as credits. Credits stay as information only.';

-- ---------------------------------------------------------------------
-- 1) the published (live) timetable
-- ---------------------------------------------------------------------
create table public.publications (
  id                 uuid primary key default gen_random_uuid(),
  school_id          uuid not null references public.schools (id) on delete cascade,
  term_id            uuid not null references public.academic_terms (id) on delete cascade,
  semester_id        uuid not null,
  version            integer not null default 1 check (version >= 1),
  source_draft_id    uuid references public.timetable_drafts (id) on delete set null,
  source_draft_name  text,
  published_at       timestamptz not null default now(),
  published_by       uuid,
  unique (term_id, semester_id),
  unique (id, school_id),
  foreign key (semester_id, school_id) references public.semesters (id, school_id) on delete cascade
);
create index publications_school_idx on public.publications (school_id, term_id);

-- one row = one lecture (kind 'lecture') or one locked slot (kind 'fixed': PE / SOL / combined / other) of ONE class.
-- Rows that happen together share group_key: the options of an elective group (parallel) or the classes of a combined session.
create table public.published_entries (
  id              uuid primary key default gen_random_uuid(),
  publication_id  uuid not null,
  school_id       uuid not null,
  term_id         uuid not null,
  semester_id     uuid not null,
  kind            text not null check (kind in ('lecture', 'fixed')),
  subject_id      uuid,
  fixed_kind      text check (fixed_kind is null or fixed_kind in ('PE', 'SOL', 'OTHER')),
  label           text not null check (char_length(label) between 1 and 160),
  day             smallint not null check (day between 1 and 6),
  start_period    smallint not null check (start_period between 1 and 30),
  length          smallint not null check (length between 1 and 8),
  group_key       uuid,
  teacher_id      uuid,
  co_teacher_id   uuid,
  combined_with   text check (combined_with is null or char_length(combined_with) <= 300),
  foreign key (publication_id, school_id) references public.publications (id, school_id) on delete cascade,
  foreign key (subject_id, school_id) references public.subjects (id, school_id) on delete cascade,
  foreign key (teacher_id, school_id) references public.profiles (id, school_id) on delete set null (teacher_id),
  foreign key (co_teacher_id, school_id) references public.profiles (id, school_id) on delete set null (co_teacher_id),
  check ((kind = 'lecture' and subject_id is not null) or kind = 'fixed'),
  -- one class has one thing at a time - except the rows of one parallel placement / combined session
  constraint published_entries_no_overlap
    exclude using gist (
      publication_id with =, day with =,
      int4range(start_period::int, (start_period + length)::int) with &&,
      (coalesce(group_key, id)) with <>
    )
);
create index published_entries_publication_idx on public.published_entries (publication_id);
create index published_entries_teacher_idx on public.published_entries (teacher_id) where teacher_id is not null;
create index published_entries_co_teacher_idx on public.published_entries (co_teacher_id) where co_teacher_id is not null;
create index published_entries_subject_idx on public.published_entries (subject_id) where subject_id is not null;
create index published_entries_school_idx on public.published_entries (school_id, term_id);

-- every Publish click is one event (a log for the HOD; also the id behind "one notification per person per event")
create table public.publish_events (
  id            uuid primary key default gen_random_uuid(),
  school_id     uuid not null references public.schools (id) on delete cascade,
  term_id       uuid not null references public.academic_terms (id) on delete cascade,
  number        integer not null check (number >= 1),
  scope         text not null check (scope in ('all', 'class')),
  published_by  uuid,
  published_at  timestamptz not null default now(),
  summary       jsonb not null default '{}'::jsonb,
  notified      integer not null default 0,
  unique (school_id, term_id, number)
);
create index publish_events_school_idx on public.publish_events (school_id, term_id, published_at desc);

-- ---------------------------------------------------------------------
-- 2) notifications (bell) and Web Push subscriptions
-- ---------------------------------------------------------------------
create table public.notifications (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references public.profiles (id) on delete cascade,
  school_id   uuid references public.schools (id) on delete cascade,
  kind        text not null check (char_length(kind) between 1 and 40),
  title       text not null check (char_length(title) between 1 and 160),
  body        text check (body is null or char_length(body) <= 800),
  link        text check (link is null or char_length(link) <= 200),
  data        jsonb not null default '{}'::jsonb,
  dedupe_key  text check (dedupe_key is null or char_length(dedupe_key) <= 120),
  is_read     boolean not null default false,
  read_at     timestamptz,
  created_at  timestamptz not null default now()
);
-- "one notification per person per event": the same event can never reach the same person twice
create unique index notifications_dedupe_uq on public.notifications (user_id, dedupe_key) where dedupe_key is not null;
create index notifications_user_idx on public.notifications (user_id, created_at desc);
create index notifications_unread_idx on public.notifications (user_id) where not is_read;

create table public.push_subscriptions (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references public.profiles (id) on delete cascade,
  endpoint      text not null unique check (char_length(endpoint) between 10 and 1000),
  p256dh        text not null check (char_length(p256dh) between 10 and 200),
  auth          text not null check (char_length(auth) between 10 and 100),
  user_agent    text check (user_agent is null or char_length(user_agent) <= 300),
  created_at    timestamptz not null default now(),
  last_ok_at    timestamptz,
  failures      smallint not null default 0
);
create index push_subscriptions_user_idx on public.push_subscriptions (user_id);

-- ---------------------------------------------------------------------
-- 3) extra (compensation) lectures: a one-off lecture on one date
-- ---------------------------------------------------------------------
create table public.extra_lectures (
  id             uuid primary key default gen_random_uuid(),
  school_id      uuid not null references public.schools (id) on delete cascade,
  term_id        uuid not null references public.academic_terms (id) on delete cascade,
  semester_id    uuid not null,
  subject_id     uuid not null,
  lecture_date   date not null,
  start_period   smallint not null check (start_period between 1 and 30),
  length         smallint not null check (length between 1 and 8),
  parallel_key   uuid,
  teacher_id     uuid,
  co_teacher_id  uuid,
  reason         text check (reason is null or char_length(reason) <= 300),
  created_by     uuid,
  created_at     timestamptz not null default now(),
  foreign key (semester_id, school_id) references public.semesters (id, school_id) on delete cascade,
  foreign key (subject_id, school_id) references public.subjects (id, school_id) on delete cascade,
  foreign key (teacher_id, school_id) references public.profiles (id, school_id) on delete set null (teacher_id),
  foreign key (co_teacher_id, school_id) references public.profiles (id, school_id) on delete set null (co_teacher_id),
  -- a class has one extra lecture at a time (the options of a parallel group share parallel_key)
  constraint extra_lectures_no_overlap
    exclude using gist (
      term_id with =, semester_id with =, lecture_date with =,
      int4range(start_period::int, (start_period + length)::int) with &&,
      (coalesce(parallel_key, id)) with <>
    )
);
create index extra_lectures_school_idx on public.extra_lectures (school_id, term_id, lecture_date);
create index extra_lectures_subject_idx on public.extra_lectures (subject_id);

-- ---------------------------------------------------------------------
-- RLS. Everything is written by the backend only; browsers may only READ what belongs to them:
--   admin = everything, HOD = own school, a teacher = only the published rows where they teach, own notifications.
-- ---------------------------------------------------------------------
alter table public.publications      enable row level security;
alter table public.published_entries enable row level security;
alter table public.publish_events    enable row level security;
alter table public.notifications     enable row level security;
alter table public.push_subscriptions enable row level security;
alter table public.extra_lectures    enable row level security;

create policy publications_select on public.publications for select to authenticated
  using ((select private.can_see_school(school_id)));
create policy published_entries_select on public.published_entries for select to authenticated
  using (
    (select private.is_admin())
    or ((select private.current_app_role()) = 'hod' and school_id = (select private.current_school_id()))
    or teacher_id = (select auth.uid()) or co_teacher_id = (select auth.uid())
  );
create policy publish_events_select on public.publish_events for select to authenticated
  using ((select private.is_admin()) or ((select private.current_app_role()) = 'hod' and school_id = (select private.current_school_id())));
create policy notifications_select on public.notifications for select to authenticated
  using (user_id = (select auth.uid()));
create policy extra_lectures_select on public.extra_lectures for select to authenticated
  using (
    (select private.is_admin())
    or ((select private.current_app_role()) = 'hod' and school_id = (select private.current_school_id()))
    or teacher_id = (select auth.uid()) or co_teacher_id = (select auth.uid())
  );
-- push_subscriptions: no policy at all = invisible to browsers (the keys inside are secrets)

revoke all on public.publications, public.published_entries, public.publish_events, public.notifications,
              public.push_subscriptions, public.extra_lectures from anon, authenticated;
grant select on public.publications, public.published_entries, public.publish_events, public.notifications,
                public.extra_lectures to authenticated;
grant all on public.publications, public.published_entries, public.publish_events, public.notifications,
             public.push_subscriptions, public.extra_lectures to service_role;
