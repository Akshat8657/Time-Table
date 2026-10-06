import type { ReactNode } from 'react'

export interface Column<T> {
  key: string
  header: ReactNode
  cell: (row: T) => ReactNode
  /** extra classes for the column (width, alignment) */
  className?: string
  /** hide on small screens */
  hideBelow?: 'sm' | 'md' | 'lg'
}

const HIDE = { sm: 'hidden sm:table-cell', md: 'hidden md:table-cell', lg: 'hidden lg:table-cell' } as const

/** A plain, readable table. Scrolls sideways on a phone instead of breaking the page. */
export function DataTable<T>({
  columns,
  rows,
  rowKey,
  empty,
  caption,
  dense = false,
  rowClass,
  card,
}: {
  columns: Column<T>[]
  rows: T[]
  rowKey: (row: T) => string
  empty?: ReactNode
  caption: string
  dense?: boolean
  rowClass?: (row: T) => string
  /** how a row looks on a phone: the table is replaced by a list of cards (no sideways scrolling) */
  card?: (row: T) => ReactNode
}) {
  return (
    <>
      {card && (
        <ul className="space-y-2.5 sm:hidden">
          {rows.length === 0 ? (
            <li className="rounded-xl border border-line px-4 py-8 text-center text-sm text-muted">{empty ?? 'Nothing to show.'}</li>
          ) : (
            rows.map((row) => (
              <li key={rowKey(row)} className={`rounded-xl border border-line bg-surface p-3.5 ${rowClass?.(row) ?? ''}`}>
                {card(row)}
              </li>
            ))
          )}
        </ul>
      )}
    <div className={`relative overflow-x-auto rounded-xl border border-line ${card ? 'hidden sm:block' : ''}`}>
      <table className="w-full min-w-[34rem] border-collapse text-left text-sm">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr className="border-b border-line bg-surface-2 text-xs font-semibold uppercase tracking-wide text-muted">
            {columns.map((c) => (
              <th key={c.key} scope="col" className={`px-4 py-2.5 ${c.hideBelow ? HIDE[c.hideBelow] : ''} ${c.className ?? ''}`}>
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {rows.length === 0 ? (
            <tr>
              <td colSpan={columns.length} className="px-4 py-10 text-center text-muted">
                {empty ?? 'Nothing to show.'}
              </td>
            </tr>
          ) : (
            rows.map((row) => (
              <tr key={rowKey(row)} className={`transition-colors hover:bg-hover/60 ${rowClass?.(row) ?? ''}`}>
                {columns.map((c) => (
                  <td key={c.key} className={`${dense ? 'px-4 py-2' : 'px-4 py-3'} align-middle ${c.hideBelow ? HIDE[c.hideBelow] : ''} ${c.className ?? ''}`}>
                    {c.cell(row)}
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
    </>
  )
}
