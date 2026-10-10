import { useEffect, useRef, useState } from 'react'
import { Ban, Check, Lock, Star } from 'lucide-react'
import { DAY_NAMES, DAY_SHORT } from '../lib/helpers'
import type { GridInfo, PrefKind } from '../lib/types'

type Tool = 'available' | PrefKind

const TOOLS: { value: Tool; label: string; hint: string }[] = [
  { value: 'available', label: 'Available', hint: 'Fine with me - the default' },
  { value: 'preferred', label: 'Preferred', hint: 'I would like to teach then' },
  { value: 'unavailable', label: 'Not available', hint: 'I cannot teach then - never used for me' },
]

const WORDS: Record<Tool, string> = { available: 'Available', preferred: 'Preferred', unavailable: 'Not available' }

function Mark({ kind }: { kind: Tool }) {
  if (kind === 'preferred') return <Star className="size-[18px] fill-warning text-warning" aria-hidden="true" />
  if (kind === 'unavailable') return <Ban className="size-[18px] text-danger" aria-hidden="true" />
  return <Check className="size-4 text-subtle/50 dark:text-subtle/75" aria-hidden="true" />
}

const CELL: Record<Tool, string> = {
  available: 'bg-surface hover:bg-hover',
  preferred: 'bg-warning-soft ring-1 ring-inset ring-warning-line',
  unavailable: 'bg-danger-soft ring-1 ring-inset ring-danger-line',
}

/**
 * A teacher's weekly grid: every slot is Available / Preferred (star) / Not available (cross).
 * Pick a brush, then click - or drag across - the slots. Days a visiting teacher does not come are locked (a separate, fixed rule).
 * The school's limit on "not available" marks is enforced while painting (the server checks it again on save).
 */
export function PrefGrid({ grid, days, slots, lockedDays, editable, limit, onChange, onLimit }: {
  grid: GridInfo
  days: number
  slots: Record<string, PrefKind>
  lockedDays: number[]
  editable: boolean
  limit: number
  onChange?: (next: Record<string, PrefKind>) => void
  onLimit?: () => void
}) {
  const [tool, setTool] = useState<Tool>('unavailable')
  const painting = useRef<{ kind: Tool } | null>(null)
  const latest = useRef(slots)
  latest.current = slots

  useEffect(() => {
    const stop = () => {
      painting.current = null
    }
    window.addEventListener('pointerup', stop)
    window.addEventListener('pointercancel', stop)
    return () => {
      window.removeEventListener('pointerup', stop)
      window.removeEventListener('pointercancel', stop)
    }
  }, [])

  function paint(day: number, period: number, kind: Tool) {
    if (!editable || !onChange || lockedDays.includes(day)) return
    const key = `${day}:${period}`
    const cur = latest.current
    if ((cur[key] ?? 'available') === kind) return
    if (kind === 'unavailable' && Object.values(cur).filter((v) => v === 'unavailable').length >= limit) {
      onLimit?.()
      return
    }
    const next = { ...cur }
    if (kind === 'available') delete next[key]
    else next[key] = kind
    latest.current = next
    onChange(next)
  }

  function press(day: number, period: number) {
    const current = latest.current[`${day}:${period}`] ?? 'available'
    const kind: Tool = current === tool ? 'available' : tool   // clicking a slot that already has the brush undoes it
    painting.current = { kind }
    paint(day, period, kind)
  }

  const used = Object.values(slots).filter((v) => v === 'unavailable').length
  const starred = Object.values(slots).filter((v) => v === 'preferred').length
  const dayList = Array.from({ length: days }, (_, i) => i + 1)
  const lunch = grid.lunch
  const rows: ({ kind: 'period'; n: number; time: string } | { kind: 'lunch'; time: string })[] = []
  let lunchShown = false
  grid.periods.forEach((p, i) => {
    if (lunch && !lunchShown && i > 0 && grid.periods[i - 1].end <= lunch.start && p.start >= lunch.end) {
      rows.push({ kind: 'lunch', time: `${lunch.start}-${lunch.end}` })
      lunchShown = true
    }
    rows.push({ kind: 'period', n: p.number, time: `${p.start}-${p.end}` })
  })

  return (
    <div>
      {editable && (
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <div role="radiogroup" aria-label="Brush" className="inline-flex flex-wrap gap-1 rounded-xl bg-surface-2 p-1 ring-1 ring-line">
            {TOOLS.map((t) => (
              <button
                key={t.value}
                type="button"
                role="radio"
                aria-checked={tool === t.value}
                title={t.hint}
                onClick={() => setTool(t.value)}
                data-testid={`brush-${t.value}`}
                className={`inline-flex items-center gap-2 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${
                  tool === t.value ? 'bg-surface text-ink shadow-sm ring-1 ring-line' : 'text-muted hover:text-ink'
                }`}
              >
                <Mark kind={t.value} />
                {t.label}
              </button>
            ))}
          </div>
          <p className="text-[13px] text-muted">Pick a brush, then click or drag over the slots.</p>
        </div>
      )}
      <div className="overflow-x-auto">
        <table className="w-full min-w-[34rem] table-fixed border-separate border-spacing-1 select-none" aria-label="Weekly preference grid">
          <thead>
            <tr>
              <th className="w-[5.5rem] text-left text-[12px] font-medium text-muted">Period</th>
              {dayList.map((d) => (
                <th key={d} scope="col" className="text-center text-[13px] font-semibold text-ink">
                  <span className="hidden sm:inline">{DAY_NAMES[d - 1]}</span>
                  <span className="sm:hidden">{DAY_SHORT[d - 1]}</span>
                  {lockedDays.includes(d) && <Lock className="ml-1 inline size-3 text-subtle" aria-label="not a visiting day" />}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) =>
              row.kind === 'lunch' ? (
                <tr key="lunch">
                  <td colSpan={days + 1} className="rounded-lg bg-warning-soft/60 py-1 text-center text-[12px] font-medium text-warning">
                    Lunch {row.time}
                  </td>
                </tr>
              ) : (
                <tr key={row.n}>
                  <th scope="row" className="text-left">
                    <span className="block text-[13px] font-semibold text-ink">P{row.n}</span>
                    <span className="block text-[11px] font-normal text-muted">{row.time}</span>
                  </th>
                  {dayList.map((d) => {
                    const locked = lockedDays.includes(d)
                    const kind: Tool = slots[`${d}:${row.n}`] ?? 'available'
                    return (
                      <td key={d} className="p-0">
                        <button
                          type="button"
                          disabled={locked || !editable}
                          onPointerDown={(e) => {
                            if (e.button === 0 || e.pointerType === 'touch') press(d, row.n)
                          }}
                          onPointerEnter={() => painting.current && paint(d, row.n, painting.current.kind)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter' || e.key === ' ') {
                              e.preventDefault()
                              press(d, row.n)
                              painting.current = null
                            }
                          }}
                          aria-label={`${DAY_NAMES[d - 1]} period ${row.n}: ${locked ? 'not a visiting day' : WORDS[kind]}`}
                          data-testid={`pref-${d}-${row.n}`}
                          data-kind={locked ? 'locked' : kind}
                          className={`flex h-11 w-full touch-manipulation items-center justify-center rounded-lg transition-colors ${
                            locked ? 'cursor-not-allowed bg-surface-2 opacity-50' : `${CELL[kind]} ${editable ? 'cursor-pointer' : 'cursor-default'}`
                          } focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent`}
                        >
                          {locked ? <Lock className="size-3.5 text-subtle" aria-hidden="true" /> : <Mark kind={kind} />}
                        </button>
                      </td>
                    )
                  })}
                </tr>
              ),
            )}
          </tbody>
        </table>
      </div>
      <p className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-1 text-[13px] text-muted" data-testid="pref-counts">
        <span className="inline-flex items-center gap-1.5">
          <Ban className="size-4 text-danger" aria-hidden="true" />
          <span className={`font-semibold tabular-nums ${used >= limit ? 'text-danger' : 'text-ink'}`}>{used}</span> of {limit} "not available" used
        </span>
        <span className="inline-flex items-center gap-1.5">
          <Star className="size-4 fill-warning text-warning" aria-hidden="true" />
          <span className="font-semibold tabular-nums text-ink">{starred}</span> preferred
        </span>
        {lockedDays.length > 0 && (
          <span className="inline-flex items-center gap-1.5">
            <Lock className="size-3.5 text-subtle" aria-hidden="true" /> grey days are not your visiting days (fixed by your HOD)
          </span>
        )}
      </p>
    </div>
  )
}
