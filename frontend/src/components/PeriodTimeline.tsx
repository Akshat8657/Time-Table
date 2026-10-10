import { Utensils } from 'lucide-react'
import type { GridInfo } from '../lib/types'
import { Alert } from './Alert'

/**
 * The daily period grid, drawn as a strip: Period 1 ... lunch ... Period 6.
 * It is built by the server from the timing settings - this is only the picture.
 */
export function PeriodTimeline({ grid, highlight }: { grid: GridInfo; highlight?: (number | null) }) {
  const items: ({ type: 'period'; number: number; start: string; end: string } | { type: 'lunch'; start: string; end: string })[] = []
  grid.periods.forEach((p, i) => {
    items.push({ type: 'period', ...p })
    const next = grid.periods[i + 1]
    if (grid.lunch && next && p.end <= grid.lunch.start && grid.lunch.end <= next.start) {
      items.push({ type: 'lunch', ...grid.lunch })
    }
  })
  return (
    <div>
      {grid.periods.length === 0 ? (
        <Alert tone="warning">No lecture fits between these timings. Make the college day longer or the lecture shorter.</Alert>
      ) : (
        <ol className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 xl:grid-cols-6" aria-label="Periods of the day">
          {items.map((item) =>
            item.type === 'lunch' ? (
              <li
                key="lunch"
                className="flex flex-col items-center justify-center rounded-xl border border-dashed border-warning-line bg-warning-soft px-2 py-2.5 text-center text-warning"
              >
                <Utensils className="mb-0.5 size-4" aria-hidden="true" />
                <span className="text-xs font-semibold">Lunch</span>
                <span className="text-xs tabular-nums">
                  {item.start}-{item.end}
                </span>
              </li>
            ) : (
              <li
                key={item.number}
                className={`rounded-xl border px-2 py-2.5 text-center ${
                  highlight === item.number ? 'border-accent bg-brand-soft text-brand-text' : 'border-line bg-surface-2 text-ink'
                }`}
              >
                <span className="block text-xs font-semibold uppercase tracking-wide text-muted">Period {item.number}</span>
                <span className="mt-0.5 block text-[13px] font-semibold tabular-nums">
                  {item.start}-{item.end}
                </span>
              </li>
            ),
          )}
        </ol>
      )}
      {grid.notes.length > 0 && (
        <ul className="mt-3 space-y-1 text-[13px] text-muted">
          {grid.notes.map((n) => (
            <li key={n} className="flex gap-2">
              <span aria-hidden="true">•</span>
              {n}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
