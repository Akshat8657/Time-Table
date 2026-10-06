import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { BookOpen, FileSpreadsheet, Layers, Pencil, Plus, Trash2 } from 'lucide-react'
import { useAuth } from '../auth/AuthProvider'
import { Alert } from '../components/Alert'
import { Badge } from '../components/Badge'
import { Button } from '../components/Button'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { DataTable } from '../components/DataTable'
import { Checkbox, TextField } from '../components/Field'
import { ImportDialog } from '../components/ImportDialog'
import { Modal } from '../components/Modal'
import { EmptyState, PageHeader, Panel, Skeleton } from '../components/Panel'
import { SchoolBar } from '../components/SchoolBar'
import { SearchBox } from '../components/SearchBox'
import { SelectField } from '../components/Select'
import { Tabs } from '../components/Tabs'
import { useToast } from '../components/Toast'
import { api, ApiError } from '../lib/api'
import { queryString, useDebounced } from '../lib/helpers'
import { ROLE_PREFIX } from '../lib/nav'
import { useSchoolScope } from '../lib/scope'
import { useTerm } from '../lib/term'
import type { ElectiveGroup, StructureTree, SubjectRow } from '../lib/types'
import { useApi } from '../lib/useApi'

interface ClassOption {
  id: string
  label: string
}

interface SubjectForm {
  semester_id: string
  name: string
  code: string
  credits: string
  semester_hours: string
  is_lab: boolean
  is_elective: boolean
  special_kind: '' | 'PE' | 'SOL'
  elective_group_id: string
}

const EMPTY: SubjectForm = {
  semester_id: '', name: '', code: '', credits: '3', semester_hours: '45', is_lab: false, is_elective: false, special_kind: '', elective_group_id: '',
}

function SubjectDialog({
  open, subject, classes, defaultClass, onClose, onSaved,
}: {
  open: boolean
  subject: SubjectRow | null
  classes: ClassOption[]
  defaultClass: string
  onClose: () => void
  onSaved: () => void
}) {
  const scope = useSchoolScope()
  const toast = useToast()
  const [form, setForm] = useState<SubjectForm>(EMPTY)
  const [busy, setBusy] = useState(false)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [top, setTop] = useState<string | null>(null)
  const set = <K extends keyof SubjectForm>(key: K, value: SubjectForm[K]) => setForm((f) => ({ ...f, [key]: value }))

  useEffect(() => {
    if (!open) return
    setErrors({})
    setTop(null)
    if (subject) {
      setForm({
        semester_id: subject.semester_id, name: subject.name, code: subject.code, credits: String(subject.credits),
        semester_hours: String(subject.semester_hours), is_lab: subject.is_lab, is_elective: subject.is_elective,
        special_kind: subject.special_kind ?? '', elective_group_id: subject.elective_group_id ?? '',
      })
    } else setForm({ ...EMPTY, semester_id: defaultClass || classes[0]?.id || '' })
  }, [open, subject, defaultClass, classes])

  const groupsPath = open && form.is_elective && form.semester_id ? scope.path(`/subjects/groups?semester_id=${form.semester_id}`) : null
  const groups = useApi<{ groups: ElectiveGroup[] }>(groupsPath)

  const special = form.special_kind !== ''

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setErrors({})
    setTop(null)
    const body = {
      name: form.name,
      code: form.code,
      credits: Number(form.credits),
      semester_hours: Number(form.semester_hours),
      is_lab: form.is_lab && !special,
      is_elective: form.is_elective && !special,
      special_kind: form.special_kind || null,
      elective_group_id: form.is_elective && !special && form.elective_group_id ? form.elective_group_id : null,
    }
    try {
      const res = subject
        ? await api<{ warnings: string[] }>(`/subjects/${subject.id}`, { method: 'PATCH', body })
        : await api<{ warnings: string[] }>('/subjects', { method: 'POST', body: { ...body, semester_id: form.semester_id } })
      toast.success(`${form.name} saved.`)
      res.warnings.forEach((w) => toast.warning(w))
      onSaved()
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
      open={open}
      onClose={() => !busy && onClose()}
      size="lg"
      title={subject ? `Edit ${subject.name}` : 'Add a subject'}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button type="submit" form="subject-form" loading={busy}>
            {subject ? 'Save changes' : 'Add subject'}
          </Button>
        </>
      }
    >
      <form id="subject-form" onSubmit={submit} noValidate className="space-y-5">
        {top && <Alert tone="danger">{top}</Alert>}
        <div className="grid gap-4 sm:grid-cols-2">
          <SelectField
            label="Class"
            className="sm:col-span-2"
            value={form.semester_id}
            onChange={(v) => setForm((f) => ({ ...f, semester_id: v, elective_group_id: '' }))}
            error={errors.semester_id}
            disabled={subject !== null}
            hint={subject ? 'A subject cannot be moved to another class.' : 'Only the classes that run in the active term are listed.'}
            options={classes.map((c) => ({ value: c.id, label: c.label }))}
          />
          <TextField label="Subject name" value={form.name} onChange={(e) => set('name', e.target.value)} error={errors.name} maxLength={150} required />
          <TextField label="Subject code" value={form.code} onChange={(e) => set('code', e.target.value)} error={errors.code} maxLength={30} hint="Must be unique in your school." required />
          <TextField
            label="Credits"
            type="number"
            min={1}
            max={30}
            value={form.credits}
            onChange={(e) => set('credits', e.target.value)}
            error={errors.credits}
            hint={special ? 'For PE and SOL the credits do not decide the lectures.' : 'Credits = the minimum number of lectures per week.'}
            required
          />
          <TextField
            label="Total hours in the semester"
            type="number"
            min={1}
            max={400}
            value={form.semester_hours}
            onChange={(e) => set('semester_hours', e.target.value)}
            error={errors.semester_hours}
            hint="These hours must be completed by the end of the semester."
            required
          />
        </div>

        <div className="space-y-4 rounded-xl border border-line bg-surface-2 p-4">
          <SelectField
            label="Special subject"
            value={form.special_kind}
            onChange={(v) => setForm((f) => ({ ...f, special_kind: v as SubjectForm['special_kind'], is_lab: false, is_elective: false, elective_group_id: '' }))}
            error={errors.special_kind}
            hint="PE (Physical Education) and SOL (Science of Living) are always a double block (2 periods back-to-back). Their weekly slots are fixed on the Fixed Slots screen."
            options={[
              { value: '', label: 'No - a normal subject' },
              { value: 'PE', label: 'PE - Physical Education' },
              { value: 'SOL', label: 'SOL - Science of Living' },
            ]}
          />
          {!special && (
            <>
              <Checkbox
                checked={form.is_lab}
                onChange={(e) => set('is_lab', e.target.checked)}
                label="This is a lab"
                description="A lab is always one block of 2 periods per week, whatever its credits."
              />
              <Checkbox
                checked={form.is_elective}
                onChange={(e) => setForm((f) => ({ ...f, is_elective: e.target.checked, elective_group_id: e.target.checked ? f.elective_group_id : '' }))}
                label="This is an elective"
                description="Students of the class choose among electives. Electives of one group run at the same time."
              />
              {form.is_elective && (
                <SelectField
                  label="Elective group"
                  value={form.elective_group_id}
                  onChange={(v) => set('elective_group_id', v)}
                  error={errors.elective_group_id}
                  placeholder="No group yet"
                  hint="Create groups on the “Elective groups” tab. All electives of a group should have the same credits."
                  options={(groups.data?.groups ?? []).map((g) => ({ value: g.id, label: g.name }))}
                />
              )}
            </>
          )}
        </div>
      </form>
    </Modal>
  )
}

function GroupDialog({
  open, group, classes, defaultClass, onClose, onSaved,
}: {
  open: boolean
  group: ElectiveGroup | null
  classes: ClassOption[]
  defaultClass: string
  onClose: () => void
  onSaved: () => void
}) {
  const [name, setName] = useState('')
  const [semester, setSemester] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    setError(null)
    setName(group?.name ?? '')
    setSemester(group?.semester_id ?? (defaultClass || classes[0]?.id || ''))
  }, [open, group, defaultClass, classes])

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      if (group) await api(`/subjects/groups/${group.id}`, { method: 'PATCH', body: { name } })
      else await api('/subjects/groups', { method: 'POST', body: { semester_id: semester, name } })
      onSaved()
      onClose()
    } catch (err) {
      setError(err instanceof ApiError ? (err.fields.name ?? err.message) : 'Could not save. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      open={open}
      onClose={() => !busy && onClose()}
      size="sm"
      title={group ? 'Rename elective group' : 'New elective group'}
      description={group ? undefined : 'Example: B.Sc. IT Sem 5 - “Group A” = AI, Cyber Security, Data Science. All electives of a group run in the same time slots.'}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button type="submit" form="group-form" loading={busy}>
            {group ? 'Save' : 'Create group'}
          </Button>
        </>
      }
    >
      <form id="group-form" onSubmit={submit} noValidate className="space-y-4">
        {!group && <SelectField label="Class" value={semester} onChange={setSemester} options={classes.map((c) => ({ value: c.id, label: c.label }))} />}
        <TextField label="Group name" value={name} onChange={(e) => setName(e.target.value)} maxLength={60} error={error} placeholder="Group A" required />
      </form>
    </Modal>
  )
}

export default function SubjectsPage() {
  const { user } = useAuth()
  const scope = useSchoolScope()
  const { active, ready: termReady } = useTerm()
  const toast = useToast()
  const [tab, setTab] = useState<'subjects' | 'groups'>('subjects')
  const [classId, setClassId] = useState('all')
  const [search, setSearch] = useState('')
  const q = useDebounced(search)

  const base = scope.ready && scope.schoolId && active ? scope : null
  const tree = useApi<StructureTree>(base ? scope.path('/structure/tree') : null)
  const classes = useMemo<ClassOption[]>(
    () =>
      (tree.data?.schools[0]?.courses ?? [])
        .filter((c) => c.is_active)
        .flatMap((c) => c.semesters.filter((s) => s.running).map((s) => ({ id: s.id, label: `${c.name} - Sem ${s.number}` }))),
    [tree.data],
  )

  // a school change (admin) can make the chosen class disappear
  useEffect(() => {
    if (classId !== 'all' && classes.length > 0 && !classes.some((c) => c.id === classId)) setClassId('all')
  }, [classes, classId])

  const subjectsPath = base ? scope.path('/subjects' + queryString({ semester_id: classId, q })) : null
  const groupsPath = base ? scope.path('/subjects/groups' + queryString({ semester_id: classId })) : null
  const subjects = useApi<{ subjects: SubjectRow[] }>(subjectsPath)
  const groups = useApi<{ groups: ElectiveGroup[] }>(groupsPath)

  const [subjectDialog, setSubjectDialog] = useState<{ subject: SubjectRow | null } | null>(null)
  const [groupDialog, setGroupDialog] = useState<{ group: ElectiveGroup | null } | null>(null)
  const [deleteSubject, setDeleteSubject] = useState<SubjectRow | null>(null)
  const [deleteGroup, setDeleteGroup] = useState<ElectiveGroup | null>(null)
  const [importing, setImporting] = useState(false)

  const reloadAll = () => {
    subjects.reload()
    groups.reload()
  }
  const list = subjects.data?.subjects ?? []
  const groupList = groups.data?.groups ?? []
  const defaultClass = classId !== 'all' ? classId : ''
  const termLink = user ? `${ROLE_PREFIX[user.role]}/active-term` : '#'

  if (termReady && !active) {
    return (
      <>
        <PageHeader title="Subjects & Credits" subtitle="Subjects of each class: credits, semester hours, labs and electives." />
        <Alert tone="warning" title="Choose the active term first">
          Subjects belong to the classes of the running term (odd or even). <Link to={termLink} className="font-semibold underline">Open Active Term</Link> and pick one.
        </Alert>
      </>
    )
  }

  return (
    <>
      <PageHeader
        title="Subjects & Credits"
        subtitle={
          <>
            Subjects of the classes running in <span className="font-semibold text-ink">{active?.name ?? '…'}</span>. Credits = the minimum lectures per week; every subject also has the total hours to complete in the semester.
          </>
        }
      />

      <SchoolBar>
        <Button variant="secondary" icon={<FileSpreadsheet className="size-4" />} onClick={() => setImporting(true)} disabled={classes.length === 0}>
          Import from Excel
        </Button>
        <Button variant="secondary" icon={<Layers className="size-4" />} onClick={() => setGroupDialog({ group: null })} disabled={classes.length === 0}>
          New elective group
        </Button>
        <Button icon={<Plus className="size-4" />} onClick={() => setSubjectDialog({ subject: null })} disabled={classes.length === 0}>
          Add subject
        </Button>
      </SchoolBar>

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Tabs
          label="Subjects or elective groups"
          value={tab}
          onChange={setTab}
          items={[
            { value: 'subjects', label: 'Subjects', count: subjects.data ? list.length : undefined },
            { value: 'groups', label: 'Elective groups', count: groups.data ? groupList.length : undefined },
          ]}
        />
        <SelectField
          compact
          aria-label="Class"
          className="w-56"
          value={classId}
          onChange={setClassId}
          options={[{ value: 'all', label: 'All classes' }, ...classes.map((c) => ({ value: c.id, label: c.label }))]}
        />
        {tab === 'subjects' && <SearchBox value={search} onChange={setSearch} placeholder="Search name or code" className="w-full sm:w-64" />}
      </div>

      {tab === 'subjects' ? (
        <Panel title="Subjects" icon={BookOpen}>
          {subjects.error ? (
            <Alert tone="danger">{subjects.error.message}</Alert>
          ) : (subjects.loading && !subjects.data) || tree.loading ? (
            <div className="space-y-2">
              <Skeleton className="h-12" />
              <Skeleton className="h-12" />
              <Skeleton className="h-12" />
            </div>
          ) : classes.length === 0 ? (
            <EmptyState icon={BookOpen} title="No classes run in this term">
              The courses of this school have no semester that runs in {active?.name}. The administrator can add courses in College Setup.
            </EmptyState>
          ) : (
            <div className={subjects.loading ? 'opacity-60 transition-opacity' : 'transition-opacity'}>
              <DataTable
                caption="Subjects"
                rows={list}
                rowKey={(s) => s.id}
                empty={search || classId !== 'all' ? 'No subject matches.' : 'No subjects yet. Use “Add subject” or import an Excel sheet.'}
                card={(s) => (
                  <div>
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <p className="font-medium text-ink">{s.name}</p>
                        <p className="text-[13px] text-muted">{s.code} · {s.class_label}</p>
                      </div>
                      <div className="flex shrink-0 gap-0.5">
                        <Button variant="ghost" size="sm" aria-label={`Edit ${s.name}`} icon={<Pencil className="size-4" />} onClick={() => setSubjectDialog({ subject: s })} />
                        <Button variant="ghost" size="sm" aria-label={`Delete ${s.name}`} icon={<Trash2 className="size-4" />} onClick={() => setDeleteSubject(s)} />
                      </div>
                    </div>
                    <div className="mt-2 flex flex-wrap items-center gap-1.5">
                      <Badge>{s.credits} credits</Badge>
                      <Badge>{s.semester_hours} h</Badge>
                      {s.special_kind && <Badge tone="warning">{s.special_kind} · double block</Badge>}
                      {s.is_lab && <Badge tone="info">Lab</Badge>}
                      {s.is_elective && <Badge tone="brand">Elective{s.elective_group_name ? ` · ${s.elective_group_name}` : ''}</Badge>}
                    </div>
                    <p className="mt-2 text-[13px] text-muted">
                      {s.teacher_name ? `${s.teacher_name}${s.co_teacher_name ? ` + ${s.co_teacher_name}` : ''}` : 'No teacher assigned yet'}
                    </p>
                  </div>
                )}
                columns={[
                  {
                    key: 'name',
                    header: 'Subject',
                    cell: (s) => (
                      <div className="min-w-0">
                        <p className="font-medium text-ink">{s.name}</p>
                        <p className="text-[13px] text-muted">{s.code}</p>
                      </div>
                    ),
                  },
                  { key: 'class', header: 'Class', cell: (s) => <span className="whitespace-nowrap text-muted">{s.class_label}</span> },
                  { key: 'credits', header: 'Credits', className: 'tabular-nums', cell: (s) => s.credits },
                  { key: 'hours', header: 'Hours', hideBelow: 'sm', className: 'tabular-nums', cell: (s) => s.semester_hours },
                  {
                    key: 'type',
                    header: 'Type',
                    cell: (s) => (
                      <div className="flex flex-wrap gap-1">
                        {s.special_kind && <Badge tone="warning">{s.special_kind} · double block</Badge>}
                        {s.is_lab && <Badge tone="info">Lab</Badge>}
                        {s.is_elective && <Badge tone="brand">Elective{s.elective_group_name ? ` · ${s.elective_group_name}` : ''}</Badge>}
                        {!s.special_kind && !s.is_lab && !s.is_elective && <Badge>Core</Badge>}
                      </div>
                    ),
                  },
                  {
                    key: 'teacher',
                    header: 'Teacher',
                    hideBelow: 'lg',
                    cell: (s) =>
                      s.teacher_name ? (
                        <span className="text-muted">
                          {s.teacher_name}
                          {s.co_teacher_name ? ` + ${s.co_teacher_name}` : ''}
                        </span>
                      ) : (
                        <span className="text-subtle">Not assigned</span>
                      ),
                  },
                  {
                    key: 'actions',
                    header: <span className="sr-only">Actions</span>,
                    className: 'w-24 text-right',
                    cell: (s) => (
                      <div className="flex justify-end gap-1">
                        <Button variant="ghost" size="sm" aria-label={`Edit ${s.name}`} icon={<Pencil className="size-4" />} onClick={() => setSubjectDialog({ subject: s })} />
                        <Button variant="ghost" size="sm" aria-label={`Delete ${s.name}`} icon={<Trash2 className="size-4" />} onClick={() => setDeleteSubject(s)} />
                      </div>
                    ),
                  },
                ]}
              />
            </div>
          )}
        </Panel>
      ) : (
        <div className="space-y-4">
          <Alert tone="info">
            Electives of one group run in the <span className="font-semibold">same time slots</span>, each with its own teacher - so no student can ever clash. They should all have the same credits.
          </Alert>
          {groups.error ? (
            <Alert tone="danger">{groups.error.message}</Alert>
          ) : groups.loading && !groups.data ? (
            <Skeleton className="h-40" />
          ) : groupList.length === 0 ? (
            <Panel title="Elective groups" icon={Layers}>
              <EmptyState icon={Layers} title="No elective groups yet">
                Create a group, then mark subjects as “elective” and choose the group when you add or edit them.
              </EmptyState>
            </Panel>
          ) : (
            <div className="grid gap-4 md:grid-cols-2">
              {groupList.map((g) => (
                <Panel
                  key={g.id}
                  title={g.name}
                  icon={Layers}
                  action={
                    <div className="flex gap-1">
                      <Button variant="ghost" size="sm" aria-label={`Rename ${g.name}`} icon={<Pencil className="size-4" />} onClick={() => setGroupDialog({ group: g })} />
                      <Button variant="ghost" size="sm" aria-label={`Delete ${g.name}`} icon={<Trash2 className="size-4" />} onClick={() => setDeleteGroup(g)} />
                    </div>
                  }
                >
                  <p className="mb-3 text-[13px] text-muted">{g.class_label}</p>
                  {g.members.length === 0 ? (
                    <p className="text-sm text-muted">No elective in this group yet.</p>
                  ) : (
                    <ul className="space-y-1.5">
                      {g.members.map((m) => (
                        <li key={m.id} className="flex items-center justify-between gap-3 rounded-lg bg-surface-2 px-3 py-2 text-sm">
                          <span className="min-w-0 truncate font-medium text-ink">{m.name}</span>
                          <Badge tone="brand">{m.credits} credits</Badge>
                        </li>
                      ))}
                    </ul>
                  )}
                  {g.warnings.map((w) => (
                    <Alert key={w} tone="warning" className="mt-3">
                      {w}
                    </Alert>
                  ))}
                </Panel>
              ))}
            </div>
          )}
        </div>
      )}

      <SubjectDialog open={subjectDialog !== null} subject={subjectDialog?.subject ?? null} classes={classes} defaultClass={defaultClass} onClose={() => setSubjectDialog(null)} onSaved={reloadAll} />
      <GroupDialog open={groupDialog !== null} group={groupDialog?.group ?? null} classes={classes} defaultClass={defaultClass} onClose={() => setGroupDialog(null)} onSaved={() => { reloadAll(); toast.success('Elective group saved.') }} />
      <ImportDialog kind="subjects" open={importing} onClose={() => setImporting(false)} onDone={reloadAll} />

      <ConfirmDialog
        open={deleteSubject !== null}
        tone="danger"
        title={`Delete ${deleteSubject?.name}?`}
        confirmLabel="Delete subject"
        onClose={() => setDeleteSubject(null)}
        onConfirm={async () => {
          if (!deleteSubject) return
          await api(`/subjects/${deleteSubject.id}`, { method: 'DELETE' })
          toast.success('Subject deleted.')
          reloadAll()
        }}
      >
        <p>A subject that already has a teacher assigned or a locked slot cannot be deleted - remove those first.</p>
      </ConfirmDialog>

      <ConfirmDialog
        open={deleteGroup !== null}
        tone="danger"
        title={`Delete the group “${deleteGroup?.name}”?`}
        confirmLabel="Delete group"
        onClose={() => setDeleteGroup(null)}
        onConfirm={async () => {
          if (!deleteGroup) return
          await api(`/subjects/groups/${deleteGroup.id}`, { method: 'DELETE' })
          toast.success('Group deleted.')
          reloadAll()
        }}
      >
        <p>The electives in this group are not deleted - they just no longer belong to a group.</p>
      </ConfirmDialog>
    </>
  )
}
