import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useConsent } from '../consent/ConsentProvider'
import { COOKIE_NAMES, storeConvenience } from '../lib/cookies'

export type Theme = 'light' | 'dark'

interface ThemeContextValue {
  theme: Theme
  toggle: () => void
}

const ThemeContext = createContext<ThemeContextValue | null>(null)

/** index.html already picked the right theme before the first paint - we just read it. */
function initialTheme(): Theme {
  return document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light'
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const { consent } = useConsent()
  const [theme, setTheme] = useState<Theme>(initialTheme)
  const chosenByUser = useRef(false)

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    document.documentElement.style.colorScheme = theme
  }, [theme])

  // Remember the theme ONLY after an explicit choice AND only with cookie consent.
  useEffect(() => {
    if (consent === 'accepted' && chosenByUser.current) storeConvenience(COOKIE_NAMES.theme, theme)
  }, [consent, theme])

  const toggle = useCallback(() => {
    chosenByUser.current = true
    setTheme((current) => (current === 'dark' ? 'light' : 'dark'))
  }, [])

  const value = useMemo(() => ({ theme, toggle }), [theme, toggle])
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext)
  if (!ctx) throw new Error('useTheme must be used inside <ThemeProvider>')
  return ctx
}
