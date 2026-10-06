import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { Compass } from 'lucide-react'
import { useAuth } from '../auth/AuthProvider'
import { Button } from '../components/Button'
import { Logo } from '../components/Logo'
import { HOME } from '../lib/nav'

/** `inShell` = shown inside the signed-in layout; otherwise it is a full page of its own. */
export default function NotFoundPage({ inShell = false }: { inShell?: boolean }) {
  const { user } = useAuth()
  const navigate = useNavigate()

  useEffect(() => {
    document.title = 'Page not found · TimeTablePro'
  }, [])

  const card = (
    <div className="animate-rise mx-auto w-full max-w-md rounded-2xl border border-line bg-surface p-8 text-center shadow-card">
      <span className="mx-auto mb-4 flex size-12 items-center justify-center rounded-full bg-brand-soft text-brand-text">
        <Compass className="size-6" aria-hidden="true" />
      </span>
      <h1 className="text-xl font-semibold tracking-tight text-ink">Page not found</h1>
      <p className="mt-2 text-sm leading-relaxed text-muted">
        The page you are looking for doesn&apos;t exist, or it has moved.
      </p>
      <Button className="mt-6" onClick={() => navigate(user ? HOME[user.role] : '/login', { replace: true })}>
        {user ? 'Go to my dashboard' : 'Go to sign in'}
      </Button>
    </div>
  )

  if (inShell) return <div className="py-10">{card}</div>
  return (
    <div className="page-glow flex min-h-screen flex-col items-center justify-center gap-7 px-4 py-10">
      <Logo size={40} />
      {card}
    </div>
  )
}
