import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { Camera, CircleUser, Lock, Save, Trash2 } from 'lucide-react'
import { useAuth } from '../auth/AuthProvider'
import { Alert } from '../components/Alert'
import { Avatar } from '../components/Avatar'
import { forgetImage } from '../components/AuthedImage'
import { Badge } from '../components/Badge'
import { Button } from '../components/Button'
import { TextField } from '../components/Field'
import { PageHeader, Panel, Skeleton } from '../components/Panel'
import { SubjectPicker, type PickableSubject } from '../components/SubjectPicker'
import { useToast } from '../components/Toast'
import { api, ApiError } from '../lib/api'
import { dayList } from '../lib/helpers'
import { ROLE_LABEL } from '../lib/nav'
import type { ProfilePayload } from '../lib/types'
import { useApi } from '../lib/useApi'

const MAX_PHOTO = 5 * 1024 * 1024

/** "My Profile": a teacher (or HOD) fills in and edits their own details. Role, school and visiting settings are NOT editable here. */
export default function MyProfilePage() {
  const { user } = useAuth()
  const toast = useToast()
  const { data, error, loading, reload } = useApi<ProfilePayload>('/profile/me')
  const fileInput = useRef<HTMLInputElement>(null)

  const [form, setForm] = useState({ full_name: '', phone: '', specialization: '', subject_ids: [] as string[] })
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [top, setTop] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [photoBusy, setPhotoBusy] = useState(false)
  const [photoError, setPhotoError] = useState<string | null>(null)
  const [photoUrl, setPhotoUrl] = useState<string | null>(null)

  useEffect(() => {
    if (!data) return
    const p = data.profile
    setForm({ full_name: p.full_name, phone: p.phone ?? '', specialization: p.specialization ?? '', subject_ids: p.subject_ids ?? [] })
    setPhotoUrl(p.photo_url)
  }, [data])

  const pickable = useMemo<PickableSubject[]>(
    () =>
      (data?.available_subjects ?? []).map((s) => ({
        id: s.id, name: s.name, code: s.code, group: `${s.course} - Sem ${s.semester}`, running: s.running,
      })),
    [data],
  )

  async function save(e: FormEvent) {
    e.preventDefault()
    setSaving(true)
    setErrors({})
    setTop(null)
    try {
      await api('/profile/me', { method: 'PATCH', body: form })
      toast.success('Your profile is saved.')
      reload()
    } catch (err) {
      if (err instanceof ApiError) {
        setErrors(err.fields)
        setTop(Object.keys(err.fields).length > 0 ? 'Please check the highlighted fields.' : err.message)
      } else setTop('Could not save. Please try again.')
    } finally {
      setSaving(false)
    }
  }

  async function upload(file: File | undefined) {
    if (!file) return
    setPhotoError(null)
    if (!/^image\/(jpeg|png|webp)$/.test(file.type)) {
      setPhotoError('Please choose a JPG, PNG or WEBP picture.')
      return
    }
    if (file.size > MAX_PHOTO) {
      setPhotoError('That picture is too large (5 MB maximum).')
      return
    }
    setPhotoBusy(true)
    try {
      const form = new FormData()
      form.append('file', file)
      const res = await api<{ photo_url: string }>('/profile/me/photo', { method: 'PUT', form })
      forgetImage(photoUrl)
      setPhotoUrl(res.photo_url)
      toast.success('Photo updated.')
    } catch (err) {
      setPhotoError(err instanceof ApiError ? err.message : 'The photo could not be uploaded.')
    } finally {
      setPhotoBusy(false)
      if (fileInput.current) fileInput.current.value = ''
    }
  }

  async function removePhoto() {
    setPhotoBusy(true)
    setPhotoError(null)
    try {
      await api('/profile/me/photo', { method: 'DELETE' })
      forgetImage(photoUrl)
      setPhotoUrl(null)
      toast.success('Photo removed.')
    } catch (err) {
      setPhotoError(err instanceof ApiError ? err.message : 'The photo could not be removed.')
    } finally {
      setPhotoBusy(false)
    }
  }

  const profile = data?.profile

  return (
    <>
      <PageHeader
        title="My Profile"
        subtitle="Your details are used for substitutions, for the HOD's view of the team and for the timetable. Keep your specialization and the subjects you can teach up to date."
      />

      {error ? (
        <Alert tone="danger">{error.message}</Alert>
      ) : loading && !data ? (
        <div className="grid gap-6 lg:grid-cols-3">
          <Skeleton className="h-80" />
          <Skeleton className="h-80 lg:col-span-2" />
        </div>
      ) : profile ? (
        <div className="grid gap-6 lg:grid-cols-3">
          <div className="space-y-6">
            <Panel title="Photo" icon={Camera}>
              <div className="flex flex-col items-center text-center">
                <Avatar name={profile.full_name} photoUrl={photoUrl} size="xl" />
                <p className="mt-3 text-base font-semibold text-ink">{profile.full_name}</p>
                <p className="text-sm text-muted">{user ? ROLE_LABEL[user.role] : ''}</p>
                <div className="mt-4 flex flex-wrap justify-center gap-2">
                  <Button variant="secondary" size="sm" icon={<Camera className="size-4" />} loading={photoBusy} onClick={() => fileInput.current?.click()}>
                    {photoUrl ? 'Change photo' : 'Add photo'}
                  </Button>
                  {photoUrl && (
                    <Button variant="ghost" size="sm" icon={<Trash2 className="size-4" />} onClick={removePhoto} disabled={photoBusy}>
                      Remove
                    </Button>
                  )}
                </div>
                <input ref={fileInput} type="file" accept="image/jpeg,image/png,image/webp" className="sr-only" aria-label="Choose a photo" onChange={(e) => void upload(e.target.files?.[0])} />
                <p className="mt-2 text-xs text-muted">JPG, PNG or WEBP, up to 5 MB. It is shown as a small square.</p>
                {photoError && (
                  <Alert tone="danger" className="mt-3 w-full text-left">
                    {photoError}
                  </Alert>
                )}
              </div>
            </Panel>

            <Panel title="Set by your HOD" icon={Lock}>
              <dl className="space-y-3 text-sm">
                <div>
                  <dt className="text-muted">Sign-in email</dt>
                  <dd className="break-all font-medium text-ink">{profile.email}</dd>
                </div>
                <div>
                  <dt className="text-muted">Role and school</dt>
                  <dd className="font-medium text-ink">
                    {user ? ROLE_LABEL[user.role] : ''}
                    {profile.school_name ? ` · ${profile.school_name}` : ''}
                  </dd>
                </div>
                <div>
                  <dt className="text-muted">Visiting faculty</dt>
                  <dd className="font-medium text-ink">
                    {profile.is_visiting ? (
                      <span className="flex flex-wrap items-center gap-1.5">
                        <Badge tone="info">Yes</Badge> {dayList(profile.visiting_days)} · up to {profile.max_weekly_hours} h per week
                      </span>
                    ) : (
                      'No'
                    )}
                  </dd>
                </div>
              </dl>
              <p className="mt-3 text-xs text-muted">These can only be changed by your HOD or the administrator.</p>
            </Panel>
          </div>

          <Panel title="Your details" icon={CircleUser} className="lg:col-span-2">
            <form onSubmit={save} noValidate className="space-y-5">
              {top && <Alert tone="danger">{top}</Alert>}
              <div className="grid gap-4 sm:grid-cols-2">
                <TextField label="Full name" value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} error={errors.full_name} maxLength={120} autoComplete="name" required />
                <TextField label="Phone" value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} error={errors.phone} maxLength={30} inputMode="tel" autoComplete="tel" />
                <TextField
                  className="sm:col-span-2"
                  label="Specialization"
                  value={form.specialization}
                  onChange={(e) => setForm({ ...form, specialization: e.target.value })}
                  error={errors.specialization}
                  hint="For example: AI, Machine Learning. It helps the HOD when a substitute is needed."
                  maxLength={300}
                />
              </div>
              <SubjectPicker subjects={pickable} value={form.subject_ids} onChange={(ids) => setForm({ ...form, subject_ids: ids })} error={errors.subject_ids} />
              <div className="flex justify-end">
                <Button type="submit" loading={saving} icon={<Save className="size-4" />}>
                  Save my profile
                </Button>
              </div>
            </form>
          </Panel>
        </div>
      ) : null}
    </>
  )
}
