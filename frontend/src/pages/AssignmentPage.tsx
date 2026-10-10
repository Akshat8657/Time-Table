import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Gauge, Link2, TriangleAlert } from 'lucide-react'
import { useAuth } from '../auth/AuthProvider'
import { Alert } from '../components/Alert'
import { Badge } from '../components/Badge'
import { DataTable } from '../components/DataTable'
import { EmptyState, PageHeader, Panel, Skeleton } from '../components/Panel'
import { SchoolBar } from '../components/SchoolBar'
import { SearchBox } from '../components/SearchBox'
import { SelectField } from '../components/Select'
import { useToast } from '../components/Toast'
import { api, ApiError } from '../lib/api'
import { dayList, queryString, useDebounced } from '../lib/helpers'
import { ROLE_PREFIX } from '../lib/nav'
import { useSchoolScope } from '../lib/scope'
import { useTerm } from '../lib/term'
import type { AssignmentSubject, AssignmentTeacher, StructureTree } from '../lib/types'
import { useApi } from '../lib/useApi'

interface Payload {
  subjects: AssignmentSubject[]
  teachers: AssignmentTeacher[]
}

/** Teacher <-> subject (<-> class) for the active term. Only teachers who can teach a subject are offered for it. */
export default function AssignmentPage() {
  const { user } = useAuth()
  const scope = useSchoolScope()
  const toast = useToast()
  const { active, ready: termReady } = useTerm()
  const [classId, setClassId] = useState('all')
  const [search, setSearch] = useState('')
  const q = useDebounced(search)
  const [saving, setSaving] = useState<string | null>(null)

  const enabled = scope.ready && !!scope.schoolId && !!active
  const tree = useApi<StructureTree>(enabled ? scope.path('/structure/tree') : null)
  const classes = useMemo(
    () =>
      (tree.data?.schools[0]?.courses ?? [])
        .filter((c) => c.is_active)
        .flatMap((c) => c.semesters.filter((s) => s.running).map((s) => ({ id: s.id, label: `${c.name} - Sem ${s.number}` }))),
    [tree.data],
  )
  useEffect(() => {
    if (classId !== 'all' && classes.length > 0 && !classes.some((c) => c.id === classId)) setClassId('all')
  }, [classes, classId])

  const { data, error, loading, reload } = useApi<Payload>(enabled ? scope.path('/assignments' + queryString({ semester_id: classId, q })) : null)
  const subjects = data?.subjects ?? []
  const teachers = data?.teachers ?? []

  async function assign(subject: AssignmentSubject, teacherId: string, coId: string) {
    setSaving(subject.id)
    try {
      if (!teacherId) {
        if (subject.assignment_id) await api(`/assignments/${subject.assignment_id}`, { method: 'DELETE' })
        toast.success(`${subject.name}: teacher removed.`)
      } else {
        const res = await api<{ warnings: string[] }>('/assignments', {
          method: 'PUT',
          body: { subject_id: subject.id, teacher_id: teacherId, co_teacher_id: coId || null },
        })
        toast.success(`${subject.name} saved.`)
        res.warnings.forEach((w) => toast.warning(w))
      }
      reload()
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Could not save. Please try again.')
    } finally {
      setSaving(null)
    }
  }

  if (termReady && !active) {
    return (
      <>
        <PageHeader title="Assignment" subtitle="Which teacher teaches which subject in which class." />
        <Alert tone="warning" title="Choose the active term first">
          Assignments are made for the classes of the running term. <Link to={user ? `${ROLE_PREFIX[user.role]}/active-term` : '#'} className="font-semibold underline">Open Active Term</Link> and pick one.
        </Alert>
      </>
    )
  }

  const teacherLink = user ? `${ROLE_PREFIX[user.role]}/teachers` : '#'

  return (
    <>
      <PageHeader
        title="Assignment"
        subtitle={
          <>
            Choose the teacher of every subject for <span className="font-semibold text-ink">{active?.name ?? '…'}</span>. Only teachers who are allowed to teach a subject are offered for it. A shared subject can also have a co-teacher - both appear on the timetable.
          </>
        }
      />
      <SchoolBar />

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <SelectField compact aria-label="Class" className="w-56" value={classId} onChange={setClassId} options={[{ value: 'all', label: 'All classes' }, ...classes.map((c) => ({ value: c.id, label: c.label }))]} />
        <SearchBox value={search} onChange={setSearch} placeholder="Search subject, code or teacher" className="w-full sm:w-72" />
        {data && (
          <span className="ml-auto text-[13px] text-muted tabular-nums" aria-live="polite">
            {subjects.filter((s) => s.teacher_id).length} of {subjects.length} subjects have a teacher
          </span>
        )}
      </div>

      <div className="grid gap-6 xl:grid-cols-3">
        <Panel title="Subjects and teachers" icon={Link2} className="xl:col-span-2">
          {error ? (
            <Alert tone="danger">{error.message}</Alert>
          ) : loading && !data ? (
            <div className="space-y-2">
              <Skeleton className="h-14" />
              <Skeleton className="h-14" />
              <Skeleton className="h-14" />
            </div>
          ) : data && subjects.length === 0 && !search && classId === 'all' ? (
            <EmptyState icon={Link2} title="No subjects to assign yet">
              Add the subjects of the running classes first on the Subjects & Credits screen.
            </EmptyState>
          ) : (
            <div className={loading ? 'opacity-60 transition-opacity' : 'transition-opacity'}>
              <DataTable
                caption="Teacher of every subject"
                rows={subjects}
                rowKey={(s) => s.id}
                empty="No subject matches."
                card={(s) => (
                  <div className="space-y-2.5">
                    <div>
                      <p className="font-medium text-ink">{s.name}</p>
                      <p className="text-[13px] text-muted">{s.class_label} · {s.code} · {s.credits} cr</p>
                      {s.is_elective && <Badge tone="brand" className="mt-1">Elective{s.elective_group_name ? ` · ${s.elective_group_name}` : ''}</Badge>}
                    </div>
                    {s.eligible_teachers.length === 0 && !s.teacher_id ? (
                      <p className="flex items-start gap-2 text-[13px] text-warning">
                        <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                        <span>Nobody can teach this yet. Add it to a teacher&apos;s “subjects they can teach” on the Teachers screen.</span>
                      </p>
                    ) : (
                      <>
                        <SelectField
                          compact
                          aria-label={`Teacher of ${s.name}`}
                          value={s.teacher_id ?? ''}
                          disabled={saving === s.id}
                          placeholder="Choose a teacher"
                          onChange={(v) => void assign(s, v, v === s.co_teacher_id ? '' : (s.co_teacher_id ?? ''))}
                          options={s.eligible_teachers.map((t) => ({ value: t.id, label: t.name }))}
                        />
                        <SelectField
                          compact
                          aria-label={`Co-teacher of ${s.name}`}
                          value={s.co_teacher_id ?? ''}
                          disabled={!s.teacher_id || saving === s.id}
                          placeholder="No co-teacher"
                          onChange={(v) => s.teacher_id && void assign(s, s.teacher_id, v)}
                          options={s.eligible_teachers.filter((t) => t.id !== s.teacher_id).map((t) => ({ value: t.id, label: t.name }))}
                        />
                      </>
                    )}
                  </div>
                )}
                columns={[
                  {
                    key: 'subject',
                    header: 'Subject',
                    cell: (s) => (
                      <div className="min-w-0">
                        <p className="font-medium text-ink">{s.name}</p>
                        <p className="text-[13px] text-muted">
                          {s.class_label} · {s.code} · {s.credits} cr
                        </p>
                        {s.is_elective && <Badge tone="brand" className="mt-1">Elective{s.elective_group_name ? ` · ${s.elective_group_name}` : ''}</Badge>}
                      </div>
                    ),
                  },
                  {
                    key: 'teacher',
                    header: 'Teacher',
                    className: 'min-w-[11rem]',
                    cell: (s) =>
                      s.eligible_teachers.length === 0 && !s.teacher_id ? (
                        <div className="flex items-start gap-2 text-[13px] text-warning">
                          <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                          <span>
                            Nobody can teach this yet. Add it to a teacher&apos;s “subjects they can teach” on the{' '}
                            <Link to={teacherLink} className="font-semibold underline">Teachers</Link> screen.
                          </span>
                        </div>
                      ) : (
                        <SelectField
                          compact
                          aria-label={`Teacher of ${s.name}`}
                          value={s.teacher_id ?? ''}
                          disabled={saving === s.id}
                          placeholder="Choose a teacher"
                          onChange={(v) => void assign(s, v, v === s.co_teacher_id ? '' : (s.co_teacher_id ?? ''))}
                          options={s.eligible_teachers.map((t) => ({ value: t.id, label: t.name }))}
                        />
                      ),
                  },
                  {
                    key: 'co',
                    header: 'Co-teacher (optional)',
                    hideBelow: 'md',
                    className: 'min-w-[11rem]',
                    cell: (s) => (
                      <SelectField
                        compact
                        aria-label={`Co-teacher of ${s.name}`}
                        value={s.co_teacher_id ?? ''}
                        disabled={!s.teacher_id || saving === s.id}
                        placeholder="No co-teacher"
                        onChange={(v) => s.teacher_id && void assign(s, s.teacher_id, v)}
                        options={s.eligible_teachers.filter((t) => t.id !== s.teacher_id).map((t) => ({ value: t.id, label: t.name }))}
                      />
                    ),
                  },
                ]}
              />
            </div>
          )}
        </Panel>

        <Panel title="Teacher workload" icon={Gauge}>
          <p className="mb-3 text-[13px] text-muted">Lectures per week = the weekly lectures of the subjects they teach (credits unless changed) + their locked slots (PE / SOL are not counted).</p>
          {loading && !data ? (
            <Skeleton className="h-40" />
          ) : teachers.length === 0 ? (
            <p className="text-sm text-muted">No teachers yet.</p>
          ) : (
            <ul className="space-y-3">
              {teachers.map((t) => {
                const limit = t.is_visiting ? t.max_weekly_hours : null
                const over = limit !== null && t.weekly_load > limit
                const pct = limit ? Math.min(100, Math.round((t.weekly_load / limit) * 100)) : Math.min(100, Math.round((t.weekly_load / 24) * 100))
                return (
                  <li key={t.id}>
                    <div className="flex items-center justify-between gap-2 text-sm">
                      <span className="min-w-0 truncate font-medium text-ink">
                        {t.name}
                        {t.role === 'hod' && <span className="ml-1.5 text-xs font-normal text-muted">(HOD)</span>}
                      </span>
                      <span className={`shrink-0 tabular-nums ${over ? 'font-semibold text-danger' : 'text-muted'}`}>
                        {t.weekly_load}
                        {limit ? ` / ${limit}` : ''} h
                      </span>
                    </div>
                    <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-surface-2 ring-1 ring-line" role="presentation">
                      <div className={`h-full rounded-full ${over ? 'bg-danger-solid' : 'bg-accent'}`} style={{ width: `${pct}%` }} />
                    </div>
                    {t.is_visiting && (
                      <p className="mt-1 text-xs text-muted">
                        Visiting · {dayList(t.visiting_days)} · up to {t.max_weekly_hours} h
                      </p>
                    )}
                  </li>
                )
              })}
            </ul>
          )}
        </Panel>
      </div>
    </>
  )
}
