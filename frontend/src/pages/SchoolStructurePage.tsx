import { Building2, Lock, Network } from 'lucide-react'
import { Alert } from '../components/Alert'
import { Badge } from '../components/Badge'
import { DataTable } from '../components/DataTable'
import { EmptyState, PageHeader, Panel, Skeleton } from '../components/Panel'
import { runningSemesters, useTerm } from '../lib/term'
import type { StructureTree } from '../lib/types'
import { useApi } from '../lib/useApi'

/** The HOD's read-only view of his own school: courses and semesters (the administrator edits them). */
export default function SchoolStructurePage() {
  const { data, error, loading } = useApi<StructureTree>('/structure/tree')
  const { active } = useTerm()
  const school = data?.schools[0]

  return (
    <>
      <PageHeader
        title="School Structure"
        subtitle="The courses and semesters of your school. A “class” is a course plus a semester - for example B.Sc. IT Sem 3."
      />
      <Alert tone="info" className="mb-6">
        <span className="flex items-center gap-2">
          <Lock className="size-4 shrink-0" aria-hidden="true" />
          This page is read-only. Courses and semesters are managed by the administrator in College Setup.
        </span>
      </Alert>

      {error ? (
        <Alert tone="danger">{error.message}</Alert>
      ) : loading && !data ? (
        <Skeleton className="h-64" />
      ) : !school ? (
        <Panel title="Your school" icon={Building2}>
          <EmptyState icon={Building2} title="Your account is not linked to a school">
            Please ask the administrator to link your account to your school.
          </EmptyState>
        </Panel>
      ) : (
        <div className="space-y-6">
          <Panel title={school.name} icon={Building2}>
            <div className="flex flex-wrap items-center gap-2.5">
              <Badge tone={school.is_active ? 'success' : 'neutral'}>{school.is_active ? 'Active' : 'Inactive'}</Badge>
              <Badge tone="info">
                {school.teachers} {school.teachers === 1 ? 'teacher' : 'teachers'}
              </Badge>
              {school.hod_names.length > 0 && <Badge tone="brand">HOD: {school.hod_names.join(', ')}</Badge>}
              <Badge>{school.courses.length} {school.courses.length === 1 ? 'course' : 'courses'}</Badge>
            </div>
            <p className="mt-3 text-sm text-muted">
              {active ? (
                <>
                  Active term: <span className="font-semibold text-ink">{active.name}</span> - {runningSemesters(active.kind)} run (highlighted below).
                </>
              ) : (
                'No term is active yet - choose one on the Active Term screen.'
              )}
            </p>
          </Panel>

          <Panel title="Courses and semesters" icon={Network}>
            <DataTable
              caption="Courses of your school"
              rows={school.courses}
              rowKey={(c) => c.id}
              empty="No courses have been added to your school yet."
              columns={[
                {
                  key: 'name',
                  header: 'Course',
                  cell: (c) => (
                    <div>
                      <span className="font-medium text-ink">{c.name}</span>
                      {c.code && <span className="ml-2 text-xs text-muted">{c.code}</span>}
                    </div>
                  ),
                },
                { key: 'count', header: 'Semesters', hideBelow: 'sm', className: 'tabular-nums', cell: (c) => c.num_semesters },
                {
                  key: 'sems',
                  header: 'Classes',
                  cell: (c) => (
                    <div className="flex flex-wrap gap-1">
                      {c.semesters.map((s) => (
                        <span
                          key={s.id}
                          title={s.running ? 'Runs in the active term' : 'Does not run in the active term'}
                          className={`rounded-md px-1.5 py-0.5 text-xs font-medium tabular-nums ${s.running ? 'bg-brand-soft text-brand-text' : 'bg-surface-2 text-subtle ring-1 ring-line'}`}
                        >
                          Sem {s.number}
                        </span>
                      ))}
                    </div>
                  ),
                },
                { key: 'status', header: 'Status', hideBelow: 'sm', cell: (c) => <Badge tone={c.is_active ? 'success' : 'neutral'}>{c.is_active ? 'Active' : 'Inactive'}</Badge> },
              ]}
            />
          </Panel>
        </div>
      )}
    </>
  )
}
