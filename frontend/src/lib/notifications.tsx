import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useToast } from '../components/Toast'
import { api, ApiError, openStream } from './api'
import type { NotificationItem, NotificationsPayload } from './types'

interface NotificationsValue {
  items: NotificationItem[]
  unread: number
  hasMore: boolean
  /** true while the live feed is connected (a new notification then arrives within a second) */
  live: boolean
  /** how many notifications arrived live in this session: screens that show timetable data add it to their request so they refresh by themselves */
  arrivals: number
  loading: boolean
  reload: () => Promise<void>
  loadMore: () => Promise<void>
  markRead: (ids: string[]) => Promise<void>
  markAllRead: () => Promise<void>
}

const Ctx = createContext<NotificationsValue | null>(null)
const PAGE = 30

/**
 * The bell: the person's notifications, the unread number and the live feed.
 * A new notification arrives through a live connection to the server; if that connection is down the
 * number is re-checked every minute, so the bell is never more than a minute behind.
 */
export function NotificationsProvider({ children }: { children: ReactNode }) {
  const toast = useToast()
  const [items, setItems] = useState<NotificationItem[]>([])
  const [unread, setUnread] = useState(0)
  const [hasMore, setHasMore] = useState(false)
  const [live, setLive] = useState(false)
  const [arrivals, setArrivals] = useState(0)
  const [loading, setLoading] = useState(true)
  const loaded = useRef(false)
  const liveRef = useRef(false)
  const unreadRef = useRef(0)
  unreadRef.current = unread

  const reload = useCallback(async () => {
    try {
      const d = await api<NotificationsPayload>(`/notifications?limit=${PAGE}`)
      setItems(d.notifications)
      setUnread(d.unread)
      setHasMore(d.has_more)
      loaded.current = true
    } catch {
      /* the bell stays as it was; the next refresh tries again */
    } finally {
      setLoading(false)
    }
  }, [])

  const loadMore = useCallback(async () => {
    const last = items[items.length - 1]
    if (!last) return
    const d = await api<NotificationsPayload>(`/notifications?limit=${PAGE}&before=${encodeURIComponent(last.created_at)}`)
    setItems((cur) => [...cur, ...d.notifications.filter((n) => !cur.some((c) => c.id === n.id))])
    setHasMore(d.has_more)
  }, [items])

  const markRead = useCallback(async (ids: string[]) => {
    if (ids.length === 0) return
    setItems((cur) => cur.map((n) => (ids.includes(n.id) ? { ...n, is_read: true } : n)))
    try {
      const d = await api<{ unread: number }>('/notifications/read', { method: 'POST', body: { ids } })
      setUnread(d.unread)
    } catch {
      void reload()
    }
  }, [reload])

  const markAllRead = useCallback(async () => {
    setItems((cur) => cur.map((n) => ({ ...n, is_read: true })))
    setUnread(0)
    try {
      await api('/notifications/read', { method: 'POST', body: { all: true } })
    } catch {
      void reload()
    }
  }, [reload])

  // first load
  useEffect(() => {
    void reload()
  }, [reload])

  // the live feed (reconnects by itself; after a reconnect the list is re-read so nothing is missed)
  useEffect(() => {
    const controller = new AbortController()
    let delay = 1500
    ;(async () => {
      while (!controller.signal.aborted) {
        try {
          let firstEvent = true
          await openStream(
            '/notifications/stream',
            (name, data) => {
              if (firstEvent) {
                firstEvent = false
                liveRef.current = true
                setLive(true)
              }
              if (name !== 'notification') return
              try {
                const n = JSON.parse(data) as NotificationItem
                setItems((cur) => (cur.some((c) => c.id === n.id) ? cur : [n, ...cur]))
                setUnread((u) => u + 1)
                setArrivals((a) => a + 1)
                if (loaded.current) toast.info(n.title)
              } catch {
                /* a broken message is ignored */
              }
            },
            controller.signal,
          )
          delay = 1500
        } catch (err) {
          if (controller.signal.aborted) return
          if (err instanceof ApiError && (err.status === 401 || err.status === 403)) delay = 30000
        }
        liveRef.current = false
        setLive(false)
        if (controller.signal.aborted) return
        await new Promise((r) => window.setTimeout(r, delay))
        delay = Math.min(delay * 2, 30000)
        void reload()
      }
    })()
    return () => controller.abort()
  }, [reload, toast])

  // a safety net: while the live feed is down, look at the number every minute; always refresh when the tab comes back
  useEffect(() => {
    const tick = window.setInterval(() => {
      if (liveRef.current || document.hidden) return
      api<{ unread: number }>('/notifications/unread-count')
        .then((d) => {
          if (d.unread !== unreadRef.current) void reload()
        })
        .catch(() => undefined)
    }, 60000)
    const onVisible = () => !document.hidden && void reload()
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      window.clearInterval(tick)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [reload])

  const value = useMemo<NotificationsValue>(
    () => ({ items, unread, hasMore, live, arrivals, loading, reload, loadMore, markRead, markAllRead }),
    [items, unread, hasMore, live, arrivals, loading, reload, loadMore, markRead, markAllRead],
  )
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useNotifications(): NotificationsValue {
  const ctx = useContext(Ctx)
  if (!ctx) throw new Error('useNotifications must be used inside <NotificationsProvider>')
  return ctx
}

/** A notification's link is the same for every role ("/my/timetable"); the app redirects it to the screen of the signed-in role. */
export function resolveLink(link: string | null): string | null {
  return link && link.startsWith('/') ? link : null
}
