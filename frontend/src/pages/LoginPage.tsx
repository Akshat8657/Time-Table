import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Lock, Mail } from 'lucide-react'
import { useAuth } from '../auth/AuthProvider'
import { Alert } from '../components/Alert'
import { Button } from '../components/Button'
import { Checkbox, PasswordField, TextField } from '../components/Field'
import { Logo } from '../components/Logo'
import { ThemeToggle } from '../components/ThemeToggle'
import { useConsent } from '../consent/ConsentProvider'
import { ApiError } from '../lib/api'
import { COOKIE_NAMES, loadConvenience, removeConvenience, storeConvenience } from '../lib/cookies'

const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/

function mmss(totalSeconds: number): string {
  const m = Math.floor(totalSeconds / 60)
  const s = totalSeconds % 60
  return `${m}:${String(s).padStart(2, '0')}`
}

export default function LoginPage() {
  const { login, notice, dismissNotice } = useAuth()
  const { consent, openSettings } = useConsent()

  // Pre-filled ONLY if cookies were accepted earlier and "Remember me" was ticked then.
  const [savedEmail] = useState(() => {
    const value = loadConvenience(COOKIE_NAMES.email)
    return value && EMAIL_RE.test(value) && value.length <= 254 ? value : ''
  })
  const [email, setEmail] = useState(savedEmail)
  const [password, setPassword] = useState('')
  const [remember, setRemember] = useState(savedEmail !== '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [fieldErrors, setFieldErrors] = useState<{ email?: string; password?: string }>({})
  const [lockedUntil, setLockedUntil] = useState<number | null>(null)
  const [now, setNow] = useState(() => Date.now())

  const emailRef = useRef<HTMLInputElement>(null)
  const passwordRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    document.title = 'Sign in · TimeTablePro'
    ;(savedEmail ? passwordRef : emailRef).current?.focus()
  }, [savedEmail])

  // live countdown while sign-in is paused after too many wrong attempts
  useEffect(() => {
    if (!lockedUntil) return
    const timer = window.setInterval(() => {
      const current = Date.now()
      setNow(current)
      if (current >= lockedUntil) {
        setLockedUntil(null)
        setError(null)
      }
    }, 1000)
    return () => window.clearInterval(timer)
  }, [lockedUntil])

  const remaining = lockedUntil ? Math.max(0, Math.ceil((lockedUntil - now) / 1000)) : 0
  const locked = remaining > 0

  async function onSubmit(event: FormEvent) {
    event.preventDefault()
    if (busy || locked) return

    const problems: { email?: string; password?: string } = {}
    const cleanEmail = email.trim()
    if (!cleanEmail) problems.email = 'Please enter your email address.'
    else if (!EMAIL_RE.test(cleanEmail)) problems.email = 'That doesn’t look like an email address.'
    if (!password) problems.password = 'Please enter your password.'
    setFieldErrors(problems)
    setError(null)
    if (problems.email) return emailRef.current?.focus()
    if (problems.password) return passwordRef.current?.focus()

    setBusy(true)
    try {
      await login(cleanEmail, password, remember)
      // Remember the email for next time - only with cookie consent AND only if "Remember me" is ticked.
      if (remember) storeConvenience(COOKIE_NAMES.email, cleanEmail.toLowerCase())
      else removeConvenience(COOKIE_NAMES.email)
      // (the route guard now sends the person to the right page)
    } catch (err) {
      setBusy(false)
      if (err instanceof ApiError) {
        if (err.code === 'TOO_MANY_ATTEMPTS' || err.code === 'UPSTREAM_BUSY') {
          const seconds = err.retryAfter ?? 60
          setNow(Date.now())
          setLockedUntil(Date.now() + seconds * 1000)
          setError(err.message)
          return
        }
        if (err.code === 'VALIDATION_ERROR') {
          setFieldErrors({ email: err.fields.email, password: err.fields.password })
          return
        }
        setError(err.message)
        if (err.code === 'INVALID_CREDENTIALS') {
          setPassword('')
          // the fields are re-enabled on the next paint - focus after that
          window.setTimeout(() => passwordRef.current?.focus(), 0)
        }
        return
      }
      setError('Something went wrong. Please try again.')
    }
  }

  const rememberHint =
    consent === 'accepted'
      ? 'Stay signed in on this device, and pre-fill your email next time.'
      : consent === 'declined'
        ? 'Stay signed in on this device. (Cookies are declined, so your email won’t be pre-filled.)'
        : 'Stay signed in on this device. Not for shared computers.'

  return (
    <div className="page-glow relative flex min-h-screen flex-col items-center justify-center px-4 py-10">
      <div className="absolute right-4 top-4 sm:right-6 sm:top-5">
        <ThemeToggle />
      </div>

      <main className="animate-rise w-full max-w-[26.5rem]">
        <div className="mb-7 flex justify-center">
          <Logo size={40} />
        </div>

        <div className="rounded-2xl border border-line bg-surface p-7 shadow-card sm:p-8">
          <h1 className="text-[1.6rem] font-semibold leading-tight tracking-tight text-ink">Welcome back</h1>
          <p className="mt-1 text-sm text-muted">Sign in to manage your college timetable.</p>

          {notice && (
            <Alert tone={notice.tone} className="mt-5" onDismiss={dismissNotice}>
              {notice.message}
            </Alert>
          )}
          {error && (
            <Alert tone="danger" className="mt-5">
              {error}
              {locked && (
                <span className="mt-1 block font-medium tabular-nums">Try again in {mmss(remaining)}</span>
              )}
            </Alert>
          )}

          <form onSubmit={onSubmit} noValidate className="mt-6 space-y-4">
            <TextField
              ref={emailRef}
              label="Email address"
              name="email"
              type="email"
              inputMode="email"
              autoComplete="username"
              placeholder="you@college.edu"
              icon={<Mail className="size-[18px]" />}
              value={email}
              onChange={(e) => {
                setEmail(e.target.value)
                if (fieldErrors.email) setFieldErrors((f) => ({ ...f, email: undefined }))
              }}
              error={fieldErrors.email}
              disabled={busy}
            />
            <PasswordField
              ref={passwordRef}
              label="Password"
              name="password"
              autoComplete="current-password"
              placeholder="Enter your password"
              icon={<Lock className="size-[18px]" />}
              value={password}
              onChange={(e) => {
                setPassword(e.target.value)
                if (fieldErrors.password) setFieldErrors((f) => ({ ...f, password: undefined }))
              }}
              error={fieldErrors.password}
              disabled={busy}
            />

            <Checkbox
              name="remember"
              label="Remember me"
              description={rememberHint}
              checked={remember}
              onChange={(e) => setRemember(e.target.checked)}
              disabled={busy}
            />

            <Button type="submit" size="lg" fullWidth loading={busy} disabled={locked} className="mt-2">
              {busy ? 'Signing in…' : locked ? `Try again in ${mmss(remaining)}` : 'Sign in'}
            </Button>
          </form>

          <p className="mt-6 text-center text-[13px] leading-relaxed text-muted">
            Forgot your password? Ask your HOD or administrator to reset it for you.
          </p>
        </div>

        <p className="mt-6 flex items-center justify-center gap-2 text-xs text-subtle">
          <span>© {new Date().getFullYear()} TimeTablePro</span>
          <span aria-hidden="true">·</span>
          <button type="button" onClick={openSettings} className="underline-offset-2 hover:text-ink hover:underline">
            Cookie settings
          </button>
        </p>
      </main>
    </div>
  )
}
