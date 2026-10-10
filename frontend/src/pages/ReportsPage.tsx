import { useEffect, useMemo, useState } from 'react'
import { BookOpenCheck, Building2, CalendarX2, ChartColumn, Gauge, Repeat, Scale, SearchCheck, Settings2, Trophy, UserCheck } from 'lucide-react'
import { useAuth } from '../auth/AuthProvider'
import { Alert } from '../components/Alert'
import { Badge } from '../components/Badge'
import { Button } from '../components/Button'
import { TextField } from '../components/Field'
import { Modal } from '../components/Modal'
import { EmptyState, PageHeader, Panel } from '../components/Panel'
import { CancelledTab, CompareTab, HoursTab, SubstitutionsTab } from '../components/ReportsMore'
import { ExcelButton, ReportFrame, useReport } from '../components/reportKit'
import { SelectField } from '../components/Select'
import { Tabs, type TabItem } from '../components/Tabs'
import { useToast } from '../components/Toast'
import { HBarList, Kpi, PeriodStrip, StackedBar, toneOf, type BarRow } from '../components/charts'
import { api, ApiError } from '../lib/api'
import { formatShort, todayIso } from '../lib/helpers'
import { useSchoolScope } from '../lib/scope'
import type { FreeReport, NormInfo, RankRow, RankingsReport, WorkloadReport } from '../lib/types'

type TabKey = 'workload' | 'rankings' | 'free' | 'hours' | 'cancelled' | 'subs' | 'compare'

const STATUS_WORDS: Record<string, string> = { under: 'under norm', within: 'within norm', over: 'over norm', visiting: 'visiting', none: 'not published' }

// ====================================================================================================== the norm
function NormDialog({ open, onClose, school, isAdmin, onSaved }: { open: boolean; onClose: () => void; school: string; isAdmin: boolean; onSaved: () => void }) {
  const toast = useToast()
  const [info, setInfo] = useState<NormInfo | null>(null)
  const [norm, setNorm] = useState('16')
  const [min, setMin] = useState('12')
  const [label, setLabel] = useState('')
  const [target, setTarget] = useState<'school' | 'college'>('school')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    setError(null)
    setInfo(null)   // the fields stay locked until the current values have arrived (they would overwrite anything typed earlier)
    setTarget(isAdmin && !school ? 'college' : 'school')
    api<NormInfo>(`/reports/norms${school ? `?school_id=${school}` : ''}`).then(
      (d) => {
        setInfo(d)
        setNorm(String(d.effective.norm_hours))
        setMin(String(d.effective.min_hours))
        setLabel(d.effective.source === 'default' ? '' : d.effective.label)
      },
      (err) => setError(err instanceof ApiError ? err.message : 'Could not load the norm.'),
    )
  }, [open, school, isAdmin])

  async function save() {
    setBusy(true)
    setError(null)
    try {
      await api('/reports/norms', {
        method: 'PUT',
        body: { scope: target, school_id: target === 'school' && isAdmin ? school : undefined, norm_hours: Number(norm), min_hours: Number(min), label: label.trim() || null },
      })
      toast.success('The norm is saved. Every workload number now uses it.')
      onSaved()
      onClose()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save.')
    } finally {
      setBusy(false)
    }
  }

  async function dropSchoolNorm() {
    setBusy(true)
    try {
      await api(`/reports/norms${school ? `?school_id=${school}` : ''}`, { method: 'DELETE' })
      toast.success("The school's own norm is removed - the college default applies again.")
      onSaved()
      onClose()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not remove it.')
    } finally {
      setBusy(false)
    }
  }

  const bad = !Number.isInteger(Number(norm)) || !Number.isInteger(Number(min)) || Number(norm) < 1 || Number(min) < 0 || Number(min) > Number(norm)
  return (
    <Modal
      open={open}
      onClose={() => !busy && onClose()}
      title="Workload norm"
      description="Weekly teaching hours a full-time teacher is expected to take (UGC / AICTE). Teachers above it are 'over norm'; below the lowest healthy load they are 'under'."
      footer={
        <>
          {info?.school && target === 'school' && <Button variant="ghost" onClick={() => void dropSchoolNorm()} disabled={busy} className="mr-auto">Use the college default</Button>}
          <Button variant="secondary" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button onClick={() => void save()} loading={busy} disabled={!info || bad || (target === 'school' && isAdmin && !school)} data-testid="norm-save">Save the norm</Button>
        </>
      }
    >
      <div className="space-y-4">
        {error && <Alert tone="danger">{error}</Alert>}
        <div className="flex flex-wrap gap-2" aria-label="Quick values">
          {[['Assistant Professor', 16, 12], ['Associate Professor / Professor', 14, 10]].map(([name, n, m]) => (
            <button key={name} type="button" onClick={() => { setNorm(String(n)); setMin(String(m)); setLabel(`UGC / AICTE - ${name}`) }} className="rounded-full border border-line-strong bg-surface px-3 py-1 text-[13px] text-ink hover:bg-hover">
              {name}: {n} h
            </button>
          ))}
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <TextField label="Norm (hours per week)" type="number" min={1} max={60} value={norm} disabled={!info} onChange={(e) => setNorm(e.target.value)} data-testid="norm-hours" />
          <TextField label="Lowest healthy load (hours)" type="number" min={0} max={60} value={min} disabled={!info} onChange={(e) => setMin(e.target.value)} hint="Below this a teacher shows as 'under'." error={bad && Number(min) > Number(norm) ? 'Cannot be above the norm.' : null} data-testid="norm-min" />
        </div>
        <TextField label="Name (optional)" value={label} maxLength={80} disabled={!info} onChange={(e) => setLabel(e.target.value)} placeholder="UGC / AICTE norm" />
        {isAdmin && (
          <fieldset className="space-y-2">
            <legend className="mb-1 text-[13px] font-medium text-ink">Apply to</legend>
            {([['school', 'Only the selected school'], ['college', 'The whole college (default for every school without its own norm)']] as const).map(([v, t]) => (
              <label key={v} className={`flex items-center gap-2.5 text-sm ${v === 'school' && !school ? 'opacity-50' : ''}`}>
                <input type="radio" name="norm-target" className="accent-[var(--brand-solid)]" checked={target === v} disabled={v === 'school' && !school} onChange={() => setTarget(v)} /> {t}
              </label>
            ))}
            {!school && <p className="text-[12px] text-muted">Pick one school in the filter to give it its own norm.</p>}
          </fieldset>
        )}
        <p className="text-[12px] text-muted">The profile has no designation, so one norm is used for every regular teacher of the school. Visiting faculty are compared with their own weekly limit instead.</p>
      </div>
    </Modal>
  )
}

// ====================================================================================================== workload
function WorkloadTab({ school, isAdmin }: { school: string; isAdmin: boolean }) {
  const state = useReport<WorkloadReport>('workload', school)
  const [filter, setFilter] = useState<'all' | 'over' | 'within' | 'under'>('all')
  const [normOpen, setNormOpen] = useState(false)
  return (
    <>
      <ReportFrame state={state}>
        {(d) => {
          const s = d.summary
          const multi = d.scope.schools.length > 1
          const rows = d.rows.filter((r) => filter === 'all' || r.status === filter)
          const topHours = Math.max(1, ...d.rows.map((r) => r.hours))
          const markers = d.norm ? [{ value: d.norm.norm_hours, label: `Norm ${d.norm.norm_hours} h` }, { value: d.norm.min_hours, label: `Lowest healthy load ${d.norm.min_hours} h` }] : []
          const bars: BarRow[] = rows.map((r) => ({
            key: r.teacher_id,
            label: r.teacher,
            sub: `${r.role}${multi ? ` · ${r.school}` : ''} · ${r.lectures} lectures · ${r.teaching_days} days · max ${r.max_day}/day · ${r.free_periods} free periods`,
            value: r.hours,
            tone: toneOf(r.status),
            right: (
              <span className="inline-flex flex-col items-end gap-0.5">
                <span className="font-semibold">{r.hours} h{r.is_visiting && r.visiting_limit ? ` / ${r.visiting_limit}` : ` · ${r.load_pct}%`}</span>
                <Badge tone={toneOf(r.status)}>{STATUS_WORDS[r.status] ?? r.status}</Badge>
              </span>
            ),
          }))
          return (
            <div className="space-y-5">
              <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-line bg-surface px-4 py-3 shadow-card">
                <p className="text-sm text-ink" data-testid="norm-banner">
                  <Gauge className="mr-1.5 inline size-4 -translate-y-px text-brand-text" aria-hidden="true" />
                  {s.norm_hours ? <>Norm: <span className="font-semibold">{s.norm_hours} hours a week</span> <span className="text-muted">({s.norm_label}, {s.norm_source === 'school' ? "this school's own" : s.norm_source === 'college' ? 'college default' : 'built-in default'}; healthy from {s.min_hours} h)</span></> : <>Schools use different norms - each teacher is compared with their own school's norm.</>}
                </p>
                <div className="flex gap-2">
                  <Button size="sm" variant="secondary" icon={<Settings2 className="size-4" />} onClick={() => setNormOpen(true)} data-testid="edit-norm">Change norm</Button>
                  <ExcelButton name="workload" school={school} />
                </div>
              </div>
              {!s.published && <Alert tone="warning" title="Nothing is published yet">Workload is counted from the PUBLISHED timetable. Publish the timetables first.</Alert>}
              <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
                <Kpi label="Teachers" value={s.teachers} caption={`${s.regular} regular · ${s.visiting} visiting`} />
                <Kpi label="Average hours / week" value={s.avg_hours} caption={`${s.avg_pct}% of the norm (regular faculty)`} />
                <Kpi label="Under the norm" value={s.under} tone={s.under ? 'warning' : 'neutral'} />
                <Kpi label="Within the norm" value={s.within} tone="success" />
                <Kpi label="Over the norm" value={s.over} tone={s.over ? 'danger' : 'neutral'} />
              </div>
              <Panel title="Faculty workload against the norm" icon={ChartColumn}
                action={
                  <div role="group" aria-label="Filter" className="inline-flex gap-1 rounded-lg bg-surface-2 p-0.5 ring-1 ring-line">
                    {(['all', 'over', 'within', 'under'] as const).map((k) => (
                      <button key={k} type="button" onClick={() => setFilter(k)} aria-pressed={filter === k} className={`rounded-md px-2.5 py-1 text-[12px] font-medium capitalize ${filter === k ? 'bg-surface text-ink shadow-sm' : 'text-muted hover:text-ink'}`}>{k}</button>
                    ))}
                  </div>
                }
              >
                <div className="mb-4">
                  <StackedBar ariaLabel="Teachers by workload" segments={[{ label: 'under norm', value: s.under, tone: 'warning' }, { label: 'within norm', value: s.within, tone: 'success' }, { label: 'over norm', value: s.over, tone: 'danger' }, { label: 'visiting', value: s.visiting, tone: 'info' }]} />
                </div>
                {bars.length ? <HBarList rows={bars} max={topHours} markers={markers} ariaLabel="Weekly teaching hours per teacher" /> : <EmptyState icon={UserCheck} title="Nobody here">{d.empty_text}</EmptyState>}
                {d.note && <p className="mt-4 text-[12px] text-muted">{d.note}</p>}
              </Panel>
              {multi && (
                <Panel title="Per school" icon={Building2}>
                  <HBarList
                    ariaLabel="Average workload per school"
                    max={Math.max(100, ...d.per_school.map((p) => p.avg_pct))}
                    markers={[{ value: 100, label: 'Exactly the norm' }]}
                    rows={d.per_school.map((p) => ({ key: p.school_id, label: p.school, sub: `${p.teachers} teachers · ${p.under} under · ${p.within} within · ${p.over} over`, value: p.avg_pct, tone: p.avg_pct > 100 ? 'danger' : p.avg_pct >= 75 ? 'success' : 'warning', right: `${p.avg_hours} h · ${p.avg_pct}%` }))}
                  />
                </Panel>
              )}
            </div>
          )
        }}
      </ReportFrame>
      <NormDialog open={normOpen} onClose={() => setNormOpen(false)} school={school} isAdmin={isAdmin} onSaved={state.reload} />
    </>
  )
}

// ====================================================================================================== rankings
function RankList({ title, icon: Icon, rows, value, unit, tone, empty }: { title: string; icon: typeof Trophy; rows: RankRow[]; value: (r: RankRow) => number; unit: string; tone: 'brand' | 'success' | 'info'; empty: string }) {
  const max = Math.max(1, ...rows.map(value))
  return (
    <Panel title={title} icon={Icon}>
      {rows.length ? (
        <HBarList
          ariaLabel={title}
          max={max}
          labelWidth="sm:grid-cols-[minmax(0,9rem)_1fr_auto]"
          rows={rows.map((r) => ({ key: r.teacher_id, label: `${r.rank}. ${r.teacher}`, sub: r.school, value: value(r), tone, right: `${value(r)}${unit}` }))}
        />
      ) : (
        <p className="text-sm text-muted">{empty}</p>
      )}
    </Panel>
  )
}

function RankingsTab({ school }: { school: string }) {
  const [limit, setLimit] = useState('10')
  const state = useReport<RankingsReport>('rankings', school, { limit })
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <SelectField label="Show the top" className="w-40" value={limit} onChange={setLimit} options={['5', '10', '20'].map((v) => ({ value: v, label: v }))} />
        <ExcelButton name="rankings" school={school} extra={{ limit }} />
      </div>
      <ReportFrame state={state}>
        {(d) => (
          <div className="grid gap-5 xl:grid-cols-3">
            <RankList title="Busiest teachers" icon={Trophy} rows={d.lists.busiest} value={(r) => r.hours ?? 0} unit=" h" tone="brand" empty="No teaching hours yet." />
            <RankList title="Most free teachers" icon={SearchCheck} rows={d.lists.most_free} value={(r) => r.free_periods ?? 0} unit=" free" tone="success" empty="Nobody found." />
            <RankList title="Most substitutions covered this month" icon={Repeat} rows={d.lists.substitutes} value={(r) => r.subs_covered ?? 0} unit="" tone="info" empty="Nobody has covered a lecture this month." />
          </div>
        )}
      </ReportFrame>
    </div>
  )
}

// ====================================================================================================== free-teacher finder
function FreeTab({ school }: { school: string }) {
  const [mode, setMode] = useState<'now' | 'pick'>('now')
  const [date, setDate] = useState(todayIso())
  const [period, setPeriod] = useState('')
  const [length, setLength] = useState('1')
  const extra = useMemo(() => (mode === 'now' ? { now: true } : { date, period: period || null, length: period ? length : null }), [mode, date, period, length])
  const state = useReport<FreeReport>('free-teachers', school, extra)
  const periodCount = state.data?.schools[0]?.periods.length || 6
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-wrap items-end gap-3">
          <div role="tablist" aria-label="When" className="inline-flex gap-1 rounded-xl bg-surface-2 p-1 ring-1 ring-line">
            {([['now', 'Right now'], ['pick', 'Pick a date and period']] as const).map(([v, t]) => (
              <button key={v} type="button" role="tab" aria-selected={mode === v} onClick={() => setMode(v)} data-testid={`free-${v}`} className={`rounded-lg px-3.5 py-1.5 text-sm font-medium ${mode === v ? 'bg-surface text-ink shadow-sm ring-1 ring-line' : 'text-muted hover:text-ink'}`}>{t}</button>
            ))}
          </div>
          {mode === 'pick' && (
            <>
              <TextField label="Date" type="date" value={date} onChange={(e) => e.target.value && setDate(e.target.value)} className="w-44" data-testid="free-date" />
              <SelectField label="Period" className="w-44" value={period} onChange={setPeriod} options={[{ value: '', label: 'All day' }, ...Array.from({ length: periodCount }, (_, i) => ({ value: String(i + 1), label: `Period ${i + 1}` }))]} data-testid="free-period" />
              {period && <SelectField label="For" className="w-40" value={length} onChange={setLength} options={[{ value: '1', label: '1 period' }, { value: '2', label: '2 (a lab)' }]} />}
            </>
          )}
        </div>
        <ExcelButton name="free-teachers" school={school} extra={extra} />
      </div>
      <ReportFrame state={state}>
        {(d) => {
          const multi = d.scope.schools.length > 1
          const one = d.schools[0]
          return (
            <div className="space-y-4">
              <p className="text-sm text-muted" data-testid="free-title">
                <span className="font-semibold text-ink">{d.summary.free} teacher(s) free</span> on {formatShort(d.date)}
                {d.period && !multi && one?.time ? <> at period {d.period} ({one.time})</> : d.period || d.now ? <> at the chosen period</> : <> (whole day)</>}.
              </p>
              {d.notes.map((n) => <Alert key={n} tone="info">{n}</Alert>)}
              {d.schools.flatMap((s) => s.banners).length > 0 && <Alert tone="warning" title="Holiday or event">{d.schools.flatMap((s) => s.banners).map((b) => b.title).join(', ')}</Alert>}
              {d.rows.length === 0 ? (
                <EmptyState icon={UserCheck} title="Nobody is free then">{d.empty_text}</EmptyState>
              ) : (
                <ul className="grid gap-3 lg:grid-cols-2" data-testid="free-list">
                  {d.rows.map((t) => (
                    <li key={t.teacher_id} className="rounded-2xl border border-line bg-surface p-4 shadow-card">
                      <div className="flex flex-wrap items-start justify-between gap-2">
                        <div className="min-w-0">
                          <p className="text-[15px] font-semibold text-ink">{t.name}</p>
                          <p className="text-[12px] text-muted">{t.role}{multi ? ` · ${t.school}` : ''}{t.specialization ? ` · ${t.specialization}` : ''}</p>
                        </div>
                        <div className="flex flex-wrap gap-1.5">
                          {t.marked_unavailable && <Badge tone="warning">prefers not to teach then</Badge>}
                          <Badge tone="success">{t.free_count} free</Badge>
                          <Badge>{t.load_today} teaching</Badge>
                        </div>
                      </div>
                      <div className="mt-3 flex flex-wrap items-center gap-3">
                        <PeriodStrip mask={t.free_mask} periods={d.schools.find((s) => s.id === t.school_id)?.periods ?? []} highlight={d.period} />
                        <p className="text-[12px] text-muted">{t.free_periods.map((p) => `P${p.period} ${p.time ?? ''}`).join(' · ')}</p>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )
        }}
      </ReportFrame>
    </div>
  )
}

// ====================================================================================================== the page
export default function ReportsPage() {
  const { user } = useAuth()
  const scope = useSchoolScope()
  const isAdmin = user?.role === 'admin'
  const [tab, setTab] = useState<TabKey>('workload')
  const [school, setSchool] = useState('')   // the admin's filter; '' = every school. An HOD never sends one (the server decides).

  const tabs: TabItem<TabKey>[] = [
    { value: 'workload', label: 'Workload', icon: <Gauge className="size-4" /> },
    { value: 'rankings', label: 'Busiest & free', icon: <Trophy className="size-4" /> },
    { value: 'free', label: 'Free teachers', icon: <SearchCheck className="size-4" /> },
    { value: 'hours', label: 'Subject hours', icon: <BookOpenCheck className="size-4" /> },
    { value: 'cancelled', label: 'Cancelled', icon: <CalendarX2 className="size-4" /> },
    { value: 'subs', label: 'Substitutions', icon: <Repeat className="size-4" /> },
    ...(isAdmin ? [{ value: 'compare' as const, label: 'School vs school', icon: <Scale className="size-4" /> }] : []),
  ]

  return (
    <>
      <PageHeader
        title="Reports"
        subtitle={isAdmin ? 'Numbers from the published timetables, for every school or one school. Every report downloads as an Excel file.' : `Numbers from your school's published timetable. Every report downloads as an Excel file.`}
      />
      <div className="mb-5 flex flex-wrap items-center gap-3 rounded-2xl border border-line bg-surface px-4 py-3 shadow-card">
        <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-brand-soft text-brand-text"><Building2 className="size-[18px]" aria-hidden="true" /></span>
        {isAdmin ? (
          <>
            <span className="text-sm font-medium text-muted">School</span>
            <SelectField compact aria-label="School" className="w-60 max-w-full" value={school} onChange={setSchool} options={[{ value: '', label: 'All schools' }, ...scope.schools.map((s) => ({ value: s.id, label: s.name }))]} data-testid="report-school" />
          </>
        ) : (
          <p className="flex items-center gap-2 text-sm font-semibold text-ink">{scope.schoolName ?? 'My school'} <Badge tone="brand">Your school only</Badge></p>
        )}
      </div>
      <div className="mb-5 overflow-x-auto"><Tabs label="Report" items={tabs} value={tab} onChange={setTab} /></div>
      {tab === 'workload' && <WorkloadTab school={school} isAdmin={isAdmin} />}
      {tab === 'rankings' && <RankingsTab school={school} />}
      {tab === 'free' && <FreeTab school={school} />}
      {tab === 'hours' && <HoursTab school={school} />}
      {tab === 'cancelled' && <CancelledTab school={school} />}
      {tab === 'subs' && <SubstitutionsTab school={school} />}
      {tab === 'compare' && isAdmin && <CompareTab school="" filtered={school !== ''} />}
    </>
  )
}
