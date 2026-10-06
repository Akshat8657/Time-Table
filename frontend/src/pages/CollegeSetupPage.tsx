import { useMemo, useState, type FormEvent } from 'react'
import { Building2, Pencil, Plus, Power, Trash2, UserPlus } from 'lucide-react'
import { AccountDialog } from '../components/AccountDialog'
import { Alert } from '../components/Alert'
import { Badge } from '../components/Badge'
import { Button } from '../components/Button'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { CredentialsDialog } from '../components/CredentialsDialog'
import { DataTable } from '../components/DataTable'
import { Checkbox, TextField } from '../components/Field'
import { Modal } from '../components/Modal'
import { EmptyState, PageHeader, Panel, Skeleton } from '../components/Panel'
import { SearchBox } from '../components/SearchBox'
import { SelectField } from '../components/Select'
import { api, ApiError } from '../lib/api'
import type { CourseInfo, CreatedAccount, SchoolTree, StructureTree } from '../lib/types'
import { useSchoolScope } from '../lib/scope'
import { useAction } from '../lib/useAction'
import { useApi } from '../lib/useApi'

type SchoolDialog = { mode: 'create' } | { mode: 'rename'; school: SchoolTree } | null
type CourseDialog = { mode: 'create'; school: SchoolTree } | { mode: 'edit'; school: SchoolTree; course: CourseInfo } | null

function SchoolForm({ dialog, onClose, onDone }: { dialog: SchoolDialog; onClose: () => void; onDone: () => void }) {
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [lastKey, setLastKey] = useState<string | null>(null)

  // start with the current name each time the dialog opens
  const key = dialog ? (dialog.mode === 'rename' ? dialog.school.id : 'new') : null
  if (key !== lastKey) {
    setLastKey(key)
    setName(dialog?.mode === 'rename' ? dialog.school.name : '')
    setError(null)
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!dialog) return
    setBusy(true)
    setError(null)
    try {
      if (dialog.mode === 'create') await api('/structure/schools', { method: 'POST', body: { name } })
      else await api(`/structure/schools/${dialog.school.id}`, { method: 'PATCH', body: { name } })
      onDone()
      onClose()
    } catch (err) {
      setError(err instanceof ApiError ? (err.fields.name ?? err.message) : 'Could not save. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      open={dialog !== null}
      onClose={() => !busy && onClose()}
      size="sm"
      title={dialog?.mode === 'rename' ? 'Rename school' : 'Add a school'}
      description={dialog?.mode === 'create' ? 'A new school starts with the standard lunch time. Its HOD account is created next.' : undefined}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button type="submit" form="school-form" loading={busy}>
            {dialog?.mode === 'rename' ? 'Save' : 'Add school'}
          </Button>
        </>
      }
    >
      <form id="school-form" onSubmit={submit} noValidate>
        <TextField label="School name" value={name} onChange={(e) => setName(e.target.value)} maxLength={120} error={error} placeholder="For example: School of Business" required />
      </form>
    </Modal>
  )
}

function CourseForm({ dialog, onClose, onDone }: { dialog: CourseDialog; onClose: () => void; onDone: () => void }) {
  const [form, setForm] = useState({ name: '', code: '', num: '3', active: true })
  const [busy, setBusy] = useState(false)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [top, setTop] = useState<string | null>(null)
  const [lastKey, setLastKey] = useState<string | null>(null)

  const key = dialog ? (dialog.mode === 'edit' ? dialog.course.id : `new-${dialog.school.id}`) : null
  if (key !== lastKey) {
    setLastKey(key)
    setErrors({})
    setTop(null)
    if (dialog?.mode === 'edit') {
      setForm({ name: dialog.course.name, code: dialog.course.code ?? '', num: String(dialog.course.num_semesters), active: dialog.course.is_active })
    } else setForm({ name: '', code: '', num: '3', active: true })
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!dialog) return
    setBusy(true)
    setErrors({})
    setTop(null)
    try {
      if (dialog.mode === 'create') {
        await api('/structure/courses', {
          method: 'POST',
          body: { school_id: dialog.school.id, name: form.name, code: form.code, num_semesters: Number(form.num) },
        })
      } else {
        await api(`/structure/courses/${dialog.course.id}`, {
          method: 'PATCH',
          body: { name: form.name, code: form.code, num_semesters: Number(form.num), is_active: form.active },
        })
      }
      onDone()
      onClose()
    } catch (err) {
      if (err instanceof ApiError) {
        setErrors(err.fields)
        setTop(Object.keys(err.fields).length > 0 ? 'Please check the highlighted fields.' : err.message)
      } else setTop('Could not save. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      open={dialog !== null}
      onClose={() => !busy && onClose()}
      title={dialog?.mode === 'edit' ? `Edit ${dialog.course.name}` : `Add a course to ${dialog?.school.name ?? ''}`}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button type="submit" form="course-form" loading={busy}>
            {dialog?.mode === 'edit' ? 'Save changes' : 'Add course'}
          </Button>
        </>
      }
    >
      <form id="course-form" onSubmit={submit} noValidate className="space-y-4">
        {top && <Alert tone="danger">{top}</Alert>}
        <TextField label="Course name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} error={errors.name} placeholder="For example: B.Sc. IT" maxLength={120} required />
        <div className="grid gap-4 sm:grid-cols-2">
          <TextField label="Short code (optional)" value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value })} error={errors.code} maxLength={20} placeholder="BSCIT" />
          <SelectField
            label="Number of semesters"
            value={form.num}
            onChange={(v) => setForm({ ...form, num: v })}
            error={errors.num_semesters}
            hint={dialog?.mode === 'edit' ? 'Fewer semesters is only possible when the removed ones are empty.' : undefined}
            options={Array.from({ length: 12 }, (_, i) => ({ value: String(i + 1), label: `${i + 1} (Sem 1 to Sem ${i + 1})` }))}
          />
        </div>
        {dialog?.mode === 'edit' && (
          <Checkbox checked={form.active} onChange={(e) => setForm({ ...form, active: e.target.checked })} label="Active" description="An inactive course is hidden from the other screens but nothing is deleted." />
        )}
      </form>
    </Modal>
  )
}

export default function CollegeSetupPage() {
  const { data, error, loading, reload: reloadTree } = useApi<StructureTree>('/structure/tree')
  const scope = useSchoolScope()
  // every change of a school (or its HOD) must also refresh the school list used by the other screens
  const reload = () => {
    reloadTree()
    void scope.refreshSchools()
  }
  const { run } = useAction()
  const [search, setSearch] = useState('')
  const [schoolDialog, setSchoolDialog] = useState<SchoolDialog>(null)
  const [courseDialog, setCourseDialog] = useState<CourseDialog>(null)
  const [hodFor, setHodFor] = useState<SchoolTree | null>(null)
  const [credentials, setCredentials] = useState<CreatedAccount | null>(null)
  const [deleting, setDeleting] = useState<{ school: SchoolTree; course?: CourseInfo } | null>(null)

  const schools = useMemo(() => {
    const q = search.trim().toLowerCase()
    if (!data) return []
    if (!q) return data.schools
    return data.schools
      .map((s) => (s.name.toLowerCase().includes(q) ? s : { ...s, courses: s.courses.filter((c) => `${c.name} ${c.code ?? ''}`.toLowerCase().includes(q)) }))
      .filter((s) => s.name.toLowerCase().includes(q) || s.courses.length > 0)
  }, [data, search])

  return (
    <>
      <PageHeader
        title="College Setup"
        subtitle="Schools, their courses and semesters, and the HOD of each school. A “class” is a course plus a semester - for example B.Sc. IT Sem 3."
      />

      <div className="mb-5 flex flex-wrap items-center gap-3">
        <SearchBox value={search} onChange={setSearch} placeholder="Search schools or courses" className="w-full sm:w-80" />
        <Button className="sm:ml-auto" icon={<Plus className="size-4" />} onClick={() => setSchoolDialog({ mode: 'create' })}>
          Add school
        </Button>
      </div>

      {error ? (
        <Alert tone="danger">{error.message}</Alert>
      ) : loading && !data ? (
        <div className="space-y-4">
          <Skeleton className="h-52" />
          <Skeleton className="h-52" />
        </div>
      ) : schools.length === 0 ? (
        <Panel title="Schools" icon={Building2}>
          <EmptyState icon={Building2} title={search ? 'Nothing matches your search' : 'No schools yet'}>
            {search ? 'Try a different word.' : 'Add the first school, then its courses and its HOD.'}
          </EmptyState>
        </Panel>
      ) : (
        <div className="space-y-6">
          {schools.map((school) => (
            <Panel
              key={school.id}
              title={school.name}
              icon={Building2}
              action={
                <div className="flex flex-wrap items-center gap-1.5">
                  <Button variant="ghost" size="sm" icon={<Pencil className="size-4" />} onClick={() => setSchoolDialog({ mode: 'rename', school })}>
                    Rename
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    icon={<Power className="size-4" />}
                    onClick={() => void run(async () => { await api(`/structure/schools/${school.id}`, { method: 'PATCH', body: { is_active: !school.is_active } }); reload() }, school.is_active ? 'School deactivated.' : 'School activated.')}
                  >
                    {school.is_active ? 'Deactivate' : 'Activate'}
                  </Button>
                  <Button variant="ghost" size="sm" icon={<Trash2 className="size-4" />} onClick={() => setDeleting({ school })}>
                    Delete
                  </Button>
                </div>
              }
            >
              <div className="mb-4 flex flex-wrap items-center gap-2.5">
                <Badge tone={school.is_active ? 'success' : 'neutral'}>{school.is_active ? 'Active' : 'Inactive'}</Badge>
                <Badge tone="info">
                  {school.teachers} {school.teachers === 1 ? 'teacher' : 'teachers'}
                </Badge>
                {school.hod_names.length > 0 ? (
                  <Badge tone="brand">HOD: {school.hod_names.join(', ')}</Badge>
                ) : (
                  <Badge tone="warning">No HOD yet</Badge>
                )}
                <Button size="sm" variant="secondary" className="sm:ml-auto" icon={<UserPlus className="size-4" />} onClick={() => setHodFor(school)} disabled={!school.is_active}>
                  Create HOD account
                </Button>
              </div>

              <DataTable
                caption={`Courses of ${school.name}`}
                rows={school.courses}
                rowKey={(c) => c.id}
                empty="No courses yet. Add the first course below."
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
                  {
                    key: 'sems',
                    header: 'Semesters',
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
                  {
                    key: 'actions',
                    header: <span className="sr-only">Actions</span>,
                    className: 'w-28 text-right',
                    cell: (c) => (
                      <div className="flex justify-end gap-1">
                        <Button variant="ghost" size="sm" aria-label={`Edit ${c.name}`} icon={<Pencil className="size-4" />} onClick={() => setCourseDialog({ mode: 'edit', school, course: c })} />
                        <Button variant="ghost" size="sm" aria-label={`Delete ${c.name}`} icon={<Trash2 className="size-4" />} onClick={() => setDeleting({ school, course: c })} />
                      </div>
                    ),
                  },
                ]}
              />
              <div className="mt-4">
                <Button variant="secondary" size="sm" icon={<Plus className="size-4" />} onClick={() => setCourseDialog({ mode: 'create', school })}>
                  Add course
                </Button>
              </div>
            </Panel>
          ))}
        </div>
      )}

      <SchoolForm dialog={schoolDialog} onClose={() => setSchoolDialog(null)} onDone={reload} />
      <CourseForm dialog={courseDialog} onClose={() => setCourseDialog(null)} onDone={reload} />

      <AccountDialog
        open={hodFor !== null}
        account={null}
        defaultRole="hod"
        presetSchoolId={hodFor?.id}
        onClose={() => setHodFor(null)}
        onSaved={(created) => {
          reload()
          if (created) setCredentials(created)
        }}
      />
      <CredentialsDialog
        open={credentials !== null}
        title="HOD account created"
        items={credentials ? [{ name: credentials.user.full_name, email: credentials.user.email, temp_password: credentials.temp_password }] : []}
        onClose={() => setCredentials(null)}
      />

      <ConfirmDialog
        open={deleting !== null}
        tone="danger"
        title={deleting?.course ? `Delete ${deleting.course.name}?` : `Delete ${deleting?.school.name}?`}
        confirmLabel="Delete"
        onClose={() => setDeleting(null)}
        onConfirm={async () => {
          if (!deleting) return
          await api(deleting.course ? `/structure/courses/${deleting.course.id}` : `/structure/schools/${deleting.school.id}`, { method: 'DELETE' })
          reload()
        }}
      >
        {deleting?.course ? (
          <p>The course and its empty semesters are removed. A course that already has subjects cannot be deleted - make it inactive instead.</p>
        ) : (
          <p>A school can only be deleted while it has no courses and no accounts. Otherwise make it inactive.</p>
        )}
      </ConfirmDialog>
    </>
  )
}
