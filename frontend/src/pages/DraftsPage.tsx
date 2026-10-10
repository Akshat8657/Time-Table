import { useEffect, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Copy, LayoutGrid, Pencil, Plus, ShieldAlert, Star, Trash2, TriangleAlert } from 'lucide-react'
import { GenerationPanel } from '../components/GenerationPanel'
import { useAuth } from '../auth/AuthProvider'
import { Alert } from '../components/Alert'
import { Badge } from '../components/Badge'
import { Button } from '../components/Button'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { TextField } from '../components/Field'
import { Modal } from '../components/Modal'
import { EmptyState, PageHeader, Panel, Skeleton } from '../components/Panel'
import { SchoolBar } from '../components/SchoolBar'
import type { PublishStatus } from '../lib/types'
import { SelectField } from '../components/Select'
import { useToast } from '../components/Toast'
import { api, ApiError } from '../lib/api'
import { ROLE_PREFIX } from '../lib/nav'
import { useSchoolScope } from '../lib/scope'
import { useTerm } from '../lib/term'
import type { DraftClassRow, DraftsPayload, DraftSummary } from '../lib/types'
import { useApi } from '../lib/useApi'

function ago(iso: string): string {
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours} h ago`
  return `${Math.round(hours / 24)} d ago`
}

function Progress({ percent, tone }: { percent: number; tone: string }) {
  return (
    <div className="h-2 w-full overflow-hidden rounded-full bg-surface-2 ring-1 ring-inset ring-line" role="progressbar" aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100}>
      <div className={`h-full rounded-full transition-all ${tone}`} style={{ width: `${percent}%` }} />
    </div>
  )
}

// ---------------------------------------------------------------------- new draft (empty or copied)
function NewDraftDialog({
  row, copyFrom, onClose, onSaved,
}: {
  row: DraftClassRow | null
  copyFrom: DraftSummary | null
  onClose: () => void
  onSaved: (id: string) => void
}) {
  const [name, setName] = useState('')
  const [source, setSource] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!row) return
    setError(null)
    setSource(copyFrom?.id ?? '')
    setName(copyFrom ? `${copyFrom.name} (copy)` : row.drafts.length === 0 ? 'Plan A' : `Plan ${String.fromCharCode(65 + Math.min(row.drafts.length, 25))}`)
  }, [row, copyFrom])

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!row) return
    setBusy(true)
    setError(null)
    try {
      const res = await api<{ id: string }>('/drafts', { method: 'POST', body: { semester_id: row.semester_id, name, copy_from: source || null } })
      onSaved(res.id)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not create the draft. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      open={row !== null}
      onClose={() => !busy && onClose()}
      title="New timetable draft"
      description={row?.label}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button type="submit" form="draft-form" loading={busy} disabled={!name.trim()}>
            Create draft
          </Button>
        </>
      }
    >
      <form id="draft-form" onSubmit={submit} noValidate className="space-y-4">
        <TextField label="Name" value={name} onChange={(e) => setName(e.target.value)} maxLength={60} placeholder="For example: Plan A" required autoFocus />
        <SelectField
          label="Start from"
          value={source}
          onChange={setSource}
          placeholder="An empty timetable"
          options={(row?.drafts ?? []).map((d) => ({ value: d.id, label: `A copy of “${d.name}” (${d.placements} placements)` }))}
          hint="Copying keeps every placement; you can change the copy freely without touching the original."
        />
        {row && row.drafts.length === 0 && (
          <p className="rounded-lg bg-surface-2 px-3.5 py-2.5 text-[13px] text-muted">
            The first draft of a class becomes its <span className="font-semibold text-ink">primary</span> draft: the one other classes are checked against.
          </p>
        )}
        {error && <Alert tone="danger">{error}</Alert>}
      </form>
    </Modal>
  )
}

function RenameDialog({ draft, onClose, onSaved }: { draft: DraftSummary | null; onClose: () => void; onSaved: () => void }) {
  const [name, setName] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    if (draft) {
      setName(draft.name)
      setNote(draft.note ?? '')
      setError(null)
    }
  }, [draft])

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!draft) return
    setBusy(true)
    setError(null)
    try {
      await api(`/drafts/${draft.id}`, { method: 'PATCH', body: { name, note: note || null } })
      onSaved()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      open={draft !== null}
      onClose={() => !busy && onClose()}
      title="Rename draft"
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button type="submit" form="rename-form" loading={busy} disabled={!name.trim()}>
            Save
          </Button>
        </>
      }
    >
      <form id="rename-form" onSubmit={submit} noValidate className="space-y-4">
        <TextField label="Name" value={name} onChange={(e) => setName(e.target.value)} maxLength={60} required autoFocus />
        <TextField label="Note (optional)" value={note} onChange={(e) => setNote(e.target.value)} maxLength={300} placeholder="For example: labs in the morning" />
        {error && <Alert tone="danger">{error}</Alert>}
      </form>
    </Modal>
  )
}

// ---------------------------------------------------------------------- the page
export default function DraftsPage() {
  const { user } = useAuth()
  const toast = useToast()
  const navigate = useNavigate()
  const scope = useSchoolScope()
  const { active, ready: termReady } = useTerm()
  const enabled = scope.ready && !!scope.schoolId && !!active
  const { data, error, loading, reload } = useApi<DraftsPayload>(enabled ? scope.path('/drafts') : null)
  const publish = useApi<PublishStatus>(enabled ? scope.path('/publish/status') : null)
  const stateOf = new Map((publish.data?.classes ?? []).map((c) => [c.semester_id, c.state]))
  const unpublished = publish.data?.summary.unpublished ?? 0
  const prefix = user ? ROLE_PREFIX[user.role] : ''

  const [creating, setCreating] = useState<{ row: DraftClassRow; copyFrom: DraftSummary | null } | null>(null)
  const [renaming, setRenaming] = useState<DraftSummary | null>(null)
  const [deleting, setDeleting] = useState<{ draft: DraftSummary; label: string } | null>(null)

  async function makePrimary(d: DraftSummary) {
    try {
      await api(`/drafts/${d.id}`, { method: 'PATCH', body: { is_primary: true } })
      toast.success(`“${d.name}” is now the primary draft.`)
      reload()
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Could not change the primary draft.')
    }
  }

  if (termReady && !active) {
    return (
      <>
        <PageHeader title="Timetable Drafts" subtitle="Build the weekly timetable of every class by hand." />
        <Alert tone="warning" title="Choose the active term first">
          Drafts belong to the classes of the running term. <Link to={`${prefix}/active-term`} className="font-semibold underline">Open Active Term</Link> and pick one.
        </Alert>
      </>
    )
  }

  const totals = (data?.classes ?? []).reduce(
    (t, c) => {
      const primary = c.drafts.find((d) => d.is_primary)
      return { clashes: t.clashes + (primary?.clashes ?? 0), started: t.started + (primary ? 1 : 0) }
    },
    { clashes: 0, started: 0 },
  )

  return (
    <>
      <PageHeader
        title="Timetable Drafts"
        subtitle="Every class can have several named drafts. Open one to place subjects on the weekly grid - drag and drop or click, with live clash checks. Drafts are private: teachers see nothing until you publish the primary drafts (Publish screen)."
      />
      <SchoolBar>
        <Link to={`${prefix}/conflicts`}>
          <Button variant="secondary" size="sm" icon={<ShieldAlert className="size-4" />}>
            Conflict Report
          </Button>
        </Link>
      </SchoolBar>

      {error ? (
        <Alert tone="danger">{error.message}</Alert>
      ) : loading && !data ? (
        <div className="space-y-4">
          <Skeleton className="h-40" />
          <Skeleton className="h-40" />
        </div>
      ) : data && data.classes.length === 0 ? (
        <Panel title="Classes" icon={LayoutGrid}>
          <EmptyState icon={LayoutGrid} title="No class runs in this term">
            Add courses in College Setup (administrator) - the classes of the active term appear here.
          </EmptyState>
        </Panel>
      ) : data ? (
        <div className="space-y-5">
          {totals.clashes > 0 && (
            <Alert tone="danger" title={`${totals.clashes} clash(es) in the primary drafts`}>
              Open the <Link to={`${prefix}/conflicts`} className="font-semibold underline">Conflict Report</Link> to see exactly where.
            </Alert>
          )}
          {unpublished > 0 && (
            <Alert tone="warning" title={`${unpublished} ${unpublished === 1 ? 'class has' : 'classes have'} unpublished changes`}>
              The primary draft differs from what teachers see. <Link to={`${prefix}/publish`} className="font-semibold underline">Open Publish</Link> to make it live.
            </Alert>
          )}
          <GenerationPanel onChanged={reload} />
          {data.classes.map((c) => (
            <Panel
              key={c.semester_id}
              title={c.label}
              icon={LayoutGrid}
              action={
                <div className="flex items-center gap-2">
                  {stateOf.get(c.semester_id) === 'changed' && <Badge tone="warning" className="font-semibold">Unpublished changes</Badge>}
                  {stateOf.get(c.semester_id) === 'in_sync' && <Badge tone="success">Published</Badge>}
                  {stateOf.get(c.semester_id) === 'not_published' && <Badge tone="info">Not published</Badge>}
                  <Button size="sm" icon={<Plus className="size-4" />} onClick={() => setCreating({ row: c, copyFrom: null })} disabled={c.drafts.length >= (data.max_drafts_per_class ?? 12)}>
                    New draft
                  </Button>
                </div>
              }
            >
              <p className="mb-3 text-[13px] text-muted">
                {c.subjects} subjects · {c.locked_sessions} locked session{c.locked_sessions === 1 ? '' : 's'} (PE / SOL / fixed slots appear locked in the grid)
              </p>
              {c.drafts.length === 0 ? (
                <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-dashed border-line-strong px-4 py-5">
                  <p className="text-sm text-muted">No draft yet for this class.</p>
                  <Button variant="secondary" size="sm" icon={<Plus className="size-4" />} onClick={() => setCreating({ row: c, copyFrom: null })}>
                    Start a draft
                  </Button>
                </div>
              ) : (
                <ul className="divide-y divide-line rounded-xl border border-line">
                  {c.drafts.map((d) => (
                    <li key={d.id} className="grid gap-3 px-4 py-3.5 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)_auto] lg:items-center">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <Link to={`${prefix}/generate/${d.id}`} className="truncate text-[15px] font-semibold text-ink hover:text-brand-text">
                            {d.name}
                          </Link>
                          {d.is_primary ? (
                            <Badge tone="brand">
                              <Star className="size-3" aria-hidden="true" /> Primary
                            </Badge>
                          ) : (
                            <Badge>Alternative</Badge>
                          )}
                        </div>
                        <p className="mt-0.5 text-xs text-muted">
                          {d.placements} placement{d.placements === 1 ? '' : 's'} · edited {ago(d.updated_at)}
                          {d.note ? ` · ${d.note}` : ''}
                        </p>
                      </div>
                      <div className="min-w-0">
                        <div className="mb-1 flex items-center justify-between gap-2 text-xs">
                          <span className="text-muted">
                            {d.placed} of {d.required} lectures
                          </span>
                          <span className="font-semibold tabular-nums text-ink">{d.percent}%</span>
                        </div>
                        <Progress percent={d.percent} tone={d.percent === 100 ? 'bg-success-solid' : 'bg-accent'} />
                        <div className="mt-1.5 flex flex-wrap gap-1.5">
                          {d.clashes > 0 && (
                            <Badge tone="danger">
                              <TriangleAlert className="size-3" aria-hidden="true" /> {d.clashes} clash{d.clashes === 1 ? '' : 'es'}
                            </Badge>
                          )}
                          {d.warnings > 0 && <Badge tone="warning">{d.warnings} warning{d.warnings === 1 ? '' : 's'}</Badge>}
                          {d.clashes === 0 && d.placements > 0 && <Badge tone="success">No clash</Badge>}
                          {d.missing > 0 && <Badge>{d.missing} to place</Badge>}
                        </div>
                      </div>
                      <div className="flex flex-wrap items-center gap-1.5 lg:justify-end">
                        <Link to={`${prefix}/generate/${d.id}`}>
                          <Button size="sm">Open grid</Button>
                        </Link>
                        {!d.is_primary && (
                          <Button variant="ghost" size="sm" aria-label={`Make ${d.name} the primary draft`} title="Make primary" icon={<Star className="size-4" />} onClick={() => void makePrimary(d)} />
                        )}
                        <Button variant="ghost" size="sm" aria-label={`Duplicate ${d.name}`} title="Duplicate" icon={<Copy className="size-4" />} onClick={() => setCreating({ row: c, copyFrom: d })} disabled={c.drafts.length >= (data.max_drafts_per_class ?? 12)} />
                        <Button variant="ghost" size="sm" aria-label={`Rename ${d.name}`} title="Rename" icon={<Pencil className="size-4" />} onClick={() => setRenaming(d)} />
                        <Button variant="ghost" size="sm" aria-label={`Delete ${d.name}`} title="Delete" icon={<Trash2 className="size-4" />} onClick={() => setDeleting({ draft: d, label: c.label })} />
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </Panel>
          ))}
          <p className="text-center text-xs text-subtle">
            The <span className="font-medium">primary</span> draft of each class is the one other classes are checked against (teacher clashes). Alternatives are only compared with the primary drafts of the other classes.
          </p>
        </div>
      ) : null}

      <NewDraftDialog
        row={creating?.row ?? null}
        copyFrom={creating?.copyFrom ?? null}
        onClose={() => setCreating(null)}
        onSaved={(id) => {
          setCreating(null)
          toast.success('Draft created.')
          navigate(`${prefix}/generate/${id}`)
        }}
      />
      <RenameDialog draft={renaming} onClose={() => setRenaming(null)} onSaved={() => { setRenaming(null); toast.success('Draft saved.'); reload() }} />
      <ConfirmDialog
        open={deleting !== null}
        tone="danger"
        title={`Delete “${deleting?.draft.name}”?`}
        confirmLabel="Delete draft"
        onClose={() => setDeleting(null)}
        onConfirm={async () => {
          if (!deleting) return
          await api(`/drafts/${deleting.draft.id}`, { method: 'DELETE' })
          toast.success('Draft deleted.')
          reload()
        }}
      >
        <p>
          All {deleting?.draft.placements} placements of this draft of {deleting?.label} are removed.
          {deleting?.draft.is_primary ? ' Another draft of the class becomes the primary one.' : ''} This cannot be undone.
        </p>
      </ConfirmDialog>
    </>
  )
}
