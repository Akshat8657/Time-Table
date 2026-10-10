import { Check, Inbox, Repeat, X } from 'lucide-react'
import { Badge } from '../components/Badge'
import { Button } from '../components/Button'
import { EmptyState, PageHeader, Panel, Skeleton } from '../components/Panel'
import { formatShort } from '../lib/helpers'
import { useOffers } from '../lib/offers'

export default function OffersPage() {
  const { offers, covering, loading, accept, decline, busyId } = useOffers()

  return (
    <>
      <PageHeader
        title="Substitution Offers"
        subtitle="Lectures you have been offered to cover (first to accept gets it), and the lectures you are already covering as a substitute."
      />
      <div className="space-y-6">
        <Panel title={`Offers waiting${offers.length ? ` (${offers.length})` : ''}`} icon={Inbox}>
          {loading ? (
            <Skeleton className="h-20" />
          ) : offers.length === 0 ? (
            <EmptyState icon={Check} title="No offers right now">When a colleague is absent and you are free, an offer to cover their lecture will appear here (and pop up).</EmptyState>
          ) : (
            <ul className="space-y-3" data-testid="offer-list">
              {offers.map((o) => (
                <li key={o.id} className="rounded-xl border border-info-line bg-info-soft/40 p-4">
                  <div className="flex flex-wrap items-start gap-3">
                    <div className="min-w-0 flex-1">
                      <p className="text-[15px] font-semibold text-ink">{o.label}</p>
                      <p className="text-[13px] text-muted">
                        {o.classes.join(' + ')} · {formatShort(o.lecture_date)} · period {o.start_period}{o.length > 1 ? `-${o.start_period + o.length - 1}` : ''} ({o.time}) · for {o.absent_teacher_name}
                      </p>
                      {o.reasons.length > 0 && <p className="mt-1 text-[12px] text-subtle">{o.reasons.join(' · ')}</p>}
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      <Button size="sm" icon={<Check className="size-4" />} onClick={() => void accept(o.id)} loading={busyId === o.id}>Accept</Button>
                      <Button size="sm" variant="secondary" icon={<X className="size-4" />} onClick={() => void decline(o.id)} disabled={busyId === o.id}>Decline</Button>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel title={`Lectures I'm covering${covering.length ? ` (${covering.length})` : ''}`} icon={Repeat}>
          {covering.length === 0 ? (
            <p className="text-sm text-muted">You are not covering any lecture right now.</p>
          ) : (
            <ul className="divide-y divide-line text-sm" data-testid="covering-list">
              {covering.map((c) => (
                <li key={c.id} className="flex flex-wrap items-center gap-3 py-2.5 first:pt-0 last:pb-0">
                  <Badge tone="info"><Repeat className="size-3" aria-hidden="true" /> Substitution</Badge>
                  <span className="font-medium text-ink">{c.label}</span>
                  <span className="text-muted">
                    {c.classes.join(' + ')} · {formatShort(c.lecture_date)} · period {c.start_period}{c.length > 1 ? `-${c.start_period + c.length - 1}` : ''} ({c.time})
                  </span>
                  <span className="ml-auto text-[13px] text-subtle">for {c.absent_teacher_name}</span>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>
    </>
  )
}
