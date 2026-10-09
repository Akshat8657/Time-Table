import { useEffect, useState } from 'react'
import { CalendarPlus, Check } from 'lucide-react'
import { api, ApiError } from '../lib/api'
import { formatShort } from '../lib/helpers'
import type { HoursSubject, SuggestPayload } from '../lib/types'
import { Alert } from './Alert'
import { Button } from './Button'
import { TextField } from './Field'
import { Modal } from './Modal'
import { Skeleton } from './Panel'
import { useToast } from './Toast'

/** "Suggest extra lectures": clash-free slots for a subject that is short of hours; the HOD ticks the ones to approve. */
export function SuggestDialog({ subject, onClose, onDone }: { subject: HoursSubject | null; onClose: () => void; onDone: () => void }) {
  const toast = useToast()
  const [data, setData] = useState<SuggestPayload | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [reason, setReason] = useState('Compensation for holidays / events')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!subject) return
    setData(null)
    setError(null)
    setPicked(new Set())
    let alive = true
    api<SuggestPayload>('/hours/suggest', { method: 'POST', body: { subject_id: subject.subject_id } }).then(
      (d) => {
        if (!alive) return
        setData(d)
        setPicked(new Set(d.suggestions.map((s) => `${s.date}|${s.start_period}`)))
      },
      (err: unknown) => alive && setError(err instanceof ApiError ? err.message : 'Could not find slots. Please try again.'),
    )
    return () => {
      alive = false
    }
  }, [subject])

  async function approve() {
    if (!subject || !data) return
    setBusy(true)
    try {
      const slots = data.suggestions.filter((s) => picked.has(`${s.date}|${s.start_period}`)).map((s) => ({ date: s.date, start_period: s.start_period }))
      const res = await api<{ message: string }>('/hours/extra', { method: 'POST', body: { subject_id: subject.subject_id, slots, reason: reason.trim() || null } })
      toast.success(res.message)
      onDone()
      onClose()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not add the extra lectures. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  const toggle = (k: string) =>
    setPicked((cur) => {
      const next = new Set(cur)
      if (next.has(k)) next.delete(k)
      else next.add(k)
      return next
    })

  return (
    <Modal
      open={subject !== null}
      onClose={() => !busy && onClose()}
      size="lg"
      title={subject ? `Extra lectures for ${subject.name}` : 'Extra lectures'}
      description={subject ? `${subject.class_label} - short by ${subject.shortfall} hours. Every slot below is free for the class and the teacher, on a working day, with no holiday or event.` : undefined}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button icon={<Check className="size-4" />} onClick={() => void approve()} loading={busy} disabled={!data || picked.size === 0} data-testid="approve-extra">
            Approve {picked.size} {picked.size === 1 ? 'lecture' : 'lectures'}
          </Button>
        </>
      }
    >
      {error && <Alert tone="danger" className="mb-3">{error}</Alert>}
      {!data && !error ? (
        <div className="space-y-2"><Skeleton className="h-12" /><Skeleton className="h-12" /><Skeleton className="h-12" /></div>
      ) : data ? (
        <div className="space-y-4">
          <Alert tone={data.unfilled > 0 || data.suggestions.length === 0 ? 'warning' : 'success'}>
            Needs <span className="font-semibold">{data.needed}</span> extra {data.needed === 1 ? 'lecture' : 'lectures'}. {data.message}
            {data.group && <span className="mt-1 block">This is an elective group: all of {data.group.map((g) => g.name).join(' + ')} get the extra lecture together.</span>}
          </Alert>
          {data.suggestions.length > 0 && (
            <ul className="divide-y divide-line overflow-hidden rounded-xl border border-line" data-testid="suggestions">
              {data.suggestions.map((s) => {
                const k = `${s.date}|${s.start_period}`
                return (
                  <li key={k}>
                    <label className="flex cursor-pointer items-start gap-3 px-3.5 py-3 hover:bg-hover">
                      <input type="checkbox" className="mt-1 size-4 accent-[var(--brand-solid)]" checked={picked.has(k)} onChange={() => toggle(k)} />
                      <span className="min-w-0 flex-1">
                        <span className="flex flex-wrap items-center gap-2 text-sm font-semibold text-ink">
                          <CalendarPlus className="size-4 text-brand-text" aria-hidden="true" />
                          {formatShort(s.date)} · period {s.start_period}{s.length > 1 ? `-${s.start_period + s.length - 1}` : ''}
                          <span className="font-normal tabular-nums text-muted">{s.time}</span>
                        </span>
                        <span className="mt-0.5 block text-[13px] text-muted">{s.reasons.join(' · ')}</span>
                      </span>
                    </label>
                  </li>
                )
              })}
            </ul>
          )}
          <TextField label="Reason (the teacher sees it)" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={300} />
          <p className="text-[13px] text-muted">When you approve, an extra one-off lecture is added on those dates and the teacher is notified (bell + browser alert).</p>
        </div>
      ) : null}
    </Modal>
  )
}
