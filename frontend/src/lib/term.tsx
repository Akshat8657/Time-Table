import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { api } from './api'
import type { Term } from './types'

interface TermContextValue {
  /** the running term ("Odd 2026"), or null when nobody has chosen one yet */
  active: Term | null
  /** every term ever used (the old ones are history) */
  terms: Term[]
  loading: boolean
  /** true once we know the answer (also when there is no active term) */
  ready: boolean
  refresh: () => Promise<void>
  /** makes a term the running one (admin and HOD) */
  activate: (kind: 'odd' | 'even', year: number) => Promise<{ changed: boolean; active: Term }>
}

const TermContext = createContext<TermContextValue | null>(null)

/** Loads the active term once for the whole signed-in area; every screen and the header chip read it from here. */
export function TermProvider({ children }: { children: ReactNode }) {
  const [active, setActive] = useState<Term | null>(null)
  const [terms, setTerms] = useState<Term[]>([])
  const [loading, setLoading] = useState(true)
  const [ready, setReady] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const data = await api<{ active: Term | null; terms: Term[] }>('/terms')
      setActive(data.active)
      setTerms(data.terms)
    } catch {
      /* the header chip simply stays as it was; a screen that needs the term shows its own error */
    } finally {
      setLoading(false)
      setReady(true)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const activate = useCallback(
    async (kind: 'odd' | 'even', year: number) => {
      const result = await api<{ active: Term; changed: boolean }>('/terms/active', { method: 'PUT', body: { kind, year } })
      await refresh()
      return result
    },
    [refresh],
  )

  const value = useMemo(() => ({ active, terms, loading, ready, refresh, activate }), [active, terms, loading, ready, refresh, activate])
  return <TermContext.Provider value={value}>{children}</TermContext.Provider>
}

export function useTerm(): TermContextValue {
  const ctx = useContext(TermContext)
  if (!ctx) throw new Error('useTerm must be used inside <TermProvider>')
  return ctx
}

/** Which semesters run in a term: odd = 1, 3, 5, 7 - even = 2, 4, 6 */
export function runningSemesters(kind: 'odd' | 'even'): string {
  return kind === 'odd' ? 'Sem 1, 3, 5, 7' : 'Sem 2, 4, 6'
}
