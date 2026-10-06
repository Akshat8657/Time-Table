import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { Check, Circle, KeyRound, Lock, LogOut } from 'lucide-react'
import { useAuth } from '../auth/AuthProvider'
import { Alert } from '../components/Alert'
import { Button } from '../components/Button'
import { PasswordField } from '../components/Field'
import { Logo } from '../components/Logo'
import { ThemeToggle } from '../components/ThemeToggle'
import { ApiError } from '../lib/api'
import { HOME } from '../lib/nav'

const STRENGTH = [
  { label: 'Too short', bar: 'bg-line-strong', text: 'text-subtle' },
  { label: 'Weak', bar: 'bg-danger', text: 'text-danger' },
  { label: 'Fair', bar: 'bg-warning', text: 'text-warning' },
  { label: 'Good', bar: 'bg-info', text: 'text-info' },
  { label: 'Strong', bar: 'bg-success', text: 'text-success' },
] as const

/** Only a friendly hint - the server makes the real decision. */
function strengthOf(pw: string): number {
  if (pw.length < 8) return 0
  let score = 1
  if (pw.length >= 12) score++
  if (/[a-z]/.test(pw) && /[A-Z]/.test(pw)) score++
  if (/\d/.test(pw) && /[^A-Za-z0-9]/.test(pw)) score++
  return Math.min(score, 4)
}

function Rule({ ok, children }: { ok: boolean; children: string }) {
  return (
    <li className={`flex items-center gap-2 text-[13px] ${ok ? 'text-success' : 'text-muted'}`}>
      {ok ? <Check className="size-3.5" strokeWidth={3} aria-hidden="true" /> : <Circle className="size-3.5 opacity-50" aria-hidden="true" />}
      <span>{children}</span>
      <span className="sr-only">{ok ? '(done)' : '(not yet)'}</span>
    </li>
  )
}

export default function ChangePasswordPage() {
  const { user, changePassword, logout } = useAuth()
  const navigate = useNavigate()

  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [fieldErrors, setFieldErrors] = useState<Partial<Record<'current' | 'next' | 'confirm', string>>>({})

  const currentRef = useRef<HTMLInputElement>(null)
  const nextRef = useRef<HTMLInputElement>(null)
  const confirmRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    document.title = 'Set a new password · TimeTablePro'
    currentRef.current?.focus()
  }, [])

  const forced = user?.must_change_password ?? false
  const score = useMemo(() => strengthOf(next), [next])
  const rules = {
    length: next.length >= 8,
    letter: /[A-Za-z]/.test(next),
    number: /\d/.test(next),
    different: next.length > 0 && next !== current,
    match: confirm.length > 0 && confirm === next,
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault()
    if (busy) return
    setError(null)

    const problems: typeof fieldErrors = {}
    if (!current) problems.current = forced ? 'Please enter the temporary password you were given.' : 'Please enter your current password.'
    if (!next) problems.next = 'Please choose a new password.'
    else if (!(rules.length && rules.letter && rules.number)) problems.next = 'Your new password needs 8+ characters with a letter and a number.'
    else if (!rules.different) problems.next = 'Your new password must be different from the current one.'
    if (!confirm) problems.confirm = 'Please type the new password again.'
    else if (confirm !== next) problems.confirm = 'The two new passwords don’t match.'
    setFieldErrors(problems)
    if (problems.current) return currentRef.current?.focus()
    if (problems.next) return nextRef.current?.focus()
    if (problems.confirm) return confirmRef.current?.focus()

    setBusy(true)
    try {
      const updated = await changePassword(current, next)
      navigate(HOME[updated.role], {
        replace: true,
        state: { flash: 'Your password was updated. You’re all set.' },
      })
    } catch (err) {
      setBusy(false)
      if (err instanceof ApiError) {
        const f = err.fields
        if (f.current_password || f.new_password) {
          setFieldErrors({ current: f.current_password, next: f.new_password })
          // the fields are re-enabled on the next paint - focus after that
          window.setTimeout(() => (f.current_password ? currentRef : nextRef).current?.focus(), 0)
          return
        }
        setError(err.message)
        return
      }
      setError('Something went wrong. Please try again.')
    }
  }

  const level = STRENGTH[score]

  return (
    <div className="page-glow relative flex min-h-screen flex-col items-center justify-center px-4 py-10">
      <div className="absolute right-4 top-4 sm:right-6 sm:top-5">
        <ThemeToggle />
      </div>

      <main className="animate-rise w-full max-w-[28rem]">
        <div className="mb-7 flex justify-center">
          <Logo size={40} />
        </div>

        <div className="rounded-2xl border border-line bg-surface p-7 shadow-card sm:p-8">
          <div className="mb-4 flex size-11 items-center justify-center rounded-xl bg-brand-soft text-brand-text">
            <KeyRound className="size-5" aria-hidden="true" />
          </div>
          <h1 className="text-[1.45rem] font-semibold leading-tight tracking-tight text-ink">Set a new password</h1>
          <p className="mt-1 text-sm leading-relaxed text-muted">
            {forced
              ? 'For your security, please replace your temporary password before you continue.'
              : 'Choose a new password for your account.'}
          </p>

          {error && (
            <Alert tone="danger" className="mt-5">
              {error}
            </Alert>
          )}

          <form onSubmit={onSubmit} noValidate className="mt-6 space-y-4">
            {/* lets password managers know which account this is */}
            <input type="text" name="username" autoComplete="username" value={user?.email ?? ''} readOnly hidden />

            <PasswordField
              ref={currentRef}
              label={forced ? 'Temporary password' : 'Current password'}
              name="current-password"
              autoComplete="current-password"
              icon={<Lock className="size-[18px]" />}
              value={current}
              onChange={(e) => {
                setCurrent(e.target.value)
                setFieldErrors((f) => ({ ...f, current: undefined }))
              }}
              error={fieldErrors.current}
              disabled={busy}
            />

            <div>
              <PasswordField
                ref={nextRef}
                label="New password"
                name="new-password"
                autoComplete="new-password"
                icon={<Lock className="size-[18px]" />}
                value={next}
                onChange={(e) => {
                  setNext(e.target.value)
                  setFieldErrors((f) => ({ ...f, next: undefined }))
                }}
                error={fieldErrors.next}
                disabled={busy}
              />
              <div className="mt-2.5" aria-live="polite">
                <div className="flex gap-1.5" aria-hidden="true">
                  {[1, 2, 3, 4].map((i) => (
                    <span
                      key={i}
                      className={`h-1.5 flex-1 rounded-full transition-colors ${next && score >= i ? level.bar : 'bg-line'}`}
                    />
                  ))}
                </div>
                <p className={`mt-1.5 h-4 text-xs font-medium ${next ? level.text : 'text-transparent'}`}>
                  {next ? level.label : '.'}
                </p>
              </div>
            </div>

            <PasswordField
              ref={confirmRef}
              label="Confirm new password"
              name="confirm-password"
              autoComplete="new-password"
              icon={<Lock className="size-[18px]" />}
              value={confirm}
              onChange={(e) => {
                setConfirm(e.target.value)
                setFieldErrors((f) => ({ ...f, confirm: undefined }))
              }}
              error={fieldErrors.confirm}
              disabled={busy}
            />

            <ul className="space-y-1.5 rounded-xl border border-line bg-surface-2 px-4 py-3">
              <Rule ok={rules.length}>At least 8 characters</Rule>
              <Rule ok={rules.letter}>Contains a letter</Rule>
              <Rule ok={rules.number}>Contains a number</Rule>
              <Rule ok={rules.different}>Different from your current password</Rule>
              <Rule ok={rules.match}>Both new passwords match</Rule>
            </ul>

            <Button type="submit" size="lg" fullWidth loading={busy}>
              {busy ? 'Updating…' : 'Update password'}
            </Button>
          </form>

          <div className="mt-5 border-t border-line pt-4 text-center">
            <button
              type="button"
              onClick={() => {
                navigate('/login', { replace: true })
                void logout()
              }}
              className="inline-flex items-center gap-1.5 text-[13px] font-medium text-muted transition-colors hover:text-ink"
            >
              <LogOut className="size-3.5" aria-hidden="true" />
              Sign out instead
            </button>
          </div>
        </div>
      </main>
    </div>
  )
}
