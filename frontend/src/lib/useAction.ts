import { useCallback, useState } from 'react'
import { useToast } from '../components/Toast'
import { ApiError } from './api'

/**
 * Runs a button action (save, delete, ...): shows a spinner while it runs, a green message when it worked
 * and a red message with the server's explanation when it did not. Returns undefined on failure.
 */
export function useAction() {
  const toast = useToast()
  const [busy, setBusy] = useState(false)

  const run = useCallback(
    async <T,>(fn: () => Promise<T>, success?: string): Promise<T | undefined> => {
      setBusy(true)
      try {
        const result = await fn()
        if (success) toast.success(success)
        return result
      } catch (err) {
        toast.error(err instanceof ApiError ? err.message : 'Something went wrong. Please try again.')
        return undefined
      } finally {
        setBusy(false)
      }
    },
    [toast],
  )

  return { run, busy }
}
