import { useMemo, useState } from 'react'
import { Checkbox } from './Field'
import { SearchBox } from './SearchBox'

export interface PickableSubject {
  id: string
  name: string
  code: string
  /** "B.Sc. IT - Sem 5" */
  group: string
  /** false = this class does not run in the active term (kept as it is, but not shown) */
  running?: boolean
}

/**
 * "Subjects this person can teach". Only the classes of the ACTIVE term are listed;
 * subjects already chosen in a class that does not run right now stay selected (they are just not shown).
 */
export function SubjectPicker({
  subjects,
  value,
  onChange,
  disabled = false,
  error,
}: {
  subjects: PickableSubject[]
  value: string[]
  onChange: (ids: string[]) => void
  disabled?: boolean
  error?: string | null
}) {
  const [query, setQuery] = useState('')
  const visible = useMemo(() => subjects.filter((s) => s.running !== false), [subjects])
  const visibleIds = useMemo(() => new Set(visible.map((s) => s.id)), [visible])
  const hiddenSelected = useMemo(() => value.filter((id) => !visibleIds.has(id)), [value, visibleIds])
  const chosen = useMemo(() => new Set(value), [value])

  const groups = useMemo(() => {
    const q = query.trim().toLowerCase()
    const out = new Map<string, PickableSubject[]>()
    for (const s of visible) {
      if (q && !`${s.name} ${s.code} ${s.group}`.toLowerCase().includes(q)) continue
      out.set(s.group, [...(out.get(s.group) ?? []), s])
    }
    return [...out.entries()]
  }, [visible, query])

  function toggle(id: string, on: boolean) {
    const next = new Set(value.filter((v) => visibleIds.has(v)))
    if (on) next.add(id)
    else next.delete(id)
    onChange([...hiddenSelected, ...next])
  }

  return (
    <div>
      <div className="mb-2 flex items-center justify-between gap-3">
        <span className="text-[13px] font-medium text-ink">Subjects they can teach</span>
        <span className="text-xs text-muted tabular-nums">{value.length} selected</span>
      </div>
      <div className={`rounded-xl border ${error ? 'border-danger-line' : 'border-line-strong'} bg-surface`}>
        <div className="border-b border-line p-2.5">
          <SearchBox value={query} onChange={setQuery} placeholder="Search subjects" />
        </div>
        <div className="max-h-64 overflow-y-auto p-2.5">
          {visible.length === 0 ? (
            <p className="px-1 py-4 text-center text-sm text-muted">No subjects yet for the classes of the active term. Add them on the Subjects screen first.</p>
          ) : groups.length === 0 ? (
            <p className="px-1 py-4 text-center text-sm text-muted">No subject matches “{query}”.</p>
          ) : (
            groups.map(([group, list]) => (
              <fieldset key={group} className="mb-3 last:mb-0">
                <legend className="mb-1.5 px-1 text-[11px] font-semibold uppercase tracking-wider text-subtle">{group}</legend>
                <div className="grid gap-1.5 sm:grid-cols-2">
                  {list.map((s) => (
                    <div key={s.id} className="rounded-lg px-2 py-1.5 hover:bg-hover">
                      <Checkbox
                        checked={chosen.has(s.id)}
                        disabled={disabled}
                        onChange={(e) => toggle(s.id, e.target.checked)}
                        label={s.name}
                        description={s.code}
                      />
                    </div>
                  ))}
                </div>
              </fieldset>
            ))
          )}
        </div>
      </div>
      {error && <p className="mt-1.5 text-[13px] text-danger">{error}</p>}
      {hiddenSelected.length > 0 && (
        <p className="mt-1.5 text-xs text-muted">
          {hiddenSelected.length} more subject(s) from classes that do not run in the active term stay saved.
        </p>
      )}
    </div>
  )
}
