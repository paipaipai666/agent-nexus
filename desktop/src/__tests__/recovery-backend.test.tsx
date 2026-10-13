import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { BackendRecovery } from '../../electron/backendRecovery'
import BackendRecoveryBanner from '../components/BackendRecoveryBanner'

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers() })

it('shares concurrent restart attempts and leaves a healthy backend running', async () => {
  let starts = 0
  let stops = 0
  let release: (ready: boolean) => void = () => {}
  const started = new Promise<boolean>(resolve => { release = resolve })
  let healthy = false
  const recovery = new BackendRecovery({ healthy: async () => healthy, stop: async () => { stops++ }, start: async () => { starts++; return started } })
  const first = recovery.recover()
  const second = recovery.recover()
  await Promise.resolve()
  await Promise.resolve()
  release(true)
  expect(await Promise.all([first, second])).toEqual([true, true])
  expect({ starts, stops }).toEqual({ starts: 1, stops: 1 })
  healthy = true
  expect(await recovery.recover()).toBe(true)
  expect({ starts, stops }).toEqual({ starts: 1, stops: 1 })
})

it('restores the backend without replacing the current page or its draft', async () => {
  vi.useFakeTimers()
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 503 })))
  vi.stubGlobal('electronAPI', { getBackendStatus: async () => ({ ready: false, port: 18765 }), restartBackend: async () => ({ ready: true, port: 18765 }), onBackendError: () => () => {} })
  render(<><BackendRecoveryBanner /><textarea aria-label="active draft" defaultValue="keep this work" /></>)
  await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
  expect(screen.getByRole('alert')).toHaveTextContent('当前页面和草稿已保留')
  await act(async () => { screen.getByRole('button', { name: '重启后端' }).click() })
  expect(screen.queryByRole('alert')).toBe(null)
  expect(screen.getByRole('textbox', { name: 'active draft' })).toHaveValue('keep this work')
})
