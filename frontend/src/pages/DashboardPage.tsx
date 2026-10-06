import { useEffect, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import {
  ArrowRight, Building2, CalendarDays, CalendarOff, Clock, Cookie, Repeat, ShieldCheck, TriangleAlert, Users,
} from 'lucide-react'
import { useAuth } from '../auth/AuthProvider'
import { Alert } from '../components/Alert'
import { Button } from '../components/Button'
import { EmptyState, PageHeader, Panel, Skeleton, StatCard } from '../components/Panel'
import { useConsent } from '../consent/ConsentProvider'
import { firstName, formatDuration, greeting } from '../lib/format'
import { ROLE_LABEL } from '../lib/nav'
import type { Role } from '../lib/types'
import { useApi } from '../lib/useApi'

interface AdminOverview {
  schools: { id: string; name: string; is_active: boolean }[]
  counts: { admins: number; hods: number; teachers: number }
}
interface HodOverview {
  scope: 'school' | 'all-schools'
  school: { id: string; name: string } | null
  schools?: { id: string; name: string }[]
  counts?: { teachers: number }
}
interface TeacherOverview {
  mode: string
  school: { id: string | null; name: string | null }
}

/** The four "at a glance" cards every dashboard shows (Section 6). Real numbers arrive with the later phases. */
function GlanceCards() {
  return (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <StatCard icon={CalendarOff} label="Today’s absents" value="—" caption="Tracking starts in Phase 5" />
      <StatCard icon={Repeat} label="Pending substitutions" value="—" caption="Available from Phase 5" />
      <StatCard icon={TriangleAlert} label="Subject hours short" value="—" caption="Live tracking arrives in Phase 4" />
      <StatCard icon={CalendarDays} label="Upcoming holiday / event" value="—" caption="Set up in Phase 1" />
    </div>
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
        badge="Phase 0"
      />
      <Alert tone="info" className="mb-6">
        The foundation is ready: secure sign-in, roles and sessions. The working screens arrive phase by phase - every
        item in the menu already has its place.
      </Alert>
    </>
  )
}

export function AdminDashboard() {
  const { data, error, loading, reload } = useApi<AdminOverview>('/admin/overview')
  return (
    <>
      <Welcome role="admin" />
      <GlanceCards />
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
      <GlanceCards />
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
      <GlanceCards />
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
      <div className="mt-6">
        <SessionPanel />
      </div>
    </>
  )
}
