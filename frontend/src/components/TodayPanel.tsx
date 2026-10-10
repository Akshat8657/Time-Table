import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRightLeft, CalendarOff, ChevronLeft, ChevronRight, Clock, Layers, Lock, Plus, Repeat, Users } from 'lucide-react'
import { useAuth } from '../auth/AuthProvider'
import { addDaysIso, formatDate, todayIso, weekdayOf } from '../lib/helpers'
import { ROLE_PREFIX } from '../lib/nav'
import { useNotifications } from '../lib/notifications'
import type { TodayView, ViewCell } from '../lib/types'
import { useApi } from '../lib/useApi'
import { Badge } from './Badge'
import { Button } from './Button'
import { hueOf, teachersOf } from './TimetableGrid'
import { EmptyState, Panel, Skeleton } from './Panel'

function Lecture({ c, state }: { c: ViewCell; state: 'now' | 'next' | null }) {
  const first = c.items[0]
  const cancelled = c.status !== null
  const classes = c.classes.map((x) => x.label).join(' + ')
  return (
    <li
      className={`flex items-stretch gap-3 rounded-xl border px-3 py-2.5 ${state === 'now' ? 'border-accent bg-brand-soft/60' : 'border-line bg-surface'} ${cancelled ? 'opacity-70' : ''}`}
      data-testid="today-lecture"
      data-status={c.status ?? 'ok'}
    >
      <div className="w-[5.2rem] shrink-0 text-center">
        <p className="text-[13px] font-semibold tabular-nums text-ink">{c.time?.split('-')[0]}</p>
        <p className="text-[11px] tabular-nums text-muted">to {c.time?.split('-')[1]}</p>
        <p className="mt-0.5 text-[11px] text-subtle">P{c.start_period}{c.length > 1 ? `-${c.start_period + c.length - 1}` : ''}</p>
      </div>
      <span
        className={`w-1 shrink-0 rounded-full ${c.kind === 'fixed' ? 'bg-line-strong' : 'subj-chip border-0'}`}
        style={c.kind === 'fixed' ? undefined : ({ '--h': hueOf(first?.code ?? first?.title ?? 'x'), background: 'hsl(var(--h) 60% 55%)' } as React.CSSProperties)}
        aria-hidden="true"
      />
      <div className="min-w-0 flex-1">
        <p className={`text-sm font-semibold text-ink ${cancelled ? 'line-through' : ''}`}>
          {c.items.map((i) => i.title).join('  |  ')}
          {c.is_lab && !/lab/i.test(first?.title ?? '') ? ' (Lab)' : ''}
        </p>
        <p className="text-[13px] text-muted">
          {classes}
          {c.items.length > 1 || (c.parallel_with && c.parallel_with.length > 0) ? '' : ''}
        </p>
        <div className="mt-1 flex flex-wrap items-center gap-1.5">
          {state === 'now' && <Badge tone="brand">Now</Badge>}
          {state === 'next' && <Badge tone="info">Next</Badge>}
          {c.kind === 'substitution' && <Badge tone="info"><Repeat className="size-3" aria-hidden="true" /> Substitution · covering for {c.substitution?.for_name}</Badge>}
          {c.kind === 'extra' && <Badge tone="info"><Plus className="size-3" aria-hidden="true" /> Extra lecture{c.extra?.reason ? ` · ${c.extra.reason}` : ''}</Badge>}
          {c.kind === 'fixed' && <Badge><Lock className="size-3" aria-hidden="true" /> {c.fixed_kind === 'OTHER' ? 'Locked slot' : c.fixed_kind}</Badge>}
          {c.mine_absent && <Badge tone="warning"><CalendarOff className="size-3" aria-hidden="true" /> You are absent{c.substitutions?.[0]?.substitute_name ? ` · ${c.substitutions[0].substitute_name} covers` : ''}</Badge>}
          {!c.mine_absent && (c.substitutions ?? []).map((s) => (
            <Badge key={s.id} tone={s.status === 'filled' ? 'info' : 'warning'}><Repeat className="size-3" aria-hidden="true" /> {s.status === 'filled' ? `Sub: ${s.substitute_name}` : 'Needs substitute'}</Badge>
          ))}
          {c.adjusted?.kind === 'move' && <Badge tone="brand"><ArrowRightLeft className="size-3" aria-hidden="true" /> {c.adjusted.swap ? 'Swapped' : `Moved from P${c.moved_from}`}</Badge>}
          {c.parallel_with && c.parallel_with.length > 0 && <Badge><Layers className="size-3" aria-hidden="true" /> Parallel with {c.parallel_with.join(', ')}</Badge>}
          {(c.combined || c.combined_with) && <Badge><Users className="size-3" aria-hidden="true" /> {c.classes.length > 1 ? 'Combined session' : `Combined with ${c.combined_with}`}</Badge>}
          {c.status && (
            <Badge tone={c.status === 'holiday' ? 'success' : c.status === 'cancelled' ? 'danger' : 'warning'}>
              <CalendarOff className="size-3" aria-hidden="true" /> {c.status === 'holiday' ? 'Holiday' : c.status === 'event' ? 'Event' : c.status === 'cancelled' ? 'Cancelled today' : 'Semester not running'}
              {c.status_note ? `: ${c.status_note}` : ''}
            </Badge>
          )}
          {teachersOf(c) && c.items.length > 1 && <span className="text-xs text-muted">{teachersOf(c)}</span>}
        </div>
      </div>
    </li>
  )
}

/** "Today": the signed-in teacher's own lectures of one day (published timetable only), with holidays and events shown. */
export function TodayPanel({ title = 'Today’s schedule' }: { title?: string }) {
  const { user } = useAuth()
  const [on, setOn] = useState(todayIso())
  const { arrivals } = useNotifications()
  const { data, error, loading, reload } = useApi<TodayView>(`/views/my/today?on=${on}&_=${arrivals}`)
  const prefix = user ? ROLE_PREFIX[user.role] : ''
  const timetablePath = user?.role === 'hod' ? '/hod/my-schedule/timetable' : `${prefix}/timetable`
  const isToday = on === todayIso()

  const nav = (
    <div className="flex items-center gap-1">
      <Button variant="ghost" size="sm" aria-label="Previous day" icon={<ChevronLeft className="size-4" />} onClick={() => setOn(addDaysIso(on, -1))} />
      <Button variant="ghost" size="sm" onClick={() => setOn(todayIso())} disabled={isToday}>Today</Button>
      <Button variant="ghost" size="sm" aria-label="Next day" icon={<ChevronRight className="size-4" />} onClick={() => setOn(addDaysIso(on, 1))} />
    </div>
  )

  return (
    <Panel title={title} icon={Clock} action={nav} className="lg:col-span-2">
      <p className="mb-3 text-sm text-muted" data-testid="today-date">
        <span className="font-semibold text-ink">{weekdayOf(on)}</span>, {formatDate(on)}{isToday ? ' · today' : ''}
      </p>
      {loading && !data ? (
        <div className="space-y-2">
          <Skeleton className="h-16" />
          <Skeleton className="h-16" />
        </div>
      ) : error ? (
        <p className="text-sm text-danger">
          {error.message}{' '}
          <button type="button" onClick={reload} className="font-medium underline">Try again</button>
        </p>
      ) : data && !data.term ? (
        <EmptyState icon={Clock} title="No active term yet">Your lectures appear here once the term is set and the timetable is published.</EmptyState>
      ) : data && !data.published ? (
        <EmptyState icon={Clock} title="The timetable is not published yet">
          Your HOD will publish it soon. You will get a notification here (and a browser alert, if you turned them on) the moment it is live.
        </EmptyState>
      ) : data ? (
        <>
          {data.banners.map((b) => (
            <div key={b.id} className="mb-3 flex items-center gap-2 rounded-xl border border-success-line bg-success-soft px-3 py-2 text-sm text-success">
              <CalendarOff className="size-4 shrink-0" aria-hidden="true" />
              <span><span className="font-semibold">{b.kind === 'holiday' ? 'Holiday' : 'Event'}:</span> {b.title}{b.full_day ? '' : ` (${b.window})`}</span>
            </div>
          ))}
          {!data.is_working_day ? (
            <EmptyState icon={CalendarOff} title={`${data.weekday_name} is not a working day`}>There are no lectures. Enjoy the day off.</EmptyState>
          ) : data.cells.length === 0 ? (
            <EmptyState icon={Clock} title="No lectures on this day">
              {data.week_lectures === 0 ? 'No lecture is assigned to you in the published timetable.' : 'You are free on this day.'}
            </EmptyState>
          ) : (
            <>
              <ul className="space-y-2" data-testid="today-list">
                {data.cells.map((c) => (
                  <Lecture key={`${c.key}-${c.date}`} c={c} state={c.key === data.current_key ? 'now' : c.key === data.next_key ? 'next' : null} />
                ))}
              </ul>
              <p className="mt-3 text-[13px] text-muted">
                {data.stats.lectures} {data.stats.lectures === 1 ? 'lecture' : 'lectures'} · {data.stats.hours} {data.stats.hours === 1 ? 'hour' : 'hours'}
                {data.stats.cancelled > 0 ? ` · ${data.stats.cancelled} not happening` : ''}
              </p>
            </>
          )}
          <Link to={timetablePath} className="mt-3 inline-block text-[13px] font-medium text-brand-text hover:underline">
            Open my full week →
          </Link>
        </>
      ) : null}
    </Panel>
  )
}
