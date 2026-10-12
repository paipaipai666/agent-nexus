import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import LoadingScreen from '../components/LoadingScreen'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('Backend waiting screen', () => {
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
