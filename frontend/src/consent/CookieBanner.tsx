import { useState } from 'react'
import { ChevronDown, Cookie, X } from 'lucide-react'
import { Button } from '../components/Button'
import { useConsent } from './ConsentProvider'

export function CookieBanner() {
  const { consent, bannerOpen, accept, decline, closeBanner } = useConsent()
  const [details, setDetails] = useState(false)

  if (!bannerOpen) return null

  const alreadyChose = consent !== null

  return (
    <section
      role="region"
      aria-label="Cookie preferences"
      className="animate-rise fixed inset-x-3 bottom-3 z-50 sm:inset-x-auto sm:bottom-5 sm:left-5 sm:w-[26rem]"
    >
      <div className="rounded-2xl border border-line bg-surface p-5 shadow-pop">
        <div className="flex items-start gap-3">
          <span className="mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-xl bg-brand-soft text-brand-text">
            <Cookie className="size-[18px]" aria-hidden="true" />
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex items-start justify-between gap-2">
              <h2 className="text-[15px] font-semibold text-ink">Cookies on TimeTablePro</h2>
              {alreadyChose && (
                <button
                  type="button"
                  onClick={closeBanner}
                  aria-label="Close cookie preferences"
                  className="-mr-1 -mt-1 rounded-md p-1 text-subtle transition hover:bg-hover hover:text-ink"
                >
                  <X className="size-4" />
                </button>
              )}
            </div>
            <p className="mt-1 text-[13px] leading-relaxed text-muted">
              One essential cookie keeps you signed in - that one is always on. With your OK we can also remember small
              conveniences. We never store passwords or personal details in cookies.
            </p>

            <button
              type="button"
              onClick={() => setDetails((d) => !d)}
              aria-expanded={details}
              className="mt-2 inline-flex items-center gap-1 text-[13px] font-medium text-brand-text hover:underline"
            >
              What would be remembered?
              <ChevronDown className={`size-3.5 transition-transform ${details ? 'rotate-180' : ''}`} aria-hidden="true" />
            </button>
            {details && (
              <ul className="mt-2 space-y-1.5 text-[13px] text-muted">
                <li>
                  <span className="font-medium text-ink">Your theme</span> - light or dark.
                </li>
                <li>
                  <span className="font-medium text-ink">Your email</span> on the sign-in page (only if you tick
                  &ldquo;Remember me&rdquo;).
                </li>
                <li>
                  <span className="font-medium text-ink">The last page you opened</span>.
                </li>
                <li className="pt-1 text-subtle">If you decline, everything still works - it just won&apos;t be remembered.</li>
              </ul>
            )}

            {alreadyChose && (
              <p className="mt-3 text-[13px] text-muted">
                Your current choice: <span className="font-medium text-ink">{consent === 'accepted' ? 'Accepted' : 'Declined'}</span>
              </p>
            )}

            <div className="mt-4 flex gap-2.5">
              <Button variant="secondary" size="md" onClick={decline} className="flex-1">
                Decline
              </Button>
              <Button variant="primary" size="md" onClick={accept} className="flex-1">
                Accept
              </Button>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}
