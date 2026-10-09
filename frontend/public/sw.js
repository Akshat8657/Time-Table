/* TimeTablePro service worker - it only shows browser notifications (Web Push). It caches nothing and touches no data. */

self.addEventListener('install', () => self.skipWaiting())
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()))

self.addEventListener('push', (event) => {
  let data = {}
  try {
    data = event.data ? event.data.json() : {}
  } catch (e) {
    data = { title: 'TimeTablePro', body: event.data ? event.data.text() : '' }
  }
  const title = data.title || 'TimeTablePro'
  event.waitUntil(
    self.registration.showNotification(title, {
      body: data.body || '',
      icon: '/icon-192.png',
      badge: '/badge-96.png',
      tag: data.tag || 'timetablepro',
      renotify: true,
      data: { url: data.url || '/', id: data.id || null },
    }),
  )
})

self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  const target = new URL((event.notification.data && event.notification.data.url) || '/', self.location.origin).href
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windows) => {
      for (const w of windows) {
        if (new URL(w.url).origin === self.location.origin && 'focus' in w) {
          // the app is already open: show it and move it to the right screen
          return w.focus().then(() => ('navigate' in w ? w.navigate(target) : undefined))
        }
      }
      return self.clients.openWindow(target)
    }),
  )
})
