import { useEffect, useMemo, useState } from 'react'
import { CalendarClock, Clock, Lock, Save, Utensils } from 'lucide-react'
import { useAuth } from '../auth/AuthProvider'
import { Alert } from '../components/Alert'
import { Badge } from '../components/Badge'
import { Button } from '../components/Button'
import { Checkbox, TextField } from '../components/Field'
import { PageHeader, Panel, Skeleton } from '../components/Panel'
import { PeriodTimeline } from '../components/PeriodTimeline'
import { SchoolBar } from '../components/SchoolBar'
import { SelectField, type Option } from '../components/Select'
import { useToast } from '../components/Toast'
import { api, ApiError } from '../lib/api'
import { useDebounced } from '../lib/helpers'
import { useSchoolScope } from '../lib/scope'
import { useTerm } from '../lib/term'
import type { ClassTiming, DayInfo, GridInfo, TimingPayload } from '../lib/types'
import { useApi } from '../lib/useApi'

interface Draft {
  college_start: string
  college_end: string
  lecture_minutes: string
  break_minutes: string
  days_per_week: string
  has_lunch: boolean
  lunch_start: string
  lunch_end: string
}

function toDraft(t: TimingPayload): Draft {
  return {
    college_start: t.college.college_start,
    college_end: t.college.college_end,
    lecture_minutes: String(t.college.lecture_minutes),
    break_minutes: String(t.college.break_minutes),
    days_per_week: String(t.college.days_per_week),
    has_lunch: !!t.school?.lunch_start,
    lunch_start: t.school?.lunch_start ?? '13:10',
    lunch_end: t.school?.lunch_end ?? '13:40',
  }
}

// ---------------------------------------------------------------------- one class: each weekday its own end time / OFF
function endOptions(grid: GridInfo, current: DayInfo | null): Option[] {
  const opts: Option[] = [{ value: '', label: `Full day (till ${grid.college_end})` }]
  const seen = new Set<string>()
  for (const p of grid.periods) {
    if (p.end >= grid.college_end) continue
    seen.add(p.end)
    opts.push({ value: p.end, label: `Till ${p.end} (after period ${p.number})` })
  }
  if (current?.end_time && !seen.has(current.end_time)) opts.push({ value: current.end_time, label: `Till ${current.end_time}` })
  opts.push({ value: 'off', label: 'OFF - no lectures' })
  return opts
}

const valueOf = (d: DayInfo) => (d.is_off ? 'off' : (d.end_time ?? ''))
const toApi = (day: number, value: string) => ({ day, is_off: value === 'off', end_time: value && value !== 'off' ? value : null })

function ClassSchedule({ cls, grid, onChange, saving }: { cls: ClassTiming; grid: GridInfo; onChange: (days: { day: number; is_off: boolean; end_time: string | null }[]) => void; saving: boolean }) {
  const working = cls.days.filter((d) => d.working)
  const custom = working.some((d) => d.is_off || d.end_time)
  const weekOptions = endOptions(grid, null)
  return (
    <div className="rounded-xl border border-line bg-surface-2/50 p-4">
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <h3 className="text-sm font-semibold text-ink">{cls.label}</h3>
        <Badge tone={custom ? 'brand' : 'neutral'}>{custom ? 'Custom schedule' : 'Full days'}</Badge>
        <div className="ml-auto flex items-center gap-2">
          <span className="text-[13px] text-muted">Whole week:</span>
          <SelectField
            compact
            aria-label={`Set the whole week of ${cls.label}`}
            className="w-52"
            value=""
            placeholder="Choose for all days…"
            disabled={saving}
            onChange={(v) => v !== '' && onChange(working.map((d) => toApi(d.day, v)))}
            options={weekOptions.filter((o) => o.value !== '')}
          />
          <Button variant="ghost" size="sm" disabled={saving || !custom} onClick={() => onChange(working.map((d) => toApi(d.day, '')))}>
            Reset to full days
          </Button>
        </div>
      </div>
      <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
        {working.map((d) => (
          <div key={d.day} className={`rounded-lg border p-2.5 ${d.is_off ? 'border-warning-line bg-warning-soft' : 'border-line bg-surface'}`}>
            <p className="mb-1.5 flex items-center justify-between text-xs font-semibold uppercase tracking-wide text-muted">
              {d.short}
              <span className="font-normal normal-case tabular-nums">{d.is_off ? 'off' : `${d.periods_available} periods`}</span>
            </p>
            <SelectField
              compact
              aria-label={`${cls.label} on ${d.name}`}
              value={valueOf(d)}
              disabled={saving}
              onChange={(v) => onChange([toApi(d.day, v)])}
              options={endOptions(grid, d)}
            />
          </div>
        ))}
      </div>
    </div>
  )
}

export default function TimingPage() {
  const { user } = useAuth()
  const scope = useSchoolScope()
  const toast = useToast()
  const { active } = useTerm()
  const isAdmin = user?.role === 'admin'

  const path = scope.ready && (!isAdmin || scope.schoolId) ? scope.path('/timing') : null
  const timing = useApi<TimingPayload>(path)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [grid, setGrid] = useState<GridInfo | null>(null)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [busy, setBusy] = useState<'college' | 'lunch' | null>(null)
  const [days, setDays] = useState<Record<string, DayInfo[]>>({})
  const [savingClass, setSavingClass] = useState<string | null>(null)
  const [errors, setErrors] = useState<Record<string, string>>({})

  // load / reload: take over the saved values
  useEffect(() => {
    if (!timing.data) return
    setDraft(toDraft(timing.data))
    setGrid(timing.data.grid)
    setDays({})
    setPreviewError(null)
    setErrors({})
  }, [timing.data])

  // live preview of the period grid while typing (nothing is saved)
  const settled = useDebounced(draft, 400)
  useEffect(() => {
    if (!settled || !timing.data) return
    const numbers = [settled.lecture_minutes, settled.break_minutes].map(Number)
    if (!settled.college_start || !settled.college_end || numbers.some((n) => Number.isNaN(n))) return
    if (settled.has_lunch && (!settled.lunch_start || !settled.lunch_end)) return
    let cancelled = false
    api<{ grid: GridInfo }>('/timing/preview', {
      method: 'POST',
      body: {
        college_start: settled.college_start,
        college_end: settled.college_end,
        lecture_minutes: numbers[0],
        break_minutes: numbers[1],
        lunch_start: settled.has_lunch ? settled.lunch_start : null,
        lunch_end: settled.has_lunch ? settled.lunch_end : null,
      },
    }).then(
      (res) => {
        if (cancelled) return
        setGrid(res.grid)
        setPreviewError(null)
      },
      (err) => {
        if (!cancelled) setPreviewError(err instanceof ApiError ? Object.values(err.fields)[0] ?? err.message : 'The preview could not be built.')
      },
    )
    return () => {
      cancelled = true
    }
  }, [settled, timing.data])

  const dirtyCollege = useMemo(() => {
    if (!draft || !timing.data) return false
    const base = toDraft(timing.data)
    return (['college_start', 'college_end', 'lecture_minutes', 'break_minutes', 'days_per_week'] as const).some((k) => draft[k] !== base[k])
  }, [draft, timing.data])
  const dirtyLunch = useMemo(() => {
    if (!draft || !timing.data) return false
    const base = toDraft(timing.data)
    return draft.has_lunch !== base.has_lunch || (draft.has_lunch && (draft.lunch_start !== base.lunch_start || draft.lunch_end !== base.lunch_end))
  }, [draft, timing.data])

  const set = <K extends keyof Draft>(key: K, value: Draft[K]) => setDraft((d) => (d ? { ...d, [key]: value } : d))

  async function saveCollege() {
    if (!draft) return
    setBusy('college')
    setErrors({})
    try {
      const res = await api<{ warnings: string[] }>('/timing/college', {
        method: 'PUT',
        body: {
          college_start: draft.college_start,
          college_end: draft.college_end,
          lecture_minutes: Number(draft.lecture_minutes),
          break_minutes: Number(draft.break_minutes),
          days_per_week: Number(draft.days_per_week),
        },
      })
      toast.success('College timing saved.')
      res.warnings.forEach((w) => toast.warning(w))
      timing.reload()
    } catch (err) {
      if (err instanceof ApiError) {
        setErrors(err.fields)
        toast.error(Object.keys(err.fields).length > 0 ? 'Please check the highlighted fields.' : err.message)
      } else toast.error('Could not save. Please try again.')
    } finally {
      setBusy(null)
    }
  }

  async function saveLunch() {
    if (!draft) return
    setBusy('lunch')
    setErrors({})
    try {
      const res = await api<{ warnings: string[] }>('/timing/school', {
        method: 'PUT',
        body: { ...scope.body(), lunch_start: draft.has_lunch ? draft.lunch_start : null, lunch_end: draft.has_lunch ? draft.lunch_end : null },
      })
      toast.success('Lunch time saved.')
      res.warnings.forEach((w) => toast.warning(w))
      timing.reload()
    } catch (err) {
      if (err instanceof ApiError) {
        setErrors(err.fields)
        toast.error(Object.keys(err.fields).length > 0 ? 'Please check the lunch time.' : err.message)
      } else toast.error('Could not save. Please try again.')
    } finally {
      setBusy(null)
    }
  }

  async function saveDays(cls: ClassTiming, changes: { day: number; is_off: boolean; end_time: string | null }[]) {
    setSavingClass(cls.semester_id)
    try {
      const res = await api<{ days: DayInfo[]; warnings: string[] }>(`/timing/semesters/${cls.semester_id}/days`, { method: 'PUT', body: { days: changes } })
      setDays((d) => ({ ...d, [cls.semester_id]: res.days }))
      toast.success(`${cls.label} saved.`)
      res.warnings.forEach((w) => toast.warning(w))
    } catch (err) {
      toast.error(err instanceof ApiError ? Object.values(err.fields)[0] ?? err.message : 'Could not save. Please try again.')
    } finally {
      setSavingClass(null)
    }
  }

  const data = timing.data
  const classes = data?.classes ?? []

  return (
    <>
      <PageHeader
        title={isAdmin ? 'Timing Setup' : 'School Timing'}
        subtitle={
          isAdmin
            ? 'College hours, lecture length, the short break after every lecture and the working days. The daily period grid is built from these - and you can adjust the lunch and the day-wise schedule of any school.'
            : "Your school's lunch time and the day-wise schedule of every class: each weekday can end at its own time, or be switched OFF completely."
        }
      />

      {timing.error ? (
        <Alert tone="danger">{timing.error.message}</Alert>
      ) : !draft || !grid ? (
        <div className="space-y-4">
          <Skeleton className="h-56" />
          <Skeleton className="h-40" />
        </div>
      ) : (
        <div className="space-y-6">
          <Panel
            title="College hours and working days"
            icon={Clock}
            action={!isAdmin ? <Badge><Lock className="size-3" aria-hidden="true" /> Set by the administrator</Badge> : undefined}
          >
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
              <TextField label="College opens" type="time" value={draft.college_start} onChange={(e) => set('college_start', e.target.value)} disabled={!isAdmin} error={errors.college_start} />
              <TextField label="College closes" type="time" value={draft.college_end} onChange={(e) => set('college_end', e.target.value)} disabled={!isAdmin} error={errors.college_end} />
              <TextField label="Lecture (minutes)" type="number" min={15} max={180} value={draft.lecture_minutes} onChange={(e) => set('lecture_minutes', e.target.value)} disabled={!isAdmin} error={errors.lecture_minutes} />
              <TextField label="Short break (minutes)" type="number" min={0} max={30} value={draft.break_minutes} onChange={(e) => set('break_minutes', e.target.value)} disabled={!isAdmin} error={errors.break_minutes} hint="After every lecture" />
              <SelectField
                label="Working days"
                value={draft.days_per_week}
                onChange={(v) => set('days_per_week', v)}
                disabled={!isAdmin}
                error={errors.days_per_week}
                options={[
                  { value: '5', label: 'Monday - Friday' },
                  { value: '6', label: 'Monday - Saturday' },
                ]}
              />
            </div>
            {isAdmin && (
              <div className="mt-5 flex flex-wrap items-center justify-end gap-3">
                {dirtyCollege && <span className="text-[13px] text-muted">Not saved yet</span>}
                <Button onClick={saveCollege} loading={busy === 'college'} disabled={!dirtyCollege} icon={<Save className="size-4" />}>
                  Save college timing
                </Button>
              </div>
            )}
          </Panel>

          <Panel title="The daily period grid" icon={CalendarClock}>
            <p className="mb-4 text-sm text-muted">
              Built automatically from the hours above: every lecture is followed by the short break, and no lecture overlaps lunch. This is a preview - it changes while you type, and nothing is saved until you press a Save button.
            </p>
            {previewError && (
              <Alert tone="warning" className="mb-4">
                {previewError}
              </Alert>
            )}
            <PeriodTimeline grid={grid} />
          </Panel>

          <SchoolBar />

          <Panel title="Lunch break of the school" icon={Utensils}>
            <p className="mb-4 max-w-2xl text-sm text-muted">Every school can have its own lunch time. No lecture is placed during lunch - but a double block (2 periods back-to-back) may sit around it.</p>
            <Checkbox checked={draft.has_lunch} onChange={(e) => set('has_lunch', e.target.checked)} label="This school has a lunch break" />
            {draft.has_lunch && (
              <div className="mt-4 flex flex-wrap gap-4">
                <TextField label="Lunch starts" type="time" className="w-44" value={draft.lunch_start} onChange={(e) => set('lunch_start', e.target.value)} error={errors.lunch_start} />
                <TextField label="Lunch ends" type="time" className="w-44" value={draft.lunch_end} onChange={(e) => set('lunch_end', e.target.value)} error={errors.lunch_end} />
              </div>
            )}
            <div className="mt-5 flex flex-wrap items-center justify-end gap-3">
              {dirtyLunch && <span className="text-[13px] text-muted">Not saved yet</span>}
              <Button onClick={saveLunch} loading={busy === 'lunch'} disabled={!dirtyLunch} icon={<Save className="size-4" />}>
                Save lunch time
              </Button>
            </div>
          </Panel>

          <Panel title="Day-wise schedule of every class" icon={CalendarClock}>
            <p className="mb-4 max-w-3xl text-sm text-muted">
              Some classes finish early, some do not come at all on certain days (Saturday off, internship days). Choose one end time for the whole week, then change any single day. A day can also be switched
              <span className="font-semibold text-ink"> OFF</span>. No lecture is ever placed after the end time, or on an OFF day. Changes are saved at once.
            </p>
            {!active ? (
              <Alert tone="warning">Choose the active term first - the classes of the running term appear here.</Alert>
            ) : classes.length === 0 ? (
              <p className="text-sm text-muted">No class of this school runs in {active.name}.</p>
            ) : (
              <div className="space-y-4">
                {classes.map((cls) => (
                  <ClassSchedule
                    key={cls.semester_id}
                    cls={{ ...cls, days: days[cls.semester_id] ?? cls.days }}
                    grid={data!.grid}
                    saving={savingClass === cls.semester_id}
                    onChange={(changes) => void saveDays(cls, changes)}
                  />
                ))}
              </div>
            )}
          </Panel>
        </div>
      )}
    </>
  )
}
