/**
 * The ONLY place that talks to the backend.
 *
 *  - All logic lives in the Python API; this file just sends requests and reads answers.
 *  - The short-lived access token is kept in MEMORY only (never localStorage), so a script injected
 *    into the page cannot steal a long-lived login.
 *  - The long-lived part of the login is an httpOnly cookie that JavaScript cannot even read.
 *    When the page loads or the token is about to expire, we ask the server for a fresh token.
 */
import type { AuthPayload } from './types'

const API_BASE = ((import.meta.env.VITE_API_BASE_URL as string | undefined) ?? '').replace(/\/+$/, '')

export class ApiError extends Error {
  status: number
  code: string
  fields: Record<string, string>
  retryAfter: number | null
  reason: string | null

  constructor(
    status: number,
    code: string,
    message: string,
    extra: { fields?: Record<string, string>; retryAfter?: number | null; reason?: string | null } = {},
  ) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.fields = extra.fields ?? {}
    this.retryAfter = extra.retryAfter ?? null
    this.reason = extra.reason ?? null
  }
}

/** Answers that mean "this login is finished" - the whole app must go back to the sign-in page. */
const SESSION_LOST = new Set(['SESSION_EXPIRED', 'SESSION_REVOKED', 'INVALID_SESSION', 'NO_SESSION', 'ACCOUNT_DISABLED'])
const TOKEN_PROBLEMS = new Set(['TOKEN_EXPIRED', 'INVALID_TOKEN', 'NOT_AUTHENTICATED'])

// ---------------------------------------------------------------- access token (memory only)
let accessToken: string | null = null
let tokenExpiresAt = 0

export const tokenStore = {
  get: () => accessToken,
  set(token: string, expiresInSeconds: number) {
    accessToken = token
    tokenExpiresAt = Date.now() + expiresInSeconds * 1000
  },
  clear() {
    accessToken = null
    tokenExpiresAt = 0
  },
  /** true when there is no token, or it will run out within 45 seconds */
  needsRefresh: () => !accessToken || Date.now() > tokenExpiresAt - 45_000,
}

// ---------------------------------------------------------------- tiny event hub
type PayloadListener = (payload: AuthPayload) => void
type LostListener = (info: { code: string; reason: string | null; message: string }) => void
const payloadListeners = new Set<PayloadListener>()
const lostListeners = new Set<LostListener>()

export function onAuthPayload(fn: PayloadListener): () => void {
  payloadListeners.add(fn)
  return () => payloadListeners.delete(fn)
}

export function onSessionLost(fn: LostListener): () => void {
  lostListeners.add(fn)
  return () => lostListeners.delete(fn)
}

function announceSessionLost(err: ApiError): void {
  tokenStore.clear()
  lostListeners.forEach((fn) => fn({ code: err.code, reason: err.reason, message: err.message }))
}

function checkSessionLost(err: unknown): unknown {
  if (err instanceof ApiError && SESSION_LOST.has(err.code)) announceSessionLost(err)
  return err
}

// ---------------------------------------------------------------- plumbing
interface RawInit {
  method: string
  body?: unknown
  /** a file upload (multipart) instead of JSON */
  form?: FormData
  /** the answer is a file (picture, Excel sheet): hand it back as a Blob */
  blob?: boolean
  token?: string | null
  signal?: AbortSignal
}

type Json = any // answers of our own API: shapes are checked by the callers

async function rawRequest(path: string, init: RawInit): Promise<{ res: Response; data: Json }> {
  const headers: Record<string, string> = { Accept: init.blob ? '*/*' : 'application/json' }
  if (init.body !== undefined) headers['Content-Type'] = 'application/json'
  if (init.token) headers.Authorization = `Bearer ${init.token}`

  let res: Response
  try {
    res = await fetch(`${API_BASE}/api${path}`, {
      method: init.method,
      headers,
      body: init.form ?? (init.body !== undefined ? JSON.stringify(init.body) : undefined),
      credentials: API_BASE ? 'include' : 'same-origin',
      cache: 'no-store',
      signal: init.signal,
    })
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') throw err
    throw new ApiError(
      0,
      'NETWORK_ERROR',
      "We can't reach the TimeTablePro server. Please check that it is running, then try again.",
    )
  }

  let data: Json = null
  if (init.blob && res.ok) {
    return { res, data: await res.blob() }
  }
  const text = await res.text()
  if (text) {
    try {
      data = JSON.parse(text)
    } catch {
      data = null
    }
  }
  return { res, data }
}

function fallbackMessage(status: number): string {
  if (status === 429) return 'Too many requests. Please wait a moment and try again.'
  if (status >= 500) return 'Something went wrong on our side. Please try again in a moment.'
  if (status === 403) return "You don't have permission to do that."
  return 'Something went wrong. Please try again.'
}

function errorFrom(res: Response, data: Json): ApiError {
  const header = Number(res.headers.get('Retry-After'))
  return new ApiError(res.status, data?.code ?? 'HTTP_ERROR', data?.message ?? fallbackMessage(res.status), {
    fields: data?.fields ?? {},
    retryAfter: data?.retry_after_seconds ?? (header > 0 ? header : null),
    reason: data?.reason ?? null,
  })
}

// ---------------------------------------------------------------- refresh (shared by everybody, one at a time)
let refreshing: Promise<AuthPayload> | null = null

/** Asks the server for a new access token using the httpOnly cookie. Never runs twice at once. */
export function refreshSession(): Promise<AuthPayload> {
  refreshing ??= (async () => {
    const { res, data } = await rawRequest('/auth/refresh', { method: 'POST' })
    if (!res.ok) throw errorFrom(res, data)
    const payload = data as AuthPayload
    tokenStore.set(payload.access_token, payload.expires_in)
    payloadListeners.forEach((fn) => fn(payload))
    return payload
  })().finally(() => {
    refreshing = null
  })
  return refreshing
}

// ---------------------------------------------------------------- the one function the screens use
export interface ApiOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  body?: unknown
  /** a file upload: the browser sets the multipart headers itself */
  form?: FormData
  /** false for login only */
  auth?: boolean
  signal?: AbortSignal
  /** @internal - use apiBlob() */
  blob?: boolean
}

export async function api<T>(path: string, opts: ApiOptions = {}): Promise<T> {
  const { method = 'GET', body, form, blob, auth = true, signal } = opts

  if (!auth) {
    const { res, data } = await rawRequest(path, { method, body, signal })
    if (!res.ok) throw errorFrom(res, data)
    return data as T
  }

  try {
    if (tokenStore.needsRefresh()) await refreshSession()
  } catch (err) {
    throw checkSessionLost(err)
  }

  let { res, data } = await rawRequest(path, { method, body, form, blob, token: tokenStore.get(), signal })
  if (res.status === 401 && TOKEN_PROBLEMS.has(data?.code)) {
    // the token ran out (or was lost) just now - renew it once and try again
    try {
      await refreshSession()
    } catch (err) {
      throw checkSessionLost(err)
    }
    ;({ res, data } = await rawRequest(path, { method, body, form, blob, token: tokenStore.get(), signal }))
  }
  if (!res.ok) throw checkSessionLost(errorFrom(res, data))
  return data as T
}

/** Fetches a file (a photo, an Excel template) with the login token. */
export function apiBlob(path: string, signal?: AbortSignal): Promise<Blob> {
  return api<Blob>(path, { blob: true, signal })
}

/** Keeps the token fresh while the page is open. A finished login is announced to the whole app. */
export async function ensureToken(): Promise<void> {
  if (!tokenStore.needsRefresh()) return
  try {
    await refreshSession()
  } catch (err) {
    throw checkSessionLost(err)
  }
}

/** Tells the server to end the session (and wipe the cookie). Errors are ignored on purpose. */
export async function apiLogout(): Promise<void> {
  try {
    await rawRequest('/auth/logout', { method: 'POST', token: tokenStore.get() })
  } catch {
    /* even if the server cannot be reached, we still sign out locally */
  }
}
