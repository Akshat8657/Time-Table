import { useCallback, useEffect, useState } from 'react'
import { api, ApiError } from './api'

interface State<T> {
  data: T | null
  error: ApiError | null
  loading: boolean
}

/** Loads one API route when the screen opens. */
export function useApi<T>(path: string) {
  const [state, setState] = useState<State<T>>({ data: null, error: null, loading: true })
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
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
