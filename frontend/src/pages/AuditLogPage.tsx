import { useEffect, useState } from 'react'
import { Bot, ChevronLeft, ChevronRight, Cog, FilterX, ScrollText, Search, UserRound } from 'lucide-react'
import { Alert } from '../components/Alert'
import { Badge, type BadgeTone } from '../components/Badge'
import { Button } from '../components/Button'
import { DataTable, type Column } from '../components/DataTable'
import { TextField } from '../components/Field'
import { Modal } from '../components/Modal'
import { EmptyState, PageHeader, Panel, Skeleton } from '../components/Panel'
import { SelectField } from '../components/Select'
import { ROLE_LABEL } from '../lib/nav'
import { queryString, useDebounced } from '../lib/helpers'
import type { AuditEntry, AuditFacets, AuditPage, Role } from '../lib/types'
import { useApi } from '../lib/useApi'

const ACTION_TONE: Record<string, BadgeTone> = {
  create: 'success', open: 'success', accept: 'success', approve: 'success', publish: 'success', assign: 'success',
  update: 'info', move: 'info', swap: 'info', extend: 'info', reopen: 'info', offer: 'info',
  delete: 'danger', cancel: 'danger', close: 'danger', undo: 'warning', reject: 'danger', deactivate: 'danger',
}

function stamp(iso: string): { day: string; time: string } {
  const d = new Date(iso)
  return {
    day: d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }),
    time: d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
  }
}

function roleName(role: string | null): string {
  return role && role in ROLE_LABEL ? ROLE_LABEL[role as Role] : role ?? ''
}

function Via({ via }: { via: AuditEntry['via'] }) {
  if (via === 'ai') return <Badge tone="brand"><Bot className="size-3" aria-hidden="true" /> via AI</Badge>
  if (via === 'system') return <Badge><Cog className="size-3" aria-hidden="true" /> system</Badge>
  return <span className="text-[12px] text-muted">screen</span>
}

function DetailsDialog({ entry, onClose }: { entry: AuditEntry | null; onClose: () => void }) {
  return (
    <Modal open={entry !== null} onClose={onClose} title="Audit entry" description={entry ? `#${entry.id}` : undefined}>
      {entry && (
        <dl className="space-y-3 text-sm">
          {[
            ['When', `${stamp(entry.at).day}, ${stamp(entry.at).time} (India time)`],
            ['Who', `${entry.actor.name}${entry.actor.role ? ` - ${roleName(entry.actor.role)}` : ''}${entry.actor.email ? ` (${entry.actor.email})` : ''}`],
            ['What', entry.summary],
            ['Action / item', `${entry.action} / ${entry.entity}${entry.entity_id ? ` / ${entry.entity_id}` : ''}`],
            ['School', entry.school?.name ?? '-'],
            ['Done through', entry.via === 'ai' ? 'the AI assistant (after a confirmation)' : entry.via === 'system' ? 'the system itself' : 'a normal screen'],
          ].map(([k, v]) => (
            <div key={k} className="grid gap-1 sm:grid-cols-[8rem_1fr]">
              <dt className="font-medium text-muted">{k}</dt>
              <dd className="break-words text-ink">{v}</dd>
            </div>
          ))}
          {entry.details && Object.keys(entry.details).length > 0 && (
            <div className="grid gap-1 sm:grid-cols-[8rem_1fr]">
              <dt className="font-medium text-muted">Details</dt>
              <dd>
                <pre className="max-h-64 overflow-auto rounded-lg bg-surface-2 p-3 text-[12px] leading-relaxed text-ink">{JSON.stringify(entry.details, null, 2)}</pre>
              </dd>
            </div>
          )}
        </dl>
      )}
    </Modal>
  )
}

export default function AuditLogPage() {
  const facets = useApi<AuditFacets>('/audit/facets')
  const [person, setPerson] = useState('')
  const [action, setAction] = useState('')
  const [entity, setEntity] = useState('')
  const [via, setVia] = useState('')
  const [school, setSchool] = useState('')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [text, setText] = useState('')
  const [page, setPage] = useState(1)
  const [size, setSize] = useState('50')
  const [open, setOpen] = useState<AuditEntry | null>(null)
  const q = useDebounced(text, 350)

  useEffect(() => setPage(1), [person, action, entity, via, school, from, to, q, size])   // a new filter starts again at page 1

  const path = `/audit${queryString({ actor_id: person, action, entity, via, school_id: school, date_from: from, date_to: to, q: q.trim(), page, page_size: size })}`
  const { data, error, loading, reload } = useApi<AuditPage>(path)
  const f = facets.data
  const filtered = !!(person || action || entity || via || school || from || to || text)
  const clear = () => {
    setPerson(''); setAction(''); setEntity(''); setVia(''); setSchool(''); setFrom(''); setTo(''); setText('')
  }

  const columns: Column<AuditEntry>[] = [
    {
      key: 'when',
      header: 'When',
      className: 'whitespace-nowrap',
      cell: (e) => {
        const t = stamp(e.at)
        return <div><p className="text-sm text-ink">{t.day}</p><p className="text-[12px] tabular-nums text-muted">{t.time}</p></div>
      },
    },
    {
      key: 'who',
      header: 'Who',
      cell: (e) => (
        <div className="min-w-0">
          <p className="flex items-center gap-1.5 text-sm font-medium text-ink"><UserRound className="size-3.5 shrink-0 text-subtle" aria-hidden="true" /><span className="truncate">{e.actor.name}</span></p>
          <p className="text-[12px] text-muted">{roleName(e.actor.role)}{e.school ? ` · ${e.school.name}` : ''}</p>
        </div>
      ),
    },
    { key: 'action', header: 'Action', cell: (e) => <Badge tone={ACTION_TONE[e.action] ?? 'neutral'}>{e.action}</Badge> },
    {
      key: 'what',
      header: 'What happened',
      cell: (e) => (
        <div className="min-w-0">
          <p className="text-sm text-ink">{e.summary}</p>
          <p className="text-[12px] text-muted">{e.entity.replace(/_/g, ' ')}</p>
        </div>
      ),
    },
    { key: 'via', header: 'Through', hideBelow: 'md', cell: (e) => <Via via={e.via} /> },
    {
      key: 'more',
      header: <span className="sr-only">Details</span>,
      className: 'text-right',
      cell: (e) => <Button size="sm" variant="ghost" onClick={() => setOpen(e)} data-testid="audit-details">Details</Button>,
    },
  ]

  return (
    <>
      <PageHeader
        title="Audit Log"
        subtitle="A record of every important change in TimeTablePro - who did it, what they did and when. Entries cannot be edited or removed. Only the administrator can read this page."
        badge="Phase 6"
      />
      <Panel title="Find entries" icon={Search}
        action={filtered ? <Button size="sm" variant="ghost" icon={<FilterX className="size-4" />} onClick={clear} data-testid="audit-clear">Clear filters</Button> : null}
      >
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <SelectField label="Person" value={person} onChange={setPerson} placeholder="Anyone" options={(f?.people ?? []).map((p) => ({ value: p.id, label: `${p.name}${p.role ? ` (${roleName(p.role)})` : ''} - ${p.count}` }))} data-testid="audit-person" />
          <SelectField label="Action" value={action} onChange={setAction} placeholder="Any action" options={(f?.actions ?? []).map((a) => ({ value: a.value, label: `${a.value} (${a.count})` }))} data-testid="audit-action" />
          <SelectField label="Item" value={entity} onChange={setEntity} placeholder="Anything" options={(f?.entities ?? []).map((a) => ({ value: a.value, label: `${a.value.replace(/_/g, ' ')} (${a.count})` }))} />
          <SelectField label="School" value={school} onChange={setSchool} placeholder="All schools" options={(f?.schools ?? []).map((s) => ({ value: s.id, label: s.name }))} />
          <TextField label="From" type="date" value={from} max={to || undefined} onChange={(e) => setFrom(e.target.value)} data-testid="audit-from" />
          <TextField label="To" type="date" value={to} min={from || undefined} onChange={(e) => setTo(e.target.value)} data-testid="audit-to" />
          <SelectField label="Done through" value={via} onChange={setVia} placeholder="Any" options={[{ value: 'ui', label: 'A normal screen' }, { value: 'ai', label: 'The AI assistant' }, { value: 'system', label: 'The system' }]} />
          <TextField label="Search the text" value={text} onChange={(e) => setText(e.target.value)} placeholder="e.g. published, Dr. Das" icon={<Search className="size-4" />} data-testid="audit-search" />
        </div>
      </Panel>

      <div className="mt-5">
        {error ? (
          <Alert tone="danger">
            {error.message}{' '}
            <button type="button" onClick={reload} className="font-semibold underline">Try again</button>
          </Alert>
        ) : !data ? (
          <div className="space-y-3" aria-busy="true"><Skeleton className="h-12" /><Skeleton className="h-64" /></div>
        ) : (
          <Panel
            title={`${data.total.toLocaleString('en-GB')} ${data.total === 1 ? 'entry' : 'entries'}${filtered ? ' match' : ''}`}
            icon={ScrollText}
            action={f?.first ? <span className="hidden text-[12px] text-muted sm:inline">since {stamp(f.first).day}</span> : null}
          >
            <div className={loading ? 'opacity-60 transition-opacity' : ''} data-testid="audit-table">
              <DataTable
                caption="Audit log"
                columns={columns}
                rows={data.entries}
                rowKey={(e) => String(e.id)}
                empty={<EmptyState icon={ScrollText} title="Nothing found">{filtered ? 'No entry matches these filters. Try a wider date range or clear a filter.' : 'Nothing has been recorded yet.'}</EmptyState>}
                card={(e) => (
                  <button type="button" onClick={() => setOpen(e)} className="block w-full text-left">
                    <div className="flex items-center justify-between gap-2">
                      <Badge tone={ACTION_TONE[e.action] ?? 'neutral'}>{e.action}</Badge>
                      <span className="text-[12px] text-muted">{stamp(e.at).day}, {stamp(e.at).time}</span>
                    </div>
                    <p className="mt-1.5 text-sm text-ink">{e.summary}</p>
                    <p className="mt-0.5 text-[12px] text-muted">{e.actor.name}{e.via === 'ai' ? ' · via AI' : ''}</p>
                  </button>
                )}
              />
            </div>
            <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-line pt-4">
              <p className="text-[13px] text-muted" data-testid="audit-pager">Page <span className="font-semibold tabular-nums text-ink">{data.page}</span> of <span className="tabular-nums">{data.pages.toLocaleString('en-GB')}</span></p>
              <div className="flex items-center gap-2">
                <SelectField compact aria-label="Entries per page" className="w-32" value={size} onChange={setSize} options={['25', '50', '100'].map((v) => ({ value: v, label: `${v} per page` }))} />
                <Button size="sm" variant="secondary" icon={<ChevronLeft className="size-4" />} disabled={page <= 1 || loading} onClick={() => setPage((p) => Math.max(1, p - 1))} data-testid="audit-prev">Newer</Button>
                <Button size="sm" variant="secondary" disabled={page >= data.pages || loading} onClick={() => setPage((p) => p + 1)} data-testid="audit-next">Older <ChevronRight className="size-4" aria-hidden="true" /></Button>
              </div>
            </div>
          </Panel>
        )}
      </div>
      <DetailsDialog entry={open} onClose={() => setOpen(null)} />
    </>
  )
}
