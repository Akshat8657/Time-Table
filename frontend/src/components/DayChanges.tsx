import { useEffect, useState } from 'react'
import { ArrowRightLeft, Ban, CalendarClock, MoveRight, Sparkles, Undo2 } from 'lucide-react'
import { api, ApiError } from '../lib/api'
import { formatShort, todayIso } from '../lib/helpers'
import type { AdjustSuggestPayload, ViewCell } from '../lib/types'
import { useApi } from '../lib/useApi'
import { Alert } from './Alert'
import { Badge } from './Badge'
import { Button } from './Button'
import { ConfirmDialog } from './ConfirmDialog'
import { TextField } from './Field'
import { Modal } from './Modal'
import { EmptyState, Panel, Skeleton } from './Panel'
import { SelectField } from './Select'
import { useToast } from './Toast'

interface DayPayload {
  date: string
  class_label: string
  cells: ViewCell[]
  adjustable_starts: number[]
  adjustments: { id: string; kind: string; orig_start: number; new_start: number | null; label: string; swap_group: string | null }[]
}

/** One-day move / cancel / swap of a class's lectures with live clash checks and smart suggestions. */
export function DayChanges({ classes }: { classes: { id: string; label: string }[] }) {
  const toast = useToast()
  const [classId, setClassId] = useState('')
  const [date, setDate] = useState(todayIso())
  useEffect(() => {
    if (!classId && classes.length) setClassId(classes[0].id)
  }, [classes, classId])
  const path = classId && date ? `/adjust?semester_id=${classId}&date=${date}` : null
  const { data, error, loading, reload } = useApi<DayPayload>(path)
  const [suggestFor, setSuggestFor] = useState<{ start: number; label: string } | null>(null)
  const [cancelFor, setCancelFor] = useState<{ start: number; label: string } | null>(null)

  const lectures = (data?.cells ?? [])
    .filter((c) => c.kind === 'lecture' || c.kind === 'fixed')
    .map((c) => ({ cell: c, base: c.moved_from ?? c.start_period }))
    .sort((a, b) => a.base - b.base)

  async function doCancel() {
    if (!cancelFor || !classId) return
    await api('/adjust/cancel', { method: 'POST', body: { semester_id: classId, date, start_period: cancelFor.start } })
    toast.success('Lecture cancelled for that day. Affected teachers are told.')
    reload()
  }

  async function undo(id: string) {
    await api(`/adjust/${id}`, { method: 'DELETE' })
    toast.success('Change undone.')
    reload()
  }

  return (
    <Panel title="Day changes - move / cancel / swap" icon={CalendarClock}>
      <div className="mb-4 flex flex-wrap items-end gap-3">
        <SelectField label="Class" className="w-64" value={classId} onChange={setClassId} options={classes.map((c) => ({ value: c.id, label: c.label }))} />
        <TextField label="Date" type="date" value={date} onChange={(e) => setDate(e.target.value)} className="w-44" />
      </div>
      <p className="mb-3 text-[13px] text-muted">The weekly timetable never changes - this only adjusts one date.</p>
      {error ? (
        <Alert tone="danger">{error.message}</Alert>
      ) : loading && !data ? (
        <Skeleton className="h-40" />
      ) : data ? (
        lectures.length === 0 ? (
          <EmptyState icon={CalendarClock} title="No lecture on this day">Pick another date or class.</EmptyState>
        ) : (
          <ul className="divide-y divide-line overflow-hidden rounded-xl border border-line" data-testid="day-lectures">
            {lectures.map(({ cell, base }) => {
              const adj = data.adjustments.find((a) => a.orig_start === base)
              const canAdjust = data.adjustable_starts.includes(base) && !adj
              return (
                <li key={cell.key} className="flex flex-wrap items-center gap-3 px-3.5 py-3">
                  <span className="w-20 shrink-0 text-[13px] font-semibold tabular-nums text-ink">
                    P{base}
                    {adj?.kind === 'move' && <span className="font-normal text-brand-text"> → P{adj.new_start}</span>}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className={`text-sm font-medium text-ink ${cell.status === 'cancelled' ? 'line-through' : ''}`}>{cell.items.map((i) => i.title).join(' / ')}</p>
                    <p className="text-[12px] text-muted">{cell.time}{cell.combined || cell.classes.length > 1 ? ' · combined' : ''}</p>
                  </div>
                  {cell.status === 'cancelled' && <Badge tone="danger">Cancelled</Badge>}
                  {adj?.kind === 'move' && <Badge tone="brand">{adj.swap_group ? 'Swapped' : 'Moved'}</Badge>}
                  <div className="flex shrink-0 items-center gap-1.5">
                    {adj ? (
                      <Button variant="ghost" size="sm" icon={<Undo2 className="size-4" />} onClick={() => void undo(adj.id)}>Undo</Button>
                    ) : canAdjust ? (
                      <>
                        <Button variant="secondary" size="sm" icon={<MoveRight className="size-4" />} onClick={() => setSuggestFor({ start: base, label: cell.items[0]?.title ?? '' })} data-testid="adjust-move">Move / swap</Button>
                        <Button variant="ghost" size="sm" icon={<Ban className="size-4" />} onClick={() => setCancelFor({ start: base, label: cell.items[0]?.title ?? '' })}>Cancel</Button>
                      </>
                    ) : (
                      <Badge>combined - fixed</Badge>
                    )}
                  </div>
                </li>
              )
            })}
          </ul>
        )
      ) : null}

      {suggestFor && classId && (
        <SuggestDialog classId={classId} date={date} start={suggestFor.start} label={suggestFor.label} onClose={() => setSuggestFor(null)} onDone={reload} />
      )}
      <ConfirmDialog open={cancelFor !== null} title="Cancel this lecture for the day?" tone="danger" confirmLabel="Cancel the lecture" onConfirm={doCancel} onClose={() => setCancelFor(null)}>
        {cancelFor && <p>{cancelFor.label} on {formatShort(date)} will be marked cancelled for that date only. It counts as a lost hour in the hours tracking, and the teachers are told.</p>}
      </ConfirmDialog>
    </Panel>
  )
}

function SuggestDialog({ classId, date, start, label, onClose, onDone }: { classId: string; date: string; start: number; label: string; onClose: () => void; onDone: () => void }) {
  const toast = useToast()
  const [data, setData] = useState<AdjustSuggestPayload | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let alive = true
    api<AdjustSuggestPayload>(`/adjust/suggest?semester_id=${classId}&date=${date}&start_period=${start}`).then(
      (d) => alive && setData(d),
      (err) => alive && setError(err instanceof ApiError ? err.message : 'Could not load suggestions.'),
    )
    return () => {
      alive = false
    }
  }, [classId, date, start])

  async function apply(body: Record<string, unknown>, path: string) {
    setBusy(true)
    try {
      await api(path, { method: 'POST', body: { semester_id: classId, date, start_period: start, ...body } })
      toast.success('Done. Affected teachers are notified.')
      onDone()
      onClose()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not apply the change.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal open onClose={() => !busy && onClose()} size="md" title={`Move or swap - ${label}`} description={`Period ${start} on ${formatShort(date)}. Only clash-free options are shown.`}>
      {error && <Alert tone="danger" className="mb-3">{error}</Alert>}
      {!data && !error ? (
        <Skeleton className="h-24" />
      ) : data ? (
        <div className="space-y-4">
          <div>
            <p className="mb-1.5 text-[13px] font-semibold text-ink">Move to another period</p>
            {data.moves.length === 0 ? (
              <p className="text-[13px] text-muted">No free period to move it to today.</p>
            ) : (
              <div className="flex flex-wrap gap-2">
                {data.moves.map((m) => (
                  <Button key={m.new_start} variant="secondary" size="sm" icon={<MoveRight className="size-4" />} loading={busy} onClick={() => void apply({ new_start: m.new_start }, '/adjust/move')} title={m.reasons.join(' · ')}>
                    P{m.new_start} <span className="font-normal text-muted">{m.time}</span>
                  </Button>
                ))}
              </div>
            )}
          </div>
          <div>
            <p className="mb-1.5 text-[13px] font-semibold text-ink">Swap with another lecture</p>
            {data.swaps.length === 0 ? (
              <p className="text-[13px] text-muted">No same-length lecture to swap with today.</p>
            ) : (
              <div className="flex flex-col gap-2">
                {data.swaps.map((s) => (
                  <Button key={s.swap_with_start} variant="secondary" size="sm" icon={<ArrowRightLeft className="size-4" />} loading={busy} onClick={() => void apply({ with_start: s.swap_with_start }, '/adjust/swap')} className="justify-start">
                    Swap with {s.with_label} (P{s.swap_with_start})
                  </Button>
                ))}
              </div>
            )}
          </div>
          <p className="flex items-center gap-1.5 text-[12px] text-muted"><Sparkles className="size-3.5 text-brand-text" aria-hidden="true" /> {data.message}</p>
        </div>
      ) : null}
    </Modal>
  )
}
