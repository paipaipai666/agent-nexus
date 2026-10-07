import { describe, it, expect, vi } from 'vitest'
import { renderHook, waitFor, act } from '@testing-library/react'
import { useApiQuery } from './useApiQuery'

describe('useApiQuery', () => {
  it('loads data on mount and flips loading', async () => {
    const fetcher = vi.fn().mockResolvedValue('hello')
    const { result } = renderHook(() => useApiQuery(fetcher, []))

    expect(result.current.loading).toBe(true)
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.data).toBe('hello')
    expect(result.current.error).toBeNull()
    expect(fetcher).toHaveBeenCalledTimes(1)
    expect(fetcher.mock.calls[0][0]).toBeInstanceOf(AbortSignal)
  })

  it('stores rejections as Error and keeps data stale', async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce('first')
      .mockRejectedValueOnce('boom')
    const { result } = renderHook(() => useApiQuery(fetcher, []))

    await waitFor(() => expect(result.current.data).toBe('first'))

    act(() => result.current.reload())
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.data).toBe('first')
    expect(result.current.error).toBeInstanceOf(Error)
    expect(result.current.error?.message).toBe('boom')
  })

  it('re-runs the latest fetcher on reload', async () => {
    const fetcher = vi.fn().mockResolvedValue('x')
    const { result } = renderHook(() => useApiQuery(fetcher, []))
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => result.current.reload())
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(fetcher).toHaveBeenCalledTimes(2)
  })

  it('re-runs when deps change', async () => {
    const fetcher = vi.fn((signal: AbortSignal) => Promise.resolve('y'))
    const { result, rerender } = renderHook(({ q }) => useApiQuery(fetcher, [q]), { initialProps: { q: 'a' } })
    await waitFor(() => expect(result.current.loading).toBe(false))

    rerender({ q: 'b' })
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(fetcher).toHaveBeenCalledTimes(2)
  })

  it('aborts on unmount and ignores late resolutions', async () => {
    let capturedSignal: AbortSignal | undefined
    const fetcher = vi.fn((signal: AbortSignal) => {
      capturedSignal = signal
      return new Promise<string>(() => {}) // never resolves
    })
    const { unmount } = renderHook(() => useApiQuery(fetcher, []))

    expect(fetcher).toHaveBeenCalledTimes(1)
    unmount()
    expect(capturedSignal?.aborted).toBe(true)
  })
})
