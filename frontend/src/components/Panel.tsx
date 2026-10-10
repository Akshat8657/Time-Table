import type { ReactNode } from 'react'
import type { LucideIcon } from 'lucide-react'

export function PageHeader({ title, subtitle, badge }: { title: string; subtitle?: ReactNode; badge?: string }) {
  return (
    <header className="mb-6 sm:mb-8">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-[1.65rem] font-semibold leading-tight tracking-tight text-ink sm:text-[1.9rem]">{title}</h1>
        {badge && (
          <span className="rounded-full bg-brand-soft px-2.5 py-0.5 text-xs font-semibold text-brand-text">{badge}</span>
        )}
      </div>
      {subtitle && <p className="mt-1.5 max-w-2xl text-[15px] leading-relaxed text-muted">{subtitle}</p>}
    </header>
  )
}

export function Panel({
  title,
  icon: Icon,
  action,
  children,
  className = '',
}: {
  title: string
  icon?: LucideIcon
  action?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <section className={`min-w-0 rounded-2xl border border-line bg-surface shadow-card ${className}`}>
      <div className="flex items-center justify-between gap-3 border-b border-line px-5 py-4">
        <h2 className="flex items-center gap-2.5 text-[15px] font-semibold text-ink">
          {Icon && <Icon className="size-[18px] text-brand-text" aria-hidden="true" />}
          {title}
        </h2>
        {action}
      </div>
      <div className="p-5">{children}</div>
    </section>
  )
}

export function StatCard({
  icon: Icon,
  label,
  value,
  caption,
}: {
  icon: LucideIcon
  label: string
  value: ReactNode
  caption: string
}) {
  return (
    <div className="rounded-2xl border border-line bg-surface p-5 shadow-card">
      <div className="flex items-center justify-between">
        <p className="text-[13px] font-medium text-muted">{label}</p>
        <span className="flex size-9 items-center justify-center rounded-xl bg-brand-soft text-brand-text">
          <Icon className="size-[18px]" aria-hidden="true" />
        </span>
      </div>
      <p className="mt-3 text-[1.9rem] font-semibold leading-none tracking-tight text-ink tabular-nums">{value}</p>
      <p className="mt-2 text-xs text-subtle">{caption}</p>
    </div>
  )
}

export function EmptyState({ icon: Icon, title, children }: { icon: LucideIcon; title: string; children: ReactNode }) {
  return (
    <div className="flex flex-col items-center px-4 py-8 text-center">
      <span className="mb-3 flex size-12 items-center justify-center rounded-2xl bg-surface-2 text-subtle ring-1 ring-line">
        <Icon className="size-6" aria-hidden="true" />
      </span>
      <p className="text-[15px] font-semibold text-ink">{title}</p>
      <p className="mt-1 max-w-sm text-sm leading-relaxed text-muted">{children}</p>
    </div>
  )
}

export function Skeleton({ className = '' }: { className?: string }) {
  return <div className={`skeleton ${className}`} aria-hidden="true" />
}
