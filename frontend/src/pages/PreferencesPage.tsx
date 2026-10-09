import { useEffect, useMemo, useState } from 'react'
import { Ban, BellRing, CalendarClock, CircleCheck, CircleDashed, Eye, History, Lock, LockOpen, Save, Star, Timer, UserRound } from 'lucide-react'
import { Alert } from '../components/Alert'
import { Badge } from '../components/Badge'
import { Button } from '../components/Button'
import { Ring } from '../components/charts'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { DataTable, type Column } from '../components/DataTable'
import { TextField } from '../components/Field'
import { Modal } from '../components/Modal'
import { EmptyState, PageHeader, Panel, Skeleton } from '../components/Panel'
import { PrefGrid } from '../components/PrefGrid'
import { SchoolBar } from '../components/SchoolBar'
import { useToast } from '../components/Toast'
import { api, ApiError } from '../lib/api'
import { formatDuration } from '../lib/format'
import { addDaysIso, dayList, todayIso } from '../lib/helpers'
import { useSchoolScope } from '../lib/scope'
import type { PrefOverview, PrefPayload, PrefTeacherRow } from '../lib/types'
import { useApi } from '../lib/useApi'

type Filter = 'all' | 'missing' | 'filled'

function whenText(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : ''
}

/** Asks for a deadline (and, when opening, an optional note). The browser's date box is local time = IST. */
function DeadlineDialog({ open, title, description, confirmLabel, withNote, onClose, onSubmit }: {
  open: boolean
  title: string
  description: string
  confirmLabel: string
  withNote?: boolean
  onClose: () => void
  onSubmit: (deadline: string, note: string) => Promise<void>
}) {
  const [deadline, setDeadline] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    if (open) {
      setDeadline(`${addDaysIso(todayIso(), 3)}T17:00`)
      setNote('')
      setError(null)
    }
  }, [open])
  async function go() {
    setBusy(true)
    setError(null)
    try {
      await onSubmit(deadline, note)
      onClose()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong. Please try again.')
    } finally {
      setBusy(false)
    }
  }
  return (
    <Modal
      open={open}
      onClose={() => !busy && onClose()}
      title={title}
      description={description}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button onClick={() => void go()} loading={busy} disabled={!deadline} data-testid="deadline-confirm">{confirmLabel}</Button>
        </>
      }
    >
      <div className="space-y-4">
        {error && <Alert tone="danger">{error}</Alert>}
        <TextField label="Deadline" type="datetime-local" value={deadline} onChange={(e) => setDeadline(e.target.value)} data-testid="deadline-input" />
        {withNote && <TextField label="A note for the teachers (optional)" value={note} maxLength={300} onChange={(e) => setNote(e.target.value)} placeholder="For example: please keep Saturday free if you can" />}
      </div>
    </Modal>
  )
}

function TeacherDialog({ teacher, onClose }: { teacher: PrefTeacherRow | null; onClose: () => void }) {
  const { data, error } = useApi<PrefPayload>(teacher ? `/prefs/teacher/${teacher.id}` : null)
  return (
    <Modal open={teacher !== null} onClose={onClose} size="lg" title={teacher ? `${teacher.name} - preferences` : 'Preferences'} description="Read only: the teacher fills this in themselves.">
      {error ? (
        <Alert tone="danger">{error.message}</Alert>
      ) : !data ? (
        <Skeleton className="h-64" />
      ) : (
        <div className="space-y-4">
          <p className="flex flex-wrap items-center gap-2 text-[13px] text-muted">
            {data.teacher.is_visiting && <Badge tone="info">Visiting: {dayList(data.teacher.visiting_days)}</Badge>}
            {data.prefer_first && <Badge tone="brand">likes the first lecture</Badge>}
            {data.avoid_last && <Badge tone="brand">avoids the last lecture</Badge>}
            {data.max_per_day ? <Badge tone="warning">max {data.max_per_day} lectures a day</Badge> : <Badge>no daily maximum</Badge>}
            {data.submitted ? <Badge tone="success">filled in this round</Badge> : <Badge>not filled in this round</Badge>}
          </p>
          {data.fixed_notes.length > 0 && <Alert tone="warning" title="Locked sessions">{data.fixed_notes.join(' ')}</Alert>}
          <PrefGrid grid={data.grid} days={data.days} slots={data.slots} lockedDays={data.locked_days} editable={false} limit={data.limit} />
        </div>
      )}
    </Modal>
  )
}

export default function PreferencesPage() {
  const toast = useToast()
  const scope = useSchoolScope()
  const enabled = scope.ready && !!scope.schoolId
  const { data, error, loading, reload } = useApi<PrefOverview>(enabled ? scope.path('/prefs/overview') : null)
  const [filter, setFilter] = useState<Filter>('all')
  const [dialog, setDialog] = useState<'open' | 'extend' | 'reopen' | 'close' | null>(null)
  const [view, setView] = useState<PrefTeacherRow | null>(null)
  const [limit, setLimit] = useState('')
  const [remind, setRemind] = useState('')
  const [saving, setSaving] = useState(false)
  const [settingsNote, setSettingsNote] = useState<string | null>(null)

  const win = data?.window ?? null
  const open = !!win?.is_open

  // the "filled / not filled" list is live: while a window is open it refreshes itself
  useEffect(() => {
    if (!open) return
    const t = window.setInterval(() => {
      if (document.visibilityState === 'visible') reload()
    }, 20_000)
    return () => window.clearInterval(t)
  }, [open, reload])
  useEffect(() => {
    if (data) {
      setLimit(String(data.settings.max_unavailable))
      setRemind(String(data.settings.reminder_hours))
    }
  }, [data?.settings.max_unavailable, data?.settings.reminder_hours]) // eslint-disable-line react-hooks/exhaustive-deps

  const rows = useMemo(() => {
    const all = data?.teachers ?? []
    const shown = filter === 'all' ? all : all.filter((t) => (filter === 'filled' ? t.filled : !t.filled))
    return [...shown].sort((a, b) => Number(a.filled) - Number(b.filled) || a.name.localeCompare(b.name))
  }, [data, filter])

  async function post(path: string, body: Record<string, unknown>, done: string) {
    const r = await api<{ notified?: number }>(path, { method: 'POST', body: { ...scope.body(), ...body } })
    toast.success(r.notified !== undefined ? `${done} ${r.notified} teacher(s) notified.` : done)
    reload()
  }

  async function saveSettings() {
    setSaving(true)
    setSettingsNote(null)
    try {
      const r = await api<{ settings: { max_unavailable: number }; teachers_over_limit: number }>('/prefs/settings', {
        method: 'PUT',
        body: { ...scope.body(), max_unavailable: Number(limit), reminder_hours: Number(remind) },
      })
      setSettingsNote(r.teachers_over_limit > 0 ? `${r.teachers_over_limit} teacher(s) already have more than this. They keep what they saved but must reduce it before they can save again.` : null)
      toast.success('Saved.')
      reload()
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Could not save.')
    } finally {
      setSaving(false)
    }
  }

  const columns: Column<PrefTeacherRow>[] = [
    {
      key: 'name',
      header: 'Teacher',
      cell: (t) => (
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-1.5 text-sm font-medium text-ink">{t.name}{t.is_visiting && <Badge tone="info">visiting</Badge>}</p>
          <p className="truncate text-[12px] text-muted">{t.email}</p>
        </div>
      ),
    },
    {
      key: 'filled',
      header: 'This round',
      cell: (t) =>
        t.filled ? (
          <span className="inline-flex items-center gap-1.5 text-[13px] text-success"><CircleCheck className="size-4" aria-hidden="true" /> Filled <span className="text-muted">{whenText(t.submitted_at)}</span></span>
        ) : (
          <span className="inline-flex items-center gap-1.5 text-[13px] text-warning"><CircleDashed className="size-4" aria-hidden="true" /> Not filled</span>
        ),
    },
    {
      key: 'marks',
      header: 'Slots',
      hideBelow: 'sm',
      cell: (t) => (
        <span className="inline-flex items-center gap-3 text-[13px] tabular-nums text-ink">
          <span className="inline-flex items-center gap-1" title="Not available"><Ban className="size-3.5 text-danger" aria-hidden="true" /> {t.unavailable}{t.over_limit && <Badge tone="danger">over limit</Badge>}</span>
          <span className="inline-flex items-center gap-1" title="Preferred"><Star className="size-3.5 fill-warning text-warning" aria-hidden="true" /> {t.preferred}</span>
        </span>
      ),
    },
    {
      key: 'special',
      header: 'Special choices',
      hideBelow: 'md',
      cell: (t) => <span className="text-[13px] text-muted">{t.has_prefs ? t.summary.split(' · ').filter((x) => !/not available|preferred/.test(x)).join(' · ') || '-' : 'none set'}</span>,
    },
    {
      key: 'view',
      header: <span className="sr-only">View</span>,
      className: 'w-28 text-right',
      cell: (t) => <Button size="sm" variant="secondary" icon={<Eye className="size-4" />} onClick={() => setView(t)} data-testid="view-teacher">View</Button>,
    },
  ]

  return (
    <>
      <PageHeader
        title="Teacher Preferences"
        subtitle="Open a window so teachers can tell the timetable when they can and cannot teach. Their 'not available' slots and daily maximum are firm rules for both the automatic and the manual timetable; stars and first / last lecture are honoured as far as possible."
        badge="Phase 6"
      />
      <SchoolBar />
      {error ? (
        <Alert tone="danger">
          {error.message}{' '}
          <button type="button" onClick={reload} className="font-semibold underline">Try again</button>
        </Alert>
      ) : loading && !data ? (
        <div className="space-y-3"><Skeleton className="h-40" /><Skeleton className="h-72" /></div>
      ) : data ? (
        <div className="space-y-5">
          <Panel title="Preference window" icon={CalendarClock}>
            <div className="flex flex-wrap items-center gap-6">
              <Ring
                percent={data.total ? Math.round((100 * data.filled) / data.total) : 0}
                tone={data.filled === data.total && data.total > 0 ? 'success' : 'brand'}
                label={`${data.filled}/${data.total}`}
                caption="teachers have filled"
              />
              <div className="min-w-0 flex-1" data-testid="window-state">
                {!win ? (
                  <>
                    <p className="text-[15px] font-semibold text-ink">No window yet</p>
                    <p className="mt-1 text-sm text-muted">Teachers cannot change their preferences until you open a window. Every teacher of the school is told when you do.</p>
                    <div className="mt-4"><Button icon={<LockOpen className="size-4" />} onClick={() => setDialog('open')} data-testid="open-window">Open a preference window</Button></div>
                  </>
                ) : open ? (
                  <>
                    <p className="flex flex-wrap items-center gap-2 text-[15px] font-semibold text-ink"><Badge tone="success">Open</Badge> until {win.deadline_text}</p>
                    <p className="mt-1 text-sm text-muted">About {formatDuration(win.seconds_left)} left.{win.note ? ` Your note: "${win.note}".` : ''} A reminder goes to the teachers who have not filled {data.settings.reminder_hours} hours before the deadline, and the window closes by itself at the deadline.</p>
                    <div className="mt-4 flex flex-wrap gap-2">
                      <Button variant="secondary" icon={<Timer className="size-4" />} onClick={() => setDialog('extend')} data-testid="extend-window">Extend the deadline</Button>
                      <Button variant="danger" icon={<Lock className="size-4" />} onClick={() => setDialog('close')} data-testid="close-window">Close now</Button>
                    </div>
                  </>
                ) : (
                  <>
                    <p className="flex flex-wrap items-center gap-2 text-[15px] font-semibold text-ink">
                      <Badge tone="neutral">{win.status === 'open' ? 'Ending' : 'Closed'}</Badge>
                      {win.closed_reason === 'deadline' || win.status === 'open' ? 'The deadline passed' : 'You closed it'}
                      {win.closed_at ? ` (${whenText(win.closed_at)})` : ''}
                    </p>
                    <p className="mt-1 text-sm text-muted">Teachers can still look at their preferences but cannot change them. Reopen the same round to let the {data.total - data.filled} who have not filled catch up, or start a fresh round.</p>
                    <div className="mt-4 flex flex-wrap gap-2">
                      <Button icon={<LockOpen className="size-4" />} onClick={() => setDialog('reopen')} data-testid="reopen-window">Reopen this round</Button>
                      <Button variant="secondary" onClick={() => setDialog('open')} data-testid="open-window">Start a new round</Button>
                    </div>
                  </>
                )}
              </div>
            </div>
          </Panel>

          <Panel title="Rules for this school" icon={BellRing}>
            <div className="grid items-end gap-4 sm:grid-cols-[1fr_1fr_auto]">
              <TextField label="Most 'not available' slots one teacher may mark" type="number" min={0} max={72} value={limit} onChange={(e) => setLimit(e.target.value)} hint="Stops anyone from blocking the whole week. A teacher above it must reduce before saving." data-testid="limit-input" />
              <TextField label="Send the reminder this many hours before the deadline" type="number" min={1} max={168} value={remind} onChange={(e) => setRemind(e.target.value)} hint="Only teachers who have not filled get it, once." />
              <Button icon={<Save className="size-4" />} onClick={() => void saveSettings()} loading={saving} disabled={limit === '' || remind === ''} data-testid="save-settings">Save</Button>
            </div>
            {settingsNote && <Alert tone="warning" className="mt-4">{settingsNote}</Alert>}
          </Panel>

          <Panel
            title={`Teachers of ${data.school.name}`}
            icon={UserRound}
            action={
              <div role="group" aria-label="Filter" className="inline-flex gap-1 rounded-lg bg-surface-2 p-0.5 ring-1 ring-line">
                {([['all', `All ${data.total}`], ['missing', `Not filled ${data.total - data.filled}`], ['filled', `Filled ${data.filled}`]] as [Filter, string][]).map(([k, label]) => (
                  <button key={k} type="button" onClick={() => setFilter(k)} aria-pressed={filter === k} className={`rounded-md px-2.5 py-1 text-[12px] font-medium ${filter === k ? 'bg-surface text-ink shadow-sm' : 'text-muted hover:text-ink'}`}>{label}</button>
                ))}
              </div>
            }
          >
            <DataTable
              caption="Teachers and whether they have filled their preferences"
              columns={columns}
              rows={rows}
              rowKey={(t) => t.id}
              empty={<EmptyState icon={UserRound} title="Nobody here">{filter === 'missing' ? 'Everybody has filled.' : 'No teacher matches this filter.'}</EmptyState>}
              card={(t) => (
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-ink">{t.name}</p>
                    <p className="text-[12px] text-muted">{t.filled ? 'Filled' : 'Not filled'} · {t.unavailable} not available · {t.preferred} preferred</p>
                  </div>
                  <Button size="sm" variant="secondary" icon={<Eye className="size-4" />} onClick={() => setView(t)}>View</Button>
                </div>
              )}
            />
          </Panel>

          {data.history.length > 1 && (
            <Panel title="Earlier rounds" icon={History}>
              <ul className="divide-y divide-line text-sm">
                {data.history.map((h) => (
                  <li key={h.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                    <span className="text-ink">{whenText(h.opened_at)} to {whenText(h.deadline)}</span>
                    <span className="text-muted">{h.filled} filled · {h.status === 'open' ? 'open' : h.closed_reason === 'deadline' ? 'closed at the deadline' : 'closed by you'}</span>
                  </li>
                ))}
              </ul>
            </Panel>
          )}
        </div>
      ) : null}

      <DeadlineDialog
        open={dialog === 'open'}
        title="Open a preference window"
        description="Every teacher of the school (and you, as a teacher) is notified straight away. Pick the last moment they can save."
        confirmLabel="Open and notify"
        withNote
        onClose={() => setDialog(null)}
        onSubmit={(deadline, note) => post('/prefs/window/open', { deadline, note: note || null }, 'Window opened.')}
      />
      <DeadlineDialog
        open={dialog === 'extend'}
        title="Extend the deadline"
        description="Teachers who have not filled yet are told the new deadline."
        confirmLabel="Extend"
        onClose={() => setDialog(null)}
        onSubmit={(deadline) => post('/prefs/window/extend', { deadline }, 'Deadline extended.')}
      />
      <DeadlineDialog
        open={dialog === 'reopen'}
        title="Reopen this round"
        description="The teachers who already filled stay filled; the others are told the window is open again."
        confirmLabel="Reopen"
        onClose={() => setDialog(null)}
        onSubmit={(deadline) => post('/prefs/window/reopen', { deadline }, 'Window reopened.')}
      />
      <ConfirmDialog
        open={dialog === 'close'}
        title="Close the window now?"
        tone="danger"
        confirmLabel="Close it"
        onClose={() => setDialog(null)}
        onConfirm={() => post('/prefs/window/close', {}, 'Window closed.')}
      >
        <p>Teachers will be able to look at their preferences but not change them. You can reopen it later.</p>
      </ConfirmDialog>
      <TeacherDialog teacher={view} onClose={() => setView(null)} />
    </>
  )
}
