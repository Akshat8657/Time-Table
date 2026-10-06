import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { api, apiLogout, ApiError, ensureToken, onAuthPayload, onSessionLost, refreshSession, tokenStore } from '../lib/api'
import type { AuthPayload, MePayload, Notice, SessionInfo, User } from '../lib/types'

type Status = 'loading' | 'authenticated' | 'anonymous' | 'unreachable'

interface AuthContextValue {
  status: Status
  user: User | null
  session: SessionInfo | null
  /** a message for the sign-in page ("you were signed out because ...") */
  notice: Notice | null
  login: (email: string, password: string, rememberMe: boolean) => Promise<User>
  logout: (notice?: Notice) => Promise<void>
  changePassword: (currentPassword: string, newPassword: string) => Promise<User>
  dismissNotice: () => void
  retry: () => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

const IDLE_NOTICE: Notice = {
  tone: 'info',
  message: 'You were signed out because you were inactive for a while. Please sign in again.',
}
const TICK_MS = 15_000 // how often the inactivity watcher looks at the clock
const PING_EVERY_MS = 5 * 60_000 // tell the server "I'm still here" at most this often (and only if the person is active)
const CHANNEL_NAME = 'timetablepro-auth'

/** One shared start-up check, so React's double start-up in development only asks the server once. */
let bootPromise: Promise<AuthPayload> | null = null
function bootOnce(): Promise<AuthPayload> {
  bootPromise ??= refreshSession()
  return bootPromise
}

function noticeFor(err: ApiError): Notice | null {
  switch (err.code) {
    case 'SESSION_EXPIRED':
    case 'SESSION_REVOKED':
    case 'INVALID_SESSION':
      return { tone: 'info', message: err.message }
    case 'ACCOUNT_DISABLED':
      return { tone: 'warning', message: err.message }
    default:
      return null
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Status>('loading')
  const [user, setUser] = useState<User | null>(null)
  const [session, setSession] = useState<SessionInfo | null>(null)
  const [notice, setNotice] = useState<Notice | null>(null)

  const lastActivity = useRef(Date.now()) // last time the PERSON did something (clicked, typed, scrolled)
  const lastPing = useRef(Date.now())
  const channel = useRef<BroadcastChannel | null>(null)

  const apply = useCallback((data: { user: User; session: SessionInfo }, syncActivity: boolean) => {
    setUser(data.user)
    setSession(data.session)
    setStatus('authenticated')
    if (syncActivity) {
      // the server tells us how long the person has already been idle
      const idleSoFar = (data.session.idle_timeout_seconds - data.session.idle_expires_in_seconds) * 1000
      lastActivity.current = Date.now() - idleSoFar
      lastPing.current = Date.now()
    }
  }, [])

  const clearLocal = useCallback((next: Notice | null) => {
    tokenStore.clear()
    setUser(null)
    setSession(null)
    setNotice(next)
    setStatus('anonymous')
  }, [])

  // ------------------------------------------------------------ start-up: is there a valid login already?
  const runBoot = useCallback(() => {
    setStatus('loading')
    bootOnce().then(
      (payload) => apply(payload, true),
      (err: unknown) => {
        if (err instanceof ApiError) {
          if (err.status === 0 || err.status >= 500 || err.code === 'AUTH_UNAVAILABLE') {
            setStatus('unreachable')
            return
          }
          clearLocal(noticeFor(err))
          return
        }
        clearLocal(null)
      },
    )
  }, [apply, clearLocal])

  useEffect(() => {
    runBoot()
  }, [runBoot])

  const retry = useCallback(() => {
    bootPromise = null
    runBoot()
  }, [runBoot])

  // ------------------------------------------------------------ the API tells us the login is over
  useEffect(() => {
    const offLost = onSessionLost((info) => {
      clearLocal({ tone: info.code === 'ACCOUNT_DISABLED' ? 'warning' : 'info', message: info.message })
      channel.current?.postMessage('logout')
    })
    const offPayload = onAuthPayload((payload) => apply(payload, false))
    return () => {
      offLost()
      offPayload()
    }
  }, [apply, clearLocal])

  // ------------------------------------------------------------ signing out in one tab signs out every tab
  useEffect(() => {
    if (typeof BroadcastChannel === 'undefined') return
    const ch = new BroadcastChannel(CHANNEL_NAME)
    channel.current = ch
    ch.onmessage = (event) => {
      if (event.data === 'logout') clearLocal(null)
    }
    return () => {
      ch.close()
      channel.current = null
    }
  }, [clearLocal])

  // ------------------------------------------------------------ actions
  const login = useCallback(
    async (email: string, password: string, rememberMe: boolean) => {
      const payload = await api<AuthPayload>('/auth/login', {
        method: 'POST',
        body: { email, password, remember_me: rememberMe },
        auth: false,
      })
      tokenStore.set(payload.access_token, payload.expires_in)
      setNotice(null)
      apply(payload, true)
      return payload.user
    },
    [apply],
  )

  const logout = useCallback(
    async (next?: Notice) => {
      const pending = apiLogout() // starts right now, while the token is still known
      clearLocal(next ?? null)
      channel.current?.postMessage('logout')
      await pending
    },
    [clearLocal],
  )

  const changePassword = useCallback(
    async (currentPassword: string, newPassword: string) => {
      const data = await api<MePayload>('/auth/change-password', {
        method: 'POST',
        body: { current_password: currentPassword, new_password: newPassword },
      })
      apply(data, true)
      return data.user
    },
    [apply],
  )

  // ------------------------------------------------------------ auto sign-out after inactivity (+ keep the token fresh)
  const idleSeconds = session?.idle_timeout_seconds ?? null
  useEffect(() => {
    if (status !== 'authenticated' || idleSeconds === null) return
    const idleLimitMs = idleSeconds * 1000
    // normally every 5 minutes; for a very short idle time (testing) several times inside the idle window
    const pingEveryMs = Math.min(PING_EVERY_MS, idleLimitMs / 4)
    const tickMs = Math.min(TICK_MS, Math.max(1000, idleLimitMs / 8))

    const markActive = () => {
      lastActivity.current = Date.now()
    }
    const events = ['pointerdown', 'keydown', 'wheel', 'touchstart', 'scroll'] as const
    events.forEach((name) => window.addEventListener(name, markActive, { passive: true, capture: true }))

    const tick = () => {
      const now = Date.now()
      if (now - lastActivity.current >= idleLimitMs) {
        void logout(IDLE_NOTICE)
        return
      }
      // while the person really is using the page, tell the server so its inactivity clock restarts
      if (lastActivity.current > lastPing.current && now - lastPing.current >= pingEveryMs) {
        lastPing.current = now
        api<MePayload>('/auth/me')
          .then((data) => apply(data, false))
          .catch(() => undefined) // a finished login is handled globally
      }
      ensureToken().catch(() => undefined)
    }
    const timer = window.setInterval(tick, tickMs)
    const onVisible = () => {
      if (document.visibilityState === 'visible') tick()
    }
    document.addEventListener('visibilitychange', onVisible)

    return () => {
      events.forEach((name) => window.removeEventListener(name, markActive, { capture: true }))
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [status, idleSeconds, logout, apply])

  const value = useMemo<AuthContextValue>(
    () => ({
      status,
      user,
      session,
      notice,
      login,
      logout,
      changePassword,
      dismissNotice: () => setNotice(null),
      retry,
    }),
    [status, user, session, notice, login, logout, changePassword, retry],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>')
  return ctx
}
