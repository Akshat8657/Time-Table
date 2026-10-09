import { Link } from 'react-router-dom'
import { BookOpen, TriangleAlert } from 'lucide-react'
import { Alert } from '../components/Alert'
import { Badge } from '../components/Badge'
import { HoursBar, ShortBadge } from '../components/HoursBar'
import { EmptyState, PageHeader, Skeleton } from '../components/Panel'
import { formatDate } from '../lib/helpers'
import { useNotifications } from '../lib/notifications'
import type { MyHoursPayload } from '../lib/types'
import { useApi } from '../lib/useApi'

/** A teacher's subjects: hours taught so far, hours still possible, and a red "short by X" when the semester cannot deliver them. */
export default function MySubjectsPage() {
  const { arrivals } = useNotifications()
  const { data, error, loading, reload } = useApi<MyHoursPayload>(`/hours/my?_=${arrivals}`)
  const short = data?.subjects.filter((s) => s.shortfall > 0) ?? []

  return (
    <>
      <PageHeader
        title="My Subjects"
        subtitle="Hours you have taught against the hours each subject needs, counted from the published timetable, the semester dates and the holidays and events."
        badge="Phase 4"
      />
      {error ? (
        <Alert tone="danger">
          {error.message}{' '}
          <button type="button" onClick={reload} className="font-semibold underline">Try again</button>
        </Alert>
      ) : loading && !data ? (
        <div className="space-y-3"><Skeleton className="h-24" /><Skeleton className="h-24" /></div>
      ) : data && !data.term ? (
        <EmptyState icon={BookOpen} title="No active term yet">Your subjects appear here after the term is set.</EmptyState>
      ) : data ? (
        <div className="space-y-5">
          {short.length > 0 && (
            <Alert tone="danger" title={`${short.length} of your ${short.length === 1 ? 'subject is' : 'subjects are'} short of hours`}>
              <ul className="mt-1 list-disc space-y-0.5 pl-5">
                {short.map((s) => <li key={s.subject_id}>{s.name} ({s.class_label}) - short by {s.shortfall} {s.shortfall === 1 ? 'hour' : 'hours'}</li>)}
              </ul>
              <p className="mt-1">Your HOD can add extra lectures to make up for holidays. You will be notified when one is added.</p>
            </Alert>
          )}
          {data.subjects.length === 0 ? (
            <EmptyState icon={BookOpen} title="No subjects yet">
              You have no subject in this term. The HOD gives subjects on the Assignment screen.
            </EmptyState>
          ) : (
            <ul className="grid gap-4 lg:grid-cols-2" data-testid="my-subjects">
              {data.subjects.map((s) => (
                <li key={s.subject_id} className="rounded-2xl border border-line bg-surface p-5 shadow-card" data-short={s.shortfall > 0 ? 'yes' : 'no'}>
                  <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="text-[15px] font-semibold text-ink">{s.name}</p>
                      <p className="text-[13px] text-muted">{s.class_label} · {s.code}{s.is_lab ? ' · Lab' : ''}</p>
                    </div>
                    <ShortBadge h={s} />
                  </div>
                  {s.status === 'ok' || s.status === 'short' ? (
                    <HoursBar h={s} />
                  ) : (
                    <p className="text-[13px] text-muted">{s.note}</p>
                  )}
                  <p className="mt-2 flex flex-wrap items-center gap-1.5 text-[12px] text-muted">
                    {s.weekly > 0 && <Badge>{s.weekly} h a week</Badge>}
                    {s.dates && <span>Semester {formatDate(s.dates.start)} to {formatDate(s.dates.end)}</span>}
                    {s.shortfall > 0 && <span className="inline-flex items-center gap-1 text-danger"><TriangleAlert className="size-3.5" aria-hidden="true" /> {s.extra_lectures_needed} extra {s.extra_lectures_needed === 1 ? 'lecture' : 'lectures'} would close the gap</span>}
                  </p>
                </li>
              ))}
            </ul>
          )}
          <p className="text-[13px] text-muted">
            Want the whole picture? Open <Link to="../timetable" relative="path" className="font-medium text-brand-text hover:underline">My Timetable</Link>.
          </p>
        </div>
      ) : null}
    </>
  )
}
