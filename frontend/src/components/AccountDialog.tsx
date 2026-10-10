import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { api, ApiError } from '../lib/api'
import { DAY_NAMES } from '../lib/helpers'
import { useSchoolScope } from '../lib/scope'
import type { Account, CreatedAccount, SubjectRow, TimingPayload } from '../lib/types'
import { useApi } from '../lib/useApi'
import { Alert } from './Alert'
import { Button } from './Button'
import { Checkbox, TextField } from './Field'
import { Modal } from './Modal'
import { SelectField } from './Select'
import { SubjectPicker, type PickableSubject } from './SubjectPicker'
import { useToast } from './Toast'

interface Props {
  open: boolean
  onClose: () => void
  /** null = create a new account */
  account: Account | null
  /** the role of a NEW account (the administrator may choose; an HOD can only add teachers) */
  defaultRole?: 'hod' | 'teacher'
  /** a new account for this school (College Setup: "Create HOD account") */
  presetSchoolId?: string
  /** called after saving. For a NEW account it gets the temporary password to show. */
  onSaved: (created?: CreatedAccount) => void
}

interface Form {
  full_name: string
  email: string
  role: 'hod' | 'teacher'
  school_id: string
  phone: string
  specialization: string
  is_visiting: boolean
  visiting_days: number[]
  max_weekly_hours: string
  subject_ids: string[]
}

function blank(role: 'hod' | 'teacher', schoolId: string): Form {
  return {
    full_name: '', email: '', role, school_id: schoolId, phone: '', specialization: '',
    is_visiting: false, visiting_days: [], max_weekly_hours: '', subject_ids: [],
  }
}

export function AccountDialog({ open, onClose, account, defaultRole = 'teacher', presetSchoolId, onSaved }: Props) {
  const scope = useSchoolScope()
  const toast = useToast()
  const editing = account !== null
  const [form, setForm] = useState<Form>(() => blank(defaultRole, presetSchoolId ?? scope.schoolId ?? ''))
  const [busy, setBusy] = useState(false)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [topError, setTopError] = useState<string | null>(null)

  // reset the form every time the dialog opens
  useEffect(() => {
    if (!open) return
    setErrors({})
    setTopError(null)
    if (account) {
      setForm({
        full_name: account.full_name,
        email: account.email,
        role: account.role,
        school_id: account.school_id ?? '',
        phone: account.phone ?? '',
        specialization: account.specialization ?? '',
        is_visiting: account.is_visiting,
        visiting_days: account.visiting_days,
        max_weekly_hours: account.max_weekly_hours ? String(account.max_weekly_hours) : '',
        subject_ids: account.subject_ids ?? [],
      })
    } else {
      setForm(blank(scope.isAdmin ? defaultRole : 'teacher', presetSchoolId ?? scope.schoolId ?? ''))
    }
  }, [open, account])

  // the account's own details (with the subjects they can teach) - the list only has the count
  const detail = useApi<Account>(open && account ? `/users/${account.id}` : null)
  useEffect(() => {
    if (detail.data && account && detail.data.id === account.id) {
      setForm((f) => ({ ...f, subject_ids: detail.data!.subject_ids ?? [] }))
    }
  }, [detail.data, account])

  const schoolForSubjects = form.school_id
  const subjectsPath = open && schoolForSubjects ? `/subjects${scope.isAdmin ? `?school_id=${schoolForSubjects}` : ''}` : null
  const subjects = useApi<{ subjects: SubjectRow[] }>(subjectsPath)
  const timing = useApi<TimingPayload>(open ? '/timing' : null)

  const pickable = useMemo<PickableSubject[]>(
    () => (subjects.data?.subjects ?? []).map((s) => ({ id: s.id, name: s.name, code: s.code, group: s.class_label, running: true })),
    [subjects.data],
  )
  const workingDays = timing.data?.college.days_per_week ?? 5
  const isTeacherRole = form.role === 'teacher'
  const loadedForEdit = !editing || (detail.data !== null && detail.data.id === account?.id)

  const set = <K extends keyof Form>(key: K, value: Form[K]) => setForm((f) => ({ ...f, [key]: value }))

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!loadedForEdit) return
    setBusy(true)
    setErrors({})
    setTopError(null)
    const visiting = isTeacherRole && form.is_visiting
    const common = {
      full_name: form.full_name,
      email: form.email,
      phone: form.phone,
      specialization: form.specialization,
      subject_ids: form.subject_ids,
    }
    try {
      if (editing && account) {
        const body: Record<string, unknown> = { ...common }
        if (isTeacherRole) {
          body.is_visiting = visiting
          body.visiting_days = visiting ? form.visiting_days : []
          body.max_weekly_hours = visiting && form.max_weekly_hours ? Number(form.max_weekly_hours) : null
        }
        await api(`/users/${account.id}`, { method: 'PATCH', body })
        toast.success(`${form.full_name} saved.`)
        onSaved()
      } else {
        const body: Record<string, unknown> = {
          ...common,
          role: form.role,
          school_id: scope.isAdmin ? form.school_id : undefined,
          is_visiting: visiting,
          visiting_days: visiting ? form.visiting_days : [],
          max_weekly_hours: visiting && form.max_weekly_hours ? Number(form.max_weekly_hours) : null,
        }
        const created = await api<CreatedAccount>('/users', { method: 'POST', body })
        onSaved(created)
      }
      onClose()
    } catch (err) {
      if (err instanceof ApiError) {
        setErrors(err.fields)
        setTopError(Object.keys(err.fields).length > 0 ? 'Please check the highlighted fields.' : err.message)
      } else setTopError('Something went wrong. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  const title = editing ? `Edit ${account?.full_name}` : form.role === 'hod' ? 'New HOD account' : 'New teacher account'

  return (
    <Modal
      open={open}
      onClose={() => !busy && onClose()}
      size="lg"
      title={title}
      description={
        editing
          ? undefined
          : 'A temporary password is made for the new account and shown once on the next screen. Nothing is sent by e-mail.'
      }
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button type="submit" form="account-form" loading={busy} disabled={!loadedForEdit}>
            {editing ? 'Save changes' : 'Create account'}
          </Button>
        </>
      }
    >
      <form id="account-form" onSubmit={submit} noValidate className="space-y-5">
        {topError && <Alert tone="danger">{topError}</Alert>}

        <div className="grid gap-4 sm:grid-cols-2">
          <TextField label="Full name" value={form.full_name} onChange={(e) => set('full_name', e.target.value)} error={errors.full_name} maxLength={120} autoComplete="off" required />
          <TextField
            label="Email address"
            type="email"
            value={form.email}
            onChange={(e) => set('email', e.target.value)}
            error={errors.email}
            hint={editing ? 'Changing it also changes the sign-in email.' : 'This is what they sign in with.'}
            maxLength={254}
            autoComplete="off"
            required
          />
          {!editing && scope.isAdmin && (
            <>
              <SelectField
                label="Role"
                value={form.role}
                onChange={(v) => set('role', v as 'hod' | 'teacher')}
                options={[
                  { value: 'teacher', label: 'Teacher' },
                  { value: 'hod', label: 'Head of Department (HOD)' },
                ]}
              />
              <SelectField
                label="School"
                value={form.school_id}
                onChange={(v) => setForm((f) => ({ ...f, school_id: v, subject_ids: [] }))}
                error={errors.school_id}
                options={scope.schools.filter((s) => s.is_active).map((s) => ({ value: s.id, label: s.name }))}
                disabled={!!presetSchoolId}
              />
            </>
          )}
          <TextField label="Phone (optional)" value={form.phone} onChange={(e) => set('phone', e.target.value)} error={errors.phone} maxLength={30} inputMode="tel" autoComplete="off" />
          <TextField
            label="Specialization (optional)"
            value={form.specialization}
            onChange={(e) => set('specialization', e.target.value)}
            error={errors.specialization}
            hint="For example: AI, Machine Learning"
            maxLength={300}
            autoComplete="off"
          />
        </div>

        {isTeacherRole && (
          <div className="rounded-xl border border-line bg-surface-2 p-4">
            <Checkbox
              checked={form.is_visiting}
              onChange={(e) => set('is_visiting', e.target.checked)}
              label="Visiting faculty"
              description="Comes only on some days and for a limited number of hours per week. These limits are never crossed."
            />
            {form.is_visiting && (
              <div className="mt-4 space-y-4">
                <fieldset>
                  <legend className="mb-2 text-[13px] font-medium text-ink">Days they can come</legend>
                  <div className="flex flex-wrap gap-2">
                    {DAY_NAMES.slice(0, workingDays).map((name, i) => {
                      const day = i + 1
                      const on = form.visiting_days.includes(day)
                      return (
                        <label
                          key={name}
                          className={`cursor-pointer select-none rounded-lg border px-3 py-1.5 text-sm font-medium transition-colors ${
                            on ? 'border-accent bg-brand text-white' : 'border-line-strong bg-surface text-muted hover:bg-hover'
                          }`}
                        >
                          <input
                            type="checkbox"
                            className="sr-only"
                            checked={on}
                            onChange={() => set('visiting_days', on ? form.visiting_days.filter((d) => d !== day) : [...form.visiting_days, day].sort())}
                          />
                          {name.slice(0, 3)}
                        </label>
                      )
                    })}
                  </div>
                  {errors.visiting_days && <p className="mt-1.5 text-[13px] text-danger">{errors.visiting_days}</p>}
                </fieldset>
                <TextField
                  label="Maximum hours per week"
                  type="number"
                  min={1}
                  max={60}
                  className="w-52"
                  value={form.max_weekly_hours}
                  onChange={(e) => set('max_weekly_hours', e.target.value)}
                  error={errors.max_weekly_hours}
                />
              </div>
            )}
          </div>
        )}

        {subjects.loading && !subjects.data ? (
          <p className="text-sm text-muted">Loading subjects…</p>
        ) : (
          <SubjectPicker subjects={pickable} value={form.subject_ids} onChange={(ids) => set('subject_ids', ids)} error={errors.subject_ids} />
        )}
      </form>
    </Modal>
  )
}
