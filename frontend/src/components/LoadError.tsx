import { RotateCw } from 'lucide-react'
import type { ApiError } from '../lib/api'
import { Alert } from './Alert'
import { Button } from './Button'

/** The same friendly "could not load" box everywhere: the server's own simple sentence + a Try again button. */
export function LoadError({ error, onRetry, className = '' }: { error: ApiError | null; onRetry?: () => void; className?: string }) {
  if (!error) return null
  return (
    <Alert tone="danger" title="This could not be loaded" className={className}>
      <span>{error.message}</span>
      {onRetry && (
        <Button variant="secondary" size="sm" className="ml-0 mt-2 block sm:ml-3 sm:mt-0 sm:inline-flex" icon={<RotateCw className="size-3.5" aria-hidden="true" />} onClick={onRetry}>
          Try again
        </Button>
      )}
    </Alert>
  )
}
