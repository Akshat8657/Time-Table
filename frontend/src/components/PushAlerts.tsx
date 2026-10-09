import { useCallback, useEffect, useState } from 'react'
import { BellOff, BellRing, Send, X } from 'lucide-react'
import { api, ApiError } from '../lib/api'
import { currentSubscription, disablePush, enablePush, pushPermission, pushSupported, registerWorker, type PushPermission } from '../lib/push'
import type { PushConfig } from '../lib/types'
import { Alert } from './Alert'
import { Button } from './Button'
import { Panel } from './Panel'
import { useToast } from './Toast'

const SNOOZE_KEY = 'tt.push.snoozed-until'

function snoozedNow(): boolean {
  try {
    return Number(localStorage.getItem(SNOOZE_KEY) ?? 0) > Date.now()
  } catch {
    return false
  }
}

function snooze(days: number): void {
  try {
    localStorage.setItem(SNOOZE_KEY, String(Date.now() + days * 86_400_000))
  } catch {
    /* private window: the question simply comes back next time */
  }
}

/** The state of browser alerts on THIS browser, and the actions to change it. */
export function usePush() {
  const toast = useToast()
  const [config, setConfig] = useState<PushConfig | null>(null)
  const [subscribed, setSubscribed] = useState(false)
  const [permission, setPermission] = useState<PushPermission>(pushPermission())
  const [busy, setBusy] = useState(false)

  const refresh = useCallback(async () => {
    setPermission(pushPermission())
    try {
      const cfg = await api<PushConfig>('/push/config')
      setConfig(cfg)
      if (pushSupported() && cfg.enabled) setSubscribed((await currentSubscription()) !== null)
    } catch {
      setConfig({ enabled: false, public_key: null, devices: 0 })
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  // alerts already allowed earlier: make sure the little worker that shows them is alive
  useEffect(() => {
    if (permission === 'granted') void registerWorker()
  }, [permission])

  const enable = useCallback(async () => {
    if (!config) return
    setBusy(true)
    try {
      const result = await enablePush(config)
      setPermission(pushPermission())
      if (result === 'on') {
        setSubscribed(true)
        toast.success('Alerts are on for this browser.')
      } else if (result === 'denied') {
        toast.info('Alerts stay off. You can allow them later in your browser’s site settings.')
      } else if (result === 'unsupported') {
        toast.warning('This browser cannot show alerts.')
      } else {
        toast.warning('Browser alerts are not set up on this server.')
      }
      await refresh()
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Could not turn the alerts on. Please try again.')
    } finally {
      setBusy(false)
    }
  }, [config, refresh, toast])

  const disable = useCallback(async () => {
    setBusy(true)
    try {
      await disablePush()
      setSubscribed(false)
      toast.success('Alerts are off for this browser.')
      await refresh()
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Could not turn the alerts off. Please try again.')
    } finally {
      setBusy(false)
    }
  }, [refresh, toast])

  const sendTest = useCallback(async () => {
    setBusy(true)
    try {
      await api('/push/test', { method: 'POST' })
      toast.info('A test notification was sent.')
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Could not send the test.')
    } finally {
      setBusy(false)
    }
  }, [toast])

  return { config, subscribed, permission, busy, enable, disable, sendTest, supported: pushSupported() }
}

/** A polite one-time question at the top of the page - never a browser pop-up until the person clicks "Turn on alerts". */
export function PushPrompt() {
  const { config, permission, busy, enable, supported } = usePush()
  const [hidden, setHidden] = useState(snoozedNow())
  if (!supported || !config?.enabled || permission !== 'default' || hidden) return null
  return (
    <div className="mb-5 flex flex-wrap items-center gap-3 rounded-2xl border border-info-line bg-info-soft px-4 py-3 text-sm text-info" role="region" aria-label="Browser alerts" data-testid="push-prompt">
      <BellRing className="size-5 shrink-0" aria-hidden="true" />
      <p className="min-w-0 basis-full leading-snug sm:flex-1 sm:basis-0">
        <span className="font-semibold">Get alerts on this computer?</span> We will tell you when your timetable is published or changes - even when this page is closed. Nothing is shown until you say yes.
      </p>
      <div className="flex shrink-0 items-center gap-2">
        <Button size="sm" onClick={() => void enable()} loading={busy}>
          Turn on alerts
        </Button>
        <Button
          size="sm"
          variant="secondary"
          onClick={() => {
            snooze(7)
            setHidden(true)
          }}
        >
          Not now
        </Button>
        <button
          type="button"
          aria-label="Do not ask again for a week"
          onClick={() => {
            snooze(7)
            setHidden(true)
          }}
          className="rounded-lg p-1.5 hover:bg-info-line/40"
        >
          <X className="size-4" />
        </button>
      </div>
    </div>
  )
}

/** The "Browser alerts" box on the Notifications screen: on / off for this browser, and a test button. */
export function PushSettings() {
  const { config, subscribed, permission, busy, enable, disable, sendTest, supported } = usePush()
  let body
  if (!config) body = <p className="text-sm text-muted">Checking…</p>
  else if (!supported) body = <Alert tone="info">This browser cannot show alerts. The bell inside TimeTablePro still works.</Alert>
  else if (!config.enabled) body = <Alert tone="info">Browser alerts are not set up on this server yet. The bell inside TimeTablePro still works.</Alert>
  else if (permission === 'denied')
    body = (
      <Alert tone="warning" title="Alerts are blocked in this browser">
        Click the lock icon next to the address, allow “Notifications” for this site, then come back here.
      </Alert>
    )
  else
    body = (
      <div className="flex flex-wrap items-center gap-3">
        <p className="min-w-0 flex-1 text-sm text-muted">
          {subscribed ? (
            <>
              <span className="font-semibold text-success">On</span> for this browser. You get an alert when your timetable is published or changes, even if this page is closed.
            </>
          ) : (
            <>Off for this browser. Turn it on to get an alert when your timetable is published or changes, even if this page is closed.</>
          )}
        </p>
        {subscribed ? (
          <>
            <Button variant="secondary" size="sm" icon={<Send className="size-4" />} onClick={() => void sendTest()} loading={busy}>
              Send a test
            </Button>
            <Button variant="secondary" size="sm" icon={<BellOff className="size-4" />} onClick={() => void disable()} loading={busy}>
              Turn off
            </Button>
          </>
        ) : (
          <Button size="sm" icon={<BellRing className="size-4" />} onClick={() => void enable()} loading={busy}>
            Turn on alerts
          </Button>
        )}
      </div>
    )
  return (
    <Panel title="Browser alerts" icon={BellRing}>
      {body}
    </Panel>
  )
}
