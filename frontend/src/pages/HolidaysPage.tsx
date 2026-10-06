import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { CalendarDays, Clock, Pencil, Plus, RotateCcw, Trash2 } from 'lucide-react'
import { useAuth } from '../auth/AuthProvider'
import { Alert } from '../components/Alert'
import { Badge } from '../components/Badge'
import { Button } from '../components/Button'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { Checkbox, TextField } from '../components/Field'
import { Modal } from '../components/Modal'
import { EmptyState, PageHeader, Panel, Skeleton } from '../components/Panel'
import { SchoolBar } from '../components/SchoolBar'
import { SearchBox } from '../components/SearchBox'
import { SelectField } from '../components/Select'
import { Tabs } from '../components/Tabs'
import { TextAreaField } from '../components/Select'
import { useToast } from '../components/Toast'
import { api, ApiError } from '../lib/api'
import { formatRange, queryString, todayIso, useDebounced, weekdayOf } from '../lib/helpers'
import { useSchoolScope } from '../lib/scope'
import { useTerm } from '../lib/term'
import type { CalendarEntry, StructureTree } from '../lib/types'
import { useApi } from '../lib/useApi'

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
const SHORT = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

interface Form {
  kind: 'holiday' | 'event'
  scope: 'college' | 'school'
  title: string
  date_from: string
  date_to: string
  full_day: boolean
  start_time: string
  end_time: string
  note: string
  semester_ids: string[]
}

function EntryDialog({
  open, entry, classes, onClose, onSaved,
}: {
  open: boolean
  entry: CalendarEntry | null
  classes: { id: string; label: string }[]
  onClose: () => void
  onSaved: () => void
}) {
  const { user } = useAuth()
  const scope = useSchoolScope()
  const toast = useToast()
  const isAdmin = user?.role === 'admin'
  const [form, setForm] = useState<Form>({ kind: 'holiday', scope: 'school', title: '', date_from: '', date_to: '', full_day: true, start_time: '10:00', end_time: '13:00', note: '', semester_ids: [] })
  const [busy, setBusy] = useState(false)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [top, setTop] = useState<string | null>(null)
  const set = <K extends keyof Form>(key: K, value: Form[K]) => setForm((f) => ({ ...f, [key]: value }))

  useEffect(() => {
    if (!open) return
    setErrors({})
    setTop(null)
    if (entry) {
      setForm({
        kind: entry.kind, scope: entry.scope, title: entry.title, date_from: entry.date_from,
        date_to: entry.date_to === entry.date_from ? '' : entry.date_to, full_day: entry.full_day,
        start_time: entry.start_time ?? '10:00', end_time: entry.end_time ?? '13:00', note: entry.note ?? '',
        semester_ids: entry.classes.map((c) => c.semester_id),
      })
    } else {
      setForm({ kind: 'holiday', scope: isAdmin ? 'college' : 'school', title: '', date_from: todayIso(), date_to: '', full_day: true, start_time: '10:00', end_time: '13:00', note: '', semester_ids: [] })
    }
  }, [open, entry, isAdmin])

  const college = form.scope === 'college'

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setErrors({})
    setTop(null)
    const body: Record<string, unknown> = {
      kind: college ? 'holiday' : form.kind,
      title: form.title,
      date_from: form.date_from,
      date_to: form.date_to || form.date_from,
      full_day: college ? true : form.full_day,
      start_time: !college && !form.full_day ? form.start_time : null,
      end_time: !college && !form.full_day ? form.end_time : null,
      note: form.note,
      semester_ids: college ? [] : form.semester_ids,
    }
    try {
      if (entry) await api(`/calendar/${entry.id}`, { method: 'PATCH', body })
      else await api('/calendar', { method: 'POST', body: { ...body, scope: form.scope, ...(college ? {} : scope.body()) } })
      toast.success(entry ? 'Saved.' : form.kind === 'holiday' ? 'Holiday added.' : 'Event added.')
      onSaved()
      onClose()
    } catch (err) {
      if (err instanceof ApiError) {
        setErrors(err.fields)
        setTop(err.fields._ ?? (Object.keys(err.fields).length > 0 ? 'Please check the highlighted fields.' : err.message))
      } else setTop('Could not save. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  const title = entry ? `Edit “${entry.title}”` : isAdmin ? 'Add a holiday or event' : 'Add an event or holiday'

  return (
    <Modal
      open={open}
      onClose={() => !busy && onClose()}
      size="lg"
      title={title}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button type="submit" form="entry-form" loading={busy}>
            {entry ? 'Save changes' : 'Add'}
          </Button>
        </>
      }
    >
      <form id="entry-form" onSubmit={submit} noValidate className="space-y-4">
        {top && <Alert tone="danger">{top}</Alert>}

        {isAdmin && !entry && (
          <fieldset>
            <legend className="mb-2 text-[13px] font-medium text-ink">Who is it for?</legend>
            <div className="grid gap-2.5 sm:grid-cols-2">
              {(['college', 'school'] as const).map((s) => (
                <label key={s} className={`flex cursor-pointer items-center gap-2.5 rounded-xl border px-3.5 py-2.5 text-sm font-medium ${form.scope === s ? 'border-brand bg-brand-soft text-brand-text' : 'border-line-strong bg-surface text-muted hover:bg-hover'}`}>
                  <input type="radio" name="entry-scope" className="size-4 accent-[var(--brand-solid)]" checked={form.scope === s} onChange={() => set('scope', s)} />
                  {s === 'college' ? 'The whole college' : (scope.schoolName ?? 'The chosen school')}
                </label>
              ))}
            </div>
          </fieldset>
        )}

        {!college && (
          <fieldset>
            <legend className="mb-2 text-[13px] font-medium text-ink">What is it?</legend>
            <div className="grid gap-2.5 sm:grid-cols-2">
              {(['holiday', 'event'] as const).map((k) => (
                <label key={k} className={`flex cursor-pointer items-center gap-2.5 rounded-xl border px-3.5 py-2.5 text-sm font-medium ${form.kind === k ? 'border-brand bg-brand-soft text-brand-text' : 'border-line-strong bg-surface text-muted hover:bg-hover'}`}>
                  <input type="radio" name="entry-kind" className="size-4 accent-[var(--brand-solid)]" checked={form.kind === k} onChange={() => set('kind', k)} />
                  {k === 'holiday' ? 'Holiday - no lectures' : 'Event - blocks the slots'}
                </label>
              ))}
            </div>
          </fieldset>
        )}

        <TextField label="Title" value={form.title} onChange={(e) => set('title', e.target.value)} error={errors.title} maxLength={150} placeholder={college ? 'For example: Founders Day' : 'For example: Annual Day rehearsal'} required />

        <div className="grid gap-4 sm:grid-cols-2">
          <TextField label="Date" type="date" value={form.date_from} onChange={(e) => set('date_from', e.target.value)} error={errors.date_from} required />
          <TextField label="Last day (only if it lasts longer)" type="date" min={form.date_from} value={form.date_to} onChange={(e) => set('date_to', e.target.value)} error={errors.date_to} />
        </div>

        {!college && (
          <>
            <Checkbox checked={form.full_day} onChange={(e) => set('full_day', e.target.checked)} label="The whole day" description="Untick it to block only some hours of the day." />
            {!form.full_day && (
              <div className="grid gap-4 sm:grid-cols-2">
                <TextField label="From" type="time" value={form.start_time} onChange={(e) => set('start_time', e.target.value)} error={errors.start_time} />
                <TextField label="Until" type="time" value={form.end_time} onChange={(e) => set('end_time', e.target.value)} error={errors.end_time} />
              </div>
            )}
            <fieldset>
              <legend className="mb-1 text-[13px] font-medium text-ink">Which classes?</legend>
              <p className="mb-2 text-xs text-muted">Tick the classes it applies to. Nothing ticked = every class of the school.</p>
              <div className="grid gap-1.5 rounded-xl border border-line-strong bg-surface p-3 sm:grid-cols-2">
                {classes.length === 0 ? (
                  <p className="text-sm text-muted">No class runs in the active term.</p>
                ) : (
                  classes.map((c) => (
                    <div key={c.id} className="rounded-lg px-2 py-1.5 hover:bg-hover">
                      <Checkbox
                        checked={form.semester_ids.includes(c.id)}
                        onChange={(e) => set('semester_ids', e.target.checked ? [...form.semester_ids, c.id] : form.semester_ids.filter((x) => x !== c.id))}
                        label={c.label}
                      />
                    </div>
                  ))
                )}
              </div>
              {errors.semester_ids && <p className="mt-1.5 text-[13px] text-danger">{errors.semester_ids}</p>}
            </fieldset>
          </>
        )}

        <TextAreaField label="Note (optional)" value={form.note} onChange={(v) => set('note', v)} maxLength={300} error={errors.note} rows={2} />
      </form>
    </Modal>
  )
}

export default function HolidaysPage() {
  const { user } = useAuth()
  const scope = useSchoolScope()
  const toast = useToast()
  const { active } = useTerm()
  const isAdmin = user?.role === 'admin'
  const thisYear = new Date().getFullYear()
  const [year, setYear] = useState(String(thisYear))
  const [kind, setKind] = useState<'all' | 'holiday' | 'event'>('all')
  const [search, setSearch] = useState('')
  const q = useDebounced(search)

  const enabled = scope.ready && (!isAdmin || !!scope.schoolId)
  const { data, error, loading, reload } = useApi<{ entries: CalendarEntry[] }>(enabled ? scope.path('/calendar' + queryString({ year, kind, q })) : null)
  const tree = useApi<StructureTree>(enabled && active ? scope.path('/structure/tree') : null)
  const classes = useMemo(
    () =>
      (tree.data?.schools[0]?.courses ?? [])
        .filter((c) => c.is_active)
        .flatMap((c) => c.semesters.filter((s) => s.running).map((s) => ({ id: s.id, label: `${c.name} - Sem ${s.number}` }))),
    [tree.data],
  )

  const [dialog, setDialog] = useState<{ entry: CalendarEntry | null } | null>(null)
  const [deleting, setDeleting] = useState<CalendarEntry | null>(null)
  const [restoring, setRestoring] = useState(false)

  const entries = data?.entries ?? []
  const months = useMemo(() => {
    const map = new Map<string, CalendarEntry[]>()
    for (const e of entries) map.set(e.date_from.slice(0, 7), [...(map.get(e.date_from.slice(0, 7)) ?? []), e])
    return [...map.entries()]
  }, [entries])
  const today = todayIso()

  const years = [thisYear - 1, thisYear, thisYear + 1, thisYear + 2]

  return (
    <>
      <PageHeader
        title={isAdmin ? 'Holidays & Events' : 'Events & Holidays'}
        subtitle={
          isAdmin
            ? 'College-wide holidays (the standard India list is already loaded - edit anything), plus school events. Lectures are never planned on a holiday, and an event can block a whole day or a few hours for chosen classes.'
            : "College holidays are set by the administrator. Here you add your school's own holidays and events - a full day, or only some hours for the classes you choose."
        }
      />
      <SchoolBar>
        {isAdmin && (
          <Button variant="secondary" icon={<RotateCcw className="size-4" />} onClick={() => setRestoring(true)}>
            Restore standard India list
          </Button>
        )}
        <Button icon={<Plus className="size-4" />} onClick={() => setDialog({ entry: null })}>
          {isAdmin ? 'Add holiday / event' : 'Add event / holiday'}
        </Button>
      </SchoolBar>

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Tabs
          label="Show"
          value={kind}
          onChange={setKind}
          items={[
            { value: 'all', label: 'All' },
            { value: 'holiday', label: 'Holidays' },
            { value: 'event', label: 'Events' },
          ]}
        />
        <SelectField compact aria-label="Year" className="w-28" value={year} onChange={setYear} options={years.map((y) => ({ value: String(y), label: String(y) }))} />
        <SearchBox value={search} onChange={setSearch} placeholder="Search by name" className="w-full sm:w-64" />
        {data && <span className="ml-auto text-[13px] text-muted tabular-nums">{entries.length} {entries.length === 1 ? 'entry' : 'entries'}</span>}
      </div>

      {error ? (
        <Alert tone="danger">{error.message}</Alert>
      ) : loading && !data ? (
        <div className="space-y-3">
          <Skeleton className="h-24" />
          <Skeleton className="h-24" />
        </div>
      ) : months.length === 0 ? (
        <Panel title="Calendar" icon={CalendarDays}>
          <EmptyState icon={CalendarDays} title={search ? 'Nothing matches your search' : `No ${kind === 'all' ? 'holiday or event' : kind} in ${year}`}>
            {search ? 'Try a different word.' : 'Use the button above to add one.'}
          </EmptyState>
        </Panel>
      ) : (
        <div className={`space-y-6 ${loading ? 'opacity-60 transition-opacity' : 'transition-opacity'}`}>
          {months.map(([key, list]) => {
            const [y, m] = key.split('-').map(Number)
            return (
              <section key={key} aria-label={`${MONTHS[m - 1]} ${y}`}>
                <h2 className="mb-2.5 px-1 text-sm font-semibold text-muted">
                  {MONTHS[m - 1]} {y}
                </h2>
                <ul className="space-y-2.5">
                  {list.map((e) => {
                    const past = e.date_to < today
                    const day = Number(e.date_from.slice(8))
                    return (
                      <li key={e.id} className={`flex items-start gap-4 rounded-2xl border border-line bg-surface p-4 shadow-card ${past ? 'opacity-60' : ''}`}>
                        <div className={`flex size-14 shrink-0 flex-col items-center justify-center rounded-xl ${e.kind === 'holiday' ? 'bg-success-soft text-success' : 'bg-brand-soft text-brand-text'}`}>
                          <span className="text-lg font-semibold leading-none tabular-nums">{day}</span>
                          <span className="mt-0.5 text-[11px] font-semibold uppercase tracking-wide">{SHORT[m - 1]}</span>
                        </div>
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-2">
                            <h3 className="text-[15px] font-semibold text-ink">{e.title}</h3>
                            <Badge tone={e.kind === 'holiday' ? 'success' : 'brand'}>{e.kind === 'holiday' ? 'Holiday' : 'Event'}</Badge>
                            {e.scope === 'college' ? <Badge tone="info">College-wide</Badge> : <Badge>{e.school_name ?? 'School'}</Badge>}
                            {e.is_preloaded && <Badge>Standard list</Badge>}
                          </div>
                          <p className="mt-0.5 text-sm text-muted">
                            {formatRange(e.date_from, e.date_to)} · {weekdayOf(e.date_from)}
                            {e.date_to !== e.date_from ? ` to ${weekdayOf(e.date_to)}` : ''}
                            {!e.full_day && e.start_time && (
                              <span className="ml-2 inline-flex items-center gap-1 tabular-nums">
                                <Clock className="size-3.5" aria-hidden="true" /> {e.start_time}-{e.end_time}
                              </span>
                            )}
                          </p>
                          {e.scope === 'school' && (
                            <div className="mt-1.5 flex flex-wrap gap-1">
                              {e.classes.length === 0 ? <Badge>All classes</Badge> : e.classes.map((c) => <Badge key={c.semester_id} tone="neutral">{c.label}</Badge>)}
                            </div>
                          )}
                          {e.note && <p className="mt-1.5 text-[13px] text-muted">{e.note}</p>}
                        </div>
                        {e.can_edit && (
                          <div className="flex shrink-0 gap-1">
                            <Button variant="ghost" size="sm" aria-label={`Edit ${e.title}`} icon={<Pencil className="size-4" />} onClick={() => setDialog({ entry: e })} />
                            <Button variant="ghost" size="sm" aria-label={`Delete ${e.title}`} icon={<Trash2 className="size-4" />} onClick={() => setDeleting(e)} />
                          </div>
                        )}
                      </li>
                    )
                  })}
                </ul>
              </section>
            )
          })}
        </div>
      )}

      <EntryDialog open={dialog !== null} entry={dialog?.entry ?? null} classes={classes} onClose={() => setDialog(null)} onSaved={reload} />

      <ConfirmDialog
        open={deleting !== null}
        tone="danger"
        title={`Delete “${deleting?.title}”?`}
        confirmLabel="Delete"
        onClose={() => setDeleting(null)}
        onConfirm={async () => {
          if (!deleting) return
          await api(`/calendar/${deleting.id}`, { method: 'DELETE' })
          toast.success('Deleted.')
          reload()
        }}
      >
        <p>{deleting?.is_preloaded ? 'This is one of the standard holidays. You can bring it back with “Restore standard India list”.' : 'This cannot be undone.'}</p>
      </ConfirmDialog>

      <ConfirmDialog
        open={restoring}
        title="Restore the standard India holiday list?"
        confirmLabel="Restore"
        onClose={() => setRestoring(false)}
        onConfirm={async () => {
          const res = await api<{ added: number }>('/calendar/restore-india-list', { method: 'POST' })
          toast.success(res.added === 0 ? 'Nothing was missing - the list is complete.' : `${res.added} missing holiday(s) added back.`)
          reload()
        }}
      >
        <p>Any official 2026 / 2027 holiday that is missing from the college list is added again. Nothing you changed or added yourself is touched.</p>
      </ConfirmDialog>
    </>
  )
}
