import { useLocation, useNavigate } from 'react-router-dom'
import { ArrowLeft, Hammer } from 'lucide-react'
import { useAuth } from '../auth/AuthProvider'
import { Button } from '../components/Button'
import { PageHeader } from '../components/Panel'
import { findItem, HOME } from '../lib/nav'

export default function PlaceholderPage() {
  const { user } = useAuth()
  const { pathname } = useLocation()
  const navigate = useNavigate()
  if (!user) return null

  const item = findItem(user.role, pathname)
  const Icon = item?.icon ?? Hammer
  const when = item?.phase ?? 'a later phase'

  return (
    <>
      <PageHeader title={item?.label ?? 'Coming soon'} subtitle={item?.about} badge={item?.phase ?? undefined} />
      <div className="rounded-2xl border border-dashed border-line-strong bg-surface px-6 py-14 text-center">
        <span className="mx-auto mb-5 flex size-16 items-center justify-center rounded-2xl bg-brand-soft text-brand-text">
          <Icon className="size-8" aria-hidden="true" />
        </span>
        <h2 className="text-lg font-semibold text-ink">{item?.phase ? `Coming in ${item.phase}` : 'Coming soon'}</h2>
        <p className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-muted">
          This screen already has its place in the menu. It will be built in {when}, exactly as described in the plan.
        </p>
        <Button variant="secondary" className="mt-6" icon={<ArrowLeft className="size-4" />} onClick={() => navigate(HOME[user.role])}>
          Back to dashboard
        </Button>
      </div>
    </>
  )
}
