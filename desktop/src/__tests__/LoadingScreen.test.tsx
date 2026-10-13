import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import LoadingScreen from '../components/LoadingScreen'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('Backend waiting screen', () => {
  it('offers connection retry and backend restart after startup failure', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 503 })))
    const restartBackend = vi.fn().mockResolvedValue({ ready: true, port: 18765 })
    vi.stubGlobal('electronAPI', { restartBackend, getBackendStatus: vi.fn().mockResolvedValue({ ready: false, port: 18765 }), onBackendError: () => () => {}, close: vi.fn() })
    const onReady = vi.fn()
    render(<LoadingScreen onReady={onReady} />)
    await act(async () => { await vi.advanceTimersByTimeAsync(32000) })
    expect(screen.getByRole('button', { name: '重试连接' })).toBeInTheDocument()
    await act(async () => { screen.getByRole('button', { name: '重启后端' }).click() })
    expect(onReady).toHaveBeenCalledOnce()
    expect(restartBackend).toHaveBeenCalledOnce()
  })
  it('shows the new brand and connects to the selected backend port', async () => {
    const fetchHealth = vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 200 }))
    vi.stubGlobal('fetch', fetchHealth)
    const onReady = vi.fn()
    render(<LoadingScreen onReady={onReady} backendPort={19191} />)

    expect(screen.getByRole('img', { name: 'AgentNexus logo' })).toHaveAttribute('src', '/agentnexus-mark.png')
    expect(screen.getByRole('heading', { name: 'AgentNexus' })).toBeInTheDocument()
    await waitFor(() => expect(onReady).toHaveBeenCalledOnce())
    expect(fetchHealth.mock.calls[0]?.[0]).toBe('http://127.0.0.1:19191/health')
  })

  it('ignores a health response received after the screen is closed', async () => {
    let reply: (response: Response) => void = () => {}
    const response = new Promise<Response>(resolve => { reply = resolve })
    vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockReturnValue(response))
    const onReady = vi.fn()
    const screen = render(<LoadingScreen onReady={onReady} />)
    screen.unmount()

    await act(async () => { reply(new Response(null, { status: 200 })) })
    expect(onReady).not.toHaveBeenCalled()
  })
})
