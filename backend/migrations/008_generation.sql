-- =====================================================================
--  TimeTablePro - Migration 008  (PHASE 3: the automatic generation engine)
--
--  * draft_entries.is_locked      semi-automatic mode: a hand-locked placement the generator must keep (plan Section 10)
--  * generation_settings          the soft-rule weights (0-100) of each school
--  * generation_runs              one Generate click = one run (whole school, all classes together)
--  * generation_solutions         each run keeps its best 2-3 scored solutions; a solution's drafts carry solution_id
-- =====================================================================
alter table public.draft_entries add column if not exists is_locked boolean not null default false;
comment on column public.draft_entries.is_locked is 'true = pinned by hand; the generator keeps it and the editor refuses to move/remove it until unpinned';

create table public.generation_settings (
  school_id   uuid primary key references public.schools (id) on delete cascade,
  weights     jsonb not null default '{}'::jsonb,
  updated_by  uuid,
  updated_at  timestamptz not null default now()
);
create trigger generation_settings_updated_at before update on public.generation_settings
  for each row execute function private.set_updated_at();

create table public.generation_runs (
  id          uuid primary key default gen_random_uuid(),
  school_id   uuid not null references public.schools (id) on delete cascade,
  term_id     uuid not null references public.academic_terms (id) on delete cascade,
  name        text not null check (char_length(btrim(name)) between 1 and 80),
  mode        text not null default 'fresh' check (mode in ('fresh', 'keep_locked')),
  status      text not null default 'running' check (status in ('running', 'done', 'failed', 'cancelled')),
  progress    smallint not null default 0 check (progress between 0 and 100),
  message     text,
  weights     jsonb not null default '{}'::jsonb,
  precheck    jsonb not null default '[]'::jsonb,     -- the capacity / bottleneck findings shown with the result
  error       text,
  created_by  uuid,
  created_at  timestamptz not null default now(),
  finished_at timestamptz
);
create index generation_runs_school_idx on public.generation_runs (school_id, term_id, created_at desc);

create table public.generation_solutions (
  id          uuid primary key default gen_random_uuid(),
  run_id      uuid not null references public.generation_runs (id) on delete cascade,
  school_id   uuid not null references public.schools (id) on delete cascade,
  label       text not null check (label in ('A', 'B', 'C')),
  score       numeric(5, 2) not null,
  breakdown   jsonb not null default '{}'::jsonb,     -- per-rule scores + per-class completeness + anything unplaced
  is_used     boolean not null default false,         -- "Use this solution" pressed: its drafts were made primary
  created_at  timestamptz not null default now(),
  unique (run_id, label)
);

alter table public.timetable_drafts add column if not exists solution_id uuid references public.generation_solutions (id) on delete set null;
create index timetable_drafts_solution_idx on public.timetable_drafts (solution_id) where solution_id is not null;

-- ---------------------------------------------------------------------
-- RLS: like the drafts - admin sees everything, the HOD only his school, teachers nothing. Writes only through the backend.
-- ---------------------------------------------------------------------
alter table public.generation_settings  enable row level security;
alter table public.generation_runs      enable row level security;
alter table public.generation_solutions enable row level security;

create policy generation_settings_select on public.generation_settings for select to authenticated
  using ((select private.is_admin()) or ((select private.current_app_role()) = 'hod' and school_id = (select private.current_school_id())));
create policy generation_runs_select on public.generation_runs for select to authenticated
  using ((select private.is_admin()) or ((select private.current_app_role()) = 'hod' and school_id = (select private.current_school_id())));
create policy generation_solutions_select on public.generation_solutions for select to authenticated
  using ((select private.is_admin()) or ((select private.current_app_role()) = 'hod' and school_id = (select private.current_school_id())));

revoke all on public.generation_settings, public.generation_runs, public.generation_solutions from anon, authenticated;
grant select on public.generation_settings, public.generation_runs, public.generation_solutions to authenticated;
grant all on public.generation_settings, public.generation_runs, public.generation_solutions to service_role;
