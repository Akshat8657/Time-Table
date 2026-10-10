import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ChevronDown, CircleCheck, FilePen, History, Send, ShieldAlert, TriangleAlert } from 'lucide-react'
import { useAuth } from '../auth/AuthProvider'
import { Alert } from '../components/Alert'
import { Badge } from '../components/Badge'
import { Button } from '../components/Button'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { EmptyState, PageHeader, Panel, Skeleton } from '../components/Panel'
import { SchoolBar } from '../components/SchoolBar'
import { api } from '../lib/api'
import { formatStamp } from '../lib/helpers'
import { ROLE_PREFIX } from '../lib/nav'
import { useSchoolScope } from '../lib/scope'
import { useTerm } from '../lib/term'
import type { PublishClass, PublishEvent, PublishResult, PublishStatus } from '../lib/types'
import { useApi } from '../lib/useApi'

function StateBadge({ c }: { c: PublishClass }) {
  switch (c.state) {
    case 'in_sync':
      return <Badge tone="success"><CircleCheck className="size-3" aria-hidden="true" /> Published · up to date</Badge>
    case 'changed':
      return <Badge tone="warning"><TriangleAlert className="size-3" aria-hidden="true" /> Unpublished changes</Badge>
    case 'not_published':
      return <Badge tone="info">Not published yet</Badge>
    case 'draft_removed':
      return <Badge tone="warning">Primary draft missing</Badge>
    default:
      return <Badge>No primary draft</Badge>
  }
}

function ClassCard({ c, busy, onPublish, editPath }: { c: PublishClass; busy: boolean; onPublish: () => void; editPath: string | null }) {
  const [open, setOpen] = useState(false)
  const changes = c.changes
  const total = changes ? changes.added_count + changes.removed_count : 0
  return (
    <li className="rounded-xl border border-line bg-surface p-4" data-testid="publish-class" data-state={c.state}>
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <p className="flex flex-wrap items-center gap-2 text-[15px] font-semibold text-ink">
            {c.class_label} <StateBadge c={c} />
          </p>
          <p className="mt-1 text-[13px] text-muted">
            {c.primary ? <>Primary draft: <span className="font-medium text-ink">{c.primary.name}</span></> : 'Make a draft and mark it as primary (star) on the Generate Timetable screen.'}
            {c.published && (
              <> · live version <span className="font-medium text-ink">{c.published.version}</span> published {formatStamp(c.published.published_at)}{c.published.published_by ? ` by ${c.published.published_by}` : ''}</>
            )}
          </p>
          {c.completeness && (
            <p className="mt-1 text-[13px] text-muted">
              Draft is {c.completeness.percent}% complete
              {c.completeness.missing > 0 ? ` - ${c.completeness.missing} lecture${c.completeness.missing === 1 ? '' : 's'} still to place (teachers will see what is placed)` : ''}.
            </p>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {editPath && (
            <Link to={editPath} className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-line-strong bg-surface px-3 text-[13px] font-medium text-ink hover:bg-hover">
              <FilePen className="size-4" aria-hidden="true" /> Open draft
            </Link>
          )}
          <Button size="sm" icon={<Send className="size-4" />} onClick={onPublish} disabled={!c.can_publish || busy} data-testid="publish-one">
            {c.state === 'changed' ? 'Publish changes' : 'Publish'}
          </Button>
        </div>
      </div>

      {c.blockers.length > 0 && (
        <Alert tone="danger" className="mt-3" title={`${c.blocker_count} problem${c.blocker_count === 1 ? '' : 's'} stop this class from being published`}>
          <ul className="mt-1 list-disc space-y-0.5 pl-5">
            {c.blockers.map((b) => <li key={b}>{b}</li>)}
          </ul>
          {c.blocker_count > c.blockers.length && <p className="mt-1">…and {c.blocker_count - c.blockers.length} more. The Conflict Report lists them all.</p>}
        </Alert>
      )}

      {changes && total > 0 && (
        <div className="mt-3">
          <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} className="inline-flex items-center gap-1.5 text-[13px] font-medium text-brand-text hover:underline">
            <ChevronDown className={`size-4 transition-transform ${open ? 'rotate-180' : ''}`} aria-hidden="true" />
            {c.state === 'not_published' ? `${changes.added_count} lectures will go live` : `What changed: ${changes.added_count} added / changed, ${changes.removed_count} removed or moved away`}
          </button>
          {open && (
            <div className="mt-2 grid gap-3 text-[13px] sm:grid-cols-2">
              <div>
                <p className="mb-1 font-semibold text-success">New or changed</p>
                <ul className="space-y-0.5 text-muted">{changes.added.map((t) => <li key={t}>+ {t}</li>)}</ul>
                {changes.added_count > changes.added.length && <p className="text-subtle">…and {changes.added_count - changes.added.length} more</p>}
              </div>
              {changes.removed_count > 0 && (
                <div>
                  <p className="mb-1 font-semibold text-danger">Removed or moved away</p>
                  <ul className="space-y-0.5 text-muted">{changes.removed.map((t) => <li key={t}>− {t}</li>)}</ul>
                  {changes.removed_count > changes.removed.length && <p className="text-subtle">…and {changes.removed_count - changes.removed.length} more</p>}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </li>
  )
}

export default function PublishPage() {
  const { user } = useAuth()
  const scope = useSchoolScope()
  const { active, ready: termReady } = useTerm()
  const prefix = user ? ROLE_PREFIX[user.role] : ''
  const enabled = scope.ready && !!scope.schoolId && !!active
  const status = useApi<PublishStatus>(enabled ? scope.path('/publish/status') : null)
  const events = useApi<{ events: PublishEvent[] }>(enabled ? scope.path('/publish/events') : null)
  const [confirm, setConfirm] = useState<{ kind: 'all' } | { kind: 'class'; c: PublishClass } | null>(null)
  const [result, setResult] = useState<PublishResult | null>(null)

  async function doPublish() {
    if (!confirm) return
    const body = confirm.kind === 'all' ? { ...scope.body(), scope: 'all' } : { ...scope.body(), scope: 'class', semester_id: confirm.c.semester_id }
    const res = await api<PublishResult>('/publish', { method: 'POST', body })
    setResult(res)
    status.reload()
    events.reload()
  }

  if (termReady && !active) {
    return (
      <>
        <PageHeader title="Publish Timetable" subtitle="Make the primary drafts live for the teachers." />
        <Alert tone="warning" title="Choose the active term first">
          Publishing works on the running term. <Link to={`${prefix}/active-term`} className="font-semibold underline">Open Active Term</Link> and pick one.
        </Alert>
      </>
    )
  }

  const classes = status.data?.classes ?? []
  const summary = status.data?.summary ?? {}
  const toPublish = classes.filter((c) => c.can_publish)
  const chip = (label: string, n: number | undefined, tone: 'success' | 'warning' | 'info' | 'neutral') => (
    <div className="rounded-xl border border-line bg-surface px-4 py-2.5 shadow-card">
      <span className="block text-xs font-medium text-muted">{label}</span>
      <span className="mt-0.5 flex items-center gap-2">
        <span className="text-2xl font-semibold tabular-nums text-ink">{n ?? 0}</span>
        <Badge tone={tone}>{label.includes('Unpublished') ? ((n ?? 0) > 0 ? 'publish these' : 'none') : (n ?? 0) > 0 ? 'classes' : 'none'}</Badge>
      </span>
    </div>
  )

  return (
    <>
      <PageHeader
        title="Publish Timetable"
        subtitle="Publishing copies the PRIMARY draft of each class into the live timetable that teachers see. Teachers never see drafts. Publish again after any change - only the teachers whose own lectures changed are notified, once."
      />
      <SchoolBar>
        <Button
          icon={<Send className="size-4" />}
          onClick={() => setConfirm({ kind: 'all' })}
          disabled={toPublish.length === 0}
          data-testid="publish-all"
        >
          Publish all changes{toPublish.length > 0 ? ` (${toPublish.length})` : ''}
        </Button>
      </SchoolBar>

      {result && (
        <Alert tone="success" className="mb-5" title={result.message} onDismiss={() => setResult(null)}>
          <ul className="mt-1 list-disc space-y-0.5 pl-5">
            {result.published.map((p) => (
              <li key={p.semester_id}>
                {p.class_label}: version {p.version} - {p.lectures} lectures{p.first ? ' (first time live)' : `, ${p.added} added / changed, ${p.removed} removed`}
              </li>
            ))}
          </ul>
          {result.skipped.length > 0 && (
            <p className="mt-2">Not published: {result.skipped.map((s) => `${s.class_label} (${s.reasons[0]})`).join('; ')}</p>
          )}
        </Alert>
      )}

      {status.error ? (
        <Alert tone="danger">{status.error.message}</Alert>
      ) : status.loading && !status.data ? (
        <div className="space-y-3">
          <Skeleton className="h-20" />
          <Skeleton className="h-40" />
        </div>
      ) : status.data ? (
        <div className="space-y-6">
          <div className="flex flex-wrap items-stretch gap-3">
            {chip('Published · up to date', summary.in_sync, 'success')}
            {chip('Unpublished changes', (summary.changed ?? 0) + (summary.not_published ?? 0), 'warning')}
            {chip('No primary draft', summary.no_draft, 'neutral')}
          </div>

          {classes.length === 0 ? (
            <EmptyState icon={ShieldAlert} title="No classes run in this term">Add courses and semesters in College Setup first.</EmptyState>
          ) : (
            <ul className="space-y-3" data-testid="publish-list">
              {classes.map((c) => (
                <ClassCard
                  key={c.semester_id}
                  c={c}
                  busy={false}
                  onPublish={() => setConfirm({ kind: 'class', c })}
                  editPath={c.primary ? `${prefix}/generate/${c.primary.id}` : null}
                />
              ))}
            </ul>
          )}

          <Panel title="Publish history" icon={History}>
            {events.data && events.data.events.length > 0 ? (
              <ul className="divide-y divide-line text-sm">
                {events.data.events.map((e) => (
                  <li key={e.id} className="py-2.5 first:pt-0 last:pb-0">
                    <p className="font-medium text-ink">
                      Publish #{e.number} · {formatStamp(e.published_at)}{e.published_by ? ` · ${e.published_by}` : ''}
                    </p>
                    <p className="text-[13px] text-muted">
                      {e.classes.map((c) => `${c.class_label} v${c.version}`).join(', ')} · {e.notified} {e.notified === 1 ? 'teacher' : 'teachers'} notified
                    </p>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted">Nothing has been published yet in this term.</p>
            )}
          </Panel>
        </div>
      ) : null}

      <ConfirmDialog
        open={confirm !== null}
        title={confirm?.kind === 'all' ? `Publish ${toPublish.length} ${toPublish.length === 1 ? 'class' : 'classes'}?` : confirm ? `Publish ${confirm.c.class_label}?` : ''}
        confirmLabel={confirm?.kind === 'class' && confirm.c.state === 'changed' ? 'Publish changes' : 'Publish'}
        onConfirm={doPublish}
        onClose={() => setConfirm(null)}
      >
        <p>
          The primary draft{confirm?.kind === 'all' && toPublish.length !== 1 ? 's' : ''} will become the live timetable that teachers see
          {confirm?.kind === 'all' ? '' : ''}. The version number goes up and the audit log records it.
        </p>
        <p className="mt-2">
          Only teachers whose <span className="font-semibold">own lectures</span> changed get a notification (one each, in the bell and as a browser alert).
        </p>
        {confirm?.kind === 'all' && (
          <ul className="mt-2 list-disc space-y-0.5 pl-5 text-[13px]">
            {toPublish.map((c) => <li key={c.semester_id}>{c.class_label}{c.state === 'changed' ? ' (update)' : ' (first time)'}</li>)}
          </ul>
        )}
        {confirm?.kind === 'class' && confirm.c.completeness && confirm.c.completeness.missing > 0 && (
          <p className="mt-2 text-warning">This draft still has {confirm.c.completeness.missing} lecture(s) to place. Teachers will see what is placed so far.</p>
        )}
      </ConfirmDialog>
    </>
  )
}
