import { useState } from 'react'
import { Link } from 'react-router-dom'
import { FileSpreadsheet, KeyRound, Pencil, Plus, Power, UserPlus, Users } from 'lucide-react'
import { useAuth } from '../auth/AuthProvider'
import { AccountDialog } from '../components/AccountDialog'
import { Alert } from '../components/Alert'
import { Avatar } from '../components/Avatar'
import { Badge } from '../components/Badge'
import { Button } from '../components/Button'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { CredentialsDialog, type Credential } from '../components/CredentialsDialog'
import { DataTable } from '../components/DataTable'
import { ImportDialog } from '../components/ImportDialog'
import { PageHeader, Panel, Skeleton } from '../components/Panel'
import { SchoolBar } from '../components/SchoolBar'
import { SearchBox } from '../components/SearchBox'
import { SelectField } from '../components/Select'
import { api } from '../lib/api'
import { dayList, queryString, useDebounced } from '../lib/helpers'
import { ROLE_PREFIX } from '../lib/nav'
import { useSchoolScope } from '../lib/scope'
import type { Account } from '../lib/types'
import { useAction } from '../lib/useAction'
import { useApi } from '../lib/useApi'

export default function TeachersPage() {
  const { user } = useAuth()
  const scope = useSchoolScope()
  const { run } = useAction()
  const [search, setSearch] = useState('')
  const [role, setRole] = useState('all')
  const [status, setStatus] = useState('active')
  const q = useDebounced(search)

  const path = scope.ready && scope.schoolId ? scope.path('/users' + queryString({ q, role, status })) : null
  const { data, error, loading, reload } = useApi<{ users: Account[]; total: number }>(path)

  const [dialog, setDialog] = useState<{ account: Account | null; role: 'hod' | 'teacher' } | null>(null)
  const [importing, setImporting] = useState(false)
  const [credentials, setCredentials] = useState<{ title: string; items: Credential[] } | null>(null)
  const [confirm, setConfirm] = useState<{ kind: 'deactivate' | 'reset'; account: Account } | null>(null)

  const isAdmin = user?.role === 'admin'
  const profileLink = user ? `${ROLE_PREFIX[user.role]}/my-schedule/profile` : '#'

  const users = data?.users ?? []
  const noSchool = scope.ready && !scope.schoolId

  return (
    <>
      <PageHeader
        title={isAdmin ? 'Teachers' : 'My School Teachers'}
        subtitle={
          isAdmin
            ? 'HOD and teacher accounts of a school. A temporary password is made for every new account and shown once - nothing is sent by e-mail.'
            : 'The teachers of your school. Add accounts, fill in their details, choose the subjects they can teach, and reset a password if someone forgets it.'
        }
      />

      <SchoolBar>
        <Button variant="secondary" icon={<FileSpreadsheet className="size-4" />} onClick={() => setImporting(true)} disabled={noSchool}>
          Import from Excel
        </Button>
        {isAdmin && (
          <Button variant="secondary" icon={<UserPlus className="size-4" />} onClick={() => setDialog({ account: null, role: 'hod' })} disabled={noSchool}>
            Add HOD
          </Button>
        )}
        <Button icon={<Plus className="size-4" />} onClick={() => setDialog({ account: null, role: 'teacher' })} disabled={noSchool}>
          Add teacher
        </Button>
      </SchoolBar>

      <Panel title="People" icon={Users}>
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <SearchBox value={search} onChange={setSearch} placeholder="Search name, email or specialization" className="w-full sm:w-80" />
          {isAdmin && (
            <SelectField
              compact
              aria-label="Show"
              className="w-40"
              value={role}
              onChange={setRole}
              options={[
                { value: 'all', label: 'HODs and teachers' },
                { value: 'hod', label: 'HODs only' },
                { value: 'teacher', label: 'Teachers only' },
              ]}
            />
          )}
          <SelectField
            compact
            aria-label="Status"
            className="w-36"
            value={status}
            onChange={setStatus}
            options={[
              { value: 'active', label: 'Active' },
              { value: 'inactive', label: 'Inactive' },
              { value: 'all', label: 'All' },
            ]}
          />
          {data && (
            <span className="ml-auto text-[13px] text-muted tabular-nums" aria-live="polite">
              {data.total} {data.total === 1 ? 'person' : 'people'}
            </span>
          )}
        </div>

        {error ? (
          <Alert tone="danger">{error.message}</Alert>
        ) : loading && !data ? (
          <div className="space-y-2">
            <Skeleton className="h-14" />
            <Skeleton className="h-14" />
            <Skeleton className="h-14" />
          </div>
        ) : (
          <div className={loading ? 'opacity-60 transition-opacity' : 'transition-opacity'}>
            <DataTable
              caption="Accounts of the school"
              rows={users}
              rowKey={(u) => u.id}
              empty={search ? 'Nobody matches your search.' : 'No accounts yet. Use “Add teacher” to create the first one.'}
              rowClass={(u) => (u.is_active ? '' : 'opacity-60')}
              columns={[
                {
                  key: 'person',
                  header: 'Person',
                  cell: (u) => (
                    <div className="flex items-center gap-3">
                      <Avatar name={u.full_name} photoUrl={u.photo_url} />
                      <div className="min-w-0">
                        <p className="truncate font-medium text-ink">
                          {u.full_name}
                          {u.id === user?.id && <span className="ml-2 text-xs font-normal text-muted">(you)</span>}
                        </p>
                        <p className="truncate text-[13px] text-muted">{u.email}</p>
                      </div>
                    </div>
                  ),
                },
                {
                  key: 'role',
                  header: 'Role',
                  cell: (u) => (
                    <div className="flex flex-wrap gap-1.5">
                      <Badge tone={u.role === 'hod' ? 'brand' : 'neutral'}>{u.role === 'hod' ? 'HOD' : 'Teacher'}</Badge>
                      {u.is_visiting && (
                        <Badge tone="info" >
                          Visiting · {dayList(u.visiting_days)} · {u.max_weekly_hours ?? '?'} h
                        </Badge>
                      )}
                    </div>
                  ),
                },
                {
                  key: 'spec',
                  header: 'Specialization',
                  hideBelow: 'lg',
                  cell: (u) => <span className="text-muted">{u.specialization ?? '-'}</span>,
                },
                {
                  key: 'subjects',
                  header: 'Can teach',
                  hideBelow: 'md',
                  className: 'tabular-nums',
                  cell: (u) => (
                    <span className="text-muted">
                      {u.subjects_count} {u.subjects_count === 1 ? 'subject' : 'subjects'}
                    </span>
                  ),
                },
                {
                  key: 'status',
                  header: 'Status',
                  hideBelow: 'sm',
                  cell: (u) =>
                    !u.is_active ? (
                      <Badge tone="danger">Inactive</Badge>
                    ) : u.must_change_password ? (
                      <Badge tone="warning">Not signed in yet</Badge>
                    ) : (
                      <Badge tone="success">Active</Badge>
                    ),
                },
                {
                  key: 'actions',
                  header: <span className="sr-only">Actions</span>,
                  className: 'text-right',
                  cell: (u) =>
                    u.id === user?.id ? (
                      <Link to={profileLink} className="text-[13px] font-medium text-brand-text hover:underline">
                        Edit in My Profile
                      </Link>
                    ) : (
                      <div className="flex justify-end gap-1">
                        <Button variant="ghost" size="sm" aria-label={`Edit ${u.full_name}`} title="Edit" icon={<Pencil className="size-4" />} onClick={() => setDialog({ account: u, role: u.role })} />
                        <Button variant="ghost" size="sm" aria-label={`Reset the password of ${u.full_name}`} title="Reset password" icon={<KeyRound className="size-4" />} onClick={() => setConfirm({ kind: 'reset', account: u })} disabled={!u.is_active} />
                        {u.is_active ? (
                          <Button variant="ghost" size="sm" aria-label={`Deactivate ${u.full_name}`} title="Deactivate" icon={<Power className="size-4" />} onClick={() => setConfirm({ kind: 'deactivate', account: u })} />
                        ) : (
                          <Button
                            variant="ghost"
                            size="sm"
                            aria-label={`Reactivate ${u.full_name}`}
                            title="Reactivate"
                            icon={<Power className="size-4 text-success" />}
                            onClick={() => void run(async () => { await api(`/users/${u.id}/reactivate`, { method: 'POST' }); reload() }, `${u.full_name} is active again.`)}
                          />
                        )}
                      </div>
                    ),
                },
              ]}
            />
          </div>
        )}
      </Panel>

      <AccountDialog
        open={dialog !== null}
        account={dialog?.account ?? null}
        defaultRole={dialog?.role ?? 'teacher'}
        onClose={() => setDialog(null)}
        onSaved={(created) => {
          reload()
          if (created) {
            setCredentials({
              title: created.user.role === 'hod' ? 'HOD account created' : 'Teacher account created',
              items: [{ name: created.user.full_name, email: created.user.email, temp_password: created.temp_password }],
            })
          }
        }}
      />

      <ImportDialog kind="teachers" open={importing} onClose={() => setImporting(false)} onDone={reload} />

      <CredentialsDialog open={credentials !== null} title={credentials?.title} items={credentials?.items ?? []} onClose={() => setCredentials(null)} />

      <ConfirmDialog
        open={confirm?.kind === 'deactivate'}
        tone="danger"
        title={`Deactivate ${confirm?.account.full_name}?`}
        confirmLabel="Deactivate"
        onClose={() => setConfirm(null)}
        onConfirm={async () => {
          if (!confirm) return
          await api(`/users/${confirm.account.id}/deactivate`, { method: 'POST' })
          reload()
        }}
      >
        <p>They are signed out at once and cannot sign in until you reactivate the account. Nothing is deleted.</p>
        <p>If they still teach a subject or have a locked slot in the active term, please reassign it first - the system will tell you.</p>
      </ConfirmDialog>

      <ConfirmDialog
        open={confirm?.kind === 'reset'}
        title={`Reset the password of ${confirm?.account.full_name}?`}
        confirmLabel="Make a new password"
        onClose={() => setConfirm(null)}
        onConfirm={async () => {
          if (!confirm) return
          const result = await api<{ user: Account; temp_password: string }>(`/users/${confirm.account.id}/reset-password`, { method: 'POST' })
          reload()
          setCredentials({
            title: 'New temporary password',
            items: [{ name: result.user.full_name, email: result.user.email, temp_password: result.temp_password }],
          })
        }}
      >
        <p>A new temporary password is made and shown once. The old password stops working and they are signed out everywhere.</p>
        <p>They must choose their own password when they sign in again.</p>
      </ConfirmDialog>
    </>
  )
}
