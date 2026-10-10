import { useMemo, type CSSProperties } from 'react'
import { ArrowRightLeft, CalendarOff, Layers, Lock, Plus, Repeat, Users, Utensils } from 'lucide-react'
import type { DayHeader, GridInfo, MasterView, ViewCell } from '../lib/types'
import { Badge } from './Badge'

export function hueOf(text: string): number {
  let h = 0
  for (let i = 0; i < text.length; i++) h = (h * 31 + text.charCodeAt(i)) >>> 0
  return h % 360
}

type Row = { type: 'period'; number: number; start: string; end: string } | { type: 'lunch'; start: string; end: string }

export function buildRows(grid: GridInfo): Row[] {
  const rows: Row[] = []
  grid.periods.forEach((p, i) => {
    rows.push({ type: 'period', ...p })
    const next = grid.periods[i + 1]
    if (grid.lunch && next && p.end <= grid.lunch.start && grid.lunch.end <= next.start) rows.push({ type: 'lunch', ...grid.lunch })
  })
  return rows
}

function shortDate(iso: string): string {
  const [y, m, d] = iso.split('-').map(Number)
  return new Date(y, m - 1, d).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
}

export function teachersOf(cell: ViewCell): string {
  const names = cell.items.flatMap((i) => [i.teacher_name, i.co_teacher_name]).filter((n): n is string => !!n)
  return [...new Set(names)].join(' / ')
}

/** The inside of one lecture box. `mode`: what the second line says - the teacher (class / master views) or the class (teacher views). */
export function CellContent({ cell, mode, compact = false }: { cell: ViewCell; mode: 'class' | 'teacher' | 'master'; compact?: boolean }) {
  const cancelled = cell.status !== null
  const classes = cell.classes.map((c) => c.label).join(' + ')
  const tag = 'inline-flex items-center gap-1 rounded px-1 py-px text-[10px] font-semibold leading-tight'
  const subs = cell.substitutions ?? []
  return (
    <div className={`flex h-full flex-col justify-center gap-0.5 text-center ${cancelled ? 'opacity-70 dark:opacity-85' : ''}`}>
      {cell.kind === 'substitution' && (
        <span className={`${tag} mx-auto bg-info-solid text-white`}>
          <Repeat className="size-2.5" aria-hidden="true" /> SUBSTITUTION
        </span>
      )}
      {cell.kind === 'extra' && (
        <span className={`${tag} mx-auto bg-info-solid text-white`}>
          <Plus className="size-2.5" aria-hidden="true" /> EXTRA {cell.date ? shortDate(cell.date) : ''}
        </span>
      )}
      {cell.kind === 'fixed' && (
        <span className={`${tag} mx-auto bg-black/10 dark:bg-white/10`}>
          <Lock className="size-2.5" aria-hidden="true" /> {cell.fixed_kind === 'OTHER' ? 'Locked' : cell.fixed_kind}
        </span>
      )}
      {cell.items.length > 1 ? (
        <>
          <span className={`${tag} mx-auto bg-black/10 dark:bg-white/10`}>
            <Layers className="size-2.5" aria-hidden="true" /> Parallel electives
          </span>
          {cell.items.map((it) => (
            <span key={it.subject_id ?? it.title} className={`block leading-tight ${cancelled ? 'line-through' : ''}`}>
              <span className="font-semibold">{it.title}</span>
              {mode !== 'teacher' && (it.teacher_name || it.co_teacher_name) ? (
                <span className="block opacity-80">{[it.teacher_name, it.co_teacher_name].filter(Boolean).join(' / ')}</span>
              ) : null}
            </span>
          ))}
        </>
      ) : (
        <>
          <span className={`block text-[13px] font-semibold leading-tight ${cancelled ? 'line-through' : ''}`}>
            {cell.items[0]?.title}
            {cell.is_lab && !/lab/i.test(cell.items[0]?.title ?? '') ? ' (Lab)' : ''}
          </span>
          {mode === 'teacher' ? (
            <span className="block leading-tight opacity-80">{classes}</span>
          ) : (
            teachersOf(cell) && <span className="block leading-tight opacity-80">{teachersOf(cell)}</span>
          )}
        </>
      )}
      {mode === 'teacher' && cell.items.length > 1 && <span className="block leading-tight opacity-80">{classes}</span>}
      {mode === 'teacher' && cell.parallel_with && cell.parallel_with.length > 0 && (
        <span className="block text-[11px] leading-tight opacity-80">Parallel with: {cell.parallel_with.join(', ')}</span>
      )}
      {!compact && (cell.combined || cell.combined_with) && (
        <span className={`${tag} mx-auto bg-black/10 dark:bg-white/10`}>
          <Users className="size-2.5" aria-hidden="true" /> {cell.combined_with ? `Combined with ${cell.combined_with}` : mode === 'class' ? 'Combined session' : 'Combined session'}
        </span>
      )}
      {compact && cell.classes.length > 1 && <span className="block text-[11px] leading-tight opacity-80">Combined: {classes}</span>}
      {cell.kind === 'substitution' && cell.substitution && (
        <span className="block text-[11px] leading-tight opacity-90">for {cell.substitution.for_name}</span>
      )}
      {subs.map((s) => (
        <span key={s.id} className={`${tag} mx-auto ${s.status === 'filled' ? 'bg-info-solid text-white' : 'bg-warning-solid text-white'}`}>
          <Repeat className="size-2.5" aria-hidden="true" />
          {s.status === 'filled' ? `Sub: ${s.substitute_name}` : s.status === 'offering' ? 'Offer sent' : 'Needs substitute'}
        </span>
      ))}
      {cell.mine_absent && <span className={`${tag} mx-auto bg-warning-solid text-white`}><CalendarOff className="size-2.5" aria-hidden="true" /> You are absent</span>}
      {cell.adjusted?.kind === 'move' && (
        <span className={`${tag} mx-auto bg-brand text-white`}>
          <ArrowRightLeft className="size-2.5" aria-hidden="true" /> {cell.adjusted.swap ? 'Swapped' : `Moved from P${cell.moved_from}`}
        </span>
      )}
      {cell.status && (
        <span className={`${tag} mx-auto ${cell.status === 'holiday' ? 'bg-success-solid text-white' : cell.status === 'event' ? 'bg-warning-solid text-white' : cell.status === 'cancelled' ? 'bg-danger-solid text-white' : 'bg-neutral-solid text-white'}`}>
          <CalendarOff className="size-2.5" aria-hidden="true" />
          {cell.status === 'holiday' ? 'Holiday' : cell.status === 'event' ? 'Event' : cell.status === 'cancelled' ? 'Cancelled' : 'No semester'}
          {cell.status_note ? `: ${cell.status_note}` : ''}
        </span>
      )}
    </div>
  )
}

function boxStyle(cell: ViewCell): { className: string; style?: CSSProperties } {
  if (cell.kind === 'substitution') return { className: 'border border-info-line bg-info-soft text-ink' }
  if (cell.status === 'cancelled') return { className: 'border border-danger-line bg-danger-soft text-ink' }
  if (cell.kind === 'fixed') return { className: 'border bg-surface-2 text-ink' }
  const first = cell.items[0]
  return { className: 'subj-chip border', style: { '--h': hueOf(first?.code ?? first?.title ?? 'x') } as CSSProperties }
}

/** The read-only weekly timetable of one class or one teacher (periods down, days across). */
export function TimetableGrid({
  grid, days, cells, mode, offDays, caption,
}: {
  grid: GridInfo
  days: DayHeader[]
  cells: ViewCell[]
  mode: 'class' | 'teacher'
  /** class view only: which weekdays are OFF / end early (greyed out) */
  offDays?: Record<string, { is_off: boolean; end_time: string | null }>
  caption: string
}) {
  const rows = useMemo(() => buildRows(grid), [grid])
  const rowOfPeriod = useMemo(() => {
    const m = new Map<number, number>()
    rows.forEach((r, i) => r.type === 'period' && m.set(r.number, i))
    return m
  }, [rows])
  const { starts, covered } = useMemo(() => {
    const starts = new Map<string, { cell: ViewCell; span: number }>()
    const covered = new Set<string>()
    for (const c of cells) {
      const a = rowOfPeriod.get(c.start_period)
      const b = rowOfPeriod.get(c.start_period + c.length - 1)
      if (a === undefined || b === undefined) continue
      const key = `${c.day}:${a}`
      // two things in one cell (a lecture and an extra lecture of another week) cannot happen: extras only use free slots
      starts.set(key, { cell: c, span: b - a + 1 })
      for (let r = a + 1; r <= b; r++) covered.add(`${c.day}:${r}`)
    }
    return { starts, covered }
  }, [cells, rowOfPeriod])

  const greyed = (day: number, periodNumber: number): boolean => {
    const rule = offDays?.[String(day)]
    if (!rule) return false
    if (rule.is_off) return true
    const p = grid.periods[periodNumber - 1]
    return !!(rule.end_time && p && p.end > rule.end_time)
  }

  return (
    <div className="overflow-x-auto rounded-xl border border-line bg-surface">
      <table className="w-full min-w-[46rem] table-fixed border-collapse text-sm" data-testid="timetable-grid">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr className="bg-surface-2 text-xs font-semibold text-muted">
            <th scope="col" className="sticky left-0 z-10 w-24 bg-surface-2 px-3 py-2.5 text-left uppercase tracking-wide">Period</th>
            {days.map((d) => {
              const holiday = d.banners.find((b) => b.full_day)
              return (
                <th key={d.day} scope="col" className={`px-2 py-2 text-center ${d.is_today ? 'bg-brand-soft text-brand-text' : ''}`}>
                  <span className="block text-[13px] uppercase tracking-wide">{d.name}</span>
                  <span className="block text-[11px] font-normal normal-case">{shortDate(d.date)}{d.is_today ? ' · today' : ''}</span>
                  {d.banners.slice(0, 2).map((b) => (
                    <Badge key={b.id} tone={holiday && b.id === holiday.id ? 'success' : 'warning'} className="mt-1 max-w-full truncate text-[10px]">
                      {b.kind === 'holiday' ? 'Holiday' : 'Event'}: {b.title}
                    </Badge>
                  ))}
                </th>
              )
            })}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, ri) => (
            <tr key={ri} className="border-t border-line">
              <th scope="row" className={`sticky left-0 z-10 px-3 py-2 text-left align-middle ${row.type === 'lunch' ? 'bg-warning-soft' : 'bg-surface-2'}`}>
                {row.type === 'lunch' ? (
                  <span className="flex items-center gap-1.5 text-xs font-semibold text-warning">
                    <Utensils className="size-3.5" aria-hidden="true" /> Lunch
                    <span className="font-normal tabular-nums">{row.start}</span>
                  </span>
                ) : (
                  <>
                    <span className="block text-[13px] font-semibold text-ink">P{row.number}</span>
                    <span className="block text-[11px] tabular-nums text-muted">{row.start}-{row.end}</span>
                  </>
                )}
              </th>
              {days.map((d) => {
                const key = `${d.day}:${ri}`
                if (covered.has(key)) return null
                if (row.type === 'lunch') return <td key={d.day} className="bg-warning-soft/60" />
                const hit = starts.get(key)
                if (hit) {
                  const { className, style } = boxStyle(hit.cell)
                  return (
                    <td key={d.day} rowSpan={hit.span} className="p-1 align-middle">
                      <div
                        className={`h-full min-h-[3.4rem] rounded-lg px-2 py-1.5 ${className}`}
                        style={style}
                        data-testid="cell"
                        data-title={hit.cell.items[0]?.title}
                        data-status={hit.cell.status ?? 'ok'}
                        title={`${hit.cell.items.map((i) => i.title).join(' / ')} · ${hit.cell.time ?? ''}`}
                      >
                        <CellContent cell={hit.cell} mode={mode} />
                      </div>
                    </td>
                  )
                }
                const off = greyed(d.day, row.number)
                return <td key={d.day} className={`h-[3.6rem] ${off ? 'bg-surface-2 [background-image:repeating-linear-gradient(135deg,transparent,transparent_6px,var(--border)_6px,var(--border)_7px)]' : d.is_today ? 'bg-brand-soft/30' : ''}`} />
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** The whole school for one or more days: classes down the side, periods across. */
export function MasterGrid({ view }: { view: MasterView }) {
  const rows = useMemo(() => buildRows(view.grid), [view.grid])
  const shownDays = view.days.filter((d) => view.day === null || view.day === d.day)
  const idxOfPeriod = useMemo(() => {
    const m = new Map<number, number>()
    rows.forEach((r, i) => r.type === 'period' && m.set(r.number, i))
    return m
  }, [rows])

  if (view.day === 0) {
    return <p className="rounded-xl border border-line bg-surface-2 px-4 py-6 text-center text-sm text-muted">That day is not a working day - there are no lectures.</p>
  }
  return (
    <div className="space-y-6">
      {shownDays.map((d) => {
        const dayCells = view.cells.filter((c) => c.day === d.day)
        const holiday = d.banners.find((b) => b.full_day)
        return (
          <section key={d.day} aria-label={d.name}>
            <h3 className="mb-2 flex flex-wrap items-center gap-2 text-sm font-semibold text-ink">
              {d.name} <span className="font-normal text-muted">{shortDate(d.date)}</span>
              {d.is_today && <Badge tone="brand">today</Badge>}
              {d.banners.map((b) => (
                <Badge key={b.id} tone={holiday && b.id === holiday.id ? 'success' : 'warning'}>
                  {b.kind === 'holiday' ? 'Holiday' : 'Event'}: {b.title}
                </Badge>
              ))}
            </h3>
            <div className="overflow-x-auto rounded-xl border border-line bg-surface">
              <table className="w-full min-w-[56rem] table-fixed border-collapse text-sm" data-testid="master-grid">
                <thead>
                  <tr className="bg-surface-2 text-xs font-semibold text-muted">
                    <th scope="col" className="sticky left-0 z-10 w-36 bg-surface-2 px-3 py-2 text-left uppercase tracking-wide">Class</th>
                    {rows.map((r, i) =>
                      r.type === 'lunch' ? (
                        <th key={i} scope="col" className="w-10 bg-warning-soft px-1 py-2 text-center text-warning" title={`Lunch ${r.start}-${r.end}`}>
                          <Utensils className="mx-auto size-3.5" aria-label="Lunch" />
                        </th>
                      ) : (
                        <th key={i} scope="col" className="px-1 py-2 text-center">
                          <span className="block text-[13px] text-ink">P{r.number}</span>
                          <span className="block text-[10px] font-normal tabular-nums">{r.start}-{r.end}</span>
                        </th>
                      ),
                    )}
                  </tr>
                </thead>
                <tbody>
                  {view.classes.map((cl) => {
                    const mine = dayCells.filter((c) => c.classes.some((x) => x.id === cl.id))
                    const byStart = new Map(mine.map((c) => [c.start_period, c]))
                    const skip = new Set<number>()   // row indexes already covered by a longer block
                    const rule = view.off_days?.[cl.id]?.[String(d.day)]
                    return (
                      <tr key={cl.id} className="border-t border-line">
                        <th scope="row" className="sticky left-0 z-10 bg-surface-2 px-3 py-2 text-left align-middle text-[13px] font-semibold text-ink">
                          {cl.label}
                          {!cl.published && <span className="block text-[11px] font-normal text-warning">not published</span>}
                        </th>
                        {rows.map((r, i) => {
                          if (skip.has(i)) return null
                          if (r.type === 'lunch') return <td key={i} className="bg-warning-soft/60" />
                          const c = byStart.get(r.number)
                          if (c) {
                            const last = idxOfPeriod.get(r.number + c.length - 1) ?? i
                            for (let k = i + 1; k <= last; k++) skip.add(k)
                            const { className, style } = boxStyle(c)
                            return (
                              <td key={i} colSpan={last - i + 1} className="p-1 align-middle">
                                <div className={`h-full min-h-[3.4rem] rounded-lg px-1.5 py-1 text-[12px] ${className}`} style={style} data-testid="cell" data-title={c.items[0]?.title}>
                                  <CellContent cell={c} mode="master" compact />
                                </div>
                              </td>
                            )
                          }
                          const p = view.grid.periods[r.number - 1]
                          const off = !!rule && (rule.is_off || (!!rule.end_time && !!p && p.end > rule.end_time))
                          return <td key={i} className={`h-[3.4rem] ${off ? 'bg-surface-2 [background-image:repeating-linear-gradient(135deg,transparent,transparent_6px,var(--border)_6px,var(--border)_7px)]' : ''}`} />
                        })}
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </section>
        )
      })}
    </div>
  )
}
