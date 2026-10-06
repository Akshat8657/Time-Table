/**
 * Cookie helpers with CONSENT built in.
 *
 *  - The login session cookie is set by the SERVER (httpOnly, essential) - it is not handled here.
 *  - Everything below is only a small convenience, stored ONLY if the visitor pressed "Accept".
 *  - Passwords are never stored. The remembered e-mail is the only personal item, and only with
 *    consent AND only when "Remember me" is ticked.
 *  - If cookies are declined (or blocked by the browser) the app works exactly the same.
 */
export const COOKIE_NAMES = {
  consent: 'tt_consent', // the visitor's choice itself (needed so we do not ask again)
  theme: 'tt_theme',
  email: 'tt_email',
  lastView: 'tt_last_view',
} as const

export type Consent = 'accepted' | 'declined'
export type ConvenienceName = typeof COOKIE_NAMES.theme | typeof COOKIE_NAMES.email | typeof COOKIE_NAMES.lastView

const CONVENIENCE_COOKIES: ConvenienceName[] = [COOKIE_NAMES.theme, COOKIE_NAMES.email, COOKIE_NAMES.lastView]
const DEFAULT_DAYS = 180

export function readCookie(name: string): string | null {
  try {
    const match = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`))
    return match ? decodeURIComponent(match[1]) : null
  } catch {
    return null
  }
}

function writeCookie(name: string, value: string, days: number): void {
  try {
    const secure = window.location.protocol === 'https:' ? '; Secure' : ''
    document.cookie = `${name}=${encodeURIComponent(value)}; Max-Age=${Math.round(days * 86400)}; Path=/; SameSite=Lax${secure}`
  } catch {
    /* cookies are blocked - everything still works without them */
  }
}

function deleteCookie(name: string): void {
  try {
    document.cookie = `${name}=; Max-Age=0; Path=/; SameSite=Lax`
  } catch {
    /* ignore */
  }
}

export function getConsent(): Consent | null {
  const value = readCookie(COOKIE_NAMES.consent)
  return value === 'accepted' || value === 'declined' ? value : null
}

export function saveConsent(choice: Consent): void {
  writeCookie(COOKIE_NAMES.consent, choice, DEFAULT_DAYS)
  if (choice === 'declined') clearConvenienceCookies()
}

/** Stores a convenience cookie - but ONLY if the visitor accepted cookies. Returns true if stored. */
export function storeConvenience(name: ConvenienceName, value: string, days = DEFAULT_DAYS): boolean {
  if (getConsent() !== 'accepted') return false
  writeCookie(name, value, days)
  return true
}

/** Reads a convenience cookie - and ignores it completely unless the visitor accepted cookies. */
export function loadConvenience(name: ConvenienceName): string | null {
  if (getConsent() !== 'accepted') return null
  return readCookie(name)
}

export function removeConvenience(name: ConvenienceName): void {
  deleteCookie(name)
}

export function clearConvenienceCookies(): void {
  CONVENIENCE_COOKIES.forEach(deleteCookie)
}

/** Handy for the self-test: which of OUR cookies exist right now (names only). */
export function listOurCookies(): string[] {
  return Object.values(COOKIE_NAMES).filter((name) => readCookie(name) !== null)
}
