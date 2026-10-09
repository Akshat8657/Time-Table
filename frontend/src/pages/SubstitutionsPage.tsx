import { useState } from 'react'
import { Link } from 'react-router-dom'
import {
  CalendarClock, CalendarDays, Check, ChevronLeft, ChevronRight, FileText, Inbox, Repeat, Send, UserX, X,
} from 'lucide-react'
import { useAuth } from '../auth/AuthProvider'
import { Alert } from '../components/Alert'
import { AssignDialog } from '../components/AssignDialog'
import { Badge } from '../components/Badge'
import { Button } from '../components/Button'
import { DayChanges } from '../components/DayChanges'
import { TextField } from '../components/Field'
import { EmptyState, PageHeader, Panel, Skeleton } from '../components/Panel'
import { SchoolBar } from '../components/SchoolBar'
import { SelectField } from '../components/Select'
import { Tabs } from '../components/Tabs'
import { useToast } from '../components/Toast'
import { api, ApiError, apiBlob } from '../lib/api'
import { addDaysIso, downloadBlob, formatShort, todayIso } from '../lib/helpers'
import { ROLE_PREFIX } from '../lib/nav'
import { useSchoolScope } from '../lib/scope'
import { useTerm } from '../lib/term'
import type { AbsenceRow, LeaveRequest, MonthlySummary, Substitution, ViewOptions } from '../lib/types'
import { useApi } from '../lib/useApi'

type Tab = 'today' | 'leave' | 'adjust' | 'summary'

function statusBadge(s: Substitution['status']) {
  const map = { open: ['warning', 'needs a substitute'], offering: ['info', 'offer sent'], filled: ['success', 'filled'], unfilled: ['danger', 'not covered'], cancelled: ['neutral', 'cancelled'] } as const
  const [tone, text] = map[s]
  return <Badge tone={tone}>{text}</Badge>
}

export default function SubstitutionsPage() {
  const { user } = useAuth()
  const toast = useToast()
  const scope = useSchoolScope()
  const { active, ready: termReady } = useTerm()
  const prefix = user ? ROLE_PREFIX[user.role] : ''
  const [tab, setTab] = useState<Tab>('today')
  const [date, setDate] = useState(todayIso())
  const enabled = scope.ready && !!scope.schoolId && !!active
  const options = useApi<ViewOptions>(enabled ? scope.path('/views/options') : null)
  const subs = useApi<{ date: string; substitutions: Substitution[]; summary: Record<string, number> }>(enabled && tab === 'today' ? scope.path(`/subs?date=${date}`) : null)
  const absences = useApi<{ absences: AbsenceRow[] }>(enabled && tab === 'today' ? scope.path(`/absence?date=${date}`) : null)
  const [assignSub, setAssignSub] = useState<Substitution | null>(null)
  const [markOpen, setMarkOpen] = useState(false)
  const [markTeacher, setMarkTeacher] = useState('')
  const [markReason, setMarkReason] = useState('')

  if (termReady && !active) {
    return (
      <>
        <PageHeader title="Substitutions" subtitle="Absences, substitution offers and one-day changes." />
        <Alert tone="warning" title="Choose the active term first">
          <Link to={`${prefix}/active-term`} className="font-semibold underline">Open Active Term</Link> and pick one.
        </Alert>
      </>
    )
  }

  function refreshToday() {
    subs.reload()
    absences.reload()
  }

  async function markAbsent() {
    if (!markTeacher) return
    try {
      const r = await api<{ message: string }>('/absence', { method: 'POST', body: { ...scope.body(), teacher_id: markTeacher, date, reason: markReason.trim() || null } })
      toast.success(r.message)
      setMarkOpen(false)
      setMarkReason('')
      refreshToday()
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Could not mark absent.')
    }
  }

  async function cancelSub(id: string) {
    await api(`/subs/${id}/cancel`, { method: 'POST' })
    toast.success('Substitution cancelled.')
    refreshToday()
  }

  async function downloadSheet() {
    try {
      const blob = await apiBlob(scope.path(`/subs/sheet?date=${date}`))
      downloadBlob(blob, `Substitution_Sheet_${date}.pdf`)
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Could not make the sheet.')
    }
  }

  const need = (subs.data?.summary?.need ?? 0)

  return (
    <>
      <PageHeader
        title="Substitutions"
        subtitle="Mark teachers absent, find and assign substitutes (manually or with auto popup-offers), approve leave, and move / cancel / swap a lecture for one day."
        badge="Phase 5"
      />
      <SchoolBar />
      <div className="mb-5">
        <Tabs
          label="Section"
          value={tab}
          onChange={setTab}
          items={[
            { value: 'today', label: 'Absences & subs', icon: <UserX className="size-4" /> },
            { value: 'leave', label: 'Leave requests', icon: <Inbox className="size-4" /> },
            { value: 'adjust', label: 'Day changes', icon: <CalendarClock className="size-4" /> },
            { value: 'summary', label: 'Summary', icon: <CalendarDays className="size-4" /> },
          ]}
        />
      </div>

      {tab === 'today' && (
        <div className="space-y-5">
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-2" role="group" aria-label="Choose the date">
              <Button variant="secondary" size="sm" aria-label="Previous day" icon={<ChevronLeft className="size-4" />} onClick={() => setDate(addDaysIso(date, -1))} />
              <span className="min-w-[9rem] text-center text-sm font-medium tabular-nums text-ink">{formatShort(date)}</span>
              <Button variant="secondary" size="sm" aria-label="Next day" icon={<ChevronRight className="size-4" />} onClick={() => setDate(addDaysIso(date, 1))} />
              <Button variant="ghost" size="sm" onClick={() => setDate(todayIso())} disabled={date === todayIso()}>Today</Button>
            </div>
            <Button icon={<UserX className="size-4" />} onClick={() => setMarkOpen((o) => !o)}>Mark someone absent</Button>
            <Button variant="secondary" icon={<FileText className="size-4" />} onClick={() => void downloadSheet()} className="ml-auto">Sheet (PDF)</Button>
          </div>

          {markOpen && (
            <Panel title="Mark a teacher absent" icon={UserX}>
              <div className="flex flex-wrap items-end gap-3">
                <SelectField label="Teacher" className="w-64" value={markTeacher} onChange={setMarkTeacher} placeholder="Choose a teacher" options={(options.data?.teachers ?? []).map((t) => ({ value: t.id, label: t.name }))} />
                <TextField label="Reason (optional)" value={markReason} onChange={(e) => setMarkReason(e.target.value)} maxLength={300} className="min-w-[12rem] flex-1" />
                <Button onClick={() => void markAbsent()} disabled={!markTeacher}>Mark absent on {formatShort(date)}</Button>
              </div>
            </Panel>
          )}

          {need > 0 && (
            <Alert tone="warning" title={`${need} lecture(s) still need a substitute on ${formatShort(date)}`}>
              Assign a teacher or send auto offers below.
            </Alert>
          )}

          <Panel title={`Absent on ${formatShort(date)}`} icon={UserX}>
            {absences.loading && !absences.data ? (
              <Skeleton className="h-16" />
            ) : absences.data && absences.data.absences.length > 0 ? (
              <ul className="divide-y divide-line text-sm">
                {absences.data.absences.map((a) => (
                  <li key={a.id} className="flex flex-wrap items-center gap-2 py-2 first:pt-0 last:pb-0">
                    <span className="font-medium text-ink">{a.teacher_name}</span>
                    {a.reason && <span className="text-muted">· {a.reason}</span>}
                    <Badge tone="neutral">{a.source === 'leave' ? 'leave' : a.source === 'self' ? 'self' : 'by HOD'}</Badge>
                    <span className="ml-auto text-[13px] text-muted">
                      {a.filled} covered{a.open + a.offering + a.unfilled > 0 ? ` · ${a.open + a.offering + a.unfilled} to arrange` : ''}
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted">No teacher is absent on this day.</p>
            )}
          </Panel>

          <Panel title="Lectures to cover" icon={Repeat}>
            {subs.loading && !subs.data ? (
              <Skeleton className="h-24" />
            ) : subs.data && subs.data.substitutions.length > 0 ? (
              <ul className="space-y-2.5" data-testid="sub-list">
                {subs.data.substitutions.map((s) => (
                  <li key={s.id} className="flex flex-wrap items-center gap-3 rounded-xl border border-line bg-surface p-3" data-status={s.status}>
                    <span className="w-16 shrink-0 text-[13px] font-semibold tabular-nums text-ink">P{s.start_period}{s.length > 1 ? `-${s.start_period + s.length - 1}` : ''}</span>
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-semibold text-ink">{s.label}</p>
                      <p className="text-[12px] text-muted">{s.classes.join(', ')} · {s.time} · absent: {s.absent_teacher_name}{s.substitute_name ? ` · covered by ${s.substitute_name}` : ''}</p>
                    </div>
                    {statusBadge(s.status)}
                    {s.status !== 'filled' && s.status !== 'cancelled' && (
                      <div className="flex shrink-0 items-center gap-1.5">
                        <Button size="sm" variant="secondary" icon={<Send className="size-4" />} onClick={() => setAssignSub(s)} data-testid="find-sub">Find a substitute</Button>
                        <Button size="sm" variant="ghost" icon={<X className="size-4" />} onClick={() => void cancelSub(s.id)}>Cancel</Button>
                      </div>
                    )}
                    {s.status === 'filled' && <Button size="sm" variant="ghost" onClick={() => void cancelSub(s.id)}>Undo</Button>}
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyState icon={Check} title="Nothing to cover">Mark a teacher absent to see the lectures that need a substitute.</EmptyState>
            )}
          </Panel>
        </div>
      )}

      {tab === 'leave' && <LeaveInbox scope={scope} />}
      {tab === 'adjust' && <DayChanges classes={options.data?.classes ?? []} />}
      {tab === 'summary' && <SummaryTab scope={scope} downloadSheet={downloadSheet} />}

      <AssignDialog sub={assignSub} onClose={() => setAssignSub(null)} onDone={refreshToday} />
    </>
  )
}

function LeaveInbox({ scope }: { scope: ReturnType<typeof useSchoolScope> }) {
  const toast = useToast()
  const { data, loading, reload } = useApi<{ leave: LeaveRequest[] }>(scope.schoolId ? scope.path('/leave?scope=school') : null)
  const [note, setNote] = useState<Record<string, string>>({})

  async function decide(id: string, decision: 'approve' | 'reject') {
    try {
      const r = await api<{ substitutions_prepared?: number }>(`/leave/${id}/decide`, { method: 'POST', body: { decision, note: note[id]?.trim() || null } })
      toast.success(decision === 'approve' ? `Approved.${r.substitutions_prepared ? ` ${r.substitutions_prepared} substitution(s) prepared.` : ''}` : 'Rejected.')
      reload()
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Could not decide.')
    }
  }

  const pending = (data?.leave ?? []).filter((l) => l.status === 'pending')
  const decided = (data?.leave ?? []).filter((l) => l.status !== 'pending')

  return (
    <div className="space-y-5">
      <Panel title={`Pending leave${pending.length ? ` (${pending.length})` : ''}`} icon={Inbox}>
        {loading && !data ? (
          <Skeleton className="h-20" />
        ) : pending.length === 0 ? (
          <EmptyState icon={Check} title="No pending leave">Leave requests from your teachers appear here.</EmptyState>
        ) : (
          <ul className="space-y-3" data-testid="leave-pending">
            {pending.map((l) => (
              <li key={l.id} className="rounded-xl border border-line bg-surface p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-[15px] font-semibold text-ink">{l.teacher_name}</span>
                  <span className="text-muted">· {formatShort(l.date_from)}{l.date_from !== l.date_to ? ` to ${formatShort(l.date_to)}` : ''} ({l.days} {l.days === 1 ? 'day' : 'days'})</span>
                  {l.reason && <Badge>{l.reason}</Badge>}
                </div>
                <div className="mt-3 flex flex-wrap items-end gap-2">
                  <TextField label="Note (optional)" value={note[l.id] ?? ''} onChange={(e) => setNote((n) => ({ ...n, [l.id]: e.target.value }))} className="min-w-[14rem] flex-1" />
                  <Button size="sm" icon={<Check className="size-4" />} onClick={() => void decide(l.id, 'approve')}>Approve</Button>
                  <Button size="sm" variant="secondary" icon={<X className="size-4" />} onClick={() => void decide(l.id, 'reject')}>Reject</Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Panel>
      {decided.length > 0 && (
        <Panel title="Recent decisions" icon={CalendarDays}>
          <ul className="divide-y divide-line text-sm">
            {decided.slice(0, 20).map((l) => (
              <li key={l.id} className="flex flex-wrap items-center gap-2 py-2 first:pt-0 last:pb-0">
                <span className="font-medium text-ink">{l.teacher_name}</span>
                <span className="text-muted">· {formatShort(l.date_from)}{l.date_from !== l.date_to ? `–${formatShort(l.date_to)}` : ''}</span>
                <Badge tone={l.status === 'approved' ? 'success' : l.status === 'rejected' ? 'danger' : 'neutral'}>{l.status}</Badge>
                {l.decided_by_name && <span className="text-[13px] text-subtle">by {l.decided_by_name}</span>}
              </li>
            ))}
          </ul>
        </Panel>
      )}
    </div>
  )
}

function SummaryTab({ scope, downloadSheet }: { scope: ReturnType<typeof useSchoolScope>; downloadSheet: () => void }) {
  const [month, setMonth] = useState(todayIso().slice(0, 7))
  const { data, loading } = useApi<MonthlySummary>(scope.schoolId ? scope.path(`/subs/summary?month=${month}`) : null)
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end gap-3">
        <TextField label="Month" type="month" value={month} onChange={(e) => setMonth(e.target.value)} className="w-44" />
        <Button variant="secondary" icon={<FileText className="size-4" />} onClick={downloadSheet}>Today's sheet (PDF)</Button>
      </div>
      {loading && !data ? (
        <Skeleton className="h-64" />
      ) : data ? (
        <>
          <div className="flex flex-wrap gap-3">
            {([['Absences', data.totals.absences, 'neutral'], ['Substitutions filled', data.totals.filled, 'success'], ['Still open', data.totals.unfilled, data.totals.unfilled > 0 ? 'warning' : 'neutral']] as const).map(([label, n, tone]) => (
              <div key={label} className="rounded-xl border border-line bg-surface px-4 py-2.5 shadow-card">
                <span className="block text-xs font-medium text-muted">{label}</span>
                <span className="mt-0.5 flex items-center gap-2"><span className="text-2xl font-semibold tabular-nums text-ink">{n}</span><Badge tone={tone}>{data.month_label}</Badge></span>
              </div>
            ))}
          </div>
          <Panel title={`Per teacher - ${data.month_label}`} icon={CalendarDays}>
            {data.teachers.every((t) => !t.absences && !t.covered) ? (
              <p className="text-sm text-muted">No absences or substitutions this month.</p>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-muted">
                    <th className="py-2 font-semibold">Teacher</th><th className="py-2 text-center font-semibold">Absences</th>
                    <th className="py-2 text-center font-semibold">Lectures covered</th><th className="py-2 text-center font-semibold">Needed covering</th>
                  </tr>
                </thead>
                <tbody>
                  {data.teachers.filter((t) => t.absences || t.covered || t.needed_cover).map((t) => (
                    <tr key={t.teacher_id} className="border-b border-line last:border-0">
                      <td className="py-2 font-medium text-ink">{t.name}</td>
                      <td className="py-2 text-center tabular-nums">{t.absences || '–'}</td>
                      <td className="py-2 text-center tabular-nums">{t.covered || '–'}</td>
                      <td className="py-2 text-center tabular-nums">{t.needed_cover || '–'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Panel>
        </>
      ) : null}
    </div>
  )
}
