import { useState } from 'react'
import { CalendarOff, CalendarRange, Check, CircleUser, Clock, Send, Trash2, X } from 'lucide-react'
import { Alert } from '../components/Alert'
import { Badge } from '../components/Badge'
import { Button } from '../components/Button'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { TextField } from '../components/Field'
import { EmptyState, PageHeader, Panel, Skeleton } from '../components/Panel'
import { useToast } from '../components/Toast'
import { api, ApiError } from '../lib/api'
import { formatRange, formatShort, todayIso } from '../lib/helpers'
import type { LeaveRequest, MarkAbsentResult, MyAbsence } from '../lib/types'
import { useApi } from '../lib/useApi'
import { LoadError } from '../components/LoadError'

function statusTone(s: LeaveRequest['status']) {
  return s === 'approved' ? 'success' : s === 'rejected' ? 'danger' : s === 'cancelled' ? 'neutral' : 'warning'
}

export default function AbsentLeavePage() {
  const toast = useToast()
  const absences = useApi<{ absences: MyAbsence[] }>('/absence/mine')
  const leave = useApi<{ leave: LeaveRequest[] }>('/leave?scope=mine')
  const [absDate, setAbsDate] = useState(todayIso())
  const [absReason, setAbsReason] = useState('')
  const [absBusy, setAbsBusy] = useState(false)
  const [result, setResult] = useState<MarkAbsentResult | null>(null)
  const [undo, setUndo] = useState<MyAbsence | null>(null)
  const [from, setFrom] = useState(todayIso())
  const [to, setTo] = useState('')
  const [leaveReason, setLeaveReason] = useState('')
  const [leaveBusy, setLeaveBusy] = useState(false)

  async function markAbsent() {
    setAbsBusy(true)
    try {
      const r = await api<MarkAbsentResult>('/absence', { method: 'POST', body: { date: absDate, reason: absReason.trim() || null } })
      setResult(r)
      setAbsReason('')
      absences.reload()
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Could not mark absent.')
    } finally {
      setAbsBusy(false)
    }
  }

  async function doUndo() {
    if (!undo) return
    await api(`/absence/${undo.id}`, { method: 'DELETE' })
    toast.success('Absence removed.')
    setResult(null)
    absences.reload()
  }

  async function requestLeave() {
    setLeaveBusy(true)
    try {
      await api('/leave', { method: 'POST', body: { date_from: from, date_to: to || null, reason: leaveReason.trim() || null } })
      toast.success('Leave request sent to your HOD.')
      setLeaveReason('')
      setTo('')
      leave.reload()
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Could not send the request.')
    } finally {
      setLeaveBusy(false)
    }
  }

  async function cancelLeave(id: string) {
    await api(`/leave/${id}/cancel`, { method: 'POST' })
    toast.success('Leave request withdrawn.')
    leave.reload()
  }

  return (
    <>
      <PageHeader
        title="Absent & Leave"
        subtitle="Mark yourself absent for a day (your HOD is told at once and your lectures are listed for a substitute), or request leave for a date or a range."
      />
      <div className="space-y-6">
        <div className="grid gap-6 lg:grid-cols-2">
          <Panel title="Mark myself absent" icon={CalendarOff}>
            <div className="flex flex-wrap items-end gap-3">
              <TextField label="Date" type="date" value={absDate} min={todayIso()} onChange={(e) => setAbsDate(e.target.value)} className="w-44" />
              <TextField label="Reason (optional)" value={absReason} onChange={(e) => setAbsReason(e.target.value)} maxLength={300} className="min-w-[12rem] flex-1" />
              <Button icon={<CalendarOff className="size-4" />} onClick={() => void markAbsent()} loading={absBusy}>Mark absent</Button>
            </div>
            {result && (
              <Alert tone={result.to_cover > 0 ? 'warning' : 'success'} className="mt-4" onDismiss={() => setResult(null)}>
                {result.message}
                {result.affected.length > 0 && (
                  <ul className="mt-1 list-disc space-y-0.5 pl-5">
                    {result.affected.map((a) => (
                      <li key={a.start_period}>
                        {a.label} · {a.classes.join(', ')} · period {a.start_period} ({a.time}) — {a.status === 'covered' ? 'a co-teacher is present' : 'needs a substitute'}
                      </li>
                    ))}
                  </ul>
                )}
              </Alert>
            )}
          </Panel>

          <Panel title="Request leave" icon={CalendarRange}>
            <div className="grid gap-3 sm:grid-cols-2">
              <TextField label="From" type="date" value={from} min={todayIso()} onChange={(e) => setFrom(e.target.value)} />
              <TextField label="To (optional)" type="date" value={to} min={from} onChange={(e) => setTo(e.target.value)} hint="Leave empty for a single day" />
            </div>
            <TextField label="Reason (optional)" value={leaveReason} onChange={(e) => setLeaveReason(e.target.value)} maxLength={300} className="mt-3" />
            <div className="mt-3 flex justify-end">
              <Button icon={<Send className="size-4" />} onClick={() => void requestLeave()} loading={leaveBusy}>Send request</Button>
            </div>
          </Panel>
        </div>

        <Panel title="My absences" icon={Clock}>
          <LoadError error={absences.error} onRetry={absences.reload} className="mb-3" />
          {absences.loading && !absences.data ? (
            <Skeleton className="h-16" />
          ) : absences.data && absences.data.absences.length > 0 ? (
            <ul className="divide-y divide-line text-sm">
              {absences.data.absences.map((a) => (
                <li key={a.id} className="flex flex-wrap items-center gap-3 py-2.5 first:pt-0 last:pb-0">
                  <CircleUser className="size-4 shrink-0 text-muted" aria-hidden="true" />
                  <span className="font-medium text-ink">{formatShort(a.date)}</span>
                  {a.reason && <span className="text-muted">· {a.reason}</span>}
                  <Badge tone={a.source === 'leave' ? 'brand' : 'neutral'}>{a.source === 'leave' ? 'from leave' : a.source === 'self' ? 'self' : 'by HOD'}</Badge>
                  {a.past ? <Badge>past</Badge> : <Button variant="ghost" size="sm" icon={<Trash2 className="size-4" />} onClick={() => setUndo(a)} className="ml-auto">Undo</Button>}
                </li>
              ))}
            </ul>
          ) : absences.error ? null : (
            <p className="text-sm text-muted">You have no upcoming absences.</p>
          )}
        </Panel>

        <Panel title="My leave requests" icon={CalendarRange}>
          <LoadError error={leave.error} onRetry={leave.reload} className="mb-3" />
          {leave.loading && !leave.data ? (
            <Skeleton className="h-16" />
          ) : leave.data && leave.data.leave.length > 0 ? (
            <ul className="divide-y divide-line text-sm">
              {leave.data.leave.map((l) => (
                <li key={l.id} className="flex flex-wrap items-center gap-3 py-2.5 first:pt-0 last:pb-0">
                  <span className="font-medium text-ink">{formatRange(l.date_from, l.date_to)}</span>
                  <span className="text-muted">({l.days} {l.days === 1 ? 'day' : 'days'})</span>
                  {l.reason && <span className="text-muted">· {l.reason}</span>}
                  <Badge tone={statusTone(l.status)}>{l.status}</Badge>
                  {l.decision_note && <span className="text-[13px] text-subtle">“{l.decision_note}”</span>}
                  {l.status === 'pending' && <Button variant="ghost" size="sm" icon={<X className="size-4" />} onClick={() => void cancelLeave(l.id)} className="ml-auto">Withdraw</Button>}
                </li>
              ))}
            </ul>
          ) : leave.error ? null : (
            <EmptyState icon={Check} title="No leave requests">Request leave above and your HOD will approve or reject it.</EmptyState>
          )}
        </Panel>
      </div>

      <ConfirmDialog open={undo !== null} title="Remove this absence?" tone="danger" confirmLabel="Remove" onConfirm={doUndo} onClose={() => setUndo(null)}>
        {undo && <p>You will be back on the timetable for {formatShort(undo.date)}. Any substitute arranged will be told it is cancelled.</p>}
      </ConfirmDialog>
    </>
  )
}
