import { ServerOff } from 'lucide-react'
import { Button, Spinner } from './Button'
import { LogoMark } from './Logo'

export function FullScreenLoader({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="page-glow flex min-h-screen flex-col items-center justify-center gap-4" role="status" aria-live="polite">
      <LogoMark size={44} />
      <div className="flex items-center gap-2 text-sm text-muted">
        <Spinner />
        {label}
      </div>
    </div>
  )
}

export function ServerUnreachable({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="page-glow flex min-h-screen items-center justify-center px-4">
      <div className="animate-rise w-full max-w-md rounded-2xl border border-line bg-surface p-8 text-center shadow-card">
        <div className="mx-auto mb-4 flex size-12 items-center justify-center rounded-full bg-warning-soft text-warning">
          <ServerOff className="size-6" aria-hidden="true" />
        </div>
        <h1 className="text-xl font-semibold tracking-tight text-ink">We can&apos;t reach TimeTablePro</h1>
        <p className="mt-2 text-sm leading-relaxed text-muted">
          The TimeTablePro server doesn&apos;t seem to be running, or the internet is down. Please make sure it is
          started (double-click <span className="font-medium text-ink">start.bat</span>), then try again.
        </p>
        <Button className="mt-6" onClick={onRetry}>
          Try again
        </Button>
      </div>
    </div>
  )
}
