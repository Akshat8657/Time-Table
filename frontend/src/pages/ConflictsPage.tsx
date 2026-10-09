import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { CircleCheck, Info, RefreshCw, ShieldAlert, TriangleAlert } from 'lucide-react'
import { useAuth } from '../auth/AuthProvider'
import { Alert } from '../components/Alert'
import { Badge } from '../components/Badge'
import { Button } from '../components/Button'
import { EmptyState, PageHeader, Panel, Skeleton } from '../components/Panel'
import { SchoolBar } from '../components/SchoolBar'
import { SelectField } from '../components/Select'
import { ROLE_PREFIX } from '../lib/nav'
import { useSchoolScope } from '../lib/scope'
import { useTerm } from '../lib/term'
import type { ConflictItem, ConflictsPayload } from '../lib/types'
import { useApi } from '../lib/useApi'

type Kind = ConflictItem['kind']
const KIND_LABEL: Record<Kind, string> = { clash: 'Clashes', missing: 'Still to place', warning: 'Warnings' }

function periodsText(start: number, length: number | null): string {
  return !length || length === 1 ? `period ${start}` : `periods ${start}-${start + length - 1}`
}

function Icon({ kind }: { kind: Kind }) {
  if (kind === 'clash') return <TriangleAlert className="size-[18px] text-danger" aria-hidden="true" />
  if (kind === 'missing') return <Info className="size-[18px] text-info" aria-hidden="true" />
  return <TriangleAlert className="size-[18px] text-warning" aria-hidden="true" />
}

export default function ConflictsPage() {
  const { user } = useAuth()
  const scope = useSchoolScope()
  const { active, ready: termReady } = useTerm()
  const prefix = user ? ROLE_PREFIX[user.role] : ''
  const [classId, setClassId] = useState('')
  const [draftScope, setDraftScope] = useState<'primary' | 'all'>('primary')
  const [kinds, setKinds] = useState<Record<Kind, boolean>>({ clash: true, missing: true, warning: true })
  const enabled = scope.ready && !!scope.schoolId && !!active
  const path = enabled ? scope.path(`/conflicts?scope=${draftScope}${classId ? `&semester_id=${classId}` : ''}`) : null
  const { data, error, loading, reload } = useApi<ConflictsPayload>(path)

  const shown = useMemo(() => (data?.items ?? []).filter((i) => kinds[i.kind]), [data, kinds])
  const byClass = useMemo(() => {
    const map = new Map<string, ConflictItem[]>()
    for (const i of shown) map.set(i.class_label, [...(map.get(i.class_label) ?? []), i])
    // classes with a clash first, then A-Z
    return [...map.entries()].sort((a, b) => b[1].filter((i) => i.kind === 'clash').length - a[1].filter((i) => i.kind === 'clash').length || a[0].localeCompare(b[0]))
  }, [shown])

  if (termReady && !active) {
    return (
      <>
        <PageHeader title="Conflict Report" subtitle="Every clash and every lecture still missing in the timetable drafts." />
        <Alert tone="warning" title="Choose the active term first">
          The report covers the drafts of the running term. <Link to={`${prefix}/active-term`} className="font-semibold underline">Open Active Term</Link> and pick one.
        </Alert>
      </>
    )
  }

  const counts = data?.counts ?? { clashes: 0, missing: 0, warnings: 0 }
  const chip = (kind: Kind, n: number, tone: 'danger' | 'info' | 'warning') => (
    <button
      key={kind}
      type="button"
      onClick={() => setKinds((k) => ({ ...k, [kind]: !k[kind] }))}
      aria-pressed={kinds[kind]}
      className={`rounded-xl border px-4 py-2.5 text-left transition ${kinds[kind] ? 'border-line-strong bg-surface shadow-card' : 'border-line bg-surface-2 opacity-60'}`}
    >
      <span className="block text-xs font-medium text-muted">{KIND_LABEL[kind]}</span>
      <span className="mt-0.5 flex items-center gap-2">
        <span className="text-2xl font-semibold tabular-nums text-ink">{n}</span>
        <Badge tone={n === 0 ? 'success' : tone}>{n === 0 ? 'none' : kind === 'clash' ? 'fix these' : kind === 'missing' ? 'to do' : 'check'}</Badge>
      </span>
    </button>
  )

  return (
    <>
      <PageHeader
        title="Conflict Report"
        subtitle="Every clash, every lecture still missing and every warning in the timetable drafts of the active term. Click an item to jump to the exact cell in the grid."
        badge="Phase 2"
      />
      <SchoolBar>
        <Button variant="secondary" size="sm" icon={<RefreshCw className="size-4" />} onClick={reload} loading={loading && !!data}>
          Refresh
        </Button>
      </SchoolBar>

      {error ? (
        <Alert tone="danger">{error.message}</Alert>
      ) : loading && !data ? (
        <div className="space-y-4">
          <Skeleton className="h-20" />
          <Skeleton className="h-64" />
        </div>
      ) : data ? (
        <div className="space-y-5">
          <div className="flex flex-wrap items-end gap-3">
            {chip('clash', counts.clashes, 'danger')}
            {chip('missing', counts.missing, 'info')}
            {chip('warning', counts.warnings, 'warning')}
            <div className="ml-auto flex flex-wrap items-end gap-3">
              <SelectField
                label="Class"
                className="w-56"
                value={classId}
                onChange={setClassId}
                placeholder="All classes"
                options={data.classes.map((c) => ({ value: c.semester_id, label: c.label }))}
              />
              <SelectField
                label="Drafts"
                className="w-64"
                value={draftScope}
                onChange={(v) => setDraftScope(v as 'primary' | 'all')}
                options={[
                  { value: 'primary', label: 'Primary drafts (what would be published)' },
                  { value: 'all', label: 'Every draft, alternatives too' },
                ]}
              />
            </div>
          </div>

          {counts.clashes === 0 && (data.items.length > 0 || data.classes.length > 0) && (
            <Alert tone="success" title="No clash">
              <CircleCheck className="mr-1 inline size-4 align-text-bottom" aria-hidden="true" />
              No teacher, class or timing clash in {draftScope === 'primary' ? 'the primary drafts' : 'any draft'}.
            </Alert>
          )}

          {byClass.length === 0 ? (
            <Panel title="Report" icon={ShieldAlert}>
              <EmptyState icon={ShieldAlert} title="Nothing to show">
                {shown.length === 0 && data.items.length > 0 ? 'Switch a filter back on to see the items.' : 'Start a draft for a class on the Generate Timetable screen - problems appear here as you build.'}
              </EmptyState>
            </Panel>
          ) : (
            byClass.map(([label, items]) => (
              <Panel
                key={label}
                title={label}
                icon={ShieldAlert}
                action={
                  <span className="text-xs text-muted">
                    {items.filter((i) => i.kind === 'clash').length} clash · {items.filter((i) => i.kind === 'missing').length} to place · {items.filter((i) => i.kind === 'warning').length} warning
                  </span>
                }
              >
                <ul className="divide-y divide-line">
                  {items.map((i) => {
                    const to = i.draft_id ? `${prefix}/generate/${i.draft_id}${i.day ? `?day=${i.day}&period=${i.start_period}` : ''}` : `${prefix}/generate`
                    return (
                      <li key={i.id} className="flex items-start gap-3 py-3">
                        <span className="mt-0.5 shrink-0">
                          <Icon kind={i.kind} />
                        </span>
                        <div className="min-w-0 flex-1">
                          <p className={`text-sm font-medium ${i.kind === 'clash' ? 'text-danger' : 'text-ink'}`}>{i.message}</p>
                          <p className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">
                            {i.draft_name ? (
                              <>
                                <span>Draft “{i.draft_name}”</span>
                                {i.is_primary ? <Badge tone="brand">Primary</Badge> : <Badge>Alternative</Badge>}
                              </>
                            ) : (
                              <span>No draft yet</span>
                            )}
                            {i.day_name && i.start_period && (
                              <span>
                                · {i.day_name}, {periodsText(i.start_period, i.length)}
                              </span>
                            )}
                            {i.subjects && <span>· {i.subjects}</span>}
                          </p>
                        </div>
                        <Link to={to} className="shrink-0">
                          <Button variant="secondary" size="sm">
                            {i.draft_id ? 'Open in grid' : 'Start a draft'}
                          </Button>
                        </Link>
                      </li>
                    )
                  })}
                </ul>
              </Panel>
            ))
          )}
        </div>
      ) : null}
    </>
  )
}
