import type { ReactNode } from 'react'

export interface TabItem<T extends string> {
  value: T
  label: string
  count?: number
  icon?: ReactNode
}

/** A row of tabs (segmented control). `value` is the open tab. */
export function Tabs<T extends string>({
  items,
  value,
  onChange,
  label,
}: {
  items: TabItem<T>[]
  value: T
  onChange: (value: T) => void
  label: string
}) {
  return (
    <div role="tablist" aria-label={label} className="inline-flex max-w-full flex-wrap gap-1 rounded-xl bg-surface-2 p-1 ring-1 ring-line">
      {items.map((item) => {
        const active = item.value === value
        return (
          <button
            key={item.value}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(item.value)}
            className={`inline-flex items-center gap-2 rounded-lg px-3.5 py-1.5 text-sm font-medium transition-colors ${
              active ? 'bg-surface text-ink shadow-sm ring-1 ring-line' : 'text-muted hover:text-ink'
            }`}
          >
            {item.icon}
            {item.label}
            {item.count !== undefined && (
              <span className={`rounded-full px-1.5 text-xs tabular-nums ${active ? 'bg-brand-soft text-brand-text' : 'bg-line text-muted'}`}>
                {item.count}
              </span>
            )}
          </button>
        )
      })}
    </div>
  )
}
