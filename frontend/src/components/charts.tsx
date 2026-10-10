import type { ReactNode } from 'react'

/**
 * Small, dependency-free charts for the Reports screen (no chart library: nothing to download, works offline, follows the
 * light / dark theme). Every chart has a text alternative (`aria-label`) and shows its numbers, never only colours.
 */
export type Tone = 'brand' | 'success' | 'warning' | 'danger' | 'info' | 'neutral'

const BG: Record<Tone, string> = { brand: 'bg-accent', success: 'bg-success-solid', warning: 'bg-warning-solid', danger: 'bg-danger-solid', info: 'bg-info-solid', neutral: 'bg-neutral-solid' }
const SOFT: Record<Tone, string> = {
  brand: 'bg-brand-soft', success: 'bg-success-soft', warning: 'bg-warning-soft', danger: 'bg-danger-soft', info: 'bg-info-soft', neutral: 'bg-surface-2',
}
const STROKE: Record<Tone, string> = {
  brand: 'stroke-accent', success: 'stroke-success-solid', warning: 'stroke-warning-solid', danger: 'stroke-danger-solid', info: 'stroke-info-solid', neutral: 'stroke-neutral-solid',
}
const TEXT: Record<Tone, string> = {
  brand: 'text-brand-text', success: 'text-success', warning: 'text-warning', danger: 'text-danger', info: 'text-info', neutral: 'text-muted',
}

export function toneOf(status: string): Tone {
  switch (status) {
    case 'over':
    case 'short':
      return 'danger'
    case 'under':
      return 'warning'
    case 'within':
    case 'on track':
      return 'success'
    case 'visiting':
      return 'info'
    default:
      return 'neutral'
  }
}

export function Legend({ items }: { items: { label: string; tone: Tone }[] }) {
  return (
    <ul className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px] text-muted">
      {items.map((i) => (
        <li key={i.label} className="inline-flex items-center gap-1.5">
          <span className={`size-2.5 rounded-sm ${BG[i.tone]}`} aria-hidden="true" />
          {i.label}
        </li>
      ))}
    </ul>
  )
}

// ---------------------------------------------------------------------------------------------- horizontal bars
export interface BarRow {
  key: string
  label: ReactNode
  sub?: ReactNode
  value: number
  tone?: Tone
  /** the number / badge shown at the right end of the row */
  right?: ReactNode
}

export interface BarMarker {
  value: number
  label: string
}

/** One bar per row on a common scale (0..max). Markers are vertical lines on every bar - the UGC / AICTE norm, for example. */
export function HBarList({ rows, max, markers = [], ariaLabel, labelWidth = 'sm:grid-cols-[minmax(0,13rem)_1fr_auto]' }: {
  rows: BarRow[]
  max: number
  markers?: BarMarker[]
  ariaLabel: string
  labelWidth?: string
}) {
  const top = Math.max(max, ...markers.map((m) => m.value), 1)
  return (
    <div role="list" aria-label={ariaLabel}>
      {markers.length > 0 && (
        <p className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-muted">
          {markers.map((m) => (
            <span key={m.label} className="inline-flex items-center gap-1.5">
              <span className="h-3.5 w-0.5 rounded-full bg-ink" aria-hidden="true" />
              {m.label}
            </span>
          ))}
        </p>
      )}
      <ul className="space-y-2.5">
        {rows.map((r) => (
          <li key={r.key} role="listitem" className={`grid items-center gap-x-3 gap-y-1 ${labelWidth}`}>
            <div className="min-w-0">
              <p className="text-sm font-medium leading-snug text-ink">{r.label}</p>
              {r.sub && <p className="text-[12px] leading-snug text-muted">{r.sub}</p>}
            </div>
            <div className="relative h-3 rounded-full bg-surface-2 ring-1 ring-line" aria-hidden="true">
              <div className={`absolute inset-y-0 left-0 rounded-full ${BG[r.tone ?? 'brand']}`} style={{ width: `${Math.min(100, (r.value / top) * 100)}%` }} />
              {markers.map((m) => (
                <span key={m.label} title={m.label} className="absolute -inset-y-[3px] w-0.5 rounded-full bg-ink/80" style={{ left: `${Math.min(100, (m.value / top) * 100)}%` }} />
              ))}
            </div>
            <div className="text-right text-[13px] tabular-nums text-ink sm:min-w-[4.5rem]">{r.right ?? r.value}</div>
          </li>
        ))}
      </ul>
    </div>
  )
}

// ---------------------------------------------------------------------------------------------- one stacked bar
export interface Segment {
  label: string
  value: number
  tone: Tone
}

export function StackedBar({ segments, ariaLabel }: { segments: Segment[]; ariaLabel: string }) {
  const total = segments.reduce((s, x) => s + x.value, 0)
  return (
    <div>
      <div className="flex h-4 overflow-hidden rounded-full bg-surface-2 ring-1 ring-line" role="img" aria-label={`${ariaLabel}: ${segments.map((s) => `${s.value} ${s.label}`).join(', ')}`}>
        {total > 0 &&
          segments
            .filter((s) => s.value > 0)
            .map((s) => <div key={s.label} className={BG[s.tone]} style={{ width: `${(s.value / total) * 100}%` }} title={`${s.label}: ${s.value}`} />)}
      </div>
      <ul className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-[13px]">
        {segments.map((s) => (
          <li key={s.label} className="inline-flex items-center gap-1.5 text-muted">
            <span className={`size-2.5 rounded-sm ${BG[s.tone]}`} aria-hidden="true" />
            <span className="font-semibold tabular-nums text-ink">{s.value}</span> {s.label}
          </li>
        ))}
      </ul>
    </div>
  )
}

// ---------------------------------------------------------------------------------------------- grouped columns
export interface ColumnItem {
  label: string
  values: number[]
  title?: string
}

export function ColumnChart({ items, series, height = 150, ariaLabel, showZero = false }: {
  items: ColumnItem[]
  series: { name: string; tone: Tone }[]
  height?: number
  ariaLabel: string
  showZero?: boolean
}) {
  const max = Math.max(1, ...items.flatMap((i) => i.values))
  return (
    <div>
      {series.length > 1 && <Legend items={series.map((s) => ({ label: s.name, tone: s.tone }))} />}
      <div className="mt-2 overflow-x-auto pb-1">
        <div className="flex min-w-full items-end gap-1.5" style={{ height: height + 34 }} role="img" aria-label={`${ariaLabel}: ${items.map((i) => `${i.label} ${i.values.join(' / ')}`).join('; ')}`}>
          {items.map((item) => (
            <div key={item.label} className="flex min-w-[1.5rem] flex-1 flex-col items-center justify-end" title={item.title ?? `${item.label}: ${item.values.join(' / ')}`}>
              <div className="flex w-full items-end justify-center gap-0.5" style={{ height }}>
                {item.values.map((v, i) => (
                  <div key={i} className="flex h-full min-w-[0.6rem] max-w-[2.2rem] flex-1 flex-col items-center justify-end">
                    {(v > 0 || showZero) && <span className="mb-0.5 text-[11px] font-semibold tabular-nums text-ink">{v}</span>}
                    <div className={`w-full rounded-t-md ${BG[series[i]?.tone ?? 'brand']} ${v === 0 ? 'opacity-30' : ''}`} style={{ height: `${Math.max(v === 0 ? 2 : 4, (v / max) * 100 * 0.82)}%` }} />
                  </div>
                ))}
              </div>
              <span className="mt-1.5 text-[11px] text-muted">{item.label}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------------------------- progress ring
export function Ring({ percent, tone = 'brand', label, caption, size = 112 }: { percent: number; tone?: Tone; label?: ReactNode; caption?: string; size?: number }) {
  const r = 42
  const c = 2 * Math.PI * r
  const clamped = Math.max(0, Math.min(100, percent))
  return (
    <div className="inline-flex flex-col items-center text-center">
      <div className="relative" style={{ width: size, height: size }}>
        <svg viewBox="0 0 100 100" className="size-full -rotate-90" role="img" aria-label={`${label ?? `${clamped}%`} ${caption ?? ''}`.trim()}>
          <circle cx="50" cy="50" r={r} fill="none" strokeWidth="10" className="stroke-line" />
          <circle cx="50" cy="50" r={r} fill="none" strokeWidth="10" strokeLinecap="round" className={STROKE[tone]} strokeDasharray={c} strokeDashoffset={c * (1 - clamped / 100)} />
        </svg>
        <span className={`absolute inset-0 flex items-center justify-center text-xl font-semibold tabular-nums ${TEXT[tone]}`}>{label ?? `${clamped}%`}</span>
      </div>
      {caption && <p className="mt-1.5 max-w-[9rem] text-[12px] leading-snug text-muted">{caption}</p>}
    </div>
  )
}

// ---------------------------------------------------------------------------------------------- free / busy periods of a day
export function PeriodStrip({ mask, periods, highlight }: { mask: boolean[]; periods: { period: number; time: string | null }[]; highlight?: number | null }) {
  return (
    <div className="flex flex-wrap gap-1" role="img" aria-label={mask.map((free, i) => `period ${i + 1} ${free ? 'free' : 'busy'}`).join(', ')}>
      {mask.map((free, i) => {
        const n = i + 1
        const t = periods.find((p) => p.period === n)?.time
        return (
          <span
            key={n}
            title={`Period ${n}${t ? ` (${t})` : ''}: ${free ? 'free' : 'teaching / busy'}`}
            className={`flex size-7 items-center justify-center rounded-md text-[11px] font-semibold tabular-nums ring-1 ring-inset ${
              free ? `${SOFT.success} text-success ring-success-line` : 'bg-surface-2 text-subtle ring-line line-through decoration-subtle/60'
            } ${highlight === n ? 'outline outline-2 outline-offset-1 outline-accent' : ''}`}
          >
            {n}
          </span>
        )
      })}
    </div>
  )
}

// ---------------------------------------------------------------------------------------------- a headline number
export function Kpi({ label, value, tone = 'neutral', caption }: { label: string; value: ReactNode; tone?: Tone; caption?: string }) {
  return (
    <div className="rounded-xl border border-line bg-surface px-4 py-3.5">
      <p className="text-[12px] font-medium text-muted">{label}</p>
      <p className={`mt-1 text-2xl font-semibold leading-none tabular-nums ${tone === 'neutral' ? 'text-ink' : TEXT[tone]}`}>{value}</p>
      {caption && <p className="mt-1.5 text-[12px] leading-snug text-subtle">{caption}</p>}
    </div>
  )
}
