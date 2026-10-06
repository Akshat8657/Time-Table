import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { CircleCheck, CircleX, Lock, Pencil, Pin, Plus, TriangleAlert, Utensils } from 'lucide-react'
import { useAuth } from '../auth/AuthProvider'
import { Alert } from '../components/Alert'
import { Badge } from '../components/Badge'
import { Button } from '../components/Button'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { DataTable } from '../components/DataTable'
import { TextField } from '../components/Field'
import { Modal } from '../components/Modal'
import { EmptyState, PageHeader, Panel, Skeleton } from '../components/Panel'
import { SchoolBar } from '../components/SchoolBar'
import { SelectField } from '../components/Select'
import { useToast } from '../components/Toast'
import { api, ApiError } from '../lib/api'
import { useDebounced } from '../lib/helpers'
import { ROLE_PREFIX } from '../lib/nav'
import { useSchoolScope } from '../lib/scope'
import { useTerm } from '../lib/term'
import type { Account, FixedSlot, FixedSlotsPayload, GridInfo, SubjectRow } from '../lib/types'
import { useApi } from '../lib/useApi'

type Kind = 'PE' | 'SOL' | 'OTHER'

interface Preset {
  semester_id: string
  day: number
  start_period: number
  kind: Kind
}

const KIND_STYLE: Record<Kind, string> = {
  PE: 'border-success-line bg-success-soft text-success',
  SOL: 'border-info-line bg-info-soft text-info',
  OTHER: 'border-brand/40 bg-brand-soft text-brand-text',
}
const KIND_NAME: Record<Kind, string> = { PE: 'PE', SOL: 'SOL', OTHER: 'Locked' }

// ---------------------------------------------------------------------- the weekly grid of one class
type Row = { type: 'period'; number: number; start: string; end: string } | { type: 'lunch'; start: string; end: string }

function buildRows(grid: GridInfo): Row[] {
  const rows: Row[] = []
  grid.periods.forEach((p, i) => {
    rows.push({ type: 'period', ...p })
    const next = grid.periods[i + 1]
    if (grid.lunch && next && p.end <= grid.lunch.start && grid.lunch.end <= next.start) rows.push({ type: 'lunch', ...grid.lunch })
  })
  return rows
}

function WeekGrid({
  grid, days, slots, onAdd, onEdit,
}: {
  grid: GridInfo
  days: FixedSlotsPayload['classes'][number]['days']
  slots: FixedSlot[]
  onAdd: (day: number, period: number) => void
  onEdit: (slot: FixedSlot) => void
}) {
  const rows = useMemo(() => buildRows(grid), [grid])
  const working = days.filter((d) => d.working)
  const rowOfPeriod = useMemo(() => {
    const map = new Map<number, number>()
    rows.forEach((r, i) => r.type === 'period' && map.set(r.number, i))
    return map
  }, [rows])

  // which cell is the start of a slot (with its row span) and which cells are covered by one
  const { starts, covered } = useMemo(() => {
    const starts = new Map<string, { slot: FixedSlot; span: number }>()
    const covered = new Set<string>()
    for (const s of slots) {
      const a = rowOfPeriod.get(s.start_period)
      const b = rowOfPeriod.get(s.start_period + s.length - 1)
      if (a === undefined || b === undefined) continue
      starts.set(`${s.day}:${a}`, { slot: s, span: b - a + 1 })
      for (let r = a + 1; r <= b; r++) covered.add(`${s.day}:${r}`)
    }
    return { starts, covered }
  }, [slots, rowOfPeriod])

  return (
    <div className="relative overflow-x-auto rounded-xl border border-line">
      <table className="w-full min-w-[40rem] table-fixed border-collapse text-sm">
        <caption className="sr-only">Weekly grid of the class: periods in the rows, days in the columns</caption>
        <thead>
          <tr className="bg-surface-2 text-xs font-semibold uppercase tracking-wide text-muted">
            <th scope="col" className="w-28 px-3 py-2.5 text-left">Period</th>
            {working.map((d) => (
              <th key={d.day} scope="col" className="px-2 py-2.5 text-center">
                {d.name}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, ri) => (
            <tr key={ri} className="border-t border-line">
              <th scope="row" className={`px-3 py-2 text-left align-middle ${row.type === 'lunch' ? 'bg-warning-soft' : 'bg-surface-2/60'}`}>
                {row.type === 'lunch' ? (
                  <span className="flex items-center gap-1.5 text-xs font-semibold text-warning">
                    <Utensils className="size-3.5" aria-hidden="true" /> Lunch
                    <span className="font-normal tabular-nums">{row.start}-{row.end}</span>
                  </span>
                ) : (
                  <>
                    <span className="block text-[13px] font-semibold text-ink">Period {row.number}</span>
                    <span className="block text-xs tabular-nums text-muted">{row.start}-{row.end}</span>
                  </>
                )}
              </th>
              {working.map((d) => {
                const key = `${d.day}:${ri}`
                if (covered.has(key)) return null
                const start = starts.get(key)
                if (start) {
                  const { slot, span } = start
                  const bad = slot.problems.length > 0
                  return (
                    <td key={d.day} rowSpan={span} className="p-1.5 align-top">
                      <button
                        type="button"
                        onClick={() => onEdit(slot)}
                        title={bad ? slot.problems.join(' ') : 'Click to edit'}
                        className={`flex h-full min-h-[3.5rem] w-full flex-col items-start justify-center gap-0.5 rounded-lg border px-2.5 py-2 text-left transition hover:brightness-95 ${bad ? 'border-danger-line bg-danger-soft text-danger' : KIND_STYLE[slot.kind]}`}
                      >
                        <span className="flex items-center gap-1 text-xs font-bold uppercase tracking-wide">
                          {bad ? <TriangleAlert className="size-3.5" aria-hidden="true" /> : <Lock className="size-3" aria-hidden="true" />}
                          {KIND_NAME[slot.kind]}
                          {slot.length > 1 ? ` · ${slot.length} periods` : ''}
                        </span>
                        <span className="text-[13px] font-semibold leading-tight">{slot.label}</span>
                        {slot.time && <span className="text-xs tabular-nums opacity-80">{slot.time}</span>}
                        {slot.teacher_name && (
                          <span className="text-xs opacity-80">
                            {slot.teacher_name}
                            {slot.co_teacher_name ? ` + ${slot.co_teacher_name}` : ''}
                          </span>
                        )}
                      </button>
                    </td>
                  )
                }
                if (row.type === 'lunch') {
                  return <td key={d.day} className="bg-warning-soft/40 p-1.5" aria-label="Lunch" />
                }
                const info = d
                const unavailable = info.is_off ? 'OFF' : info.end_time && row.end > info.end_time ? `After ${info.end_time}` : null
                if (unavailable) {
                  return (
                    <td key={d.day} className="p-1.5">
                      <div
                        className="flex h-full min-h-[3.5rem] items-center justify-center rounded-lg bg-[repeating-linear-gradient(135deg,var(--surface-2),var(--surface-2)_6px,var(--surface-hover)_6px,var(--surface-hover)_12px)] text-xs text-subtle"
                        title={info.is_off ? `${d.name} is OFF for this class` : `This class ends at ${info.end_time} on ${d.name}`}
                      >
                        {unavailable}
                      </div>
                    </td>
                  )
                }
                return (
                  <td key={d.day} className="p-1.5">
                    <button
                      type="button"
                      onClick={() => onAdd(d.day, row.number)}
                      aria-label={`Lock ${d.name} period ${row.number}`}
                      className="group flex h-full min-h-[3.5rem] w-full items-center justify-center rounded-lg border border-dashed border-line-strong text-subtle transition-colors hover:border-brand hover:bg-brand-soft hover:text-brand-text"
                    >
                      <Plus className="size-4 opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100" aria-hidden="true" />
                    </button>
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ---------------------------------------------------------------------- add / edit dialog
function SlotDialog({
  open, slot, preset, payload, people, onClose, onSaved,
}: {
  open: boolean
  slot: FixedSlot | null
  preset: Preset | null
  payload: FixedSlotsPayload
  people: Account[]
  onClose: () => void
  onSaved: () => void
}) {
  const scope = useSchoolScope()
  const toast = useToast()
  const editing = slot !== null
  const [form, setForm] = useState({ kind: 'PE' as Kind, semester_id: '', day: '1', start_period: '1', length: '1', label: '', teacher_id: '', co_teacher_id: '', subject_id: '', note: '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [check, setCheck] = useState<{ ok: boolean; problems: string[]; notes: string[] } | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const set = <K extends keyof typeof form>(key: K, value: (typeof form)[K]) => setForm((f) => ({ ...f, [key]: value }))

  useEffect(() => {
    if (!open) return
    setError(null)
    setCheck(null)
    if (slot) {
      setForm({
        kind: slot.kind, semester_id: slot.semester_id, day: String(slot.day), start_period: String(slot.start_period), length: String(slot.length),
        label: slot.kind === 'OTHER' ? slot.label : '', teacher_id: slot.teacher_id ?? '', co_teacher_id: slot.co_teacher_id ?? '',
        subject_id: slot.subject_id ?? '', note: slot.note ?? '',
      })
    } else if (preset) {
      setForm({ kind: preset.kind, semester_id: preset.semester_id, day: String(preset.day), start_period: String(preset.start_period), length: '1', label: '', teacher_id: '', co_teacher_id: '', subject_id: '', note: '' })
    }
  }, [open, slot, preset])

  const isOther = form.kind === 'OTHER'
  const length = isOther ? Number(form.length) : 2
  const periods = payload.grid.periods
  const first = periods[Number(form.start_period) - 1]
  const last = periods[Number(form.start_period) + length - 2]
  const cls = payload.classes.find((c) => c.semester_id === form.semester_id)
  const workingDays = (cls?.days ?? payload.classes[0]?.days ?? []).filter((d) => d.working)

  const subjects = useApi<{ subjects: SubjectRow[] }>(open && isOther && form.semester_id ? scope.path(`/subjects?semester_id=${form.semester_id}`) : null)

  // live "is this possible?" (only for a NEW slot; an existing one is checked when you press Save)
  const settled = useDebounced(form, 350)
  useEffect(() => {
    if (!open || editing || !settled.semester_id) return
    if (settled.kind === 'OTHER' && !settled.label.trim()) {
      setCheck(null)
      return
    }
    let cancelled = false
    api<{ ok: boolean; problems: string[]; notes: string[] }>('/fixed-slots/check', {
      method: 'POST',
      body: {
        semester_id: settled.semester_id, day: Number(settled.day), start_period: Number(settled.start_period), kind: settled.kind,
        length: settled.kind === 'OTHER' ? Number(settled.length) : undefined,
        label: settled.label || undefined, teacher_id: settled.teacher_id || undefined, co_teacher_id: settled.co_teacher_id || undefined,
        subject_id: settled.subject_id || undefined,
      },
    }).then(
      (res) => !cancelled && setCheck(res),
      () => !cancelled && setCheck(null),
    )
    return () => {
      cancelled = true
    }
  }, [settled, open, editing])

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    const body = {
      day: Number(form.day), start_period: Number(form.start_period), kind: form.kind,
      length: isOther ? Number(form.length) : undefined,
      label: form.label || undefined, teacher_id: form.teacher_id || null, co_teacher_id: form.co_teacher_id || null,
      subject_id: form.subject_id || null, note: form.note || null,
    }
    try {
      if (editing && slot) {
        const { kind: _kind, ...patch } = body
        void _kind
        await api(`/fixed-slots/${slot.id}`, { method: 'PATCH', body: patch })
      } else {
        await api('/fixed-slots', { method: 'POST', body: { ...body, semester_id: form.semester_id } })
      }
      toast.success('Locked slot saved.')
      onSaved()
      onClose()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  const personOptions = people.map((p) => ({ value: p.id, label: p.role === 'hod' ? `${p.full_name} (HOD)` : p.full_name }))
  const blocked = !editing && !!check && !check.ok

  return (
    <>
      <Modal
        open={open}
        onClose={() => !busy && onClose()}
        size="lg"
        title={editing ? `Edit “${slot?.label}”` : 'Lock a slot'}
        description={editing ? slot?.class_label : 'A locked slot is never moved when timetables are generated.'}
        footer={
          <>
            {editing && (
              <Button variant="danger" className="mr-auto" onClick={() => setConfirmDelete(true)} disabled={busy}>
                Remove lock
              </Button>
            )}
            <Button variant="secondary" onClick={onClose} disabled={busy}>
              Cancel
            </Button>
            <Button type="submit" form="slot-form" loading={busy} disabled={blocked}>
              {editing ? 'Save changes' : 'Lock this slot'}
            </Button>
          </>
        }
      >
        <form id="slot-form" onSubmit={submit} noValidate className="space-y-4">
          {!editing && (
            <fieldset>
              <legend className="mb-2 text-[13px] font-medium text-ink">What is it?</legend>
              <div className="grid gap-2.5 sm:grid-cols-3">
                {(['PE', 'SOL', 'OTHER'] as Kind[]).map((k) => (
                  <label key={k} className={`flex cursor-pointer items-center gap-2.5 rounded-xl border px-3.5 py-2.5 text-sm font-medium transition-colors ${form.kind === k ? 'border-brand bg-brand-soft text-brand-text' : 'border-line-strong bg-surface text-muted hover:bg-hover'}`}>
                    <input type="radio" name="slot-kind" className="size-4 accent-[var(--brand-solid)]" checked={form.kind === k} onChange={() => set('kind', k)} />
                    {k === 'PE' ? 'PE - Physical Education' : k === 'SOL' ? 'SOL - Science of Living' : 'Something else'}
                  </label>
                ))}
              </div>
            </fieldset>
          )}

          <div className="grid gap-4 sm:grid-cols-3">
            <SelectField
              label="Class"
              value={form.semester_id}
              onChange={(v) => set('semester_id', v)}
              disabled={editing}
              options={payload.classes.map((c) => ({ value: c.semester_id, label: c.label }))}
            />
            <SelectField label="Day" value={form.day} onChange={(v) => set('day', v)} options={workingDays.map((d) => ({ value: String(d.day), label: d.name }))} />
            <SelectField
              label="Starts at"
              value={form.start_period}
              onChange={(v) => set('start_period', v)}
              options={periods.map((p) => ({ value: String(p.number), label: `Period ${p.number} (${p.start})` }))}
            />
          </div>

          {isOther ? (
            <div className="grid gap-4 sm:grid-cols-3">
              <TextField className="sm:col-span-2" label="Name" value={form.label} onChange={(e) => set('label', e.target.value)} maxLength={80} placeholder="For example: Seminar" required />
              <SelectField
                label="Number of periods"
                value={form.length}
                onChange={(v) => set('length', v)}
                options={[1, 2, 3, 4].map((n) => ({ value: String(n), label: `${n} ${n === 1 ? 'period' : 'periods'}` }))}
              />
            </div>
          ) : (
            <p className="rounded-lg bg-surface-2 px-3.5 py-2.5 text-[13px] text-muted">
              {form.kind} is <span className="font-semibold text-ink">always a double block</span>: 2 periods one after the other. The short break - or even the lunch - between them is fine. Credits do not decide this.
            </p>
          )}

          {first && last && (
            <p className="text-sm text-muted">
              Occupies <span className="font-semibold text-ink">{length === 1 ? `period ${first.number}` : `periods ${first.number}-${last.number}`}</span> · {first.start}-{last.end}
            </p>
          )}

          <div className="grid gap-4 sm:grid-cols-2">
            <SelectField label="Teacher (optional)" value={form.teacher_id} onChange={(v) => set('teacher_id', v)} placeholder="No teacher" options={personOptions} />
            <SelectField label="Co-teacher (optional)" value={form.co_teacher_id} onChange={(v) => set('co_teacher_id', v)} placeholder="No co-teacher" disabled={!form.teacher_id} options={personOptions.filter((o) => o.value !== form.teacher_id)} />
            {isOther && (
              <SelectField
                className="sm:col-span-2"
                label="Subject (optional)"
                value={form.subject_id}
                onChange={(v) => set('subject_id', v)}
                placeholder="Not linked to a subject"
                hint="If you link a subject and leave the teacher empty, its assigned teacher is used."
                options={(subjects.data?.subjects ?? []).map((s) => ({ value: s.id, label: `${s.name} (${s.code})` }))}
              />
            )}
            <TextField className="sm:col-span-2" label="Note (optional)" value={form.note} onChange={(e) => set('note', e.target.value)} maxLength={300} />
          </div>

          {error && <Alert tone="danger">{error}</Alert>}
          {!editing && check && check.problems.length > 0 && (
            <Alert tone="danger" title="This slot is not possible">
              <ul className="list-disc space-y-0.5 pl-4">
                {check.problems.map((p) => (
                  <li key={p}>{p}</li>
                ))}
              </ul>
            </Alert>
          )}
          {!editing && check && check.ok && (
            <Alert tone="success">
              This slot is possible.
              {check.notes.map((n) => (
                <span key={n} className="mt-0.5 block opacity-90">{n}</span>
              ))}
            </Alert>
          )}
        </form>
      </Modal>

      <ConfirmDialog
        open={confirmDelete}
        tone="danger"
        title="Remove this locked slot?"
        confirmLabel="Remove lock"
        onClose={() => setConfirmDelete(false)}
        onConfirm={async () => {
          if (!slot) return
          await api(`/fixed-slots/${slot.id}`, { method: 'DELETE' })
          toast.success('Locked slot removed.')
          onSaved()
          onClose()
        }}
      >
        <p>The period becomes free again. Nothing else is changed.</p>
      </ConfirmDialog>
    </>
  )
}

// ---------------------------------------------------------------------- the page
export default function FixedSlotsPage() {
  const { user } = useAuth()
  const scope = useSchoolScope()
  const { active, ready: termReady } = useTerm()
  const enabled = scope.ready && !!scope.schoolId && !!active
  const { data, error, loading, reload } = useApi<FixedSlotsPayload>(enabled ? scope.path('/fixed-slots') : null)
  const people = useApi<{ users: Account[] }>(enabled ? scope.path('/users?status=active') : null)

  const [classId, setClassId] = useState('')
  const [dialog, setDialog] = useState<{ slot: FixedSlot | null; preset: Preset | null } | null>(null)

  useEffect(() => {
    if (!data) return
    if (!data.classes.some((c) => c.semester_id === classId)) setClassId(data.classes[0]?.semester_id ?? '')
  }, [data, classId])

  const cls = data?.classes.find((c) => c.semester_id === classId)
  const classSlots = useMemo(() => (data?.slots ?? []).filter((s) => s.semester_id === classId), [data, classId])
  const allProblems = (data?.slots ?? []).filter((s) => s.problems.length > 0)

  if (termReady && !active) {
    return (
      <>
        <PageHeader title="Fixed Slots" subtitle="Lock slots by hand - PE and SOL double blocks, or anything else." />
        <Alert tone="warning" title="Choose the active term first">
          Locked slots belong to the classes of the running term. <Link to={user ? `${ROLE_PREFIX[user.role]}/active-term` : '#'} className="font-semibold underline">Open Active Term</Link> and pick one.
        </Alert>
      </>
    )
  }

  return (
    <>
      <PageHeader
        title="Fixed Slots"
        subtitle="Lock slots by hand before generating timetables. PE and SOL are compulsory for every class and are always a double block (2 periods back-to-back). The generator never moves a locked slot."
      />
      <SchoolBar />

      {error ? (
        <Alert tone="danger">{error.message}</Alert>
      ) : loading && !data ? (
        <div className="space-y-4">
          <Skeleton className="h-40" />
          <Skeleton className="h-96" />
        </div>
      ) : data && data.classes.length === 0 ? (
        <Panel title="Classes" icon={Pin}>
          <EmptyState icon={Pin} title="No class runs in this term">
            Add courses in College Setup (administrator) - the classes of the active term appear here.
          </EmptyState>
        </Panel>
      ) : data ? (
        <div className="space-y-6">
          {allProblems.length > 0 && (
            <Alert tone="danger" title={`${allProblems.length} locked slot(s) are not possible with the current timing`}>
              Change the timing back, or move / remove these slots: {allProblems.map((s) => `${s.class_label} - ${s.label} (${s.day_name})`).join('; ')}.
            </Alert>
          )}

          <Panel title="PE and SOL - compulsory for every class" icon={Pin}>
            <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {data.classes.map((c) => (
                <li key={c.semester_id} className="rounded-xl border border-line bg-surface-2/50 p-3.5">
                  <button type="button" onClick={() => setClassId(c.semester_id)} className="mb-2.5 text-left text-sm font-semibold text-ink hover:text-brand-text">
                    {c.label}
                  </button>
                  <div className="flex flex-wrap gap-2">
                    {(['PE', 'SOL'] as const).map((k) => {
                      const has = k === 'PE' ? c.has_pe : c.has_sol
                      return has ? (
                        <Badge key={k} tone="success">
                          <CircleCheck className="size-3.5" aria-hidden="true" /> {k} fixed
                        </Badge>
                      ) : (
                        <button
                          key={k}
                          type="button"
                          onClick={() => setDialog({ slot: null, preset: { semester_id: c.semester_id, day: 1, start_period: 1, kind: k } })}
                          className="inline-flex items-center gap-1 rounded-full bg-warning-soft px-2.5 py-0.5 text-xs font-medium text-warning ring-1 ring-inset ring-warning-line hover:brightness-95"
                        >
                          <CircleX className="size-3.5" aria-hidden="true" /> {k} missing - add
                        </button>
                      )
                    })}
                  </div>
                </li>
              ))}
            </ul>
          </Panel>

          <Panel
            title={cls?.label ?? 'Weekly grid'}
            icon={Pin}
            action={
              <SelectField
                compact
                aria-label="Class"
                className="w-52"
                value={classId}
                onChange={setClassId}
                options={data.classes.map((c) => ({ value: c.semester_id, label: c.label }))}
              />
            }
          >
            <p className="mb-4 text-sm text-muted">
              Click an empty period to lock it. Click a locked slot to edit or remove it. Hatched cells are not available for this class (day OFF, or after its end time).
            </p>
            {cls && (
              <WeekGrid
                grid={data.grid}
                days={cls.days}
                slots={classSlots}
                onAdd={(day, period) => setDialog({ slot: null, preset: { semester_id: classId, day, start_period: period, kind: cls.has_pe ? (cls.has_sol ? 'OTHER' : 'SOL') : 'PE' } })}
                onEdit={(slot) => setDialog({ slot, preset: null })}
              />
            )}
            <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-muted">
              <Badge tone="success">PE</Badge>
              <Badge tone="info">SOL</Badge>
              <Badge tone="brand">Other locked slot</Badge>
              <Badge tone="danger">Not possible with the current timing</Badge>
            </div>
          </Panel>

          <Panel title="All locked slots of the school" icon={Pin}>
            <DataTable
              caption="All locked slots"
              rows={data.slots}
              rowKey={(s) => s.id}
              empty="No slot is locked yet. Start with PE and SOL for every class."
              columns={[
                { key: 'class', header: 'Class', cell: (s) => <span className="whitespace-nowrap font-medium text-ink">{s.class_label}</span> },
                {
                  key: 'what',
                  header: 'Slot',
                  cell: (s) => (
                    <div className="flex flex-wrap items-center gap-1.5">
                      <Badge tone={s.kind === 'PE' ? 'success' : s.kind === 'SOL' ? 'info' : 'brand'}>{KIND_NAME[s.kind]}</Badge>
                      <span className="text-ink">{s.label}</span>
                    </div>
                  ),
                },
                {
                  key: 'when',
                  header: 'When',
                  cell: (s) => (
                    <div className="whitespace-nowrap">
                      <p className="text-ink">{s.day_name}, {s.length === 1 ? `period ${s.start_period}` : `periods ${s.start_period}-${s.start_period + s.length - 1}`}</p>
                      <p className="text-xs tabular-nums text-muted">{s.time}</p>
                    </div>
                  ),
                },
                {
                  key: 'teacher',
                  header: 'Teacher',
                  hideBelow: 'md',
                  cell: (s) => <span className="text-muted">{s.teacher_name ? `${s.teacher_name}${s.co_teacher_name ? ` + ${s.co_teacher_name}` : ''}` : '-'}</span>,
                },
                {
                  key: 'state',
                  header: 'Status',
                  hideBelow: 'sm',
                  cell: (s) =>
                    s.problems.length > 0 ? (
                      <span title={s.problems.join(' ')}>
                        <Badge tone="danger">Not possible now</Badge>
                      </span>
                    ) : s.notes.length > 0 ? (
                      <span title={s.notes.join(' ')}>
                        <Badge tone="info">Around lunch</Badge>
                      </span>
                    ) : (
                      <Badge tone="success">OK</Badge>
                    ),
                },
                {
                  key: 'edit',
                  header: <span className="sr-only">Edit</span>,
                  className: 'w-12 text-right',
                  cell: (s) => <Button variant="ghost" size="sm" aria-label={`Edit ${s.label}`} icon={<Pencil className="size-4" />} onClick={() => setDialog({ slot: s, preset: null })} />,
                },
              ]}
            />
          </Panel>
        </div>
      ) : null}

      {data && (
        <SlotDialog
          open={dialog !== null}
          slot={dialog?.slot ?? null}
          preset={dialog?.preset ?? null}
          payload={data}
          people={people.data?.users ?? []}
          onClose={() => setDialog(null)}
          onSaved={reload}
        />
      )}
    </>
  )
}
