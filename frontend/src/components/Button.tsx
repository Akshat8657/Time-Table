import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react'
import { LoaderCircle } from 'lucide-react'

export function Spinner({ className = 'size-4' }: { className?: string }) {
  return <LoaderCircle className={`${className} animate-spin`} aria-hidden="true" />
}

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger'
type Size = 'sm' | 'md' | 'lg'

const VARIANTS: Record<Variant, string> = {
  primary:
    'bg-brand text-white shadow-sm hover:bg-brand-hover hover:text-on-brand-hover active:translate-y-px disabled:bg-brand/60 disabled:text-white disabled:shadow-none',
  secondary:
    'bg-surface text-ink border border-line-strong hover:bg-hover active:translate-y-px disabled:text-subtle disabled:hover:bg-surface',
  ghost: 'bg-transparent text-muted hover:bg-hover hover:text-ink disabled:text-subtle disabled:hover:bg-transparent',
  danger:
    'bg-danger-soft text-danger border border-danger-line hover:brightness-95 active:translate-y-px disabled:opacity-60',
}

const SIZES: Record<Size, string> = {
  sm: 'h-8 px-3 text-[13px] gap-1.5 rounded-lg',
  md: 'h-10 px-4 text-sm gap-2 rounded-lg',
  lg: 'h-11 px-5 text-[15px] gap-2 rounded-xl',
}

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: Size
  loading?: boolean
  fullWidth?: boolean
  icon?: ReactNode
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'primary', size = 'md', loading = false, fullWidth = false, icon, className = '', children, disabled, type = 'button', ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={`inline-flex select-none items-center justify-center font-medium transition-colors duration-150 ${VARIANTS[variant]} ${SIZES[size]} ${fullWidth ? 'w-full' : ''} ${className}`}
      {...rest}
    >
      {loading ? <Spinner /> : icon}
      {children}
    </button>
  )
})
