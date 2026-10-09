import { useEffect, useState } from 'react'

export const DAY_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'] as const
export const DAY_SHORT = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'] as const

/** [2, 4] -> "Tue, Thu" */
export function dayList(days: number[]): string {
  return days.length === 0 ? '-' : [...days].sort().map((d) => DAY_SHORT[d - 1]).join(', ')
}

/** "2026-11-08" -> "8 Nov 2026" (no time-zone surprises: the date is split by hand) */
export function formatDate(iso: string): string {
  const [y, m, d] = iso.split('-').map(Number)
  const month = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][m - 1]
  return `${d} ${month} ${y}`
}

export function formatRange(from: string, to: string): string {
  return from === to ? formatDate(from) : `${formatDate(from)} - ${formatDate(to)}`
}

/** weekday of an ISO date, e.g. "Sunday" */
export function weekdayOf(iso: string): string {
  const [y, m, d] = iso.split('-').map(Number)
  return ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'][new Date(y, m - 1, d).getDay()]
}

export function todayIso(): string {
  const now = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
}

/** Waits until the person stops typing for a moment (so a search does not fire on every key). */
export function useDebounced<T>(value: T, delayMs = 250): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const t = window.setTimeout(() => setDebounced(value), delayMs)
    return () => window.clearTimeout(t)
  }, [value, delayMs])
  return debounced
}

/** Hands a file (Blob) to the browser as a download. */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 2000)
}

/** the query-string part for a list: only the filled values */
export function queryString(params: Record<string, string | number | boolean | null | undefined>): string {
  const parts = Object.entries(params)
    .filter(([, v]) => v !== null && v !== undefined && v !== '' && v !== 'all')
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
  return parts.length ? `?${parts.join('&')}` : ''
}

/** ISO date + n days (n may be negative), e.g. addDaysIso('2026-10-12', 7) = '2026-10-19' */
export function addDaysIso(iso: string, n: number): string {
  const [y, m, d] = iso.split('-').map(Number)
  const next = new Date(y, m - 1, d + n)
  const pad = (v: number) => String(v).padStart(2, '0')
  return `${next.getFullYear()}-${pad(next.getMonth() + 1)}-${pad(next.getDate())}`
}

/** the Monday of the week that contains this ISO date */
export function mondayOf(iso: string): string {
  const [y, m, d] = iso.split('-').map(Number)
  const dow = new Date(y, m - 1, d).getDay() // 0 = Sunday
  return addDaysIso(iso, dow === 0 ? -6 : 1 - dow)
}

/** "Mon 12 Oct" */
export function formatShort(iso: string): string {
  const [y, m, d] = iso.split('-').map(Number)
  return new Date(y, m - 1, d).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' })
}

/** "12 Oct 2026, 4:30 pm" for a timestamp */
export function formatStamp(iso: string): string {
  return new Date(iso).toLocaleString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', hour: 'numeric', minute: '2-digit', hour12: true })
}
