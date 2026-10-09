import { memo, useCallback, useEffect, useMemo, useRef, useState, type DragEvent, type FormEvent, type ReactNode } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import {
  ArrowLeft, ArrowLeftRight, Ban, Copy, Layers, Lock, MousePointerClick, Pencil, Pin, PinOff, ShieldAlert, Star, Trash2, TriangleAlert, Utensils, X,
} from 'lucide-react'
import { useAuth } from '../auth/AuthProvider'
import { Alert } from '../components/Alert'
import { Badge } from '../components/Badge'
import { Button } from '../components/Button'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { TextField } from '../components/Field'
import { Modal } from '../components/Modal'
import { Skeleton } from '../components/Panel'
import { SelectField } from '../components/Select'
import { useToast } from '../components/Toast'
import { api, ApiError } from '../lib/api'
import { ROLE_PREFIX } from '../lib/nav'
import type { CellOption, DraftDetail, GridInfo, LockedItem, OptionsPayload, PaletteGroup, PaletteSubject, Placement } from '../lib/types'
import { useApi } from '../lib/useApi'

type Pick =
  | { kind: 'subject'; id: string; name: string; length: number }
  | { kind: 'group'; id: string; name: string; length: number }
  | { kind: 'move'; key: string; name: string; length: number }

interface Mutation {
  detail: DraftDetail
  warnings?: string[]
}

const HUES = [212, 152, 28, 328, 268, 58, 182, 358, 98, 242, 18, 298]

// ---------------------------------------------------------------------- the grid geometry
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

function periodsText(start: number, length: number): string {
  return length === 1 ? `period ${start}` : `periods ${start}-${start + length - 1}`
}

// ---------------------------------------------------------------------- the weekly grid
interface GridProps {
  d: DraftDetail
  hues: Map<string, number>
  picked: Pick | null
  options: OptionsPayload | null
  hover: { day: number; start: number } | null
  flash: string | null
  onCell: (day: number, period: number) => void
  onHover: (cell: { day: number; start: number } | null) => void
  onPlacement: (p: Placement) => void
  onDragPlacement: (p: Placement, e: DragEvent) => void
  onDragEnd: (e: DragEvent) => void
  onRemove: (p: Placement) => void
  onTogglePin: (p: Placement) => void
  onDrop: (day: number, period: number, e: DragEvent) => void
}

const WeekGrid = memo(function WeekGrid({ d, hues, picked, options, hover, flash, onCell, onHover, onPlacement, onDragPlacement, onDragEnd, onRemove, onTogglePin, onDrop }: GridProps) {
  const rows = useMemo(() => buildRows(d.grid), [d.grid])
  const working = d.days.filter((x) => x.working)
  const rowOfPeriod = useMemo(() => {
    const m = new Map<number, number>()
    rows.forEach((r, i) => r.type === 'period' && m.set(r.number, i))
    return m
  }, [rows])

  const { starts, covered, lockedStarts, lockedCovered } = useMemo(() => {
    const starts = new Map<string, { p: Placement; span: number }>()
    const covered = new Set<string>()
    for (const p of d.placements) {
      const a = rowOfPeriod.get(p.start_period)
      const b = rowOfPeriod.get(p.start_period + p.length - 1)
      if (a === undefined || b === undefined) continue
      starts.set(`${p.day}:${a}`, { p, span: b - a + 1 })
      for (let r = a + 1; r <= b; r++) covered.add(`${p.day}:${r}`)
    }
    const lockedStarts = new Map<string, { l: LockedItem; span: number }>()
    const lockedCovered = new Set<string>()
    for (const l of d.locked) {
      const a = rowOfPeriod.get(l.start_period)
      const b = rowOfPeriod.get(l.start_period + l.length - 1)
      if (a === undefined || b === undefined) continue
      lockedStarts.set(`${l.day}:${a}`, { l, span: b - a + 1 })
      for (let r = a + 1; r <= b; r++) lockedCovered.add(`${l.day}:${r}`)
    }
    return { starts, covered, lockedStarts, lockedCovered }
  }, [d.placements, d.locked, rowOfPeriod])

  const length = picked?.length ?? 1
  return (
    <div className="relative overflow-x-auto rounded-xl border border-line">
      <table className="w-full min-w-[44rem] table-fixed border-collapse text-sm">
        <caption className="sr-only">Weekly timetable grid of the class: periods in the rows, days in the columns</caption>
        <thead>
          <tr className="bg-surface-2 text-xs font-semibold uppercase tracking-wide text-muted">
            <th scope="col" className="sticky left-0 z-10 w-24 bg-surface-2 px-3 py-2.5 text-left">Period</th>
            {working.map((x) => (
              <th key={x.day} scope="col" className="px-2 py-2.5 text-center">
                {x.name}
                {x.is_off ? <span className="ml-1 text-subtle">· OFF</span> : x.end_time ? <span className="ml-1 font-normal normal-case text-subtle">· till {x.end_time}</span> : null}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, ri) => (
            <tr key={ri} className="border-t border-line">
              <th scope="row" className={`sticky left-0 z-10 px-3 py-2 text-left align-middle ${row.type === 'lunch' ? 'bg-warning-soft' : 'bg-surface-2'}`}>
                {row.type === 'lunch' ? (
                  <span className="flex items-center gap-1.5 text-xs font-semibold text-warning">
                    <Utensils className="size-3.5" aria-hidden="true" /> Lunch
                  </span>
                ) : (
                  <>
                    <span className="block text-[13px] font-semibold text-ink">P{row.number}</span>
                    <span className="block text-[11px] tabular-nums text-muted">{row.start}-{row.end}</span>
                  </>
                )}
              </th>
              {working.map((x) => {
                const key = `${x.day}:${ri}`
                if (covered.has(key) || lockedCovered.has(key)) return null
                const place = starts.get(key)
                if (place) {
                  const { p, span } = place
                  const hue = hues.get(p.entries[0].subject_id) ?? 212
                  const bad = p.problems.length > 0
                  const isPicked = picked?.kind === 'move' && picked.key === p.key
                  const swap = picked?.kind === 'move' && !isPicked ? options?.swaps[p.key] : undefined
                  const cellId = `${x.day}:${p.start_period}`
                  return (
                    <td key={x.day} rowSpan={span} className="p-1 align-top">
                      <div
                        id={`cell-${cellId}`}
                        draggable={!p.is_locked}
                        onDragStart={(e) => (p.is_locked ? e.preventDefault() : onDragPlacement(p, e))}
                        onDragEnd={onDragEnd}
                        onDragOver={(e) => picked && (!swap || swap.ok) && !(picked.kind === 'move' && picked.key === p.key) && e.preventDefault()}
                        onDrop={(e) => onDrop(x.day, p.start_period, e)}
                        onMouseEnter={() => onHover({ day: x.day, start: p.start_period })}
                        onMouseLeave={() => onHover(null)}
                        onClick={() => (picked && !isPicked ? onCell(x.day, p.start_period) : onPlacement(p))}
                        role="button"
                        tabIndex={0}
                        onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), picked && !isPicked ? onCell(x.day, p.start_period) : onPlacement(p))}
                        aria-label={`${p.entries.map((e) => e.subject_name).join(' and ')}, ${x.name} ${periodsText(p.start_period, p.length)}${bad ? ', has a clash' : ''}`}
                        title={bad ? p.problems.join(' ') : p.is_locked ? 'Pinned: the generator keeps it here. Unpin to move it.' : swap ? (swap.ok ? 'Click to swap places' : `Cannot swap: ${swap.reasons[0]}`) : 'Click to move or remove'}
                        style={{ ['--h' as string]: hue }}
                        className={`group relative flex h-full min-h-[3.75rem] w-full cursor-pointer flex-col justify-center gap-0.5 rounded-lg border px-2 py-1.5 text-left transition hover:brightness-95 focus-visible:outline-2 ${bad ? 'border-danger bg-danger-soft text-danger' : 'subj-chip'} ${isPicked ? 'ring-2 ring-brand' : ''} ${swap ? (swap.ok ? 'ring-2 ring-success-line' : 'opacity-50') : ''} ${p.is_locked ? 'ring-2 ring-warning-line' : ''} ${flash === cellId ? 'cell-pulse' : ''}`}
                      >
                        {p.entries.map((e) => (
                          <span key={e.id} className="block leading-tight">
                            <span className="block text-[12.5px] font-semibold">{e.subject_name}</span>
                            {(e.teacher_name || e.co_teacher_name) && (
                              <span className="block text-[11px] opacity-80">
                                {e.teacher_name}
                                {e.co_teacher_name ? ` + ${e.co_teacher_name}` : ''}
                              </span>
                            )}
                          </span>
                        ))}
                        <span className="mt-0.5 flex flex-wrap items-center gap-1 text-[10.5px] font-medium opacity-80">
                          {p.is_lab && <span className="rounded bg-black/10 px-1">Lab · 2 periods</span>}
                          {p.parallel && (
                            <span className="inline-flex items-center gap-0.5 rounded bg-black/10 px-1">
                              <Layers className="size-3" aria-hidden="true" /> Parallel
                            </span>
                          )}
                          {p.is_locked && (
                            <span className="inline-flex items-center gap-0.5 rounded bg-black/10 px-1">
                              <Pin className="size-3" aria-hidden="true" /> Pinned
                            </span>
                          )}
                          {bad && <TriangleAlert className="size-3.5" aria-label="Clash" />}
                          {!bad && p.warnings.length > 0 && <TriangleAlert className="size-3.5 text-warning" aria-label="Warning" />}
                        </span>
                        <span className="absolute right-1 top-1 flex gap-0.5 opacity-0 transition group-hover:opacity-100 focus-within:opacity-100">
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation()
                              onTogglePin(p)
                            }}
                            aria-label={p.is_locked ? `Unpin ${p.entries.map((e) => e.subject_name).join(' and ')}` : `Pin ${p.entries.map((e) => e.subject_name).join(' and ')} (the generator keeps it here)`}
                            title={p.is_locked ? 'Unpin' : 'Pin: the generator keeps it exactly here'}
                            className="rounded-md bg-black/10 p-0.5"
                          >
                            {p.is_locked ? <PinOff className="size-3.5" /> : <Pin className="size-3.5" />}
                          </button>
                          {!p.is_locked && (
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation()
                                onRemove(p)
                              }}
                              aria-label={`Remove ${p.entries.map((e) => e.subject_name).join(' and ')}`}
                              className="rounded-md bg-black/10 p-0.5"
                            >
                              <X className="size-3.5" />
                            </button>
                          )}
                        </span>
                      </div>
                    </td>
                  )
                }
                const lock = lockedStarts.get(key)
                if (lock) {
                  const { l, span } = lock
                  return (
                    <td key={x.day} rowSpan={span} className="p-1 align-top">
                      <div
                        id={`cell-${x.day}:${l.start_period}`}
                        title={`Locked on the Fixed Slots screen${l.combined ? ` - combined with ${l.classes.join(', ')}` : ''}`}
                        onMouseEnter={() => onHover({ day: x.day, start: l.start_period })}
                        onMouseLeave={() => onHover(null)}
                        className="flex h-full min-h-[3.75rem] w-full cursor-not-allowed flex-col justify-center gap-0.5 rounded-lg border border-dashed border-line-strong bg-surface-2 px-2 py-1.5 text-left text-muted"
                      >
                        <span className="flex items-center gap-1 text-[11px] font-bold uppercase tracking-wide">
                          <Lock className="size-3" aria-hidden="true" /> {l.kind === 'OTHER' ? 'Locked' : l.kind}
                        </span>
                        <span className="text-[12.5px] font-semibold leading-tight text-ink">{l.label}</span>
                        {l.combined && (
                          <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-brand-text">
                            <Layers className="size-3" aria-hidden="true" /> Combined · {l.classes.length} classes
                          </span>
                        )}
                        {l.teacher_name && <span className="text-[11px]">{l.teacher_name}{l.co_teacher_name ? ` + ${l.co_teacher_name}` : ''}</span>}
                      </div>
                    </td>
                  )
                }
                if (row.type === 'lunch') return <td key={x.day} className="bg-warning-soft/40 p-1" aria-label="Lunch" />
                const unavailable = x.is_off ? 'OFF' : x.end_time && row.end > x.end_time ? `After ${x.end_time}` : null
                if (unavailable) {
                  return (
                    <td key={x.day} className="p-1">
                      <div
                        className="flex h-full min-h-[3.75rem] items-center justify-center rounded-lg bg-[repeating-linear-gradient(135deg,var(--surface-2),var(--surface-2)_6px,var(--surface-hover)_6px,var(--surface-hover)_12px)] text-xs text-subtle"
                        title={x.is_off ? `${x.name} is OFF for this class` : `This class ends at ${x.end_time} on ${x.name}`}
                      >
                        {unavailable}
                      </div>
                    </td>
                  )
                }
                // an empty cell of a working day
                const opt: CellOption | undefined = picked ? options?.cells[`${x.day}:${row.number}`] : undefined
                const inSpan = hover && picked && hover.day === x.day && row.number >= hover.start && row.number < hover.start + length
                const cellId = `${x.day}:${row.number}`
                const tone = !picked
                  ? 'border-dashed border-line-strong text-subtle hover:border-brand hover:bg-brand-soft'
                  : !opt
                    ? 'border-dashed border-line-strong'
                    : opt.state === 'ok'
                      ? 'border-success-line bg-success-soft'
                      : opt.state === 'warn'
                        ? 'border-warning-line bg-warning-soft'
                        : 'cursor-not-allowed border-danger-line bg-danger-soft/60 opacity-70'
                return (
                  <td key={x.day} className="p-1">
                    <button
                      type="button"
                      id={`cell-${cellId}`}
                      onClick={() => onCell(x.day, row.number)}
                      onDragOver={(e) => picked && opt?.state !== 'blocked' && e.preventDefault()}
                      onDrop={(e) => onDrop(x.day, row.number, e)}
                      onMouseEnter={() => onHover({ day: x.day, start: row.number })}
                      onMouseLeave={() => onHover(null)}
                      aria-label={`${x.name} period ${row.number}${picked ? (opt ? `: ${opt.state === 'blocked' ? 'not possible' : opt.state === 'warn' ? 'possible with a warning' : 'possible'}` : '') : ''}`}
                      title={opt ? (opt.state === 'blocked' ? opt.hard[0] : opt.soft[0] ?? 'Possible') : undefined}
                      className={`flex h-full min-h-[3.75rem] w-full items-center justify-center rounded-lg border text-xs font-medium transition-colors ${tone} ${inSpan ? 'ring-2 ring-brand' : ''} ${flash === cellId ? 'cell-pulse' : ''}`}
                    >
                      {picked && opt?.state === 'blocked' && <Ban className="size-4 text-danger opacity-70" aria-hidden="true" />}
                      {picked && opt?.state === 'ok' && <span className="text-success">Place here</span>}
                      {picked && opt?.state === 'warn' && <span className="text-warning">Place (warning)</span>}
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
})

// ---------------------------------------------------------------------- the palette
function StatusBadge({ status, placed, required }: { status: 'missing' | 'complete' | 'over'; placed: number; required: number }) {
  const tone = status === 'complete' ? 'success' : status === 'over' ? 'info' : placed === 0 ? 'neutral' : 'warning'
  return (
    <Badge tone={tone}>
      {placed} / {required}
    </Badge>
  )
}

function PaletteCard({
  title, lines, placed, required, status, badges, selected, hue, onPick, onDragStart, onDragEnd,
}: {
  title: string
  lines: string[]
  placed: number
  required: number
  status: 'missing' | 'complete' | 'over'
  badges: string[]
  selected: boolean
  hue: number
  onPick: () => void
  onDragStart: (e: DragEvent) => void
  onDragEnd: (e: DragEvent) => void
}) {
  const pct = required ? Math.min(100, Math.round((placed / required) * 100)) : 0
  return (
    <button
      type="button"
      draggable
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      onClick={onPick}
      aria-pressed={selected}
      style={{ ['--h' as string]: hue }}
      className={`w-full cursor-grab rounded-xl border px-3 py-2.5 text-left transition active:cursor-grabbing ${selected ? 'border-brand bg-brand-soft ring-2 ring-brand' : 'border-line bg-surface hover:border-line-strong hover:bg-hover'}`}
    >
      <div className="flex items-start justify-between gap-2">
        <span className="flex min-w-0 items-start gap-2">
          <span className="subj-chip mt-1 size-2.5 shrink-0 rounded-full border" aria-hidden="true" />
          <span className="min-w-0">
            <span className="block text-[13.5px] font-semibold leading-snug text-ink">{title}</span>
            {lines.map((l) => (
              <span key={l} className="block text-xs leading-snug text-muted">{l}</span>
            ))}
          </span>
        </span>
        <StatusBadge status={status} placed={placed} required={required} />
      </div>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-surface-2 ring-1 ring-inset ring-line">
        <div className={`h-full rounded-full ${status === 'complete' ? 'bg-[var(--success)]' : status === 'over' ? 'bg-[var(--info)]' : 'bg-brand'}`} style={{ width: `${pct}%` }} />
      </div>
      {badges.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {badges.map((b) => (
            <span key={b} className="rounded bg-surface-2 px-1.5 py-0.5 text-[10.5px] font-medium text-muted ring-1 ring-inset ring-line">{b}</span>
          ))}
        </div>
      )}
    </button>
  )
}

// ---------------------------------------------------------------------- the page
export default function DraftEditorPage() {
  const { draftId = '' } = useParams()
  const { user } = useAuth()
  const navigate = useNavigate()
  const toast = useToast()
  const [search] = useSearchParams()
  const prefix = user ? ROLE_PREFIX[user.role] : ''
  const { data, error, loading } = useApi<DraftDetail>(draftId ? `/drafts/${draftId}` : null)

  const [d, setD] = useState<DraftDetail | null>(null)
  const [picked, setPicked] = useState<Pick | null>(null)
  const [options, setOptions] = useState<OptionsPayload | null>(null)
  const [hover, setHover] = useState<{ day: number; start: number } | null>(null)
  const [busy, setBusy] = useState(false)
  const [flash, setFlash] = useState<string | null>(null)
  const [onlyMissing, setOnlyMissing] = useState(false)
  const [renaming, setRenaming] = useState(false)
  const [copying, setCopying] = useState(false)
  const [clearing, setClearing] = useState(false)
  const seq = useRef(0)
  const pickedRef = useRef<Pick | null>(null)
  pickedRef.current = picked
  const dragging = useRef(false) // true from "picked up with the mouse" until "dropped": a dropped lecture ends the selection

  useEffect(() => {
    if (data) setD(data)
  }, [data])
  useEffect(() => {
    setD(null)
    setPicked(null)
    setOptions(null)
  }, [draftId])

  // jump to a cell (from the conflict report: ?day=3&period=2)
  const flashFromUrl = search.get('day') && search.get('period') ? `${search.get('day')}:${search.get('period')}` : null
  useEffect(() => {
    if (!d || !flashFromUrl) return
    setFlash(flashFromUrl)
    const t = window.setTimeout(() => document.getElementById(`cell-${flashFromUrl}`)?.scrollIntoView({ block: 'center', behavior: 'smooth' }), 150)
    const off = window.setTimeout(() => setFlash(null), 4500)
    return () => {
      window.clearTimeout(t)
      window.clearTimeout(off)
    }
  }, [d?.draft.id, flashFromUrl]) // eslint-disable-line react-hooks/exhaustive-deps

  const hues = useMemo(() => {
    const m = new Map<string, number>()
    d?.subjects.forEach((s, i) => m.set(s.id, HUES[i % HUES.length]))
    return m
  }, [d?.subjects]) // eslint-disable-line react-hooks/exhaustive-deps

  const clearPick = useCallback(() => {
    seq.current += 1
    setPicked(null)
    setOptions(null)
    setHover(null)
  }, [])

  const loadOptions = useCallback(
    async (p: Pick) => {
      const mine = ++seq.current
      setOptions(null)
      try {
        const body = p.kind === 'subject' ? { subject_id: p.id } : p.kind === 'group' ? { group_id: p.id } : { placement_key: p.key }
        const res = await api<OptionsPayload>(`/drafts/${draftId}/options`, { method: 'POST', body })
        if (seq.current === mine) setOptions(res)
      } catch (err) {
        if (seq.current === mine) {
          setPicked(null)
          toast.error(err instanceof ApiError ? err.message : 'Could not check the cells. Please try again.')
        }
      }
    },
    [draftId, toast],
  )

  const pick = useCallback(
    (p: Pick) => {
      setPicked(p)
      void loadOptions(p)
    },
    [loadOptions],
  )

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && clearPick()
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [clearPick])

  /** Runs one change on the server. The answer carries the new state of the whole draft. */
  const mutate = useCallback(
    async (fn: () => Promise<Mutation>, keep?: () => Pick | null): Promise<boolean> => {
      setBusy(true)
      try {
        const res = await fn()
        setD(res.detail)
        ;(res.warnings ?? []).slice(0, 2).forEach((w) => toast.warning(w))
        const next = keep?.() ?? null
        if (next) pick(next)
        else clearPick()
        return true
      } catch (err) {
        toast.error(err instanceof ApiError ? err.message : 'Something went wrong. Please try again.')
        return false
      } finally {
        setBusy(false)
      }
    },
    [toast, pick, clearPick],
  )

  const placementAt = useCallback(
    (day: number, period: number) => d?.placements.find((p) => p.day === day && period >= p.start_period && period < p.start_period + p.length),
    [d],
  )

  const onCell = useCallback(
    async (day: number, period: number, viaDrag = false) => {
      const p = pickedRef.current
      if (!p || !d || busy) return
      const here = placementAt(day, period)
      if (p.kind === 'move') {
        if (here && here.key === p.key) {
          clearPick()
          return
        }
        if (here) {
          const sw = options?.swaps[here.key]
          if (sw && !sw.ok) {
            toast.error(sw.reasons[0] ?? 'These two cannot be swapped.')
            return
          }
          await mutate(() => api<Mutation>(`/drafts/${draftId}/swap`, { method: 'POST', body: { placement_a: p.key, placement_b: here.key } }))
          return
        }
        const opt = options?.cells[`${day}:${period}`]
        if (opt?.state === 'blocked') {
          toast.error(opt.hard[0])
          return
        }
        await mutate(() => api<Mutation>(`/drafts/${draftId}/placements/${p.key}`, { method: 'PATCH', body: { day, start_period: period } }))
        return
      }
      const opt = options?.cells[`${day}:${period}`]
      if (opt?.state === 'blocked') {
        toast.error(opt.hard[0])
        return
      }
      const body = p.kind === 'subject' ? { subject_id: p.id, day, start_period: period } : { group_id: p.id, day, start_period: period }
      await mutate(
        () => api<Mutation>(`/drafts/${draftId}/placements`, { method: 'POST', body }),
        // stay in "place more" mode while lectures are still missing (click-to-place); the selection is renewed with the new state
        () => {
          if (viaDrag) return null // a drag-and-drop is one lecture; click mode stays on until everything is placed
          const target = p.kind === 'subject' ? d.subjects.find((s) => s.id === p.id) : d.groups.find((g) => g.id === p.id)
          return target && target.placed + 1 < target.required ? p : null
        },
      )
    },
    [d, busy, options, draftId, mutate, placementAt, clearPick, toast],
  )

  const onDrop = useCallback(
    (day: number, period: number, e: DragEvent) => {
      e.preventDefault()
      const viaDrag = dragging.current
      dragging.current = false
      void onCell(day, period, viaDrag)
    },
    [onCell],
  )

  const onPlacement = useCallback(
    (p: Placement) => {
      if (p.is_locked) {
        toast.info('This placement is pinned - the generator keeps it here. Unpin it (📌) to move it.')
        return
      }
      const name = p.entries.map((e) => e.subject_name).join(' ∥ ')
      if (pickedRef.current?.kind === 'move' && pickedRef.current.key === p.key) clearPick()
      else pick({ kind: 'move', key: p.key, name, length: p.length })
    },
    [pick, clearPick, toast],
  )

  const onDragPlacement = useCallback(
    (p: Placement, e: DragEvent) => {
      dragging.current = true
      e.dataTransfer.effectAllowed = 'move'
      e.dataTransfer.setData('text/plain', p.key)
      pick({ kind: 'move', key: p.key, name: p.entries.map((x) => x.subject_name).join(' ∥ '), length: p.length })
    },
    [pick],
  )

  const onRemove = useCallback(
    (p: Placement) => void mutate(() => api<Mutation>(`/drafts/${draftId}/placements/${p.key}`, { method: 'DELETE' })).then((ok) => ok && toast.success('Removed from the draft.')),
    [draftId, mutate, toast],
  )

  const onTogglePin = useCallback(
    (p: Placement) =>
      void mutate(() => api<Mutation>(`/drafts/${draftId}/placements/${p.key}/lock`, { method: 'POST', body: { locked: !p.is_locked } })).then(
        (ok) => ok && toast.success(p.is_locked ? 'Unpinned.' : 'Pinned - "Generate (keep pinned placements)" will build around it.'),
      ),
    [draftId, mutate, toast],
  )

  const onDragEnd = useCallback(
    (e: DragEvent) => {
      dragging.current = false
      if (e.dataTransfer.dropEffect === 'none') clearPick() // let go outside the grid = cancel
    },
    [clearPick],
  )

  const dragSubject = (p: Pick) => (e: DragEvent) => {
    dragging.current = true
    e.dataTransfer.effectAllowed = 'move'
    e.dataTransfer.setData('text/plain', p.kind === 'move' ? p.key : p.id)
    pick(p)
  }

  async function savePrimary() {
    if (!d) return
    setBusy(true)
    try {
      await api(`/drafts/${d.draft.id}`, { method: 'PATCH', body: { is_primary: true } })
      setD({ ...d, draft: { ...d.draft, is_primary: true }, siblings: d.siblings.map((s) => ({ ...s, is_primary: s.id === d.draft.id })) })
      toast.success('This is now the primary draft of the class.')
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Could not change the primary draft.')
    } finally {
      setBusy(false)
    }
  }

  // ---- render
  if (error) {
    return (
      <>
        <Link to={`${prefix}/generate`} className="mb-4 inline-flex items-center gap-1.5 text-sm font-medium text-brand-text hover:underline">
          <ArrowLeft className="size-4" /> All drafts
        </Link>
        <Alert tone="danger" title="This draft cannot be opened">
          {error.message}
        </Alert>
      </>
    )
  }
  if (!d) {
    return (
      <div className="space-y-4" aria-busy={loading}>
        <Skeleton className="h-12 w-72" />
        <Skeleton className="h-[34rem]" />
      </div>
    )
  }

  const subjectsToPlace = d.subjects.filter((s) => !s.special_kind && !s.group_id)
  const fixedSubjects = d.subjects.filter((s) => s.special_kind)
  const lectureCards: { key: string; sort: number; node: ReactNode; missing: boolean }[] = []
  for (const g of d.groups as PaletteGroup[]) {
    const members = d.subjects.filter((s) => s.group_id === g.id)
    const teachers = members.map((m) => `${m.name} - ${m.teacher_name ?? 'no teacher'}${m.co_teacher_name ? ` + ${m.co_teacher_name}` : ''}`)
    lectureCards.push({
      key: `g-${g.id}`, sort: 0, missing: g.status === 'missing',
      node: (
        <PaletteCard
          title={`${g.name}`} lines={teachers} placed={g.placed} required={g.required} status={g.status}
          badges={['Parallel group', g.is_lab ? 'Lab · 2 periods' : 'All options together']} hue={hues.get(members[0]?.id ?? '') ?? 212}
          selected={picked?.kind === 'group' && picked.id === g.id}
          onPick={() => (picked?.kind === 'group' && picked.id === g.id ? clearPick() : pick({ kind: 'group', id: g.id, name: g.name, length: g.is_lab ? 2 : 1 }))}
          onDragStart={dragSubject({ kind: 'group', id: g.id, name: g.name, length: g.is_lab ? 2 : 1 })} onDragEnd={onDragEnd}
        />
      ),
    })
  }
  for (const s of subjectsToPlace as PaletteSubject[]) {
    lectureCards.push({
      key: `s-${s.id}`, sort: s.is_lab ? 1 : 2, missing: s.status === 'missing',
      node: (
        <PaletteCard
          title={s.name} lines={[`${s.teacher_name ?? 'No teacher yet'}${s.co_teacher_name ? ` + ${s.co_teacher_name}` : ''}`]}
          placed={s.placed} required={s.required} status={s.status} badges={s.is_lab ? ['Lab · 1 block of 2 periods'] : [`${s.credits} credits = ${s.required} lectures`]} hue={hues.get(s.id) ?? 212}
          selected={picked?.kind === 'subject' && picked.id === s.id}
          onPick={() => (picked?.kind === 'subject' && picked.id === s.id ? clearPick() : pick({ kind: 'subject', id: s.id, name: s.name, length: s.is_lab ? 2 : 1 }))}
          onDragStart={dragSubject({ kind: 'subject', id: s.id, name: s.name, length: s.is_lab ? 2 : 1 })} onDragEnd={onDragEnd}
        />
      ),
    })
  }
  lectureCards.sort((a, b) => a.sort - b.sort)
  const shownCards = lectureCards.filter((c) => !onlyMissing || c.missing)

  const hoverOpt = picked && hover ? options?.cells[`${hover.day}:${hover.start}`] : undefined
  const hoverOccupant = hover ? placementAt(hover.day, hover.start) : undefined
  const swapInfo = picked?.kind === 'move' && hoverOccupant && hoverOccupant.key !== picked.key ? options?.swaps[hoverOccupant.key] : undefined
  const problemList = d.placements.filter((p) => p.problems.length > 0 || p.warnings.length > 0)
  const comp = d.completeness
  const jump = (p: Placement) => {
    const id = `${p.day}:${p.start_period}`
    setFlash(id)
    document.getElementById(`cell-${id}`)?.scrollIntoView({ block: 'center', behavior: 'smooth' })
    window.setTimeout(() => setFlash(null), 3000)
  }

  return (
    <>
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <Link to={`${prefix}/generate`} className="mb-2 inline-flex items-center gap-1.5 text-sm font-medium text-brand-text hover:underline">
            <ArrowLeft className="size-4" /> All drafts
          </Link>
          <h1 className="flex flex-wrap items-center gap-2.5 text-[1.5rem] font-semibold leading-tight tracking-tight text-ink">
            {d.draft.class_label}
            <span className="text-muted">·</span>
            <span className="text-brand-text">{d.draft.name}</span>
          </h1>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {d.draft.is_primary ? (
              <Badge tone="brand">
                <Star className="size-3" aria-hidden="true" /> Primary draft
              </Badge>
            ) : (
              <Badge>Alternative draft</Badge>
            )}
            <Badge tone={comp.percent === 100 ? 'success' : 'neutral'}>{comp.percent}% placed · {comp.missing} lecture{comp.missing === 1 ? '' : 's'} to go</Badge>
            {d.problems.clashes > 0 ? (
              <Badge tone="danger">
                <TriangleAlert className="size-3" aria-hidden="true" /> {d.problems.clashes} clash{d.problems.clashes === 1 ? '' : 'es'}
              </Badge>
            ) : d.placements.length > 0 ? (
              <Badge tone="success">No clash</Badge>
            ) : null}
            {d.problems.warnings > 0 && <Badge tone="warning">{d.problems.warnings} warning{d.problems.warnings === 1 ? '' : 's'}</Badge>}
            {d.siblings.length > 1 && (
              <SelectField
                compact
                aria-label="Switch draft"
                className="w-48"
                value={d.draft.id}
                onChange={(id) => id !== d.draft.id && navigate(`${prefix}/generate/${id}`)}
                options={d.siblings.map((s) => ({ value: s.id, label: `${s.name}${s.is_primary ? ' (primary)' : ''}` }))}
              />
            )}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {!d.draft.is_primary && (
            <Button variant="secondary" size="sm" icon={<Star className="size-4" />} onClick={() => void savePrimary()} disabled={busy}>
              Make primary
            </Button>
          )}
          <Button variant="secondary" size="sm" icon={<Pencil className="size-4" />} onClick={() => setRenaming(true)}>
            Rename
          </Button>
          <Button variant="secondary" size="sm" icon={<Copy className="size-4" />} onClick={() => setCopying(true)}>
            Duplicate
          </Button>
          <Button variant="secondary" size="sm" icon={<Trash2 className="size-4" />} onClick={() => setClearing(true)} disabled={d.placements.length === 0}>
            Clear all
          </Button>
          <Link to={`${prefix}/conflicts`}>
            <Button variant="secondary" size="sm" icon={<ShieldAlert className="size-4" />}>
              Conflict Report
            </Button>
          </Link>
        </div>
      </div>

      {!d.draft.is_primary && (
        <Alert tone="info" className="mb-4">
          This is an <span className="font-semibold">alternative</span> draft. Other classes are checked against the primary draft of this class, not against this one. Press “Make primary” when this is the one you want to use.
        </Alert>
      )}
      {!d.no_pe_sol && d.subjects.some((s) => s.special_kind && s.status === 'missing') && (
        <Alert tone="warning" className="mb-4" title="PE / SOL are not locked yet">
          Their slots come from the <Link to={`${prefix}/fixed-slots`} className="font-semibold underline">Fixed Slots</Link> screen; they will appear locked in this grid.
        </Alert>
      )}

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="min-w-0 space-y-4">
          {/* what is picked up / what the hovered cell says */}
          <div
            aria-live="polite"
            className={`${picked ? 'hidden sm:flex' : 'flex'} min-h-[3.25rem] flex-wrap items-center gap-x-4 gap-y-1 rounded-xl border px-4 py-2.5 text-sm ${
              !picked ? 'border-line bg-surface text-muted' : hoverOpt?.state === 'blocked' || (swapInfo && !swapInfo.ok) ? 'border-danger-line bg-danger-soft text-danger' : hoverOpt?.state === 'warn' ? 'border-warning-line bg-warning-soft text-warning' : 'border-brand/40 bg-brand-soft text-brand-text'
            }`}
          >
            {!picked ? (
              <span className="flex items-center gap-2">
                <MousePointerClick className="size-4 shrink-0" aria-hidden="true" />
                Drag a subject onto the grid - or click a subject, then click a cell. Click a placed lecture to move, swap or remove it.
              </span>
            ) : (
              <>
                <span className="font-semibold">
                  {picked.kind === 'move' ? 'Moving' : 'Placing'} “{picked.name}”{picked.length > 1 ? ` (${picked.length} periods)` : ''}
                </span>
                <span className="min-w-0 flex-1">
                  {!options
                    ? 'Checking every cell...'
                    : swapInfo
                      ? swapInfo.ok
                        ? 'Click here to swap the two lectures.'
                        : `Cannot swap: ${swapInfo.reasons[0]}`
                      : hoverOpt
                        ? hoverOpt.state === 'blocked'
                          ? `Not possible: ${hoverOpt.hard[0]}`
                          : hoverOpt.state === 'warn'
                            ? `Possible, but: ${hoverOpt.soft[0]}`
                            : 'This cell is free and clash-free.'
                        : 'Green = free, amber = possible with a warning, red = blocked (hover for the reason).'}
                </span>
                <Button variant="ghost" size="sm" onClick={clearPick} icon={<X className="size-4" />}>
                  Cancel (Esc)
                </Button>
              </>
            )}
          </div>

          <WeekGrid
            d={d} hues={hues} picked={picked} options={options} hover={hover} flash={flash}
            onCell={(day, period) => void onCell(day, period)} onHover={setHover} onPlacement={onPlacement}
            onDragPlacement={onDragPlacement} onDragEnd={onDragEnd} onRemove={onRemove} onTogglePin={onTogglePin} onDrop={onDrop}
          />

          <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
            <span className="subj-chip rounded border px-2 py-0.5" style={{ ['--h' as string]: 212 }}>Placed lecture</span>
            <span className="rounded border border-dashed border-line-strong bg-surface-2 px-2 py-0.5"><Lock className="mr-1 inline size-3" />Locked (Fixed Slots)</span>
            <span className="rounded border border-danger bg-danger-soft px-2 py-0.5 text-danger">Clash</span>
            <span className="rounded border border-success-line bg-success-soft px-2 py-0.5 text-success">Free</span>
            <span className="rounded border border-warning-line bg-warning-soft px-2 py-0.5 text-warning">Free, with a warning</span>
            <span className="inline-flex items-center gap-1"><ArrowLeftRight className="size-3.5" /> drop on another lecture = swap</span>
            <span className="inline-flex items-center gap-1"><Pin className="size-3.5" /> pinned = kept by the generator</span>
          </div>

          {problemList.length > 0 && (
            <section className="rounded-2xl border border-line bg-surface shadow-card" aria-label="Problems in this draft">
              <h2 className="border-b border-line px-5 py-3 text-[15px] font-semibold text-ink">Problems in this draft</h2>
              <ul className="divide-y divide-line">
                {problemList.map((p) => (
                  <li key={p.key} className="flex items-start gap-3 px-5 py-3">
                    <TriangleAlert className={`mt-0.5 size-4 shrink-0 ${p.problems.length ? 'text-danger' : 'text-warning'}`} aria-hidden="true" />
                    <div className="min-w-0 flex-1 text-sm">
                      <button type="button" onClick={() => jump(p)} className="font-semibold text-ink hover:text-brand-text hover:underline">
                        {p.entries.map((e) => e.subject_name).join(' ∥ ')} · {['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][p.day - 1]} {periodsText(p.start_period, p.length)}
                      </button>
                      {p.problems.map((m) => (
                        <p key={m} className="text-danger">{m}</p>
                      ))}
                      {p.warnings.map((m) => (
                        <p key={m} className="text-warning">{m}</p>
                      ))}
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>

        {/* the subjects: what still has to be placed */}
        <aside className="min-w-0 space-y-4 lg:sticky lg:top-20 lg:max-h-[calc(100vh-6rem)] lg:self-start lg:overflow-y-auto lg:pr-1" aria-label="Subjects of the class">
          <div className="rounded-2xl border border-line bg-surface p-4 shadow-card">
            <div className="flex items-center justify-between text-sm">
              <span className="font-semibold text-ink">Completeness</span>
              <span className="tabular-nums font-semibold text-ink">{comp.percent}%</span>
            </div>
            <div className="mt-2 h-2.5 overflow-hidden rounded-full bg-surface-2 ring-1 ring-inset ring-line" role="progressbar" aria-valuenow={comp.percent} aria-valuemin={0} aria-valuemax={100}>
              <div className={`h-full rounded-full transition-all ${comp.percent === 100 ? 'bg-[var(--success)]' : 'bg-brand'}`} style={{ width: `${comp.percent}%` }} />
            </div>
            <p className="mt-2 text-xs text-muted">
              {comp.placed} of {comp.required} lectures placed (credits = lectures a week; a lab is one 2-period block; PE / SOL come from their locked slots).
              {comp.missing > 0 ? ` ${comp.missing} still to place.` : ' Everything is placed.'}
            </p>
            <label className="mt-3 flex cursor-pointer items-center gap-2 text-xs text-muted">
              <input type="checkbox" className="size-4 accent-[var(--brand-solid)]" checked={onlyMissing} onChange={(e) => setOnlyMissing(e.target.checked)} />
              Show only what is still missing
            </label>
          </div>

          <div className="space-y-2.5">
            {shownCards.length === 0 ? (
              <p className="rounded-xl border border-dashed border-line-strong px-4 py-6 text-center text-sm text-muted">Nothing is missing here.</p>
            ) : (
              shownCards.map((c) => <div key={c.key}>{c.node}</div>)
            )}
          </div>

          {fixedSubjects.length > 0 && (
            <div className="rounded-2xl border border-line bg-surface p-4 shadow-card">
              <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold text-ink">
                <Lock className="size-4 text-muted" aria-hidden="true" /> Fixed (not placed here)
              </h3>
              <ul className="space-y-2">
                {fixedSubjects.map((s) => (
                  <li key={s.id} className="flex items-start justify-between gap-2 text-[13px]">
                    <span className="min-w-0">
                      <span className="block font-medium text-ink">{s.name}</span>
                      <span className="block text-xs text-muted">{s.teacher_name ?? 'No teacher yet'}</span>
                    </span>
                    {s.status === 'complete' ? <Badge tone="success">Locked</Badge> : <Badge tone="warning">Not fixed yet</Badge>}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </aside>
      </div>

      {/* phone: what is picked up stays visible while the cells are tapped */}
      {picked && (
        <div className="fixed inset-x-3 bottom-3 z-30 flex items-center gap-2 rounded-xl border border-brand/40 bg-surface px-4 py-3 shadow-pop lg:hidden">
          <p className="min-w-0 flex-1 text-sm">
            <span className="font-semibold text-ink">{picked.kind === 'move' ? 'Moving' : 'Placing'} “{picked.name}”</span>
            <span className="block text-xs text-muted">Tap a green or amber cell{picked.kind === 'move' ? ', or another lecture to swap' : ''}.</span>
          </p>
          {picked.kind === 'move' && (
            <Button variant="danger" size="sm" onClick={() => { const p = d.placements.find((x) => x.key === picked.key); if (p) onRemove(p) }}>
              Remove
            </Button>
          )}
          <Button variant="secondary" size="sm" onClick={clearPick}>
            Cancel
          </Button>
        </div>
      )}

      <RenameDraft open={renaming} d={d} onClose={() => setRenaming(false)} onSaved={(name, note) => { setD({ ...d, draft: { ...d.draft, name, note }, siblings: d.siblings.map((s) => (s.id === d.draft.id ? { ...s, name } : s)) }); setRenaming(false); toast.success('Saved.') }} />
      <DuplicateDraft open={copying} d={d} onClose={() => setCopying(false)} onCreated={(id) => { setCopying(false); toast.success('Copy created.'); navigate(`${prefix}/generate/${id}`) }} />
      <ConfirmDialog
        open={clearing}
        tone="danger"
        title="Clear every placement of this draft?"
        confirmLabel="Clear all"
        onClose={() => setClearing(false)}
        onConfirm={async () => {
          const res = await api<Mutation>(`/drafts/${draftId}/clear`, { method: 'POST' })
          setD(res.detail)
          clearPick()
          toast.success('The draft is empty again.')
        }}
      >
        <p>
          All {d.placements.filter((p) => !p.is_locked).length} unpinned placements are removed from “{d.draft.name}”.
          {d.placements.some((p) => p.is_locked) ? ' Pinned placements stay.' : ''} Locked slots (PE / SOL / fixed) stay too. This cannot be undone.
        </p>
      </ConfirmDialog>
    </>
  )
}

function RenameDraft({ open, d, onClose, onSaved }: { open: boolean; d: DraftDetail; onClose: () => void; onSaved: (name: string, note: string | null) => void }) {
  const [name, setName] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    if (open) {
      setName(d.draft.name)
      setNote(d.draft.note ?? '')
      setError(null)
    }
  }, [open, d.draft.name, d.draft.note])
  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await api(`/drafts/${d.draft.id}`, { method: 'PATCH', body: { name, note: note || null } })
      onSaved(name.trim(), note.trim() || null)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save. Please try again.')
    } finally {
      setBusy(false)
    }
  }
  return (
    <Modal
      open={open}
      onClose={() => !busy && onClose()}
      title="Rename draft"
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button type="submit" form="rename-draft" loading={busy} disabled={!name.trim()}>Save</Button>
        </>
      }
    >
      <form id="rename-draft" onSubmit={submit} noValidate className="space-y-4">
        <TextField label="Name" value={name} onChange={(e) => setName(e.target.value)} maxLength={60} required autoFocus />
        <TextField label="Note (optional)" value={note} onChange={(e) => setNote(e.target.value)} maxLength={300} />
        {error && <Alert tone="danger">{error}</Alert>}
      </form>
    </Modal>
  )
}

function DuplicateDraft({ open, d, onClose, onCreated }: { open: boolean; d: DraftDetail; onClose: () => void; onCreated: (id: string) => void }) {
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    if (open) {
      setName(`${d.draft.name} (copy)`)
      setError(null)
    }
  }, [open, d.draft.name])
  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const res = await api<{ id: string }>('/drafts', { method: 'POST', body: { semester_id: d.draft.semester_id, name, copy_from: d.draft.id } })
      onCreated(res.id)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not copy. Please try again.')
    } finally {
      setBusy(false)
    }
  }
  return (
    <Modal
      open={open}
      onClose={() => !busy && onClose()}
      title="Duplicate this draft"
      description="The copy keeps every placement. Change it freely - the original stays as it is."
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button type="submit" form="dup-draft" loading={busy} disabled={!name.trim()}>Create copy</Button>
        </>
      }
    >
      <form id="dup-draft" onSubmit={submit} noValidate className="space-y-4">
        <TextField label="Name of the copy" value={name} onChange={(e) => setName(e.target.value)} maxLength={60} required autoFocus />
        {error && <Alert tone="danger">{error}</Alert>}
      </form>
    </Modal>
  )
}
