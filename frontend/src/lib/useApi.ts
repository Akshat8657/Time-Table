import { useCallback, useEffect, useState } from 'react'
import { api, ApiError } from './api'

interface State<T> {
  data: T | null
  error: ApiError | null
  loading: boolean
}

/**
 * Loads one API route when the screen opens (and again when the path changes or reload() is called).
 * Pass null to wait (for example until a school has been chosen).
 * The old answer stays on screen while a newer one is loading, so lists never flash empty.
 */
export function useApi<T>(path: string | null) {
  const [state, setState] = useState<State<T>>({ data: null, error: null, loading: path !== null })
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    if (path === null) {
      setState({ data: null, error: null, loading: false })
      return
    }
    const controller = new AbortController()
    setState((s) => ({ ...s, loading: true, error: null }))
    api<T>(path, { signal: controller.signal }).then(
      (data) => setState({ data, error: null, loading: false }),
      (err: unknown) => {
        if (controller.signal.aborted) return
        const error = err instanceof ApiError ? err : new ApiError(0, 'UNKNOWN', 'Something went wrong. Please try again.')
        setState({ data: null, error, loading: false })
      },
    )
    return () => controller.abort()
  }, [path, attempt])

  const reload = useCallback(() => setAttempt((n) => n + 1), [])
  return { ...state, reload }
}
