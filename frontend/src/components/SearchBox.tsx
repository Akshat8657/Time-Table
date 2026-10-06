import { Search, X } from 'lucide-react'

/** The search field above every list. */
export function SearchBox({
  value,
  onChange,
  placeholder = 'Search',
  className = '',
}: {
  value: string
  onChange: (value: string) => void
  placeholder?: string
  className?: string
}) {
  return (
    <div className={`relative ${className}`}>
      <Search className="pointer-events-none absolute left-3.5 top-1/2 size-[18px] -translate-y-1/2 text-subtle" aria-hidden="true" />
      <input
        type="search"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        aria-label={placeholder}
        maxLength={80}
        className="block h-10 w-full rounded-xl border border-line-strong bg-surface pl-10 pr-9 text-sm text-ink placeholder:text-subtle transition-colors focus:border-brand focus:outline-none focus:ring-4 focus:ring-[color:var(--brand-ring)] [&::-webkit-search-cancel-button]:hidden"
      />
      {value && (
        <button
          type="button"
          onClick={() => onChange('')}
          aria-label="Clear search"
          className="absolute right-2 top-1/2 -translate-y-1/2 rounded-md p-1 text-subtle hover:bg-hover hover:text-ink"
        >
          <X className="size-4" />
        </button>
      )}
    </div>
  )
}
