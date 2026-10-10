import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Bell, BellRing, CheckCheck } from 'lucide-react'
import { useAuth } from '../auth/AuthProvider'
import { resolveLink, useNotifications } from '../lib/notifications'
import { ROLE_PREFIX } from '../lib/nav'
import type { NotificationItem } from '../lib/types'

export function timeAgo(iso: string): string {
  const seconds = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000))
  if (seconds < 60) return 'just now'
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours} h ago`
  const days = Math.round(hours / 24)
  return days === 1 ? 'yesterday' : `${days} days ago`
}

export function NotificationRow({ n, onOpen, compact = false }: { n: NotificationItem; onOpen: (n: NotificationItem) => void; compact?: boolean }) {
  return (
    <button
      type="button"
      onClick={() => onOpen(n)}
      className={`flex w-full items-start gap-3 px-4 py-3 text-left transition-colors hover:bg-hover ${n.is_read ? '' : 'bg-brand-soft/50'}`}
    >
      <span className={`mt-1.5 size-2 shrink-0 rounded-full ${n.is_read ? 'bg-transparent' : 'bg-accent'}`} aria-label={n.is_read ? undefined : 'Unread'} />
      <span className="min-w-0 flex-1">
        <span className={`block text-sm ${n.is_read ? 'font-medium text-ink' : 'font-semibold text-ink'}`}>{n.title}</span>
        {n.body && <span className={`mt-0.5 block text-[13px] leading-snug text-muted ${compact ? 'line-clamp-2' : ''}`}>{n.body}</span>}
        <span className="mt-1 block text-xs text-subtle">{timeAgo(n.created_at)}</span>
      </span>
    </button>
  )
}

/** The bell in the top bar: unread number, the latest notifications, "mark all read" and a link to the full list. */
export function NotificationBell() {
  const { user } = useAuth()
  const { items, unread, live, markRead, markAllRead } = useNotifications()
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const box = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  if (!user) return null
  const prefix = ROLE_PREFIX[user.role]

  function openOne(n: NotificationItem) {
    void markRead([n.id])
    setOpen(false)
    const to = resolveLink(n.link)
    if (to) navigate(to)
  }

  return (
    <div ref={box} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-label={unread > 0 ? `Notifications, ${unread} unread` : 'Notifications'}
        aria-expanded={open}
        aria-haspopup="dialog"
        className="relative rounded-lg p-2 text-muted transition-colors hover:bg-hover hover:text-ink"
        data-testid="bell"
      >
        {unread > 0 ? <BellRing className="size-5" aria-hidden="true" /> : <Bell className="size-5" aria-hidden="true" />}
        {unread > 0 && (
          <span
            className="absolute -right-0.5 -top-0.5 flex min-w-[1.15rem] items-center justify-center rounded-full bg-danger-solid px-1 text-[11px] font-bold leading-[1.15rem] text-white ring-2 ring-surface"
            data-testid="bell-count"
          >
            {unread > 99 ? '99+' : unread}
          </span>
        )}
      </button>
      {open && (
        <div
          role="dialog"
          aria-label="Notifications"
          className="animate-rise absolute right-0 top-full z-50 mt-2 w-[min(24rem,calc(100vw-1.5rem))] overflow-hidden rounded-2xl border border-line bg-surface shadow-pop"
        >
          <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-3">
            <p className="text-sm font-semibold text-ink">
              Notifications
              <span className={`ml-2 inline-flex items-center gap-1 text-[11px] font-medium ${live ? 'text-success' : 'text-subtle'}`} title={live ? 'Live: new ones appear at once' : 'Checking every minute'}>
                <span className={`size-1.5 rounded-full ${live ? 'bg-success' : 'bg-line-strong'}`} aria-hidden="true" />
                {live ? 'live' : 'offline'}
              </span>
            </p>
            <button
              type="button"
              onClick={() => void markAllRead()}
              disabled={unread === 0}
              className="inline-flex items-center gap-1 text-[13px] font-medium text-brand-text hover:underline disabled:text-subtle disabled:no-underline"
            >
              <CheckCheck className="size-4" aria-hidden="true" /> Mark all read
            </button>
          </div>
          <div className="max-h-[26rem] divide-y divide-line overflow-y-auto">
            {items.length === 0 ? (
              <p className="px-4 py-8 text-center text-sm text-muted">Nothing yet. You will be told here when your timetable changes.</p>
            ) : (
              items.slice(0, 8).map((n) => <NotificationRow key={n.id} n={n} onOpen={openOne} compact />)
            )}
          </div>
          <Link
            to={`${prefix}/notifications`}
            onClick={() => setOpen(false)}
            className="block border-t border-line px-4 py-2.5 text-center text-[13px] font-medium text-brand-text hover:bg-hover"
          >
            See all notifications
          </Link>
        </div>
      )}
    </div>
  )
}
