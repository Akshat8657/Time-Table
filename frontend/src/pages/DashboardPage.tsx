import { useEffect, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import {
  ArrowRight, Building2, CalendarCheck, CalendarDays, CalendarOff, Check, CircleDashed, Clock, Cookie, ListChecks, Repeat, ShieldCheck,
  TriangleAlert, Users,
} from 'lucide-react'
import { useAuth } from '../auth/AuthProvider'
import { Alert } from '../components/Alert'
import { Badge } from '../components/Badge'
import { Button } from '../components/Button'
import { EmptyState, PageHeader, Panel, Skeleton, StatCard } from '../components/Panel'
import { useConsent } from '../consent/ConsentProvider'
import { firstName, formatDuration, greeting } from '../lib/format'
import { formatDate, formatRange } from '../lib/helpers'
import { ROLE_LABEL, ROLE_PREFIX } from '../lib/nav'
import { runningSemesters } from '../lib/term'
import type { Role, Term } from '../lib/types'
import { useApi } from '../lib/useApi'

interface Upcoming {
  title: string
  kind: 'holiday' | 'event'
  date_from: string
  date_to: string
  full_day: boolean
  start_time: string | null
  end_time: string | null
  scope: 'college' | 'school'
  school_name: string | null
}
interface Setup {
  term_set: boolean
  teachers?: number
  classes?: number
  subjects?: number
  assigned?: number
  classes_with_pe?: number
  classes_with_sol?: number
  dates_set?: number
  no_pe_sol_classes?: number
}
interface AdminOverview {
  schools: { id: string; name: string; is_active: boolean }[]
  counts: { admins: number; hods: number; teachers: number }
  active_term: Term | null
  upcoming: Upcoming[]
}
interface HodOverview {
  scope: 'school' | 'all-schools'
  school: { id: string; name: string } | null
  schools?: { id: string; name: string }[]
  counts?: { teachers: number }
  active_term: Term | null
  setup: Setup | null
  upcoming: Upcoming[]
}
interface TeacherOverview {
  mode: string
  school: { id: string | null; name: string | null }
  active_term: Term | null
  upcoming: Upcoming[]
}

/** The four "at a glance" cards every dashboard shows (Section 6). Real numbers arrive with the later phases. */
function GlanceCards({ upcoming }: { upcoming?: Upcoming[] }) {
  const next = upcoming?.[0]
  return (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <StatCard icon={CalendarOff} label="Today’s absents" value="—" caption="Tracking starts in Phase 5" />
      <StatCard icon={Repeat} label="Pending substitutions" value="—" caption="Available from Phase 5" />
      <StatCard icon={TriangleAlert} label="Subject hours short" value="—" caption="Live tracking arrives in Phase 4" />
      <StatCard
        icon={CalendarDays}
        label="Upcoming holiday / event"
        value={next ? formatDate(next.date_from).replace(/ \d{4}$/, '') : '—'}
        caption={next ? next.title : upcoming ? 'Nothing coming up' : 'Loading…'}
      />
    </div>
  )
}

function UpcomingPanel({ upcoming, linkTo }: { upcoming: Upcoming[]; linkTo: string | null }) {
  return (
    <Panel
      title="Coming up"
      icon={CalendarDays}
      action={
        linkTo ? (
          <Link to={linkTo} className="text-[13px] font-medium text-brand-text hover:underline">
            See all
          </Link>
        ) : undefined
      }
    >
      {upcoming.length === 0 ? (
        <p className="text-sm text-muted">No holiday or event is coming up.</p>
      ) : (
        <ul className="divide-y divide-line">
          {upcoming.map((u, i) => (
            <li key={`${u.title}-${i}`} className="flex items-start justify-between gap-3 py-2.5 first:pt-0 last:pb-0">
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-ink">{u.title}</p>
                <p className="text-[13px] text-muted">
                  {formatRange(u.date_from, u.date_to)}
                  {!u.full_day && u.start_time ? ` · ${u.start_time}-${u.end_time}` : ''}
                </p>
              </div>
              <Badge tone={u.kind === 'holiday' ? 'success' : 'brand'}>{u.kind === 'holiday' ? 'Holiday' : 'Event'}</Badge>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  )
}

function TermPanel({ term, canChange, role }: { term: Term | null | undefined; canChange: boolean; role: Role }) {
  return (
    <Panel title="Active term" icon={CalendarCheck}>
      {term === undefined ? (
        <Skeleton className="h-14" />
      ) : term ? (
        <div className="flex items-center gap-4">
          <span className="flex size-12 items-center justify-center rounded-xl bg-brand-soft text-brand-text">
            <CalendarCheck className="size-6" aria-hidden="true" />
          </span>
          <div>
            <p className="text-lg font-semibold text-ink">{term.name}</p>
            <p className="text-sm text-muted">{runningSemesters(term.kind)} are running</p>
          </div>
        </div>
      ) : (
        <p className="text-sm text-muted">No term is active yet{canChange ? '.' : ' - your HOD or the administrator will choose it.'}</p>
      )}
      {canChange && (
        <Link to={`${ROLE_PREFIX[role]}/active-term`} className="mt-4 inline-flex items-center gap-1 text-[13px] font-medium text-brand-text hover:underline">
          {term ? 'Change the term or set semester dates' : 'Choose the active term'} <ArrowRight className="size-3.5" aria-hidden="true" />
        </Link>
      )}
    </Panel>
  )
}

function SessionPanel() {
  const { user, session } = useAuth()
  const { consent, openSettings } = useConsent()
  if (!user || !session) return null

  const rows: [string, string][] = [
    ['Signed in as', user.email],
    ['Role', `${ROLE_LABEL[user.role]}${user.school_name ? ` · ${user.school_name}` : ' · All schools'}`],
    [
      'This device',
      session.remember_me
        ? `Remembered - stays signed in for up to ${formatDuration(session.idle_timeout_seconds)} without use`
        : 'This browser session only',
    ],
    ['Automatic sign-out', `After ${formatDuration(session.idle_timeout_seconds)} of inactivity`],
  ]

  return (
    <Panel title="Your session & privacy" icon={ShieldCheck}>
      <dl className="divide-y divide-line text-sm">
        {rows.map(([label, value]) => (
          <div key={label} className="flex flex-col gap-0.5 py-2.5 first:pt-0 sm:flex-row sm:gap-4">
            <dt className="w-40 shrink-0 text-muted">{label}</dt>
            <dd className="min-w-0 break-words font-medium text-ink">{value}</dd>
          </div>
        ))}
        <div className="flex flex-col gap-0.5 py-2.5 pb-0 sm:flex-row sm:items-center sm:gap-4">
          <dt className="w-40 shrink-0 text-muted">Cookies</dt>
          <dd className="flex min-w-0 flex-wrap items-center gap-2 font-medium text-ink">
            <Cookie className="size-4 text-subtle" aria-hidden="true" />
            {consent === 'accepted' ? 'Accepted' : consent === 'declined' ? 'Declined' : 'Not chosen yet'}
            <button type="button" onClick={openSettings} className="text-[13px] font-medium text-brand-text hover:underline">
              Change
            </button>
          </dd>
        </div>
      </dl>
    </Panel>
  )
}

function LoadFailed({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="space-y-3">
      <Alert tone="warning">{message}</Alert>
      <Button variant="secondary" size="sm" onClick={onRetry}>
        Try again
      </Button>
    </div>
  )
}

function Welcome({ role }: { role: Role }) {
  const { user } = useAuth()
  const location = useLocation()
  const navigate = useNavigate()
  // a one-time message (for example after changing the password): keep it on screen, but forget it on reload
  const [flash] = useState<string | undefined>(() => (location.state as { flash?: string } | null)?.flash)

  useEffect(() => {
    if (flash) navigate(location.pathname, { replace: true, state: null })
  }, []) // runs once on purpose

  return (
    <>
      {flash && (
        <Alert tone="success" className="mb-5">
          {flash}
        </Alert>
      )}
      <PageHeader
        title={`${greeting()}, ${firstName(user?.full_name ?? '')}`}
        subtitle={
          role === 'admin'
            ? 'Here is the whole college at a glance.'
            : role === 'hod'
              ? `Here is ${user?.school_name ?? 'your school'} at a glance.`
              : 'Here is your day at a glance.'
        }
        badge="Phase 1"
      />
    </>
  )
}

function SetupChecklist({ setup }: { setup: Setup | null | undefined }) {
  if (setup === undefined) return <Panel title="Setup checklist" icon={ListChecks}><Skeleton className="h-48" /></Panel>
  const n = (v?: number) => v ?? 0
  const need = (s: Setup) => Math.max(0, n(s.classes) - n(s.no_pe_sol_classes)) // classes that are expected to have PE / SOL
  const steps = setup
    ? [
        { done: setup.term_set, label: 'Choose the active term', detail: setup.term_set ? 'Done' : 'Odd or even + year', to: '/hod/active-term' },
        { done: n(setup.teachers) > 0, label: 'Add your teachers', detail: `${n(setup.teachers)} ${n(setup.teachers) === 1 ? 'teacher' : 'teachers'}`, to: '/hod/teachers' },
        { done: n(setup.subjects) > 0, label: 'Add subjects, credits, labs and electives', detail: `${n(setup.subjects)} ${n(setup.subjects) === 1 ? 'subject' : 'subjects'} in the running classes`, to: '/hod/subjects' },
        { done: n(setup.subjects) > 0 && n(setup.assigned) >= n(setup.subjects), label: 'Give every subject a teacher', detail: `${n(setup.assigned)} of ${n(setup.subjects)} assigned`, to: '/hod/assignment' },
        {
          done: need(setup) > 0 && n(setup.classes_with_pe) >= need(setup) && n(setup.classes_with_sol) >= need(setup),
          label: 'Fix PE and SOL for every class',
          detail: `PE ${n(setup.classes_with_pe)}/${need(setup)} · SOL ${n(setup.classes_with_sol)}/${need(setup)} classes${n(setup.no_pe_sol_classes) ? ` (${n(setup.no_pe_sol_classes)} without PE / SOL)` : ''}`,
          to: '/hod/fixed-slots',
        },
        { done: n(setup.classes) > 0 && n(setup.dates_set) >= n(setup.classes), label: 'Set the semester start and end dates', detail: `${n(setup.dates_set)} of ${n(setup.classes)} classes`, to: '/hod/active-term' },
      ]
    : []
  const done = steps.filter((x) => x.done).length
  return (
    <Panel
      title="Setup checklist"
      icon={ListChecks}
      action={steps.length > 0 ? <Badge tone={done === steps.length ? 'success' : 'brand'}>{done} of {steps.length} done</Badge> : undefined}
    >
      {steps.length === 0 ? (
        <p className="text-sm text-muted">The checklist is shown to the HOD of a school.</p>
      ) : (
        <>
          <div className="mb-4 h-1.5 overflow-hidden rounded-full bg-surface-2 ring-1 ring-line" role="presentation">
            <div className="h-full rounded-full bg-brand transition-all" style={{ width: `${Math.round((done / steps.length) * 100)}%` }} />
          </div>
          <ol className="space-y-1.5">
            {steps.map((step) => (
              <li key={step.label}>
                <Link to={step.to} className="group flex items-center gap-3 rounded-xl px-2.5 py-2 transition-colors hover:bg-hover">
                  <span className={`flex size-6 shrink-0 items-center justify-center rounded-full ${step.done ? 'bg-success text-white' : 'bg-surface-2 text-subtle ring-1 ring-line-strong'}`}>
                    {step.done ? <Check className="size-3.5" strokeWidth={3} aria-hidden="true" /> : <CircleDashed className="size-3.5" aria-hidden="true" />}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className={`block text-sm font-medium ${step.done ? 'text-muted' : 'text-ink'}`}>{step.label}</span>
                    <span className="block text-[13px] text-muted">{step.detail}</span>
                  </span>
                  <ArrowRight className="size-4 shrink-0 text-subtle opacity-0 transition-opacity group-hover:opacity-100" aria-hidden="true" />
                </Link>
              </li>
            ))}
          </ol>
        </>
      )}
    </Panel>
  )
}

export function AdminDashboard() {
  const { data, error, loading, reload } = useApi<AdminOverview>('/admin/overview')
  return (
    <>
      <Welcome role="admin" />
      <GlanceCards upcoming={data?.upcoming} />
      <div className="mt-6 grid gap-6 lg:grid-cols-3">
        <Panel title="Schools" icon={Building2} className="lg:col-span-2">
          {loading ? (
            <div className="space-y-3">
              <Skeleton className="h-12" />
              <Skeleton className="h-12" />
            </div>
          ) : error ? (
            <LoadFailed message={error.message} onRetry={reload} />
          ) : data && data.schools.length > 0 ? (
            <ul className="space-y-2.5">
              {data.schools.map((school) => (
                <li key={school.id} className="flex items-center gap-3 rounded-xl border border-line bg-surface-2 px-4 py-3">
                  <span className="flex size-9 items-center justify-center rounded-lg bg-brand-soft text-brand-text">
                    <Building2 className="size-[18px]" aria-hidden="true" />
                  </span>
                  <span className="min-w-0 flex-1 truncate text-sm font-semibold text-ink">{school.name}</span>
                  <span className="rounded-full bg-success-soft px-2.5 py-0.5 text-xs font-medium text-success">
                    {school.is_active ? 'Active' : 'Inactive'}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState icon={Building2} title="No schools yet">
              Schools and their HODs are created in College Setup (Phase 1).
            </EmptyState>
          )}
        </Panel>

        <Panel title="People" icon={Users}>
          {loading ? (
            <div className="space-y-3">
              <Skeleton className="h-8" />
              <Skeleton className="h-8" />
              <Skeleton className="h-8" />
            </div>
          ) : error ? (
            <p className="text-sm text-muted">Not available right now.</p>
          ) : data ? (
            <dl className="space-y-3 text-sm">
              {(
                [
                  ['Administrators', data.counts.admins],
                  ['Heads of Department', data.counts.hods],
                  ['Teachers', data.counts.teachers],
                ] as const
              ).map(([label, count]) => (
                <div key={label} className="flex items-center justify-between">
                  <dt className="text-muted">{label}</dt>
                  <dd className="text-lg font-semibold tabular-nums text-ink">{count}</dd>
                </div>
              ))}
            </dl>
          ) : null}
        </Panel>
      </div>
      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <TermPanel term={loading ? undefined : (data?.active_term ?? null)} canChange role="admin" />
        <UpcomingPanel upcoming={data?.upcoming ?? []} linkTo="/admin/holidays-events" />
      </div>
      <div className="mt-6">
        <SessionPanel />
      </div>
    </>
  )
}

export function HodDashboard() {
  const { data, error, loading, reload } = useApi<HodOverview>('/hod/overview')
  const navigate = useNavigate()
  return (
    <>
      <Welcome role="hod" />
      <GlanceCards upcoming={data?.upcoming} />
      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <Panel title="My school" icon={Building2}>
          {loading ? (
            <Skeleton className="h-16" />
          ) : error ? (
            <LoadFailed message={error.message} onRetry={reload} />
          ) : data?.school ? (
            <div className="flex items-center gap-4">
              <span className="flex size-12 items-center justify-center rounded-xl bg-brand-soft text-brand-text">
                <Building2 className="size-6" aria-hidden="true" />
              </span>
              <div>
                <p className="text-base font-semibold text-ink">{data.school.name}</p>
                <p className="text-sm text-muted">
                  {data.counts?.teachers ?? 0} {data.counts?.teachers === 1 ? 'teacher' : 'teachers'} in your school
                </p>
              </div>
            </div>
          ) : (
            <EmptyState icon={Building2} title="No school linked">
              Ask the administrator to link your account to a school.
            </EmptyState>
          )}
        </Panel>

        <Panel title="Teacher mode" icon={Clock}>
          <p className="text-sm leading-relaxed text-muted">
            You are also a teacher. Your own timetable, preferences, leave and substitution offers live under{' '}
            <span className="font-medium text-ink">My Schedule</span> in the menu.
          </p>
          <Button
            variant="secondary"
            className="mt-4"
            icon={<ArrowRight className="size-4" />}
            onClick={() => navigate('/hod/my-schedule/timetable')}
          >
            Open My Schedule
          </Button>
        </Panel>
      </div>
      <div className="mt-6 grid gap-6 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <SetupChecklist setup={loading ? undefined : (data?.setup ?? null)} />
        </div>
        <div className="space-y-6">
          <TermPanel term={loading ? undefined : (data?.active_term ?? null)} canChange role="hod" />
          <UpcomingPanel upcoming={data?.upcoming ?? []} linkTo="/hod/events-holidays" />
        </div>
      </div>
      <div className="mt-6">
        <SessionPanel />
      </div>
    </>
  )
}

export function TeacherDashboard() {
  const { data, error, loading, reload } = useApi<TeacherOverview>('/teacher/overview')
  return (
    <>
      <Welcome role="teacher" />
      <GlanceCards upcoming={data?.upcoming} />
      <div className="mt-6 grid gap-6 lg:grid-cols-3">
        <Panel title="Today’s schedule" icon={Clock} className="lg:col-span-2">
          <EmptyState icon={CalendarDays} title="No classes to show yet">
            Once the timetable is published, today&apos;s lectures will appear here with their times and classes.
          </EmptyState>
        </Panel>
        <Panel title="My school" icon={Building2}>
          {loading ? (
            <Skeleton className="h-12" />
          ) : error ? (
            <LoadFailed message={error.message} onRetry={reload} />
          ) : (
            <div className="flex items-center gap-3">
              <span className="flex size-10 items-center justify-center rounded-xl bg-brand-soft text-brand-text">
                <Building2 className="size-5" aria-hidden="true" />
              </span>
              <p className="text-sm font-semibold text-ink">{data?.school.name ?? 'Not linked yet'}</p>
            </div>
          )}
        </Panel>
      </div>
      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <TermPanel term={loading ? undefined : (data?.active_term ?? null)} canChange={false} role="teacher" />
        <UpcomingPanel upcoming={data?.upcoming ?? []} linkTo={null} />
      </div>
      <div className="mt-6">
        <SessionPanel />
      </div>
    </>
  )
}
