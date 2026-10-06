import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react'
import { getConsent, saveConsent, type Consent } from '../lib/cookies'

interface ConsentContextValue {
  /** null = the visitor has not decided yet */
  consent: Consent | null
  bannerOpen: boolean
  accept: () => void
  decline: () => void
  openSettings: () => void
  closeBanner: () => void
}

const ConsentContext = createContext<ConsentContextValue | null>(null)

export function ConsentProvider({ children }: { children: ReactNode }) {
  const [consent, setConsent] = useState<Consent | null>(() => getConsent())
  const [bannerOpen, setBannerOpen] = useState<boolean>(() => getConsent() === null)

  const choose = useCallback((choice: Consent) => {
    saveConsent(choice)
    setConsent(choice)
    setBannerOpen(false)
  }, [])

  const value = useMemo<ConsentContextValue>(
    () => ({
      consent,
      bannerOpen,
      accept: () => choose('accepted'),
      decline: () => choose('declined'),
      openSettings: () => setBannerOpen(true),
      closeBanner: () => setBannerOpen(false),
    }),
    [consent, bannerOpen, choose],
  )

  return <ConsentContext.Provider value={value}>{children}</ConsentContext.Provider>
}

export function useConsent(): ConsentContextValue {
  const ctx = useContext(ConsentContext)
  if (!ctx) throw new Error('useConsent must be used inside <ConsentProvider>')
  return ctx
}
