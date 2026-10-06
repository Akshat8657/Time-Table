import type { ReactNode } from 'react'
import { CircleAlert, CircleCheck, Info, TriangleAlert, X } from 'lucide-react'

type Tone = 'danger' | 'warning' | 'info' | 'success'

const TONES: Record<Tone, { box: string; icon: ReactNode }> = {
  danger: { box: 'bg-danger-soft border-danger-line text-danger', icon: <CircleAlert className="size-[18px]" /> },
  warning: { box: 'bg-warning-soft border-warning-line text-warning', icon: <TriangleAlert className="size-[18px]" /> },
  info: { box: 'bg-info-soft border-info-line text-info', icon: <Info className="size-[18px]" /> },
  success: { box: 'bg-success-soft border-success-line text-success', icon: <CircleCheck className="size-[18px]" /> },
}

interface AlertProps {
  tone?: Tone
  title?: string
  children: ReactNode
  onDismiss?: () => void
  className?: string
}

export function Alert({ tone = 'info', title, children, onDismiss, className = '' }: AlertProps) {
  const { box, icon } = TONES[tone]
  return (
    <div
      role={tone === 'danger' || tone === 'warning' ? 'alert' : 'status'}
      className={`flex items-start gap-3 rounded-xl border px-3.5 py-3 text-sm leading-snug ${box} ${className}`}
    >
      <span className="mt-px shrink-0">{icon}</span>
      <div className="min-w-0 flex-1">
        {title && <p className="font-semibold">{title}</p>}
        <div className={title ? 'mt-0.5 opacity-90' : ''}>{children}</div>
      </div>
      {onDismiss && (
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss message"
          className="-m-1 shrink-0 rounded-md p-1 opacity-70 transition hover:opacity-100"
        >
          <X className="size-4" />
        </button>
      )}
    </div>
  )
}
