import { useId } from 'react'

export function LogoMark({ size = 36 }: { size?: number }) {
  const gradient = useId()
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden="true" className="shrink-0">
      <defs>
        <linearGradient id={gradient} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" style={{ stopColor: 'var(--logo-a)' }} />
          <stop offset="1" style={{ stopColor: 'var(--logo-b)' }} />
        </linearGradient>
      </defs>
      <rect width="64" height="64" rx="16" fill={`url(#${gradient})`} />
      <g fill="#fff">
        <rect x="14" y="14" width="10" height="10" rx="2.5" />
        <rect x="27" y="14" width="10" height="10" rx="2.5" opacity=".55" />
        <rect x="40" y="14" width="10" height="10" rx="2.5" opacity=".9" />
        <rect x="14" y="27" width="10" height="10" rx="2.5" opacity=".55" />
        <rect x="27" y="27" width="10" height="10" rx="2.5" />
        <rect x="40" y="27" width="10" height="10" rx="2.5" opacity=".55" />
        <rect x="14" y="40" width="10" height="10" rx="2.5" opacity=".9" />
        <rect x="27" y="40" width="10" height="10" rx="2.5" opacity=".55" />
        <rect x="40" y="40" width="10" height="10" rx="2.5" />
      </g>
    </svg>
  )
}

export function Logo({ size = 36, className = '' }: { size?: number; className?: string }) {
  return (
    <div className={`flex items-center gap-2.5 ${className}`}>
      <LogoMark size={size} />
      <span className="text-[1.15rem] font-semibold tracking-tight text-ink">
        TimeTable<span className="text-brand-text">Pro</span>
      </span>
    </div>
  )
}
