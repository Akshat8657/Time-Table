import { useEffect, useMemo, useRef, useState } from 'react'
import { CalendarClock, Clock, Info, Lock, Save, SlidersHorizontal } from 'lucide-react'
import { Alert } from '../components/Alert'
import { Badge } from '../components/Badge'
import { Button } from '../components/Button'
import { TextField } from '../components/Field'
import { PageHeader, Panel, Skeleton } from '../components/Panel'
import { PrefGrid } from '../components/PrefGrid'
import { useToast } from '../components/Toast'
import { api, ApiError } from '../lib/api'
import { formatDuration } from '../lib/format'
import { dayList } from '../lib/helpers'
import { useNotifications } from '../lib/notifications'
import type { PrefKind, PrefPayload } from '../lib/types'
import { useApi } from '../lib/useApi'

function sameSlots(a: Record<string, PrefKind>, b: Record<string, PrefKind>): boolean {
  const ka = Object.keys(a)
  return ka.length === Object.keys(b).length && ka.every((k) => a[k] === b[k])
}

/** Every teacher (and every HOD, who is a teacher too) fills their own weekly grid here - but only while the HOD's window is open. */
export default function MyPreferencesPage() {
  const toast = useToast()
  const { arrivals } = useNotifications()   // reload when a window opens / closes (the bell tells us)
  const { data, error, loading, reload } = useApi<PrefPayload>(`/prefs/me?_=${arrivals}`)
  const [slots, setSlots] = useState<Record<string, PrefKind>>({})
  const [first, setFirst] = useState(false)
  const [last, setLast] = useState(false)
  const [maxDay, setMaxDay] = useState('')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const [saved, setSaved] = useState<PrefPayload | null>(null)

  const current = saved ?? data
  const loaded = useRef(false)    // the first answer from the server has been shown
  const unsaved = useRef(false)   // the teacher has edits that are not saved yet
  useEffect(() => {
    if (!data) return
    if (loaded.current && unsaved.current) return   // a LATER refresh (a new notification arrived) must never wipe unsaved edits
    loaded.current = true
    setSaved(null)
    setSlots(data.slots)
    setFirst(data.prefer_first)
    setLast(data.avoid_last)
    setMaxDay(data.max_per_day ? String(data.max_per_day) : '')
    setProblem(null)
  }, [data])

  const dirty = useMemo(
    () => !!current && (!sameSlots(slots, current.slots) || first !== current.prefer_first || last !== current.avoid_last || (maxDay.trim() === '' ? null : Number(maxDay)) !== current.max_per_day),
    [current, slots, first, last, maxDay],
  )

  unsaved.current = dirty

  async function save() {
    if (!current) return
    setBusy(true)
    setProblem(null)
    try {
      const body = {
        slots: Object.entries(slots).map(([k, kind]) => {
          const [day, period] = k.split(':').map(Number)
          return { day, period, kind }
        }),
        prefer_first: first,
        avoid_last: last,
        max_per_day: maxDay.trim() === '' ? null : Number(maxDay),
      }
      const next = await api<PrefPayload>('/prefs/me', { method: 'PUT', body })
      setSaved(next)
      setSlots(next.slots)
      toast.success('Your preferences are saved. Your HOD can see them now.')
    } catch (err) {
      setProblem(err instanceof ApiError ? err.message : 'Could not save. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  const win = current?.window
  const editable = !!current?.can_edit
  const maxNum = maxDay.trim() === '' ? null : Number(maxDay)
  const maxBad = maxNum !== null && (!Number.isInteger(maxNum) || maxNum < 1 || maxNum > (current?.periods ?? 6))

  return (
    <>
      <PageHeader
        title="My Preferences"
        subtitle="Tell the timetable when you would like to teach and when you cannot. Your HOD opens a window for this; outside it you can look, but not change."
      />
      {error ? (
        <Alert tone="danger">
          {error.message}{' '}
          <button type="button" onClick={reload} className="font-semibold underline">Try again</button>
        </Alert>
      ) : loading && !current ? (
        <div className="space-y-3"><Skeleton className="h-16" /><Skeleton className="h-72" /></div>
      ) : current ? (
        <div className="space-y-5">
          {editable && win ? (
            <Alert tone="success" title={`The preference window is open until ${win.deadline_text}`}>
              About {formatDuration(win.seconds_left)} left.{win.note ? ` Note from your HOD: ${win.note}` : ''} Save before the deadline - {current.submitted ? 'you have already saved once, you can still change it.' : 'your HOD sees who has filled and who has not.'}
            </Alert>
          ) : (
            <Alert tone="info" title="View only">
              {current.reason}
            </Alert>
          )}
          {current.teacher.is_visiting && (
            <Alert tone="info" title="You are visiting faculty">
              You come on {dayList(current.teacher.visiting_days)}. Visiting days and weekly hours are set by your HOD and are always respected - they cannot be changed here.
            </Alert>
          )}
          {current.fixed_notes.length > 0 && (
            <Alert tone="warning" title="A locked session is in the way">
              <ul className="list-disc space-y-0.5 pl-5">{current.fixed_notes.map((n) => <li key={n}>{n}</li>)}</ul>
            </Alert>
          )}
          {problem && (
            <Alert tone="danger" title="Not saved" onDismiss={() => setProblem(null)}>
              {problem}
            </Alert>
          )}

          <Panel title="Weekly availability" icon={CalendarClock} action={current.submitted ? <Badge tone="success">Saved</Badge> : <Badge>Not saved yet</Badge>}>
            <PrefGrid
              grid={current.grid}
              days={current.days}
              slots={slots}
              lockedDays={current.locked_days}
              editable={editable}
              limit={current.limit}
              onChange={setSlots}
              onLimit={() => setProblem(`You can mark at most ${current.limit} slot(s) as "Not available". Change one back to Available first.`)}
            />
          </Panel>

          <Panel title="Special choices" icon={SlidersHorizontal}>
            <div className="grid gap-5 lg:grid-cols-2">
              <div className="space-y-3">
                <label className={`flex items-start gap-3 rounded-xl border border-line p-3.5 ${editable ? 'cursor-pointer hover:bg-hover' : 'opacity-80'}`}>
                  <input type="checkbox" className="mt-0.5 size-4 accent-[var(--brand-solid)]" checked={first} disabled={!editable} onChange={(e) => setFirst(e.target.checked)} data-testid="pref-first" />
                  <span>
                    <span className="block text-sm font-semibold text-ink">I like the first lecture</span>
                    <span className="block text-[13px] text-muted">Start my teaching day at period 1 when it is possible.</span>
                  </span>
                </label>
                <label className={`flex items-start gap-3 rounded-xl border border-line p-3.5 ${editable ? 'cursor-pointer hover:bg-hover' : 'opacity-80'}`}>
                  <input type="checkbox" className="mt-0.5 size-4 accent-[var(--brand-solid)]" checked={last} disabled={!editable} onChange={(e) => setLast(e.target.checked)} data-testid="pref-last" />
                  <span>
                    <span className="block text-sm font-semibold text-ink">Avoid the last lecture</span>
                    <span className="block text-[13px] text-muted">Keep period {current.periods} (the last one of the day) free when it is possible.</span>
                  </span>
                </label>
              </div>
              <div>
                <TextField
                  label="Most lectures I can take in one day"
                  type="number"
                  inputMode="numeric"
                  min={1}
                  max={current.periods}
                  value={maxDay}
                  disabled={!editable}
                  onChange={(e) => setMaxDay(e.target.value)}
                  placeholder="No limit"
                  error={maxBad ? `Use a number from 1 to ${current.periods}, or leave it empty.` : null}
                  hint="A lab counts as 2 lectures. This is a firm limit: the timetable will never give you more in a day."
                  data-testid="pref-max"
                />
              </div>
            </div>
          </Panel>

          <Panel title="How your choices are used" icon={Info}>
            <ul className="grid gap-3 text-[13px] leading-relaxed text-muted sm:grid-cols-2">
              <li className="rounded-xl bg-danger-soft/60 p-3"><span className="font-semibold text-danger">Firm rules:</span> "Not available" slots and your daily maximum are never used by the timetable - when it is made automatically or by hand. Only a few "not available" marks are allowed so that nobody blocks the whole week.</li>
              <li className="rounded-xl bg-warning-soft/60 p-3"><span className="font-semibold text-warning">Best effort:</span> "Preferred" slots, the first lecture and avoiding the last lecture are honoured as far as the other classes allow. Your HOD can see how well they were met.</li>
              <li className="rounded-xl bg-surface-2 p-3 sm:col-span-2"><Lock className="mr-1.5 inline size-3.5 -translate-y-px" aria-hidden="true" /><span className="font-semibold text-ink">Covering for a colleague:</span> if the HOD needs a substitute for a period you marked "Not available", you are asked only when nobody else is free - and the request says so.</li>
            </ul>
          </Panel>

          <div className="sticky bottom-3 z-10 flex flex-wrap items-center gap-3 rounded-2xl border border-line bg-surface/95 px-4 py-3 shadow-card backdrop-blur">
            <Button icon={<Save className="size-4" />} onClick={() => void save()} loading={busy} disabled={!editable || !dirty || maxBad} data-testid="pref-save">
              Save my preferences
            </Button>
            <p className="flex items-center gap-1.5 text-[13px] text-muted">
              <Clock className="size-4" aria-hidden="true" />
              {!editable ? 'Editing is closed.' : dirty ? 'You have changes that are not saved.' : current.submitted ? 'All changes saved.' : 'Nothing saved yet.'}
            </p>
          </div>
        </div>
      ) : null}
    </>
  )
}
