import { Clock, FlaskConical, Info, TriangleAlert, Users } from 'lucide-react'
import type {
  AssistantBlock, AssistantCell, AssistantFactsBlock, AssistantFreeBlock, AssistantListBlock, AssistantNoteBlock, AssistantTimetableBlock,
} from '../lib/types'
import { formatShort } from '../lib/helpers'
import { Badge, type BadgeTone } from './Badge'

/** The pieces of an assistant answer. Every number, name and time in them comes from the live database (our own services). */

function tagTone(tag: string): BadgeTone {
  const t = tag.toLowerCase()
  if (t.startsWith('cancelled') || t.startsWith('absent') || t.startsWith('holiday') || t.startsWith('not running')) return 'danger'
  if (t.startsWith('covering') || t.startsWith('moved') || t.startsWith('event')) return 'warning'
  if (t === 'lab') return 'info'
  if (t === 'combined' || t === 'parallel' || t === 'extra lecture') return 'brand'
  return 'neutral'
}

function Notes({ notes }: { notes: string[] }) {
  if (!notes.length) return null
  return (
    <ul className="mt-2 space-y-1">
      {notes.map((n) => (
        <li key={n} className="flex items-start gap-2 text-[13px] text-muted">
          <Info className="mt-0.5 size-3.5 shrink-0 text-info" aria-hidden="true" />
          <span>{n}</span>
        </li>
      ))}
    </ul>
  )
}

// ---------------------------------------------------------------------------------------------------- free teachers
function FreeTeachers({ block }: { block: AssistantFreeBlock }) {
  const all = block.periods.length
  return (
    <div data-testid="block-free" className="mt-3">
      {block.teachers.length > 0 && (
        <ul className="divide-y divide-line overflow-hidden rounded-xl border border-line bg-surface">
          {block.teachers.map((t) => {
            const wholeDay = !block.period && all > 0 && t.free.length === all
            return (
              <li key={t.name + t.school} className="px-3.5 py-3" data-testid="free-teacher">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                  <span className="text-[15px] font-semibold text-ink">{t.name}</span>
                  {t.role !== 'Teacher' && <Badge tone="brand">{t.role}</Badge>}
                  {t.prefers_not && <Badge tone="warning">prefers not to teach then</Badge>}
                  {t.school && <span className="text-xs text-subtle">{t.school}</span>}
                </div>
                <p className="mt-0.5 text-[13px] text-muted">{t.specialization || 'Specialization not filled in'}</p>
                <div className="mt-2 flex flex-wrap items-center gap-1.5">
                  {wholeDay && <Badge tone="success">free all day</Badge>}
                  {t.free.map((f) => (
                    <span
                      key={f.period}
                      className="inline-flex items-center gap-1 rounded-lg bg-success-soft px-2 py-1 text-xs font-medium text-success ring-1 ring-inset ring-success-line"
                      title={`Period ${f.period}, ${f.time}`}
                    >
                      <Clock className="size-3" aria-hidden="true" />
                      <span className="tabular-nums">P{f.period} · {f.time}</span>
                    </span>
                  ))}
                </div>
              </li>
            )
          })}
        </ul>
      )}
      {block.total > block.shown && <p className="mt-2 text-[13px] text-muted">Showing {block.shown} of {block.total} teachers.</p>}
      <Notes notes={block.notes} />
    </div>
  )
}

// ---------------------------------------------------------------------------------------------------- one lecture
function Lecture({ c, compact = false }: { c: AssistantCell; compact?: boolean }) {
  const off = c.tone === 'off'
  return (
    <div
      className={`rounded-lg border px-2.5 py-2 ${off ? 'border-line bg-surface-2 opacity-80' : c.tone === 'lab' ? 'border-info-line bg-info-soft/50' : c.tone === 'fixed' ? 'border-warning-line bg-warning-soft/50' : 'border-line bg-surface'}`}
      data-testid="lecture"
    >
      <p className={`flex items-start gap-1.5 text-[13.5px] font-semibold leading-snug text-ink ${off ? 'line-through decoration-subtle' : ''}`}>
        {c.tone === 'lab' && <FlaskConical className="mt-0.5 size-3.5 shrink-0 text-info" aria-hidden="true" />}
        <span className="min-w-0">{c.title}</span>
      </p>
      {c.teachers && <p className="mt-0.5 text-xs text-muted">{c.teachers}</p>}
      {c.classes.length > 0 && !compact && <p className="mt-0.5 text-xs text-subtle">{c.classes.join(' · ')}</p>}
      {c.tags.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {c.tags.map((t) => (
            <Badge key={t} tone={tagTone(t)}>
              {t}
            </Badge>
          ))}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------------------------------- timetable
function DayList({ block }: { block: AssistantTimetableBlock }) {
  const byStart = [...block.cells].sort((a, b) => a.start - b.start)
  return (
    <div className="space-y-2">
      {byStart.map((c) => (
        <div key={`${c.day}-${c.start}-${c.title}`} className="flex gap-3">
          <div className="w-[5.2rem] shrink-0 pt-1.5 text-right">
            <p className="text-[13px] font-semibold text-ink">
              P{c.start}
              {c.length > 1 ? `-${c.start + c.length - 1}` : ''}
            </p>
            <p className="text-[11px] tabular-nums text-subtle">{c.time}</p>
          </div>
          <div className="min-w-0 flex-1">
            <Lecture c={c} />
          </div>
        </div>
      ))}
      {byStart.length === 0 && <p className="rounded-lg bg-surface-2 px-3 py-3 text-sm text-muted">{block.empty_text}</p>}
    </div>
  )
}

function WeekTable({ block }: { block: AssistantTimetableBlock }) {
  const days = block.days
  const rows: ({ kind: 'period'; number: number; time: string } | { kind: 'lunch'; time: string })[] = []
  block.periods.forEach((p) => {
    rows.push({ kind: 'period', number: p.number, time: p.time })
    if (block.lunch && p.time.split('-')[1] === block.lunch.split('-')[0]) rows.push({ kind: 'lunch', time: block.lunch })
  })
  const covered = new Set<string>()
  const startsAt = (day: number, period: number) => block.cells.filter((c) => c.day === day && c.start === period)
  block.cells.forEach((c) => {
    for (let n = c.start + 1; n < c.start + c.length; n += 1) covered.add(`${c.day}:${n}`)
  })
  return (
    <div className="overflow-x-auto rounded-xl border border-line">
      <table className="w-full min-w-[40rem] table-fixed border-collapse text-left">
        <thead>
          <tr className="bg-surface-2">
            <th scope="col" className="w-[4.6rem] px-2 py-2 text-xs font-medium text-muted">Period</th>
            {days.map((d) => (
              <th key={d.day} scope="col" className={`px-2 py-2 text-xs font-semibold ${d.today ? 'text-brand-text' : 'text-ink'}`}>
                {d.short} <span className="font-normal text-subtle">{formatShort(d.date).split(' ').slice(1).join(' ')}</span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) =>
            r.kind === 'lunch' ? (
              <tr key="lunch">
                <td colSpan={days.length + 1} className="bg-warning-soft/50 py-1 text-center text-[11px] font-medium text-warning">
                  Lunch {r.time}
                </td>
              </tr>
            ) : (
              <tr key={r.number} className="border-t border-line align-top">
                <th scope="row" className="px-2 py-2 text-left">
                  <span className="block text-[13px] font-semibold text-ink">P{r.number}</span>
                  <span className="block text-[10.5px] font-normal tabular-nums text-subtle">{r.time}</span>
                </th>
                {days.map((d) => {
                  if (covered.has(`${d.day}:${r.number}`)) return null
                  const here = startsAt(d.day, r.number)
                  const span = Math.max(1, ...here.map((c) => c.length))
                  return (
                    <td key={d.day} rowSpan={span} className="border-l border-line p-1">
                      <div className="space-y-1">
                        {here.map((c) => (
                          <Lecture key={c.title + c.classes.join()} c={c} compact />
                        ))}
                      </div>
                    </td>
                  )
                })}
              </tr>
            ),
          )}
        </tbody>
      </table>
    </div>
  )
}

function Timetable({ block }: { block: AssistantTimetableBlock }) {
  const holidays = block.days.flatMap((d) => d.notes.map((n) => `${d.short}: ${n}`))
  return (
    <div data-testid="block-timetable" className="mt-3">
      <p className="mb-2 flex flex-wrap items-center gap-2 text-sm font-semibold text-ink">
        {block.title}
        {block.published && block.version != null && <Badge tone="neutral">published v{block.version}</Badge>}
      </p>
      {block.mode === 'day' ? <DayList block={block} /> : block.cells.length ? <WeekTable block={block} /> : <p className="rounded-lg bg-surface-2 px-3 py-3 text-sm text-muted">{block.empty_text}</p>}
      <Notes notes={[...holidays, ...block.notes]} />
    </div>
  )
}

// ---------------------------------------------------------------------------------------------------- facts + lists + notes
function Facts({ block }: { block: AssistantFactsBlock }) {
  return (
    <div className="mt-3 rounded-xl border border-line bg-surface p-3.5" data-testid="block-facts">
      <p className="text-[15px] font-semibold text-ink">{block.title}</p>
      <dl className="mt-2 grid grid-cols-1 gap-x-6 gap-y-1.5 sm:grid-cols-[auto_1fr]">
        {block.rows.map((r) => (
          <div key={r.label} className="contents">
            <dt className="text-[13px] text-muted">{r.label}</dt>
            <dd className="text-[13.5px] font-medium text-ink">{r.value}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}

export function ItemList({ block }: { block: Pick<AssistantListBlock, 'title' | 'items' | 'empty_text'> }) {
  return (
    <div className="mt-3" data-testid="block-list">
      {block.title && <p className="mb-1.5 text-sm font-semibold text-ink">{block.title}</p>}
      {block.items.length === 0 ? (
        <p className="rounded-lg bg-surface-2 px-3 py-2.5 text-sm text-muted">{block.empty_text}</p>
      ) : (
        <ul className="divide-y divide-line overflow-hidden rounded-xl border border-line bg-surface">
          {block.items.map((it) => (
            <li key={it.title + it.meta} className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1 px-3.5 py-2.5">
              <div className="min-w-0">
                <p className="text-[13.5px] font-medium text-ink">{it.title}</p>
                {it.meta && <p className="text-xs text-muted">{it.meta}</p>}
              </div>
              {it.tag && <Badge tone={/substitute|needs/i.test(it.tag) ? 'warning' : 'neutral'}>{it.tag}</Badge>}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function Note({ block }: { block: AssistantNoteBlock }) {
  return (
    <p className={`mt-3 flex items-start gap-2 rounded-lg px-3 py-2.5 text-sm ${block.tone === 'warning' ? 'bg-warning-soft text-warning' : 'bg-info-soft text-info'}`}>
      {block.tone === 'warning' ? <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" /> : <Users className="mt-0.5 size-4 shrink-0" aria-hidden="true" />}
      <span>{block.text}</span>
    </p>
  )
}

export function BlockView({ block }: { block: AssistantBlock }) {
  switch (block.type) {
    case 'free_teachers':
      return <FreeTeachers block={block} />
    case 'timetable':
      return <Timetable block={block} />
    case 'facts':
      return <Facts block={block} />
    case 'list':
      return <ItemList block={block} />
    case 'note':
      return <Note block={block} />
    default:
      return null
  }
}
