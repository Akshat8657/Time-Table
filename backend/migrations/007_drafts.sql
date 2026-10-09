-- =====================================================================
--  TimeTablePro - Migration 007  (PHASE 2: manual timetable drafts)
--  A class may have several named DRAFT timetables. One of them is the class's "primary" draft: the one that
--  counts when other classes are checked for teacher clashes (the draft you plan to publish).
--  Drafts are private to the admin and to the HOD of the school; teachers see nothing until publish (Phase 4).
--  Teachers are NOT stored on a placement: they come live from the Assignment screen (teacher + co-teacher).
-- =====================================================================
create table public.timetable_drafts (
  id          uuid primary key default gen_random_uuid(),
  school_id   uuid not null references public.schools (id) on delete cascade,
  term_id     uuid not null references public.academic_terms (id) on delete cascade,
  semester_id uuid not null,
  name        text not null check (char_length(btrim(name)) between 1 and 60),
  note        text check (note is null or char_length(note) <= 300),
  is_primary  boolean not null default false,
  created_by  uuid,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  unique (id, school_id),
  foreign key (semester_id, school_id) references public.semesters (id, school_id) on delete cascade
);
create unique index timetable_drafts_name_uq on public.timetable_drafts (term_id, semester_id, lower(btrim(name)));
create unique index timetable_drafts_one_primary on public.timetable_drafts (term_id, semester_id) where is_primary;
create index timetable_drafts_school_idx on public.timetable_drafts (school_id, term_id);

create trigger timetable_drafts_updated_at before update on public.timetable_drafts
  for each row execute function private.set_updated_at();

-- one row = one subject placed at one time. The rows of one PARALLEL placement (the electives of one elective group,
-- e.g. the AI-track and the Cyber-track Major) share `parallel_key` and always sit at exactly the same time.
create table public.draft_entries (
  id           uuid primary key default gen_random_uuid(),
  draft_id     uuid not null,
  school_id    uuid not null,
  semester_id  uuid not null,
  subject_id   uuid not null,
  day          smallint not null check (day between 1 and 6),
  start_period smallint not null check (start_period between 1 and 30),
  length       smallint not null check (length between 1 and 8),
  parallel_key uuid,
  created_by   uuid,
  created_at   timestamptz not null default now(),
  foreign key (draft_id, school_id) references public.timetable_drafts (id, school_id) on delete cascade,
  foreign key (subject_id, school_id) references public.subjects (id, school_id) on delete cascade,
  -- one class has only one lecture at a time inside a draft - except the rows of one parallel placement
  constraint draft_entries_no_overlap
    exclude using gist (
      draft_id with =, day with =,
      int4range(start_period::int, (start_period + length)::int) with &&,
      (coalesce(parallel_key, id)) with <>
    )
);
create index draft_entries_draft_idx on public.draft_entries (draft_id);
create index draft_entries_subject_idx on public.draft_entries (subject_id);

-- ---------------------------------------------------------------------
-- RLS: admin = everything, HOD = own school only, teachers and everybody else = nothing. Writes only through the backend.
-- ---------------------------------------------------------------------
alter table public.timetable_drafts enable row level security;
alter table public.draft_entries    enable row level security;

create policy timetable_drafts_select on public.timetable_drafts for select to authenticated
  using ((select private.is_admin()) or ((select private.current_app_role()) = 'hod' and school_id = (select private.current_school_id())));
create policy draft_entries_select on public.draft_entries for select to authenticated
  using ((select private.is_admin()) or ((select private.current_app_role()) = 'hod' and school_id = (select private.current_school_id())));

revoke all on public.timetable_drafts, public.draft_entries from anon, authenticated;
grant select on public.timetable_drafts, public.draft_entries to authenticated;
grant all on public.timetable_drafts, public.draft_entries to service_role;
