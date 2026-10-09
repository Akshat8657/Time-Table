/**
 * Browser alerts (Web Push), the polite way: nothing is asked until the person clicks "Turn on alerts".
 * The server only keeps an address for THIS browser; the alert text travels encrypted and only this browser can read it.
 */
import { api } from './api'
import type { PushConfig } from './types'

export function pushSupported(): boolean {
  return typeof window !== 'undefined' && 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window
}

export type PushPermission = 'unsupported' | 'default' | 'granted' | 'denied'

export function pushPermission(): PushPermission {
  return pushSupported() ? Notification.permission : 'unsupported'
}

function keyBytes(base64Url: string): Uint8Array<ArrayBuffer> {
  const pad = '='.repeat((4 - (base64Url.length % 4)) % 4)
  const raw = atob((base64Url + pad).replace(/-/g, '+').replace(/_/g, '/'))
  const out = new Uint8Array(new ArrayBuffer(raw.length))
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i)
  return out
}

let registration: Promise<ServiceWorkerRegistration | null> | null = null

/** Registers the (tiny) service worker that shows the alerts. Safe to call many times. */
export function registerWorker(): Promise<ServiceWorkerRegistration | null> {
  if (!pushSupported()) return Promise.resolve(null)
  registration ??= navigator.serviceWorker
    .register('/sw.js')
    .then(() => navigator.serviceWorker.ready)
    .catch(() => null)
  return registration
}

export async function currentSubscription(): Promise<PushSubscription | null> {
  const reg = await registerWorker()
  return reg ? reg.pushManager.getSubscription() : null
}

/** Asks the browser for permission (only called from a click) and tells the server this browser's address. */
export async function enablePush(config: PushConfig): Promise<'on' | 'denied' | 'unsupported' | 'off'> {
  if (!pushSupported()) return 'unsupported'
  if (!config.enabled || !config.public_key) return 'off'
  const result = await Notification.requestPermission()
  if (result !== 'granted') return 'denied'
  const reg = await registerWorker()
  if (!reg) return 'unsupported'
  let sub = await reg.pushManager.getSubscription()
  sub ??= await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(config.public_key) })
  const json = sub.toJSON()
  await api('/push/subscribe', { method: 'POST', body: { endpoint: json.endpoint, keys: json.keys } })
  return 'on'
}

export async function disablePush(): Promise<void> {
  const sub = await currentSubscription()
  if (!sub) return
  const endpoint = sub.endpoint
  await sub.unsubscribe()
  await api('/push/unsubscribe', { method: 'POST', body: { endpoint } })
}
