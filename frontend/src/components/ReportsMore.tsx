import { useState } from 'react'
import { Ban, BookOpenCheck, CalendarX2, Repeat, Scale } from 'lucide-react'
import type {
  CancelledReport, CompareReport, CompareRow, HoursReport, HoursReportClass, HoursReportRow, SubstitutionsReport,
} from '../lib/types'
import { Badge } from './Badge'
import { HBarList, ColumnChart, Kpi, Ring, StackedBar, type BarRow } from './charts'
import { DataTable, type Column } from './DataTable'
import { SelectField } from './Select'
import { EmptyState, Panel } from './Panel'
import { ExcelButton, MonthField, ReportFrame, thisMonth, useReport } from './reportKit'

// ====================================================================================================== subject hours
export function HoursTab({ school }: { school: string }) {
  const state = useReport<HoursReport>('hours', school)
  const [showAll, setShowAll] = useState(false)
  return (
    <ReportFrame state={state}>
      {(d) => {
        const multi = d.scope.schools.length > 1
        const shortRows = d.rows.filter((r) => r.shortfall > 0)
        const rows = showAll ? d.rows : shortRows
        const bars: BarRow[] = d.classes.map((c: HoursReportClass) => ({
          key: `${c.school}|${c.class}`,
          label: c.class,
          sub: multi ? c.school : `${c.subjects} subjects`,
          value: c.required ? c.percent_planned : 0,
          tone: !c.required ? 'neutral' : c.shortfall > 0 ? 'danger' : 'success',
          right: c.required ? (
            <span className="inline-flex flex-col items-end leading-tight">
              <span className="font-semibold">{c.planned}/{c.required} h</span>
              <span className={`text-[11px] ${c.shortfall > 0 ? 'text-danger' : 'text-success'}`}>{c.shortfall > 0 ? `short by ${c.shortfall} h` : 'on track'}</span>
            </span>
          ) : <span className="text-[12px] text-muted">not published</span>,
        }))
        const cols: Column<HoursReportRow>[] = [
          { key: 'subject', header: 'Subject', cell: (r) => <div className="min-w-0"><p className="text-sm font-medium text-ink">{r.subject}</p><p className="truncate text-[12px] text-muted">{multi ? `${r.school} · ` : ''}{r.class} · {r.teachers}</p></div> },
          { key: 'req', header: 'Required', className: 'text-right tabular-nums', hideBelow: 'sm', cell: (r) => `${r.required} h` },
          { key: 'del', header: 'Taught', className: 'text-right tabular-nums', hideBelow: 'md', cell: (r) => `${r.delivered} h` },
          { key: 'pos', header: 'Possible', className: 'text-right tabular-nums', hideBelow: 'md', cell: (r) => `${r.deliverable} h` },
          { key: 'pct', header: '% planned', className: 'text-right tabular-nums', hideBelow: 'sm', cell: (r) => `${r.percent_planned}%` },
          { key: 'state', header: 'State', className: 'text-right', cell: (r) => (r.shortfall > 0 ? <Badge tone="danger">short by {r.shortfall} h</Badge> : r.state === 'on track' ? <Badge tone="success">on track</Badge> : <Badge>{r.state}</Badge>) },
        ]
        return (
          <div className="space-y-5">
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
              <Kpi label="Subjects tracked" value={d.summary.subjects} />
              <Kpi label="Subjects short of hours" value={d.summary.short_subjects} tone={d.summary.short_subjects ? 'danger' : 'success'} />
              <Kpi label="Hours short" value={d.summary.short_hours} tone={d.summary.short_hours ? 'danger' : 'success'} caption="in total, by the semester end" />
              <Kpi label="Hours taught so far" value={`${d.summary.taught}`} caption={`of ${d.summary.required} required`} />
              <div className="col-span-2 flex items-center justify-center rounded-xl border border-line bg-surface p-3 lg:col-span-1">
                <Ring percent={d.summary.percent_planned} tone={d.summary.short_hours ? 'warning' : 'success'} caption="of the required hours are planned" size={92} />
              </div>
            </div>
            <Panel title="Progress by class" icon={BookOpenCheck} action={<ExcelButton name="hours" school={school} />}>
              {bars.length ? (
                <HBarList rows={bars} max={100} markers={[{ value: 100, label: 'All required hours planned' }]} ariaLabel="Hours planned against hours required, per class" />
              ) : (
                <EmptyState icon={BookOpenCheck} title="No classes">{d.empty_text}</EmptyState>
              )}
            </Panel>
            <Panel
              title={showAll ? 'All subjects' : `Subjects short of hours (${shortRows.length})`}
              icon={Ban}
              action={<button type="button" onClick={() => setShowAll((v) => !v)} className="text-[13px] font-semibold text-brand-text hover:underline" data-testid="toggle-all-subjects">{showAll ? 'Show only the short ones' : 'Show all subjects'}</button>}
            >
              <DataTable
                caption="Subject hours"
                columns={cols}
                rows={rows}
                rowKey={(r) => `${r.semester_id}|${r.subject}`}
                dense
                empty={<EmptyState icon={BookOpenCheck} title="Nothing short">Every subject can still deliver its required hours.</EmptyState>}
              />
            </Panel>
          </div>
        )
      }}
    </ReportFrame>
  )
}

// ====================================================================================================== cancelled sessions
export function CancelledTab({ school }: { school: string }) {
  const [month, setMonth] = useState(thisMonth())
  const state = useReport<CancelledReport>('cancelled', school, { month })
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <MonthField value={month} onChange={(v) => v && setMonth(v)} />
        <ExcelButton name="cancelled" school={school} extra={{ month }} />
      </div>
      <ReportFrame state={state}>
        {(d) => {
          const multi = d.scope.schools.length > 1
          const cols: Column<CancelledReport['rows'][number]>[] = [
            { key: 'date', header: 'Date', cell: (r) => <span className="whitespace-nowrap text-sm text-ink">{r.weekday.slice(0, 3)} {r.date.split('-').reverse().slice(0, 2).join('/')}</span> },
            { key: 'cls', header: 'Class and subject', cell: (r) => <div className="min-w-0"><p className="text-sm font-medium text-ink">{r.subject}</p><p className="truncate text-[12px] text-muted">{multi ? `${r.school} · ` : ''}{r.class}</p></div> },
            { key: 'when', header: 'Period', hideBelow: 'sm', cell: (r) => <span className="text-[13px] text-muted">P{r.period} · {r.time}</span> },
            { key: 't', header: 'Teacher(s)', hideBelow: 'md', cell: (r) => <span className="text-[13px] text-muted">{r.teachers}</span> },
            { key: 'why', header: 'Reason', hideBelow: 'lg', cell: (r) => <span className="text-[13px] text-muted">{r.reason || '-'}</span> },
          ]
          const lost: BarRow[] = d.lost.filter((l) => l.lost_hours > 0).map((l) => ({ key: `${l.school}|${l.class}`, label: l.class, sub: multi ? l.school : undefined, value: l.lost_hours, tone: 'warning', right: `${l.lost_hours} h` }))
          return (
            <div className="space-y-5">
              <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                <Kpi label={`Cancelled in ${d.summary.month_label}`} value={d.summary.total} tone={d.summary.total ? 'warning' : 'success'} caption="one-day cancels by the HOD" />
                <Kpi label="Hours lost so far this term" value={d.summary.lost_hours_total} caption="holidays, events and cancels" />
                <Kpi label="Most affected teacher" value={<span className="text-lg">{d.summary.top_teachers[0]?.name ?? '-'}</span>} caption={d.summary.top_teachers[0] ? `${d.summary.top_teachers[0].count} session(s)` : 'none'} />
                <Kpi label="Most affected class" value={<span className="text-lg">{d.summary.top_classes[0]?.name ?? '-'}</span>} caption={d.summary.top_classes[0] ? `${d.summary.top_classes[0].count} session(s)` : 'none'} />
              </div>
              <Panel title="Cancelled sessions per day" icon={CalendarX2}>
                <ColumnChart items={d.per_day.map((p) => ({ label: p.label, values: [p.count], title: `${p.label} ${d.summary.month_label}: ${p.count} cancelled` }))} series={[{ name: 'Cancelled', tone: 'warning' }]} ariaLabel={`Cancelled sessions per day in ${d.summary.month_label}`} height={110} />
              </Panel>
              <Panel title="The sessions" icon={CalendarX2}>
                <DataTable caption="Cancelled sessions" columns={cols} rows={d.rows} rowKey={(r) => `${r.date}|${r.class}|${r.period}`} dense empty={<EmptyState icon={CalendarX2} title="Nothing cancelled">{d.empty_text}</EmptyState>} />
              </Panel>
              {lost.length > 0 && (
                <Panel title="Hours lost per class (whole term)" icon={Scale}>
                  <HBarList rows={lost} max={Math.max(...lost.map((l) => l.value))} ariaLabel="Hours lost to holidays, events and cancels per class" />
                </Panel>
              )}
            </div>
          )
        }}
      </ReportFrame>
    </div>
  )
}

// ====================================================================================================== substitutions
export function SubstitutionsTab({ school }: { school: string }) {
  const [month, setMonth] = useState(thisMonth())
  const state = useReport<SubstitutionsReport>('substitutions', school, { month })
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <MonthField value={month} onChange={(v) => v && setMonth(v)} />
        <ExcelButton name="substitutions" school={school} extra={{ month }} />
      </div>
      <ReportFrame state={state}>
        {(d) => {
          const s = d.summary
          const multi = d.scope.schools.length > 1
          const cols: Column<SubstitutionsReport['rows'][number]>[] = [
            { key: 't', header: 'Teacher', cell: (r) => <div><p className="text-sm font-medium text-ink">{r.teacher}</p>{multi && <p className="text-[12px] text-muted">{r.school}</p>}</div> },
            { key: 'a', header: 'Days absent', className: 'text-right tabular-nums', cell: (r) => r.absent_days },
            { key: 'n', header: 'Lectures needing cover', className: 'text-right tabular-nums', hideBelow: 'sm', cell: (r) => r.lectures_needing_cover },
            { key: 'c', header: 'Substitutions covered', className: 'text-right tabular-nums', cell: (r) => r.subs_covered },
            { key: 'p', header: 'Periods covered', className: 'text-right tabular-nums', hideBelow: 'md', cell: (r) => r.periods_covered },
          ]
          return (
            <div className="space-y-5">
              <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
                <Kpi label={`Absences in ${s.month_label}`} value={s.absences} caption={`${s.absent_teachers} teacher(s)`} />
                <Kpi label="Lectures needing a substitute" value={s.needs} />
                <Kpi label="Filled" value={s.filled} tone={s.filled ? 'success' : 'neutral'} caption={`${s.filled_auto} by auto-offer · ${s.filled_manual} by the HOD`} />
                <Kpi label="Not filled" value={s.unfilled} tone={s.unfilled ? 'danger' : 'success'} />
                <div className="col-span-2 flex items-center justify-center rounded-xl border border-line bg-surface p-3 lg:col-span-1">
                  <Ring percent={s.fill_rate} tone={s.needs === 0 ? 'neutral' : s.fill_rate >= 90 ? 'success' : s.fill_rate >= 60 ? 'warning' : 'danger'} caption={s.avg_minutes_to_fill !== null ? `filled, on average ${s.avg_minutes_to_fill} min after the absence was marked` : 'of the lectures got a substitute'} size={92} />
                </div>
              </div>
              <Panel title="Last 6 months" icon={Repeat}>
                <ColumnChart
                  items={d.trend.map((t) => ({ label: t.label, values: [t.needs, t.filled], title: `${t.month}: ${t.needs} needed, ${t.filled} filled` }))}
                  series={[{ name: 'Needed a substitute', tone: 'warning' }, { name: 'Filled', tone: 'success' }]}
                  ariaLabel="Substitutions needed and filled in the last six months"
                  showZero
                />
              </Panel>
              {s.needs > 0 && (
                <Panel title="How they were filled" icon={Repeat}>
                  <StackedBar
                    ariaLabel="How the substitutions were filled"
                    segments={[
                      { label: 'by auto-offer (first to accept)', value: s.filled_auto, tone: 'success' },
                      { label: 'by the HOD', value: s.filled_manual, tone: 'brand' },
                      { label: 'not filled', value: s.unfilled, tone: 'danger' },
                    ]}
                  />
                </Panel>
              )}
              <Panel title="By teacher" icon={Repeat}>
                <DataTable caption="Absences and substitutions per teacher" columns={cols} rows={d.rows} rowKey={(r) => `${r.school}|${r.teacher}`} dense empty={<EmptyState icon={Repeat} title="Nothing this month">{d.empty_text}</EmptyState>} />
              </Panel>
            </div>
          )
        }}
      </ReportFrame>
    </div>
  )
}

// ====================================================================================================== school vs school
const METRICS: { key: keyof CompareRow; label: string; unit: string; tone: 'brand' | 'danger' | 'success' | 'warning' | 'info' }[] = [
  { key: 'avg_pct', label: 'Average workload (% of norm)', unit: '%', tone: 'brand' },
  { key: 'short_hours', label: 'Hours short in subjects', unit: ' h', tone: 'danger' },
  { key: 'fill_rate', label: 'Substitutions filled', unit: '%', tone: 'success' },
  { key: 'absences', label: 'Absences', unit: '', tone: 'warning' },
  { key: 'cancelled', label: 'Cancelled sessions', unit: '', tone: 'warning' },
  { key: 'pref_pct', label: 'Teachers who filled their preferences', unit: '%', tone: 'info' },
]

export function CompareTab({ school, filtered = false }: { school: string; filtered?: boolean }) {
  const [month, setMonth] = useState(thisMonth())
  const [metric, setMetric] = useState<string>('avg_pct')
  const state = useReport<CompareReport>('compare', school, { month })
  return (
    <div className="space-y-4">
      {filtered && <p className="text-[13px] text-muted" data-testid="compare-note">This view always compares every school, so the school filter above does not apply here.</p>}
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-wrap items-end gap-3">
          <MonthField value={month} onChange={(v) => v && setMonth(v)} />
          <SelectField label="Compare by" className="w-72 max-w-full" value={metric} onChange={setMetric} options={METRICS.map((m) => ({ value: m.key, label: m.label }))} />
        </div>
        <ExcelButton name="compare" school={school} extra={{ month }} />
      </div>
      <ReportFrame state={state}>
        {(d) => {
          const m = METRICS.find((x) => x.key === metric) ?? METRICS[0]
          const values = d.rows.map((r) => Number(r[m.key] ?? 0))
          const bars: BarRow[] = d.rows.map((r) => ({
            key: r.school_id, label: r.school, sub: `${r.teachers} teachers · ${r.classes} classes`, value: Number(r[m.key] ?? 0), tone: m.tone,
            right: r[m.key] === null ? <span className="text-muted">no window</span> : `${r[m.key]}${m.unit}`,
          }))
          const cols: Column<CompareRow>[] = [
            { key: 's', header: 'School', cell: (r) => <span className="text-sm font-medium text-ink">{r.school}</span> },
            { key: 't', header: 'Teachers', className: 'text-right tabular-nums', cell: (r) => r.teachers },
            { key: 'w', header: 'Avg h/week', className: 'text-right tabular-nums', hideBelow: 'sm', cell: (r) => `${r.avg_hours} (${r.avg_pct}%)` },
            { key: 'l', header: 'Under / within / over', className: 'text-right tabular-nums', hideBelow: 'md', cell: (r) => `${r.under} / ${r.within} / ${r.over}` },
            { key: 'h', header: 'Hours short', className: 'text-right tabular-nums', cell: (r) => (r.short_hours ? <span className="font-semibold text-danger">{r.short_hours}</span> : '0') },
            { key: 'a', header: 'Absences', className: 'text-right tabular-nums', hideBelow: 'md', cell: (r) => r.absences },
            { key: 'f', header: 'Fill rate', className: 'text-right tabular-nums', hideBelow: 'sm', cell: (r) => (r.sub_needs ? `${r.fill_rate}%` : '-') },
            { key: 'c', header: 'Cancelled', className: 'text-right tabular-nums', hideBelow: 'lg', cell: (r) => r.cancelled },
          ]
          return (
            <div className="space-y-5">
              <Panel title={`${m.label} - ${d.month_label}`} icon={Scale}>
                {d.rows.length > 1 || bars.length ? (
                  <HBarList rows={bars} max={metric.endsWith('pct') || metric === 'fill_rate' ? Math.max(100, ...values) : Math.max(1, ...values)} ariaLabel={`${m.label} per school`} labelWidth="sm:grid-cols-[minmax(0,15rem)_1fr_auto]" />
                ) : (
                  <EmptyState icon={Scale} title="No schools">{d.empty_text}</EmptyState>
                )}
              </Panel>
              <Panel title="All the numbers" icon={Scale}>
                <DataTable caption="School comparison" columns={cols} rows={d.rows} rowKey={(r) => r.school_id} dense empty={<EmptyState icon={Scale} title="No schools">{d.empty_text}</EmptyState>} />
              </Panel>
            </div>
          )
        }}
      </ReportFrame>
    </div>
  )
}
