import { useEffect, useMemo, useState } from 'react'
import { CalendarCheck, CalendarRange, History, Save } from 'lucide-react'
import { Alert } from '../components/Alert'
import { Badge } from '../components/Badge'
import { Button } from '../components/Button'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { DataTable } from '../components/DataTable'
import { TextField } from '../components/Field'
import { EmptyState, PageHeader, Panel, Skeleton } from '../components/Panel'
import { SchoolBar } from '../components/SchoolBar'
import { SelectField } from '../components/Select'
import { useToast } from '../components/Toast'
import { api, ApiError } from '../lib/api'
import { formatDate } from '../lib/helpers'
import { useSchoolScope } from '../lib/scope'
import { runningSemesters, useTerm } from '../lib/term'
import type { Term } from '../lib/types'
import { useApi } from '../lib/useApi'

interface DatesPayload {
  term: Term
  school_id: string
  classes: { semester_id: string; label: string; start_date: string | null; end_date: string | null }[]
}

type Kind = 'odd' | 'even'

const KIND_CARDS: { kind: Kind; title: string; text: string }[] = [
  { kind: 'odd', title: 'Odd term', text: 'Starts around July. Semesters 1, 3, 5, 7 run.' },
  { kind: 'even', title: 'Even term', text: 'Starts around January. Semesters 2, 4, 6 run.' },
]

function usualDates(kind: Kind, year: number): { start: string; end: string } {
  return kind === 'odd' ? { start: `${year}-07-01`, end: `${year}-11-30` } : { start: `${year}-01-01`, end: `${year}-05-31` }
}

function TermSelector() {
  const { active, activate } = useTerm()
  const toast = useToast()
  const thisYear = new Date().getFullYear()
  const [kind, setKind] = useState<Kind>(active?.kind ?? 'odd')
  const [year, setYear] = useState<number>(active?.year ?? thisYear)
  const [confirm, setConfirm] = useState(false)

  useEffect(() => {
    if (active) {
      setKind(active.kind)
      setYear(active.year)
    }
  }, [active])

  const years = useMemo(() => {
    const list = new Set<number>([thisYear - 1, thisYear, thisYear + 1, thisYear + 2, thisYear + 3])
    if (active) list.add(active.year)
    return [...list].sort((a, b) => a - b)
  }, [thisYear, active])

  const same = active?.kind === kind && active?.year === year

  async function run() {
    const result = await activate(kind, year)
    toast.success(result.changed ? `${result.active.name} is now the active term.` : `${result.active.name} was already the active term.`)
  }

  return (
    <Panel title="Choose the term" icon={CalendarCheck}>
      <fieldset>
        <legend className="mb-2 text-[13px] font-medium text-ink">Which kind of term is running?</legend>
        <div className="grid gap-3 sm:grid-cols-2">
          {KIND_CARDS.map((card) => {
            const selected = kind === card.kind
            return (
              <label
                key={card.kind}
                className={`flex cursor-pointer items-start gap-3 rounded-xl border p-4 transition-colors ${
                  selected ? 'border-accent bg-brand-soft' : 'border-line-strong bg-surface hover:bg-hover'
                }`}
              >
                <input
                  type="radio"
                  name="term-kind"
                  value={card.kind}
                  checked={selected}
                  onChange={() => setKind(card.kind)}
                  className="mt-1 size-4 accent-[var(--brand-solid)]"
                />
                <span>
                  <span className="block text-sm font-semibold text-ink">{card.title}</span>
                  <span className="mt-0.5 block text-[13px] text-muted">{card.text}</span>
                </span>
              </label>
            )
          })}
        </div>
      </fieldset>

      <div className="mt-5 flex flex-wrap items-end gap-4">
        <SelectField
          label="Year"
          className="w-40"
          value={String(year)}
          onChange={(v) => setYear(Number(v))}
          options={years.map((y) => ({ value: String(y), label: String(y) }))}
        />
        <p className="pb-2.5 text-sm text-muted">
          You are choosing <span className="font-semibold text-ink">{kind === 'odd' ? 'Odd' : 'Even'} {year}</span> ({runningSemesters(kind)}).
        </p>
      </div>

      <div className="mt-5 flex flex-wrap items-center gap-3">
        <Button onClick={() => setConfirm(true)} disabled={same} icon={<CalendarCheck className="size-4" />}>
          Set as active term
        </Button>
        {same && <span className="text-[13px] text-muted">This is already the active term.</span>}
      </div>

      <ConfirmDialog
        open={confirm}
        title={`Make ${kind === 'odd' ? 'Odd' : 'Even'} ${year} the active term?`}
        confirmLabel="Yes, set it"
        onConfirm={run}
        onClose={() => setConfirm(false)}
      >
        <p>
          From now on every screen shows only <span className="font-medium text-ink">{runningSemesters(kind)}</span>.
        </p>
        {active && !same && <p>{active.name} is not deleted - it stays saved as history.</p>}
        <p>This is a college-wide setting, so it changes for every school.</p>
      </ConfirmDialog>
    </Panel>
  )
}

function SemesterDates({ term }: { term: Term }) {
  const scope = useSchoolScope()
  const toast = useToast()
  const path = scope.ready && scope.schoolId ? scope.path(`/terms/${term.id}/dates`) : null
  const { data, error, loading, reload } = useApi<DatesPayload>(path)
  const [dates, setDates] = useState<Record<string, { start: string; end: string }>>({})
  const [bulk, setBulk] = useState(() => usualDates(term.kind, term.year))
  const [saving, setSaving] = useState(false)
  const [problems, setProblems] = useState<Record<string, string>>({})
  const [formError, setFormError] = useState<string | null>(null)

  useEffect(() => {
    if (!data) return
    setDates(Object.fromEntries(data.classes.map((c) => [c.semester_id, { start: c.start_date ?? '', end: c.end_date ?? '' }])))
    setProblems({})
    setFormError(null)
  }, [data])

  useEffect(() => setBulk(usualDates(term.kind, term.year)), [term.kind, term.year])

  function applyToAll() {
    setDates((current) => Object.fromEntries(Object.keys(current).map((id) => [id, { start: bulk.start, end: bulk.end }])))
  }

  async function save() {
    if (!data) return
    const items = data.classes
      .filter((c) => dates[c.semester_id]?.start && dates[c.semester_id]?.end)
      .map((c) => ({ semester_id: c.semester_id, start_date: dates[c.semester_id].start, end_date: dates[c.semester_id].end }))
    if (items.length === 0) {
      setFormError('Please fill in the start and end date of at least one class.')
      return
    }
    setSaving(true)
    setProblems({})
    setFormError(null)
    try {
      await api(`/terms/${term.id}/dates`, { method: 'PUT', body: { ...scope.body(), items } })
      toast.success('Semester dates saved.')
      reload()
    } catch (err) {
      if (err instanceof ApiError) {
        setProblems(err.fields)
        setFormError(err.message)
      } else setFormError('Could not save. Please try again.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <>
      <SchoolBar />
      <Panel title={`Semester dates - ${term.name}`} icon={CalendarRange}>
        <p className="mb-4 max-w-2xl text-sm text-muted">
          Every class gets a start and an end date. These dates are used later to count how many lectures can really be taken
          (weeks × timetable − holidays − events).
        </p>
        {loading && !data ? (
          <div className="space-y-2">
            <Skeleton className="h-12" />
            <Skeleton className="h-12" />
            <Skeleton className="h-12" />
          </div>
        ) : error ? (
          <Alert tone="danger">{error.message}</Alert>
        ) : data && data.classes.length === 0 ? (
          <EmptyState icon={CalendarRange} title="No classes run in this term">
            Add courses and semesters in College Setup first.
          </EmptyState>
        ) : data ? (
          <>
            <div className="mb-4 flex flex-wrap items-end gap-3 rounded-xl border border-line bg-surface-2 p-4">
              <TextField label="Start date" type="date" className="w-44" value={bulk.start} onChange={(e) => setBulk({ ...bulk, start: e.target.value })} />
              <TextField label="End date" type="date" className="w-44" value={bulk.end} onChange={(e) => setBulk({ ...bulk, end: e.target.value })} />
              <Button variant="secondary" onClick={applyToAll} disabled={!bulk.start || !bulk.end}>
                Use for all classes
              </Button>
              <p className="basis-full text-xs text-muted">
                Suggested usual dates for {term.kind === 'odd' ? 'an odd term' : 'an even term'}. You can change any single class below.
              </p>
            </div>
            <DataTable
              caption="Start and end date of every class"
              rows={data.classes}
              rowKey={(c) => c.semester_id}
              card={(c) => (
                <div>
                  <div className="mb-2 flex items-center justify-between gap-2">
                    <p className="font-medium text-ink">{c.label}</p>
                    {problems[c.semester_id] ? <span className="text-[13px] text-danger">{problems[c.semester_id]}</span> : c.start_date && c.end_date ? <Badge tone="success">Saved</Badge> : <Badge tone="warning">Not set</Badge>}
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <input
                      type="date"
                      aria-label={`Start date of ${c.label}`}
                      value={dates[c.semester_id]?.start ?? ''}
                      onChange={(e) => setDates({ ...dates, [c.semester_id]: { ...dates[c.semester_id], start: e.target.value } })}
                      className="h-9 w-full rounded-lg border border-line-strong bg-field px-2 text-sm focus:border-accent focus:outline-none focus:ring-4 focus:ring-[color:var(--brand-ring)]"
                    />
                    <input
                      type="date"
                      aria-label={`End date of ${c.label}`}
                      value={dates[c.semester_id]?.end ?? ''}
                      onChange={(e) => setDates({ ...dates, [c.semester_id]: { ...dates[c.semester_id], end: e.target.value } })}
                      className="h-9 w-full rounded-lg border border-line-strong bg-field px-2 text-sm focus:border-accent focus:outline-none focus:ring-4 focus:ring-[color:var(--brand-ring)]"
                    />
                  </div>
                </div>
              )}
              columns={[
                { key: 'class', header: 'Class', cell: (c) => <span className="font-medium text-ink">{c.label}</span> },
                {
                  key: 'start',
                  header: 'Start date',
                  className: 'w-48',
                  cell: (c) => (
                    <input
                      type="date"
                      aria-label={`Start date of ${c.label}`}
                      value={dates[c.semester_id]?.start ?? ''}
                      onChange={(e) => setDates({ ...dates, [c.semester_id]: { ...dates[c.semester_id], start: e.target.value } })}
                      className="h-9 w-full rounded-lg border border-line-strong bg-field px-2.5 text-sm focus:border-accent focus:outline-none focus:ring-4 focus:ring-[color:var(--brand-ring)]"
                    />
                  ),
                },
                {
                  key: 'end',
                  header: 'End date',
                  className: 'w-48',
                  cell: (c) => (
                    <input
                      type="date"
                      aria-label={`End date of ${c.label}`}
                      value={dates[c.semester_id]?.end ?? ''}
                      onChange={(e) => setDates({ ...dates, [c.semester_id]: { ...dates[c.semester_id], end: e.target.value } })}
                      className="h-9 w-full rounded-lg border border-line-strong bg-field px-2.5 text-sm focus:border-accent focus:outline-none focus:ring-4 focus:ring-[color:var(--brand-ring)]"
                    />
                  ),
                },
                {
                  key: 'state',
                  header: 'Status',
                  hideBelow: 'sm',
                  cell: (c) =>
                    problems[c.semester_id] ? (
                      <span className="text-[13px] text-danger">{problems[c.semester_id]}</span>
                    ) : c.start_date && c.end_date ? (
                      <Badge tone="success">Saved · {formatDate(c.start_date)} - {formatDate(c.end_date)}</Badge>
                    ) : (
                      <Badge tone="warning">Not set</Badge>
                    ),
                },
              ]}
            />
            {formError && (
              <Alert tone="danger" className="mt-4">
                {formError}
              </Alert>
            )}
            <div className="mt-4 flex justify-end">
              <Button onClick={save} loading={saving} icon={<Save className="size-4" />}>
                Save dates
              </Button>
            </div>
          </>
        ) : null}
      </Panel>
    </>
  )
}

export default function ActiveTermPage() {
  const { active, terms, ready } = useTerm()

  return (
    <>
      <PageHeader
        title="Active Term"
        subtitle="The college runs only odd semesters or only even semesters at a time. Choose the running term here - every other screen then shows only its semesters. Old terms stay saved as history."
      />

      <div className="space-y-6">
        <Panel title="Running now" icon={CalendarCheck}>
          {!ready ? (
            <Skeleton className="h-16" />
          ) : active ? (
            <div className="flex flex-wrap items-center gap-4">
              <span className="flex size-14 items-center justify-center rounded-2xl bg-brand-soft text-brand-text">
                <CalendarCheck className="size-7" aria-hidden="true" />
              </span>
              <div>
                <p className="text-2xl font-semibold tracking-tight text-ink">{active.name}</p>
                <p className="text-sm text-muted">
                  {runningSemesters(active.kind)} are running
                  {active.activated_at ? ` · set on ${formatDate(active.activated_at.slice(0, 10))}` : ''}
                </p>
              </div>
            </div>
          ) : (
            <Alert tone="warning" title="No term is active yet">
              Choose Odd or Even and the year below. Until then, subjects, assignments and fixed slots cannot be set up.
            </Alert>
          )}
        </Panel>

        <TermSelector />

        {active && <SemesterDates term={active} />}

        <Panel title="History" icon={History}>
          {terms.length === 0 ? (
            <p className="text-sm text-muted">No term has been used yet.</p>
          ) : (
            <ul className="divide-y divide-line">
              {terms.map((t) => (
                <li key={t.id} className="flex items-center justify-between gap-3 py-2.5 first:pt-0 last:pb-0">
                  <span className="text-sm font-medium text-ink">{t.name}</span>
                  {t.is_active ? <Badge tone="success">Active</Badge> : <Badge>Saved as history</Badge>}
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>
    </>
  )
}
