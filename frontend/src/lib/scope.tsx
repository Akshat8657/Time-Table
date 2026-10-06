import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useAuth } from '../auth/AuthProvider'
import { api } from './api'
import type { School } from './types'

interface ScopeValue {
  /** the schools this person can work with (an HOD: only his own) */
  schools: School[]
  /** the school the screens work on right now. HOD: always his own. Admin: the one chosen in the bar. */
  schoolId: string | null
  schoolName: string | null
  setSchoolId: (id: string) => void
  isAdmin: boolean
  /** false while the admin's school list is still loading */
  ready: boolean
  /** adds ?school_id=... for the admin (the HOD never sends one - the server already knows his school) */
  path: (base: string) => string
  /** the same for a request body */
  body: () => { school_id?: string }
}

const ScopeContext = createContext<ScopeValue | null>(null)

/**
 * "Which school am I working on?" - an HOD is locked to his own school (decided by the server, not by this screen).
 * The administrator picks one school at the top of the page and every screen follows it.
 */
export function SchoolScopeProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth()
  const isAdmin = user?.role === 'admin'
  const [schools, setSchools] = useState<School[]>([])
  const [chosen, setChosen] = useState<string | null>(null)
  const [ready, setReady] = useState(!isAdmin)

  useEffect(() => {
    if (!user) return
    if (!isAdmin) {
      setSchools(user.school_id ? [{ id: user.school_id, name: user.school_name ?? 'My school', is_active: true }] : [])
      setReady(true)
      return
    }
    let cancelled = false
    api<{ schools: School[] }>('/structure/schools')
      .then((d) => {
        if (cancelled) return
        setSchools(d.schools)
        setChosen((current) => current ?? d.schools.find((s) => s.is_active)?.id ?? d.schools[0]?.id ?? null)
      })
      .catch(() => undefined)
      .finally(() => !cancelled && setReady(true))
    return () => {
      cancelled = true
    }
  }, [user, isAdmin])

  const schoolId = isAdmin ? chosen : (user?.school_id ?? null)
  const school = schools.find((s) => s.id === schoolId) ?? null

  const path = useCallback(
    (base: string) => (isAdmin && schoolId ? `${base}${base.includes('?') ? '&' : '?'}school_id=${schoolId}` : base),
    [isAdmin, schoolId],
  )
  const body = useCallback(() => (isAdmin && schoolId ? { school_id: schoolId } : {}), [isAdmin, schoolId])

  const value = useMemo<ScopeValue>(
    () => ({ schools, schoolId, schoolName: school?.name ?? null, setSchoolId: setChosen, isAdmin, ready, path, body }),
    [schools, schoolId, school, isAdmin, ready, path, body],
  )
  return <ScopeContext.Provider value={value}>{children}</ScopeContext.Provider>
}

export function useSchoolScope(): ScopeValue {
  const ctx = useContext(ScopeContext)
  if (!ctx) throw new Error('useSchoolScope must be used inside <SchoolScopeProvider>')
  return ctx
}
