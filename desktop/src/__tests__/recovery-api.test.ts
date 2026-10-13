import { afterEach, expect, it, vi } from 'vitest'
import { api } from '../services/api'

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

it('preserves status, error code and retry delay for a rate limit', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { code: 'rate_limited', message: '请求过于频繁' } }), { status: 429, headers: { 'Retry-After': '12' } })))
  await expect(api.getVersionStatus('retry')).rejects.toMatchObject({ status: 429, code: 'rate_limited', retryAfter: 12 })
})

it('bounds a stalled request and tells the user to query execution state', async () => {
  vi.useFakeTimers()
  vi.stubGlobal('fetch', vi.fn((_url: string, options: RequestInit) => new Promise((_resolve, reject) => {
    options.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
  })))
  const pending = api.getVersionStatus('timeout')
  const assertion = expect(pending).rejects.toMatchObject({ code: 'request_timeout', message: expect.stringContaining('查询') })
  await vi.advanceTimersByTimeAsync(15000)
  await assertion
})
