import { TriangleAlert } from 'lucide-react'
import type { HoursSubject } from '../lib/types'
import { Badge } from './Badge'

/** How many hours a subject has had / can still have, against the hours it needs. */
export function HoursBar({ h }: { h: HoursSubject }) {
  const need = Math.max(1, h.required)
  const planned = Math.min(100, (Math.min(h.deliverable, need) / need) * 100)
  const done = Math.min(100, (Math.min(h.delivered, need) / need) * 100)
  const short = h.shortfall > 0
  return (
    <div>
      <div
        className="relative h-2.5 overflow-hidden rounded-full bg-surface-2 ring-1 ring-line"
        role="img"
        aria-label={`${h.delivered} of ${h.required} hours taught, ${h.deliverable} possible by the end of the semester`}
      >
        <div className={`absolute inset-y-0 left-0 rounded-full ${short ? 'bg-danger-solid/30' : 'bg-success-solid/30'}`} style={{ width: `${planned}%` }} />
        <div className={`absolute inset-y-0 left-0 rounded-full ${short ? 'bg-danger-solid' : 'bg-success-solid'}`} style={{ width: `${done}%` }} />
      </div>
      <p className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-muted">
        <span><span className="font-semibold tabular-nums text-ink">{h.delivered}</span> taught</span>
        <span><span className="font-semibold tabular-nums text-ink">{h.deliverable}</span> possible</span>
        <span>needs <span className="font-semibold tabular-nums text-ink">{h.required}</span> h</span>
        {h.lost > 0 && <span>{h.lost} h lost to holidays / events</span>}
        {h.extra > 0 && <span className="text-info">+{h.extra} h extra lectures</span>}
      </p>
    </div>
  )
}

export function ShortBadge({ h }: { h: HoursSubject }) {
  if (h.status === 'unpublished') return <Badge tone="neutral">not published yet</Badge>
  if (h.status === 'no_dates') return <Badge tone="warning">semester dates missing</Badge>
  if (h.shortfall > 0)
    return (
      <span data-testid="short-badge">
        <Badge tone="danger" className="font-semibold">
          <TriangleAlert className="size-3" aria-hidden="true" /> short by {h.shortfall} h
        </Badge>
      </span>
    )
  return <Badge tone="success">on track</Badge>
}
