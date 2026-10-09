import { useEffect, useState } from 'react'
import { Check, Repeat, X } from 'lucide-react'
import { useOffers } from '../lib/offers'
import type { SubOffer } from '../lib/types'
import { Button } from './Button'

function countdown(iso: string): string {
  const ms = new Date(iso).getTime() - Date.now()
  if (ms <= 0) return 'expired'
  const mins = Math.floor(ms / 60000)
  if (mins >= 60) return `${Math.floor(mins / 60)}h ${mins % 60}m left`
  const secs = Math.floor((ms % 60000) / 1000)
  return mins > 0 ? `${mins}m ${secs}s left` : `${secs}s left`
}

/** A live popup for the top waiting substitution offer. First to accept wins; it disappears when filled by anyone. */
export function OfferPopup() {
  const { offers, accept, decline, busyId } = useOffers()
  const [dismissed, setDismissed] = useState<Set<string>>(new Set())
  const [, tick] = useState(0)

  useEffect(() => {
    const t = window.setInterval(() => tick((n) => n + 1), 1000)
    return () => window.clearInterval(t)
  }, [])

  const offer: SubOffer | undefined = offers.find((o) => !dismissed.has(o.id))
  if (!offer) return null

  return (
    <div className="fixed inset-x-0 bottom-0 z-[60] flex justify-center px-4 pb-4 sm:bottom-4" role="alertdialog" aria-label="Substitution offer" data-testid="offer-popup">
      <div className="animate-rise w-full max-w-md overflow-hidden rounded-2xl border border-info-line bg-surface shadow-pop">
        <div className="flex items-center gap-2 bg-info-soft px-4 py-2.5 text-info">
          <Repeat className="size-4" aria-hidden="true" />
          <span className="text-sm font-semibold">Can you cover a lecture?</span>
          <span className="ml-auto text-xs font-medium tabular-nums" data-testid="offer-countdown">{countdown(offer.expires_at)}</span>
          <button type="button" aria-label="Hide for now" onClick={() => setDismissed((s) => new Set(s).add(offer.id))} className="rounded p-0.5 hover:bg-info-line/40">
            <X className="size-4" />
          </button>
        </div>
        <div className="px-4 py-3">
          <p className="text-[15px] font-semibold text-ink">{offer.label}</p>
          <p className="mt-0.5 text-[13px] text-muted">
            {offer.classes.join(' + ')} · {new Date(offer.lecture_date).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' })} · period {offer.start_period}
            {offer.length > 1 ? `-${offer.start_period + offer.length - 1}` : ''} ({offer.time})
          </p>
          <p className="mt-0.5 text-[13px] text-muted">For {offer.absent_teacher_name}. First to accept gets it.</p>
          {offer.reasons.length > 0 && <p className="mt-1 text-[12px] text-subtle">{offer.reasons.slice(0, 2).join(' · ')}</p>}
          <div className="mt-3 flex items-center gap-2">
            <Button size="sm" icon={<Check className="size-4" />} onClick={() => void accept(offer.id)} loading={busyId === offer.id} data-testid="offer-accept">
              Accept
            </Button>
            <Button size="sm" variant="secondary" onClick={() => void decline(offer.id)} disabled={busyId === offer.id} data-testid="offer-decline">
              Not now
            </Button>
            {offers.length > 1 && <span className="ml-auto text-xs text-subtle">+{offers.length - 1} more</span>}
          </div>
        </div>
      </div>
    </div>
  )
}
