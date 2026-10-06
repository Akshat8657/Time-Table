import type { ReactNode } from 'react'
import { Building2 } from 'lucide-react'
import { useSchoolScope } from '../lib/scope'
import { Badge } from './Badge'
import { SelectField } from './Select'

/**
 * Top of every school screen. The administrator chooses the school here;
 * an HOD just sees his own school (he cannot choose another one).
 * `children` = extra controls on the right (filters, buttons).
 */
export function SchoolBar({ children }: { children?: ReactNode }) {
  const scope = useSchoolScope()
  return (
    <div className="mb-5 flex flex-wrap items-center gap-3 rounded-2xl border border-line bg-surface px-4 py-3 shadow-card">
      <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-brand-soft text-brand-text">
        <Building2 className="size-[18px]" aria-hidden="true" />
      </span>
      {scope.isAdmin ? (
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-3 sm:flex-none">
          <span className="text-sm font-medium text-muted">School</span>
          <SelectField
            compact
            aria-label="School"
            className="w-56 max-w-full"
            value={scope.schoolId ?? ''}
            onChange={scope.setSchoolId}
            options={scope.schools.map((s) => ({ value: s.id, label: s.is_active ? s.name : `${s.name} (inactive)` }))}
          />
        </div>
      ) : (
        <div className="flex min-w-0 flex-1 items-center gap-2 sm:flex-none">
          <p className="truncate text-sm font-semibold text-ink">{scope.schoolName ?? 'My school'}</p>
          <Badge tone="brand">Your school</Badge>
        </div>
      )}
      {children && <div className="ml-auto flex flex-wrap items-center gap-2">{children}</div>}
    </div>
  )
}
