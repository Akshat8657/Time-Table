import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useAuth } from '../auth/AuthProvider'
import { useToast } from '../components/Toast'
import { api, ApiError } from './api'
import { useNotifications } from './notifications'
import type { MyOffersPayload, Substitution, SubOffer } from './types'

interface OffersValue {
  offers: SubOffer[]
  covering: Substitution[]
  loading: boolean
  accept: (id: string) => Promise<void>
  decline: (id: string) => Promise<void>
  reload: () => Promise<void>
  busyId: string | null
}

const Ctx = createContext<OffersValue | null>(null)

/**
 * The live substitution offers for a teacher (or HOD-as-teacher): fetched on load, whenever a new notification arrives
 * (so an offer pops up the moment it is sent), and every 30 s (so expiries and "already filled" clear by themselves).
 */
export function OffersProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth()
  const toast = useToast()
  const canHaveOffers = user?.role === 'teacher' || user?.role === 'hod'
  const { arrivals } = useNotifications()
  const [offers, setOffers] = useState<SubOffer[]>([])
  const [covering, setCovering] = useState<Substitution[]>([])
  const [loading, setLoading] = useState(canHaveOffers)
  const [busyId, setBusyId] = useState<string | null>(null)
  const inFlight = useRef(false)

  const reload = useCallback(async () => {
    if (!canHaveOffers || inFlight.current) return
    inFlight.current = true
    try {
      const d = await api<MyOffersPayload>('/subs/my-offers')
      setOffers(d.offers)
      setCovering(d.covering)
    } catch {
      /* keep what we had; the next tick retries */
    } finally {
      setLoading(false)
      inFlight.current = false
    }
  }, [canHaveOffers])

  useEffect(() => {
    void reload()
  }, [reload, arrivals])

  useEffect(() => {
    if (!canHaveOffers) return
    const t = window.setInterval(() => {
      if (!document.hidden) void reload()
    }, 30000)
    return () => window.clearInterval(t)
  }, [canHaveOffers, reload])

  const accept = useCallback(
    async (id: string) => {
      setBusyId(id)
      try {
        const r = await api<{ message: string }>(`/subs/${id}/accept`, { method: 'POST' })
        toast.success(r.message || 'You are covering this lecture.')
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) toast.info('Too late - this lecture was already filled.')
        else toast.error(err instanceof ApiError ? err.message : 'Could not accept. Please try again.')
      } finally {
        setBusyId(null)
        await reload()
      }
    },
    [reload, toast],
  )

  const decline = useCallback(
    async (id: string) => {
      setBusyId(id)
      try {
        await api(`/subs/${id}/decline`, { method: 'POST' })
      } catch {
        /* ignore - declining is best effort */
      } finally {
        setBusyId(null)
        await reload()
      }
    },
    [reload],
  )

  const value = useMemo<OffersValue>(
    () => ({ offers, covering, loading, accept, decline, reload, busyId }),
    [offers, covering, loading, accept, decline, reload, busyId],
  )
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useOffers(): OffersValue {
  const ctx = useContext(Ctx)
  if (!ctx) throw new Error('useOffers must be used inside <OffersProvider>')
  return ctx
}
