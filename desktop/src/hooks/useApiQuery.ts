import { useCallback, useEffect, useRef, useState } from 'react'

export interface ApiQueryResult<T> {
  data: T | undefined
  loading: boolean
  error: Error | null
  reload: () => void
}

/** Fetch-on-mount (and on deps change) query with unified loading/error state.
 * The fetcher receives an AbortSignal that is aborted when deps change or the
 * component unmounts; aborted runs never touch state. `reload` re-runs the
 * latest fetcher. Errors are stored as Error and never console.error'd here —
 * pages that want logging do it themselves from `error`. */
export function useApiQuery<T>(fetcher: (signal: AbortSignal) => Promise<T>, deps: unknown[] = []): ApiQueryResult<T> {
  const [data, setData] = useState<T | undefined>(undefined)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<Error | null>(null)
  const [tick, setTick] = useState(0)
  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    fetcherRef.current(controller.signal)
      .then(result => {
        if (controller.signal.aborted) return
        setData(result)
        setError(null)
      })
      .catch(e => {
        if (controller.signal.aborted) return
        setError(e instanceof Error ? e : new Error(String(e)))
      })
      .finally(() => {
        if (controller.signal.aborted) return
        setLoading(false)
      })
    return () => controller.abort()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick])

  const reload = useCallback(() => setTick(t => t + 1), [])

  return { data, loading, error, reload }
}
