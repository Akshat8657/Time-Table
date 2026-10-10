import { forwardRef, useId, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from 'react'
import { ChevronDown, TriangleAlert } from 'lucide-react'

const CONTROL =
  'block w-full rounded-xl border bg-field text-[15px] text-ink placeholder:text-subtle transition-colors duration-150 ' +
  'focus:border-accent focus:outline-none focus:ring-4 focus:ring-[color:var(--brand-ring)] disabled:cursor-not-allowed disabled:bg-surface-2 disabled:text-subtle'

function Message({ id, error, hint }: { id: string; error?: string | null; hint?: ReactNode }) {
  if (error) {
    return (
      <p id={`${id}-error`} className="mt-1.5 flex items-center gap-1.5 text-[13px] text-danger">
        <TriangleAlert className="size-3.5 shrink-0" aria-hidden="true" />
        {error}
      </p>
    )
  }
  return hint ? (
    <p id={`${id}-hint`} className="mt-1.5 text-[13px] text-muted">
      {hint}
    </p>
  ) : null
}

export interface Option {
  value: string
  label: string
  disabled?: boolean
}

interface SelectProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, 'size' | 'onChange' | 'value'> {
  label?: string
  value: string
  onChange: (value: string) => void
  options: Option[]
  placeholder?: string
  error?: string | null
  hint?: ReactNode
  /** compact version for tables and toolbars (no label, smaller) */
  compact?: boolean
}

export const SelectField = forwardRef<HTMLSelectElement, SelectProps>(function SelectField(
  { label, value, onChange, options, placeholder, error, hint, compact = false, id, className = '', ...rest },
  ref,
) {
  const autoId = useId()
  const selectId = id ?? autoId
  const describedBy = error ? `${selectId}-error` : hint ? `${selectId}-hint` : undefined
  return (
    <div className={className}>
      {label && (
        <label htmlFor={selectId} className="mb-1.5 block text-[13px] font-medium text-ink">
          {label}
        </label>
      )}
      <div className="relative">
        <select
          ref={ref}
          id={selectId}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy}
          className={`${CONTROL} appearance-none ${compact ? 'h-9 pl-3 pr-8 text-sm' : 'h-11 pl-3.5 pr-10'} ${error ? 'border-danger-line' : 'border-line-strong'}`}
          {...rest}
        >
          {placeholder !== undefined && <option value="">{placeholder}</option>}
          {options.map((o) => (
            <option key={o.value} value={o.value} disabled={o.disabled}>
              {o.label}
            </option>
          ))}
        </select>
        <ChevronDown
          className={`pointer-events-none absolute top-1/2 -translate-y-1/2 text-subtle ${compact ? 'right-2.5 size-4' : 'right-3.5 size-[18px]'}`}
          aria-hidden="true"
        />
      </div>
      <Message id={selectId} error={error} hint={hint} />
    </div>
  )
})

interface TextAreaProps extends Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, 'onChange' | 'value'> {
  label: string
  value: string
  onChange: (value: string) => void
  error?: string | null
  hint?: ReactNode
}

export function TextAreaField({ label, value, onChange, error, hint, id, className = '', rows = 3, ...rest }: TextAreaProps) {
  const autoId = useId()
  const areaId = id ?? autoId
  return (
    <div className={className}>
      <label htmlFor={areaId} className="mb-1.5 block text-[13px] font-medium text-ink">
        {label}
      </label>
      <textarea
        id={areaId}
        rows={rows}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-invalid={error ? true : undefined}
        className={`${CONTROL} resize-y px-3.5 py-2.5 ${error ? 'border-danger-line' : 'border-line-strong'}`}
        {...rest}
      />
      <Message id={areaId} error={error} hint={hint} />
    </div>
  )
}
