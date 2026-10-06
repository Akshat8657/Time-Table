import { forwardRef, useId, useState, type InputHTMLAttributes, type KeyboardEvent, type ReactNode } from 'react'
import { Check, Eye, EyeOff, TriangleAlert } from 'lucide-react'

interface BaseProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'size'> {
  label: string
  error?: string | null
  hint?: ReactNode
  icon?: ReactNode
}

const INPUT_BASE =
  'peer block h-11 w-full rounded-xl border bg-surface text-[15px] text-ink placeholder:text-subtle transition-colors duration-150 ' +
  'focus:border-brand focus:outline-none focus:ring-4 focus:ring-[color:var(--brand-ring)] disabled:cursor-not-allowed disabled:bg-surface-2 disabled:text-subtle'

export const TextField = forwardRef<HTMLInputElement, BaseProps>(function TextField(
  { label, error, hint, icon, id, className = '', ...rest },
  ref,
) {
  const autoId = useId()
  const inputId = id ?? autoId
  const describedBy = error ? `${inputId}-error` : hint ? `${inputId}-hint` : undefined
  return (
    <div className={className}>
      <label htmlFor={inputId} className="mb-1.5 block text-[13px] font-medium text-ink">
        {label}
      </label>
      <div className="relative">
        <input
          ref={ref}
          id={inputId}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy}
          className={`${INPUT_BASE} ${icon ? 'pl-10' : 'pl-3.5'} pr-3.5 ${error ? 'border-danger-line' : 'border-line-strong'}`}
          {...rest}
        />
        {icon && (
          <span className="pointer-events-none absolute inset-y-0 left-0 flex w-10 items-center justify-center text-subtle transition-colors peer-focus:text-brand-text">
            {icon}
          </span>
        )}
      </div>
      {error ? (
        <p id={`${inputId}-error`} className="mt-1.5 flex items-center gap-1.5 text-[13px] text-danger">
          <TriangleAlert className="size-3.5 shrink-0" aria-hidden="true" />
          {error}
        </p>
      ) : hint ? (
        <p id={`${inputId}-hint`} className="mt-1.5 text-[13px] text-muted">
          {hint}
        </p>
      ) : null}
    </div>
  )
})

interface PasswordProps extends BaseProps {
  /** shows a "Caps Lock is on" warning while typing */
  capsWarning?: boolean
}

export const PasswordField = forwardRef<HTMLInputElement, PasswordProps>(function PasswordField(
  { label, error, hint, icon, id, className = '', capsWarning = true, onKeyUp, onKeyDown, onBlur, ...rest },
  ref,
) {
  const autoId = useId()
  const inputId = id ?? autoId
  const [visible, setVisible] = useState(false)
  const [caps, setCaps] = useState(false)

  const watchCaps = (e: KeyboardEvent<HTMLInputElement>) => {
    if (capsWarning && typeof e.getModifierState === 'function') setCaps(e.getModifierState('CapsLock'))
  }
  const describedBy = error ? `${inputId}-error` : hint ? `${inputId}-hint` : undefined

  return (
    <div className={className}>
      <label htmlFor={inputId} className="mb-1.5 block text-[13px] font-medium text-ink">
        {label}
      </label>
      <div className="relative">
        <input
          ref={ref}
          id={inputId}
          type={visible ? 'text' : 'password'}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy}
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
          className={`${INPUT_BASE} ${icon ? 'pl-10' : 'pl-3.5'} pr-11 ${error ? 'border-danger-line' : 'border-line-strong'}`}
          onKeyUp={(e) => {
            watchCaps(e)
            onKeyUp?.(e)
          }}
          onKeyDown={(e) => {
            watchCaps(e)
            onKeyDown?.(e)
          }}
          onBlur={(e) => {
            setCaps(false)
            onBlur?.(e)
          }}
          {...rest}
        />
        {icon && (
          <span className="pointer-events-none absolute inset-y-0 left-0 flex w-10 items-center justify-center text-subtle transition-colors peer-focus:text-brand-text">
            {icon}
          </span>
        )}
        <button
          type="button"
          onClick={() => setVisible((v) => !v)}
          aria-label={visible ? 'Hide password' : 'Show password'}
          aria-pressed={visible}
          className="absolute inset-y-0 right-0 flex w-11 items-center justify-center rounded-r-xl text-subtle transition-colors hover:text-ink"
        >
          {visible ? <EyeOff className="size-[18px]" /> : <Eye className="size-[18px]" />}
        </button>
      </div>
      {caps && !error && (
        <p className="mt-1.5 flex items-center gap-1.5 text-[13px] text-warning">
          <TriangleAlert className="size-3.5 shrink-0" aria-hidden="true" />
          Caps Lock is on
        </p>
      )}
      {error ? (
        <p id={`${inputId}-error`} className="mt-1.5 flex items-center gap-1.5 text-[13px] text-danger">
          <TriangleAlert className="size-3.5 shrink-0" aria-hidden="true" />
          {error}
        </p>
      ) : hint ? (
        <p id={`${inputId}-hint`} className="mt-1.5 text-[13px] text-muted">
          {hint}
        </p>
      ) : null}
    </div>
  )
})

interface CheckboxProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type' | 'size'> {
  label: string
  description?: string
}

export function Checkbox({ label, description, id, className = '', ...rest }: CheckboxProps) {
  const autoId = useId()
  const inputId = id ?? autoId
  return (
    <label htmlFor={inputId} className={`group flex cursor-pointer items-start gap-3 ${className}`}>
      <span className="relative mt-0.5 flex size-[18px] shrink-0 items-center justify-center">
        <input id={inputId} type="checkbox" className="peer absolute inset-0 size-full cursor-pointer opacity-0" {...rest} />
        <span className="pointer-events-none absolute inset-0 rounded-[5px] border border-line-strong bg-surface transition-colors peer-checked:border-brand peer-checked:bg-brand peer-focus-visible:ring-4 peer-focus-visible:ring-[color:var(--brand-ring)] group-hover:border-brand" />
        <Check className="pointer-events-none relative size-3 text-white opacity-0 transition-opacity peer-checked:opacity-100" strokeWidth={3.5} />
      </span>
      <span className="min-w-0 text-sm leading-snug">
        <span className="font-medium text-ink">{label}</span>
        {description && <span className="mt-0.5 block text-[13px] text-muted">{description}</span>}
      </span>
    </label>
  )
}
