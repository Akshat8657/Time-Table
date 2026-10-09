import { useState } from 'react'
import { Link } from 'react-router-dom'
import { CalendarPlus, ChevronDown, Timer, Trash2, TriangleAlert } from 'lucide-react'
import { useAuth } from '../auth/AuthProvider'
import { Alert } from '../components/Alert'
import { Badge } from '../components/Badge'
import { Button } from '../components/Button'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { HoursBar, ShortBadge } from '../components/HoursBar'
import { EmptyState, PageHeader, Panel, Skeleton } from '../components/Panel'
import { SchoolBar } from '../components/SchoolBar'
import { SuggestDialog } from '../components/SuggestDialog'
import { useToast } from '../components/Toast'
import { api, ApiError } from '../lib/api'
import { formatDate, formatShort } from '../lib/helpers'
import { ROLE_PREFIX } from '../lib/nav'
import { useSchoolScope } from '../lib/scope'
import { useTerm } from '../lib/term'
import type { ExtraLecture, HoursClass, HoursPayload, HoursSubject } from '../lib/types'
import { useApi } from '../lib/useApi'

function ClassBlock({ c, onlyShort, onSuggest }: { c: HoursClass; onlyShort: boolean; onSuggest: (s: HoursSubject) => void }) {
  const [open, setOpen] = useState(c.short_hours > 0)
  const rows = onlyShort ? c.subjects.filter((s) => s.shortfall > 0) : c.subjects
  if (onlyShort && rows.length === 0) return null
  return (
    <section className="rounded-2xl border border-line bg-surface shadow-card" data-testid="hours-class">
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} className="flex w-full flex-wrap items-center gap-3 px-5 py-4 text-left">
        <ChevronDown className={`size-4 shrink-0 text-muted transition-transform ${open ? 'rotate-180' : ''}`} aria-hidden="true" />
        <span className="min-w-0 flex-1">
          <span className="block text-[15px] font-semibold text-ink">{c.class_label}</span>
          <span className="block text-[13px] text-muted">
            {c.dates ? `Semester ${formatDate(c.dates.start)} to ${formatDate(c.dates.end)}` : 'Semester dates not set'}
            {!c.published && ' · not published yet'}
          </span>
        </span>
        {c.short_subjects > 0 ? (
          <Badge tone="danger" className="font-semibold"><TriangleAlert className="size-3" aria-hidden="true" /> {c.short_subjects} short · {c.short_hours} h</Badge>
        ) : c.published ? (
          <Badge tone="success">all on track</Badge>
        ) : (
          <Badge>waiting for publish</Badge>
        )}
      </button>
      {open && (
        <ul className="divide-y divide-line border-t border-line">
          {rows.map((s) => (
            <li key={s.subject_id} className="grid gap-3 px-5 py-3.5 sm:grid-cols-[minmax(0,16rem)_1fr_auto] sm:items-center" data-testid="hours-subject" data-short={s.shortfall > 0 ? 'yes' : 'no'}>
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-ink" title={s.name}>{s.name}</p>
                <p className="truncate text-[12px] text-muted">{s.code} · {s.teachers.map((t) => t.name).join(' + ') || 'no teacher'}{s.weekly ? ` · ${s.weekly} h/week` : ''}</p>
              </div>
              {s.status === 'ok' || s.status === 'short' ? <HoursBar h={s} /> : <p className="text-[13px] text-muted">{s.note}</p>}
              <div className="flex items-center gap-2 sm:justify-end">
                <ShortBadge h={s} />
                {s.shortfall > 0 && (
                  <Button size="sm" variant="secondary" icon={<CalendarPlus className="size-4" />} onClick={() => onSuggest(s)} data-testid="suggest">
                    Suggest extra lectures
                  </Button>
                )}
              </div>
            </li>
          ))}
          {rows.length === 0 && <li className="px-5 py-4 text-sm text-muted">No subjects.</li>}
        </ul>
      )}
    </section>
  )
}

export default function HoursPage() {
  const { user } = useAuth()
  const toast = useToast()
  const scope = useSchoolScope()
  const { active, ready: termReady } = useTerm()
  const prefix = user ? ROLE_PREFIX[user.role] : ''
  const enabled = scope.ready && !!scope.schoolId && !!active
  const hours = useApi<HoursPayload>(enabled ? scope.path('/hours/subjects') : null)
  const extra = useApi<{ extra: ExtraLecture[] }>(enabled ? scope.path('/hours/extra') : null)
  const [suggest, setSuggest] = useState<HoursSubject | null>(null)
  const [onlyShort, setOnlyShort] = useState(false)
  const [cancel, setCancel] = useState<ExtraLecture | null>(null)

  if (termReady && !active) {
    return (
      <>
        <PageHeader title="Semester Hours" subtitle="Hours taught versus hours required, per subject." />
        <Alert tone="warning" title="Choose the active term first">
          <Link to={`${prefix}/active-term`} className="font-semibold underline">Open Active Term</Link> and pick one.
        </Alert>
      </>
    )
  }

  const summary = hours.data?.summary
  const refresh = () => {
    hours.reload()
    extra.reload()
  }
  async function doCancel() {
    if (!cancel) return
    try {
      const r = await api<{ notified: number }>(`/hours/extra/${cancel.id}`, { method: 'DELETE' })
      toast.success(`Extra lecture cancelled. ${r.notified} teacher(s) told.`)
      refresh()
    } catch (err) {
      throw err instanceof ApiError ? err : new Error('Could not cancel it.')
    }
  }

  return (
    <>
      <PageHeader
        title="Semester Hours"
        subtitle="Live from the published timetable, the semester dates and the holidays and events: the hours each subject can still deliver against the hours it needs. When a subject falls short, one click finds clash-free extra slots for you to approve."
        badge="Phase 4"
      />
      <SchoolBar />

      {hours.error ? (
        <Alert tone="danger">{hours.error.message}</Alert>
      ) : hours.loading && !hours.data ? (
        <div className="space-y-3"><Skeleton className="h-20" /><Skeleton className="h-64" /></div>
      ) : hours.data && summary ? (
        <div className="space-y-6">
          <div className="flex flex-wrap items-stretch gap-3">
            <div className="rounded-xl border border-line bg-surface px-4 py-2.5 shadow-card">
              <span className="block text-xs font-medium text-muted">Subjects short of hours</span>
              <span className="mt-0.5 flex items-center gap-2">
                <span className={`text-2xl font-semibold tabular-nums ${summary.short_subjects > 0 ? 'text-danger' : 'text-ink'}`} data-testid="short-subjects">{summary.short_subjects}</span>
                <Badge tone={summary.short_subjects > 0 ? 'danger' : 'success'}>{summary.short_subjects > 0 ? `${summary.short_hours} ${summary.short_hours === 1 ? 'hour' : 'hours'} short` : 'all on track'}</Badge>
              </span>
            </div>
            <div className="rounded-xl border border-line bg-surface px-4 py-2.5 shadow-card">
              <span className="block text-xs font-medium text-muted">Subjects tracked</span>
              <span className="mt-0.5 block text-2xl font-semibold tabular-nums text-ink">{summary.subjects}</span>
            </div>
            {summary.unpublished_classes > 0 && (
              <div className="rounded-xl border border-warning-line bg-warning-soft px-4 py-2.5 text-sm text-warning">
                {summary.unpublished_classes} {summary.unpublished_classes === 1 ? 'class is' : 'classes are'} not published, so their hours cannot be tracked yet.
              </div>
            )}
            <label className="ml-auto flex cursor-pointer items-center gap-2 self-center text-sm text-muted">
              <input type="checkbox" className="size-4 accent-[var(--brand-solid)]" checked={onlyShort} onChange={(e) => setOnlyShort(e.target.checked)} />
              Only subjects that are short
            </label>
          </div>

          {hours.data.classes.length === 0 ? (
            <EmptyState icon={Timer} title="No classes run in this term">Add courses and semesters first.</EmptyState>
          ) : (
            <div className="space-y-4">
              {hours.data.classes.map((c) => <ClassBlock key={c.semester_id} c={c} onlyShort={onlyShort} onSuggest={setSuggest} />)}
              {onlyShort && summary.short_subjects === 0 && <EmptyState icon={Timer} title="Nothing is short">Every subject can deliver its hours.</EmptyState>}
            </div>
          )}

          <Panel title="Extra (compensation) lectures" icon={CalendarPlus}>
            {extra.data && extra.data.extra.length > 0 ? (
              <ul className="divide-y divide-line text-sm" data-testid="extra-list">
                {extra.data.extra.map((x) => (
                  <li key={x.id} className="flex flex-wrap items-center gap-3 py-2.5 first:pt-0 last:pb-0">
                    <div className="min-w-0 flex-1">
                      <p className="font-medium text-ink">{x.subjects.join(' + ')} <span className="font-normal text-muted">· {x.class_label}</span></p>
                      <p className="text-[13px] text-muted">
                        {formatShort(x.date)} · period {x.start_period}{x.length > 1 ? `-${x.start_period + x.length - 1}` : ''} ({x.time}){x.teacher_name ? ` · ${x.teacher_name}` : ''}{x.reason ? ` · ${x.reason}` : ''}
                      </p>
                    </div>
                    {x.can_cancel ? (
                      <Button variant="ghost" size="sm" icon={<Trash2 className="size-4" />} onClick={() => setCancel(x)} aria-label={`Cancel the extra lecture of ${x.subjects.join(' + ')} on ${x.date}`}>Cancel</Button>
                    ) : (
                      <Badge>done</Badge>
                    )}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted">No extra lectures yet. They appear here after you approve suggestions.</p>
            )}
          </Panel>
        </div>
      ) : null}

      <SuggestDialog subject={suggest} onClose={() => setSuggest(null)} onDone={refresh} />
      <ConfirmDialog open={cancel !== null} title="Cancel this extra lecture?" tone="danger" confirmLabel="Cancel the lecture" onConfirm={doCancel} onClose={() => setCancel(null)}>
        {cancel && <p>{cancel.subjects.join(' + ')} on {formatShort(cancel.date)} will be removed and the teacher is told.</p>}
      </ConfirmDialog>
    </>
  )
}
