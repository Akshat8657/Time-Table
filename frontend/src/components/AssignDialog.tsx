import { useEffect, useState } from 'react'
import { Send, Sparkles, UserCheck } from 'lucide-react'
import { api, ApiError } from '../lib/api'
import type { SubCandidate, Substitution } from '../lib/types'
import { Alert } from './Alert'
import { Badge } from './Badge'
import { Button } from './Button'
import { Modal } from './Modal'
import { Skeleton } from './Panel'
import { useToast } from './Toast'

/** Pick a substitute manually from the ranked free teachers, or send auto popup-offers to the top few. */
export function AssignDialog({ sub, onClose, onDone }: { sub: Substitution | null; onClose: () => void; onDone: () => void }) {
  const toast = useToast()
  const [cands, setCands] = useState<SubCandidate[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  useEffect(() => {
    if (!sub) return
    setCands(null)
    setError(null)
    let alive = true
    api<{ candidates: SubCandidate[] }>(`/subs/${sub.id}/candidates`).then(
      (d) => alive && setCands(d.candidates),
      (err) => alive && setError(err instanceof ApiError ? err.message : 'Could not load candidates.'),
    )
    return () => {
      alive = false
    }
  }, [sub])

  async function assign(teacherId: string) {
    if (!sub) return
    setBusy(teacherId)
    try {
      await api(`/subs/${sub.id}/assign`, { method: 'POST', body: { teacher_id: teacherId } })
      toast.success('Substitute assigned. They have been notified.')
      onDone()
      onClose()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not assign.')
    } finally {
      setBusy(null)
    }
  }

  async function offer() {
    if (!sub) return
    setBusy('offer')
    try {
      const r = await api<{ offered: SubCandidate[] }>(`/subs/${sub.id}/offer`, { method: 'POST', body: { count: 5 } })
      toast.success(`Offer sent to ${r.offered.length} teacher(s). First to accept gets it.`)
      onDone()
      onClose()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not send offers.')
    } finally {
      setBusy(null)
    }
  }

  return (
    <Modal
      open={sub !== null}
      onClose={() => !busy && onClose()}
      size="lg"
      title={sub ? `Find a substitute - ${sub.label}` : 'Find a substitute'}
      description={sub ? `${sub.classes.join(', ')} · ${new Date(sub.lecture_date).toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'short' })} · period ${sub.start_period} (${sub.time}) · for ${sub.absent_teacher_name}` : undefined}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={!!busy}>Close</Button>
          <Button icon={<Send className="size-4" />} onClick={() => void offer()} loading={busy === 'offer'} disabled={!cands || cands.length === 0} data-testid="send-offers">
            Send auto offers (top 5)
          </Button>
        </>
      }
    >
      {error && <Alert tone="danger" className="mb-3">{error}</Alert>}
      {!cands && !error ? (
        <div className="space-y-2"><Skeleton className="h-12" /><Skeleton className="h-12" /></div>
      ) : cands && cands.length === 0 ? (
        <Alert tone="warning" title="No teacher is free for this lecture">
          Nobody is free at that period. Try a one-day <span className="font-semibold">move or swap</span> (Day changes tab) so a free teacher can take it, or cancel the lecture for that day.
        </Alert>
      ) : cands ? (
        <ul className="divide-y divide-line overflow-hidden rounded-xl border border-line" data-testid="candidate-list">
          {cands.map((c, i) => (
            <li key={c.teacher_id} className="flex flex-wrap items-center gap-3 px-3.5 py-3">
              <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-brand-soft text-xs font-semibold text-brand-text">{i + 1}</span>
              <div className="min-w-0 flex-1">
                <p className="flex flex-wrap items-center gap-1.5 text-sm font-semibold text-ink">
                  {c.name}
                  {c.can_teach && <Badge tone="success">can teach</Badge>}
                  {c.taught_before && <Badge tone="brand">taught this class</Badge>}
                  {c.is_visiting && <Badge tone="warning">visiting</Badge>}
                </p>
                <p className="text-[12px] text-muted">{c.reasons.join(' · ')}</p>
              </div>
              <Button size="sm" variant="secondary" icon={<UserCheck className="size-4" />} onClick={() => void assign(c.teacher_id)} loading={busy === c.teacher_id} data-testid="assign-one">
                Assign
              </Button>
            </li>
          ))}
        </ul>
      ) : null}
      {cands && cands.length > 0 && (
        <p className="mt-3 flex items-center gap-1.5 text-[13px] text-muted">
          <Sparkles className="size-4 text-brand-text" aria-hidden="true" /> Only teachers who are free at that time are shown, best first.
        </p>
      )}
    </Modal>
  )
}
