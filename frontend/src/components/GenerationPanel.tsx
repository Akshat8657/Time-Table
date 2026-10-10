import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { Check, ChevronDown, CircleX, Copy, Loader2, Play, SlidersHorizontal, Sparkles, Star, Trash2, TriangleAlert } from 'lucide-react'
import { useAuth } from '../auth/AuthProvider'
import { api, ApiError } from '../lib/api'
import { ROLE_PREFIX } from '../lib/nav'
import { useSchoolScope } from '../lib/scope'
import type { CopyableTerm, GenRun, GenRunsPayload, GenSolution, WeightsPayload } from '../lib/types'
import { useApi } from '../lib/useApi'
import { Alert } from './Alert'
import { Badge } from './Badge'
import { Button } from './Button'
import { ConfirmDialog } from './ConfirmDialog'
import { TextField } from './Field'
import { Modal } from './Modal'
import { Panel } from './Panel'
import { SelectField } from './Select'
import { useToast } from './Toast'

function timeOf(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })
}

// ---------------------------------------------------------------------- one solution card
function SolutionCard({ sol, best, busy, onUse }: { sol: GenSolution; best: boolean; busy: boolean; onUse: () => void }) {
  const { user } = useAuth()
  const prefix = user ? ROLE_PREFIX[user.role] : ''
  const [open, setOpen] = useState(false)
  const classes = Object.values(sol.breakdown.classes).sort((a, b) => a.label.localeCompare(b.label))
  const draftOf = new Map(sol.drafts.map((d) => [d.semester_id, d.draft_id]))
  const semOf = new Map(Object.entries(sol.breakdown.classes).map(([sem, v]) => [v.label, sem]))
  return (
    <div className={`rounded-xl border p-4 ${sol.in_use ? 'border-accent bg-brand-soft/40' : 'border-line bg-surface-2/50'}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="flex size-8 items-center justify-center rounded-lg bg-brand-soft text-sm font-bold text-brand-text">{sol.label}</span>
        <span className="text-2xl font-semibold tabular-nums text-ink">{sol.score}</span>
        <span className="text-xs text-muted">/ 100 soft-rule score</span>
        {best && <Badge tone="brand">Best score</Badge>}
        {sol.complete ? <Badge tone="success">All lectures placed</Badge> : <Badge tone="warning">{sol.breakdown.required - sol.breakdown.placed} not placed</Badge>}
        {sol.in_use && (
          <Badge tone="brand">
            <Star className="size-3" aria-hidden="true" /> In use
          </Badge>
        )}
      </div>

      <ul className="mt-3 grid gap-1.5 sm:grid-cols-2">
        {classes.map((c) => {
          const sem = semOf.get(c.label)
          const draft = sem ? draftOf.get(sem) : undefined
          return (
            <li key={c.label} className="flex items-center justify-between gap-2 text-[13px]">
              {draft ? (
                <Link to={`${prefix}/generate/${draft}`} className="truncate font-medium text-ink underline-offset-2 hover:text-brand-text hover:underline">
                  {c.label}
                </Link>
              ) : (
                <span className="truncate font-medium text-ink">{c.label}</span>
              )}
              <Badge tone={c.percent === 100 ? 'success' : 'warning'}>{c.percent}%</Badge>
            </li>
          )
        })}
      </ul>

      {sol.breakdown.unplaced.length > 0 && (
        <ul className="mt-2 space-y-1 rounded-lg bg-warning-soft px-3 py-2 text-xs text-warning">
          {sol.breakdown.unplaced.map((u) => (
            <li key={`${u.class_label}-${u.unit}`}>{u.message}</li>
          ))}
        </ul>
      )}

      <button type="button" onClick={() => setOpen(!open)} className="mt-3 flex items-center gap-1 text-xs font-medium text-brand-text hover:underline" aria-expanded={open}>
        <ChevronDown className={`size-3.5 transition-transform ${open ? 'rotate-180' : ''}`} aria-hidden="true" />
        Where the points went
      </button>
      {open && (
        <ul className="mt-2 space-y-1.5">
          {sol.breakdown.rules.map((r) => (
            <li key={r.rule} className="text-xs">
              <div className="flex items-center justify-between gap-2 text-muted">
                <span>
                  {r.label} <span className="text-subtle">(weight {r.weight})</span>
                </span>
                <span className={r.points_lost > 0.5 ? 'font-medium text-warning' : 'text-subtle'}>
                  {r.points_lost > 0 ? `-${r.points_lost} pts` : 'perfect'}
                </span>
              </div>
              <div className="mt-0.5 h-1.5 overflow-hidden rounded-full bg-surface-2 ring-1 ring-inset ring-line">
                <div className="h-full rounded-full bg-accent" style={{ width: `${Math.round(r.satisfaction * 100)}%` }} />
              </div>
              {r.detail && (
                <div className="mt-1 text-[11px] leading-snug text-muted" data-testid="pref-detail">
                  <p>{r.detail}</p>
                  {r.teachers && r.teachers.length > 0 && (
                    <ul className="mt-0.5 space-y-0.5">
                      {r.teachers.map((t) => (
                        <li key={t.teacher_id}>
                          <span className="font-medium text-ink">{t.name}</span>
                          {t.stars ? ` · stars ${t.stars[0]}/${t.stars[1]}` : ''}
                          {t.first ? ` · first lecture ${t.first[0]}/${t.first[1]} days` : ''}
                          {t.last ? ` · last period avoided ${t.last[0]}/${t.last[1]} days` : ''}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}

      <div className="mt-3">
        <Button size="sm" icon={<Check className="size-4" />} onClick={onUse} disabled={busy || sol.in_use}>
          {sol.in_use ? 'This is the timetable' : 'Use this solution'}
        </Button>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------- the panel
export function GenerationPanel({ onChanged }: { onChanged: () => void }) {
  const scope = useSchoolScope()
  const toast = useToast()
  const enabled = scope.ready && !!scope.schoolId
  const weightsApi = useApi<WeightsPayload>(enabled ? scope.path('/generation/weights') : null)
  const runsApi = useApi<GenRunsPayload>(enabled ? scope.path('/generation/runs') : null)

  const [weights, setWeights] = useState<Record<string, number> | null>(null)
  const [mode, setMode] = useState<'fresh' | 'keep_locked'>('fresh')
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [showWeights, setShowWeights] = useState(false)
  const [showAllRuns, setShowAllRuns] = useState(false)
  const [discarding, setDiscarding] = useState<GenRun | null>(null)
  const [copyOpen, setCopyOpen] = useState(false)

  useEffect(() => {
    if (weightsApi.data && weights === null) setWeights(weightsApi.data.weights)
  }, [weightsApi.data, weights])

  const runs = runsApi.data?.runs ?? []
  const running = runs.find((r) => r.status === 'running')

  // poll while a run works; refresh the drafts list the moment it finishes
  const wasRunning = useRef<string | null>(null)
  useEffect(() => {
    if (running) {
      wasRunning.current = running.id
      const t = window.setInterval(() => runsApi.reload(), 1200)
      return () => window.clearInterval(t)
    }
    if (wasRunning.current) {
      wasRunning.current = null
      onChanged()
    }
  }, [running?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  async function act(fn: () => Promise<unknown>, success?: string) {
    setBusy(true)
    try {
      await fn()
      if (success) toast.success(success)
      runsApi.reload()
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Something went wrong. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  const start = () =>
    act(async () => {
      await api('/generation/runs', { method: 'POST', body: { ...scope.body(), name: name.trim() || null, mode, time_limit_s: 120 } })
      setName('')
    }, 'Generation started - the engine is working in the background.')

  const saveWeights = (e: FormEvent) => {
    e.preventDefault()
    if (!weights) return
    void act(() => api('/generation/weights', { method: 'PUT', body: { ...scope.body(), weights } }), 'Weights saved for this school.')
  }

  const shown = showAllRuns ? runs : runs.slice(0, 2)

  return (
    <>
      <Panel
        title="Generate automatically"
        icon={Sparkles}
        action={
          <Button variant="secondary" size="sm" icon={<Copy className="size-4" />} onClick={() => setCopyOpen(true)}>
            Copy a past term
          </Button>
        }
      >
        <p className="mb-4 text-sm text-muted">
          One run schedules <span className="font-semibold text-ink">all classes together</span> (teachers are shared between classes). It never breaks a hard rule - fixed slots, visiting days, OFF days, double blocks - and gives 2-3 scored solutions to compare. Every solution is a normal set of drafts you can still edit by hand.
        </p>

        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_auto]">
          <div className="flex flex-wrap items-end gap-3">
            <TextField label="Name (optional)" className="w-56" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} placeholder="For example: with new weights" />
            <fieldset>
              <legend className="mb-1.5 block text-[13px] font-medium text-ink">Mode</legend>
              <div className="flex gap-2">
                {([['fresh', 'Fresh'], ['keep_locked', 'Keep pinned placements']] as const).map(([value, label]) => (
                  <label key={value} className={`flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-2.5 text-[13px] font-medium ${mode === value ? 'border-accent bg-brand-soft text-brand-text' : 'border-line-strong text-muted hover:bg-hover'}`}>
                    <input type="radio" name="gen-mode" className="size-3.5 accent-[var(--brand-solid)]" checked={mode === value} onChange={() => setMode(value)} />
                    {label}
                  </label>
                ))}
              </div>
            </fieldset>
          </div>
          <div className="flex items-end">
            <Button icon={<Play className="size-4" />} onClick={() => void start()} loading={busy && !running} disabled={!!running}>
              Generate
            </Button>
          </div>
        </div>
        <p className="mt-2 text-xs text-muted">
          “Keep pinned placements” = semi-automatic: anything you pinned (📌) in a primary draft stays exactly where it is, the engine fills the rest.
        </p>

        <button type="button" onClick={() => setShowWeights(!showWeights)} className="mt-4 flex items-center gap-1.5 text-sm font-medium text-brand-text hover:underline" aria-expanded={showWeights}>
          <SlidersHorizontal className="size-4" aria-hidden="true" />
          Soft-rule weights
          <ChevronDown className={`size-4 transition-transform ${showWeights ? 'rotate-180' : ''}`} aria-hidden="true" />
        </button>
        {showWeights && weights && weightsApi.data && (
          <form onSubmit={saveWeights} className="mt-3 rounded-xl border border-line bg-surface-2/50 p-4">
            <p className="mb-3 text-xs text-muted">0 = ignore the rule, 100 = try hardest. Hard rules (clashes, fixed slots, visiting days...) are never traded away.</p>
            <div className="grid gap-x-6 gap-y-3 md:grid-cols-2">
              {weightsApi.data.rules.map((r) => (
                <label key={r.name} className="block text-[13px]">
                  <span className="mb-1 flex items-center justify-between text-ink">
                    {r.label}
                    <span className="tabular-nums font-semibold">{weights[r.name]}</span>
                  </span>
                  <input
                    type="range" min={0} max={100} step={5} value={weights[r.name]}
                    onChange={(e) => setWeights({ ...weights, [r.name]: Number(e.target.value) })}
                    className="w-full accent-[var(--brand-solid)]"
                    aria-label={`${r.label} (0 to 100)`}
                  />
                </label>
              ))}
            </div>
            <div className="mt-3 flex items-center gap-3">
              <Button type="submit" variant="secondary" size="sm" loading={busy}>
                Save weights
              </Button>
              <span className="text-xs text-subtle">Saved per school; the next Generate uses them.</span>
            </div>
          </form>
        )}

        {running && (
          <div className="mt-4 rounded-xl border border-accent/40 bg-brand-soft/40 p-4" aria-live="polite">
            <div className="flex flex-wrap items-center gap-3">
              <Loader2 className="size-5 animate-spin text-brand-text" aria-hidden="true" />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-semibold text-ink">
                  {running.name} · {running.progress}%
                </p>
                <p className="truncate text-xs text-muted">{running.message}</p>
              </div>
              <Button variant="secondary" size="sm" icon={<CircleX className="size-4" />} onClick={() => void act(() => api(`/generation/runs/${running.id}/cancel`, { method: 'POST' }))}>
                Cancel
              </Button>
            </div>
            <div className="mt-3 h-2 overflow-hidden rounded-full bg-surface ring-1 ring-inset ring-line" role="progressbar" aria-valuenow={running.progress} aria-valuemin={0} aria-valuemax={100}>
              <div className="h-full rounded-full bg-accent transition-all" style={{ width: `${running.progress}%` }} />
            </div>
            {running.precheck.length > 0 && (
              <ul className="mt-3 space-y-1 text-xs text-warning">
                {running.precheck.map((f, i) => (
                  <li key={i} className="flex gap-1.5">
                    <TriangleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
                    {f.message}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        {shown.filter((r) => r.status !== 'running').map((run) => (
          <div key={run.id} className="mt-4 rounded-xl border border-line p-4">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-semibold text-ink">{run.name}</span>
              {run.status === 'done' ? <Badge tone="success">Finished</Badge> : run.status === 'cancelled' ? <Badge>Stopped</Badge> : <Badge tone="danger">Failed</Badge>}
              <span className="text-xs text-muted">
                {timeOf(run.created_at)} · {run.mode === 'keep_locked' ? 'kept pinned placements' : 'fresh'}
              </span>
              <span className="ml-auto">
                <Button variant="ghost" size="sm" aria-label={`Discard ${run.name}`} title="Discard this run and its drafts" icon={<Trash2 className="size-4" />} onClick={() => setDiscarding(run)} />
              </span>
            </div>
            {run.error && <Alert tone="danger" className="mt-2">{run.error}</Alert>}
            {run.precheck.length > 0 && (
              <Alert tone="warning" className="mt-2" title="The engine warned before solving">
                <ul className="list-disc space-y-0.5 pl-4">
                  {run.precheck.map((f, i) => (
                    <li key={i}>{f.message}</li>
                  ))}
                </ul>
              </Alert>
            )}
            {run.solutions.length > 0 && (
              <div className="mt-3 grid gap-3 xl:grid-cols-3 lg:grid-cols-2">
                {run.solutions.map((sol, i) => (
                  <SolutionCard
                    key={sol.id} sol={sol} best={i === 0} busy={busy}
                    onUse={() => void act(() => api(`/generation/solutions/${sol.id}/use`, { method: 'POST' }), `Solution ${sol.label} is now the timetable of every class.`).then(onChanged)}
                  />
                ))}
              </div>
            )}
          </div>
        ))}
        {runs.length > 2 && (
          <button type="button" onClick={() => setShowAllRuns(!showAllRuns)} className="mt-3 text-sm font-medium text-brand-text hover:underline">
            {showAllRuns ? 'Show fewer runs' : `Show all ${runs.length} runs`}
          </button>
        )}
      </Panel>

      <ConfirmDialog
        open={discarding !== null}
        tone="danger"
        title={`Discard “${discarding?.name}”?`}
        confirmLabel="Discard run"
        onClose={() => setDiscarding(null)}
        onConfirm={async () => {
          if (!discarding) return
          await api(`/generation/runs/${discarding.id}`, { method: 'DELETE' })
          toast.success('Run and its drafts removed.')
          runsApi.reload()
          onChanged()
        }}
      >
        <p>All solutions of this run and their drafts are deleted. A solution that is in use cannot be discarded.</p>
      </ConfirmDialog>

      <CopyTermDialog open={copyOpen} onClose={() => setCopyOpen(false)} onCopied={() => { setCopyOpen(false); onChanged() }} />
    </>
  )
}

// ---------------------------------------------------------------------- copy a past term
function CopyTermDialog({ open, onClose, onCopied }: { open: boolean; onClose: () => void; onCopied: () => void }) {
  const scope = useSchoolScope()
  const toast = useToast()
  const terms = useApi<{ terms: CopyableTerm[] }>(open ? scope.path('/drafts/copyable-terms') : null)
  const [termId, setTermId] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    if (open) {
      setTermId('')
      setError(null)
    }
  }, [open])
  const usable = (terms.data?.terms ?? []).filter((t) => t.same_kind && t.primaries > 0)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const res = await api<{ created: { class_label: string }[]; source: string }>('/drafts/copy-term', { method: 'POST', body: { ...scope.body(), term_id: termId } })
      toast.success(`Copied ${res.created.length} timetable(s) from ${res.source} as new drafts.`)
      onCopied()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not copy. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      open={open}
      onClose={() => !busy && onClose()}
      title="Copy a past term as a base"
      description="The primary timetable of each class in that term becomes a new draft here - then adjust it by hand or regenerate."
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button type="submit" form="copy-term-form" loading={busy} disabled={!termId}>
            Copy drafts
          </Button>
        </>
      }
    >
      <form id="copy-term-form" onSubmit={submit} noValidate className="space-y-4">
        {terms.loading ? (
          <p className="text-sm text-muted">Looking for past terms…</p>
        ) : usable.length === 0 ? (
          <Alert tone="info">No past term has finished timetables yet. After your first term, its timetable can seed the next one here.</Alert>
        ) : (
          <SelectField
            label="Copy from"
            value={termId}
            onChange={setTermId}
            placeholder="Choose a past term"
            options={usable.map((t) => ({ value: t.id, label: `${t.name} (${t.primaries} class timetables)` }))}
          />
        )}
        {error && <Alert tone="danger">{error}</Alert>}
      </form>
    </Modal>
  )
}
