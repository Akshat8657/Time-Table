import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { FullScreenLoader, ServerUnreachable } from '../components/FullScreen'
import { COOKIE_NAMES, loadConvenience } from '../lib/cookies'
import { HOME, isKnownPath } from '../lib/nav'
import type { Role, User } from '../lib/types'
import { useAuth } from './AuthProvider'

/** Where does a person land right after signing in? */
export function destinationAfterLogin(user: User, from?: string | null): string {
  if (user.must_change_password) return '/change-password'
  if (from && isKnownPath(user.role, from)) return from // the page they originally asked for
  const last = loadConvenience(COOKIE_NAMES.lastView) // remembered only if cookies were accepted
  if (last && isKnownPath(user.role, last)) return last
  return HOME[user.role]
}

/** Only for signed-in people. Everybody else goes to the sign-in page. */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { status, user, retry } = useAuth()
  const location = useLocation()

  if (status === 'loading') return <FullScreenLoader label="Checking your session…" />
  if (status === 'unreachable') return <ServerUnreachable onRetry={retry} />
  if (status !== 'authenticated' || !user) {
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />
  }
  if (user.must_change_password && location.pathname !== '/change-password') {
    return <Navigate to="/change-password" replace />
  }
  return <>{children}</>
}

/** Only for the listed roles. Anybody else is sent to their own home. */
export function RequireRole({ roles, children }: { roles: Role[]; children: ReactNode }) {
  const { user } = useAuth()
  if (!user) return <Navigate to="/login" replace />
  if (!roles.includes(user.role)) return <Navigate to={HOME[user.role]} replace />
  return <>{children}</>
}

/** The sign-in page: signed-in people are sent on to where they belong. */
export function PublicOnly({ children }: { children: ReactNode }) {
  const { status, user, retry } = useAuth()
  const location = useLocation()

  if (status === 'loading') return <FullScreenLoader label="Checking your session…" />
  if (status === 'unreachable') return <ServerUnreachable onRetry={retry} />
  if (status === 'authenticated' && user) {
    const from = (location.state as { from?: string } | null)?.from ?? null
    return <Navigate to={destinationAfterLogin(user, from)} replace />
  }
  return <>{children}</>
}

/** "/" -> the right home, or the sign-in page */
export function RoleRedirect() {
  const { status, user, retry } = useAuth()
  if (status === 'loading') return <FullScreenLoader label="Checking your session…" />
  if (status === 'unreachable') return <ServerUnreachable onRetry={retry} />
  if (status === 'authenticated' && user) return <Navigate to={destinationAfterLogin(user)} replace />
  return <Navigate to="/login" replace />
}
