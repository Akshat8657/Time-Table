import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { BellRing, CheckCheck } from 'lucide-react'
import { NotificationRow } from '../components/NotificationBell'
import { PushSettings } from '../components/PushAlerts'
import { Button } from '../components/Button'
import { EmptyState, PageHeader, Panel, Skeleton } from '../components/Panel'
import { Tabs } from '../components/Tabs'
import { resolveLink, useNotifications } from '../lib/notifications'
import type { NotificationItem } from '../lib/types'

/** Everything that was sent to me: newest first, unread marked, with "mark all read" and the browser-alert switch. */
export default function NotificationsPage() {
  const { items, unread, hasMore, live, loading, loadMore, markRead, markAllRead } = useNotifications()
  const navigate = useNavigate()
  const [filter, setFilter] = useState<'all' | 'unread'>('all')
  const [more, setMore] = useState(false)
  const shown = filter === 'unread' ? items.filter((n) => !n.is_read) : items

  function open(n: NotificationItem) {
    void markRead([n.id])
    const to = resolveLink(n.link)
    if (to) navigate(to)
  }

  return (
    <>
      <PageHeader
        title="Notifications"
        subtitle="Everything that changed for you: your timetable being published, lectures moved, extra lectures. You get each one only once."
        badge="Phase 4"
      />
      <div className="space-y-6">
        <PushSettings />
        <Panel
          title={`Your notifications${unread > 0 ? ` (${unread} unread)` : ''}`}
          icon={BellRing}
          action={
            <Button variant="ghost" size="sm" icon={<CheckCheck className="size-4" />} onClick={() => void markAllRead()} disabled={unread === 0} data-testid="mark-all-read">
              Mark all read
            </Button>
          }
        >
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <Tabs
              label="Filter"
              value={filter}
              onChange={setFilter}
              items={[
                { value: 'all', label: 'All', count: items.length },
                { value: 'unread', label: 'Unread', count: unread },
              ]}
            />
            <span className={`inline-flex items-center gap-1.5 text-xs ${live ? 'text-success' : 'text-subtle'}`}>
              <span className={`size-1.5 rounded-full ${live ? 'bg-success' : 'bg-line-strong'}`} aria-hidden="true" />
              {live ? 'Live - new notifications appear at once' : 'Checking every minute'}
            </span>
          </div>
          {loading && items.length === 0 ? (
            <div className="space-y-2"><Skeleton className="h-16" /><Skeleton className="h-16" /></div>
          ) : shown.length === 0 ? (
            <EmptyState icon={BellRing} title={filter === 'unread' ? 'You are all caught up' : 'No notifications yet'}>
              {filter === 'unread' ? 'Nothing unread.' : 'When your timetable is published or changes, it is shown here.'}
            </EmptyState>
          ) : (
            <div className="divide-y divide-line overflow-hidden rounded-xl border border-line" data-testid="notification-list">
              {shown.map((n) => <NotificationRow key={n.id} n={n} onOpen={open} />)}
            </div>
          )}
          {hasMore && filter === 'all' && (
            <div className="mt-4 text-center">
              <Button variant="secondary" size="sm" loading={more} onClick={() => { setMore(true); void loadMore().finally(() => setMore(false)) }}>
                Show older
              </Button>
            </div>
          )}
        </Panel>
      </div>
    </>
  )
}
