import { ChevronLeft, ChevronRight } from 'lucide-react'
import { addDaysIso, formatDate, mondayOf, todayIso } from '../lib/helpers'
import { Button } from './Button'

/** Previous week / this week / next week. `monday` is an ISO date. */
export function WeekBar({ monday, onChange }: { monday: string; onChange: (monday: string) => void }) {
  const thisWeek = mondayOf(todayIso())
  return (
    <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Choose the week">
      <Button variant="secondary" size="sm" aria-label="Previous week" icon={<ChevronLeft className="size-4" />} onClick={() => onChange(addDaysIso(monday, -7))} />
      <span className="min-w-[11rem] text-center text-sm font-medium tabular-nums text-ink" data-testid="week-label">
        {formatDate(monday)} – {formatDate(addDaysIso(monday, 5))}
      </span>
      <Button variant="secondary" size="sm" aria-label="Next week" icon={<ChevronRight className="size-4" />} onClick={() => onChange(addDaysIso(monday, 7))} />
      <Button variant="ghost" size="sm" onClick={() => onChange(thisWeek)} disabled={monday === thisWeek}>
        This week
      </Button>
    </div>
  )
}
