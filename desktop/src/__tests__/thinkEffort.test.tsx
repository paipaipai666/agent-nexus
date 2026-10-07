import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import ThinkEffortControl from '../components/chat/ThinkEffortControl'
import { api } from '../services/api'
import { thinkingEffortArm } from '../services/thinkingEffortArm'

// The control reads sessionId from useSession() instead of a prop — tests
// drive it through this mock, mirroring provider-backed session switches.
const sessionMock = vi.hoisted(() => ({ sessionId: null as string | null }))

vi.mock('../components/session/SessionManager', () => ({
  useSession: () => ({ sessionId: sessionMock.sessionId }),
}))

vi.mock('../services/api', () => ({
  api: {
    getSession: vi.fn(),
    setThinkingEffort: vi.fn(),
  },
}))

const mockedApi = vi.mocked(api)

describe('ThinkEffortControl', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    thinkingEffortArm.set(null)
    sessionMock.sessionId = null
  })

  it('loads session override and shows code on chip', async () => {
    mockedApi.getSession.mockResolvedValue({
      session_id: 's1', skill: null, profile: null, workspace: null,
      plan_mode: false, thinking_effort: 'high',
    })
    sessionMock.sessionId = 's1'
    render(<ThinkEffortControl />)
    await waitFor(() => expect(screen.getByText('HIGH')).toBeInTheDocument())
  })

  it('follow default shows AUTO when no override', async () => {
    mockedApi.getSession.mockResolvedValue({
      session_id: 's1', skill: null, profile: null, workspace: null,
      plan_mode: false, thinking_effort: null,
    })
    sessionMock.sessionId = 's1'
    render(<ThinkEffortControl />)
    await waitFor(() => expect(screen.getByText('AUTO')).toBeInTheDocument())
  })

  it('commits a level via setThinkingEffort', async () => {
    mockedApi.getSession.mockResolvedValue({
      session_id: 's1', skill: null, profile: null, workspace: null,
      plan_mode: false, thinking_effort: null,
    })
    mockedApi.setThinkingEffort.mockResolvedValue({ session_id: 's1', thinking_effort: 'high' })
    sessionMock.sessionId = 's1'
    render(<ThinkEffortControl />)
    await screen.findByText('AUTO')
    await userEvent.click(screen.getByRole('button', { name: /思考/ }))
    await userEvent.click(screen.getByRole('button', { name: /HIGH/ }))
    await waitFor(() => expect(mockedApi.setThinkingEffort).toHaveBeenCalledWith('s1', 'high'))
  })

  it('AUTO sends null to clear the override', async () => {
    mockedApi.getSession.mockResolvedValue({
      session_id: 's1', skill: null, profile: null, workspace: null,
      plan_mode: false, thinking_effort: 'high',
    })
    mockedApi.setThinkingEffort.mockResolvedValue({ session_id: 's1', thinking_effort: null })
    sessionMock.sessionId = 's1'
    render(<ThinkEffortControl />)
    await screen.findByText('HIGH')
    await userEvent.click(screen.getByRole('button', { name: /思考/ }))
    await userEvent.click(screen.getByRole('button', { name: 'AUTO' }))
    await waitFor(() => expect(mockedApi.setThinkingEffort).toHaveBeenCalledWith('s1', null))
  })

  it('pre-session arms the override once a session appears', async () => {
    mockedApi.setThinkingEffort.mockResolvedValue({ session_id: 's1', thinking_effort: 'low' })
    const { rerender } = render(<ThinkEffortControl />)
    await userEvent.click(screen.getByRole('button', { name: /思考/ }))
    await userEvent.click(screen.getByRole('button', { name: /LOW/ }))
    expect(mockedApi.setThinkingEffort).not.toHaveBeenCalled()
    sessionMock.sessionId = 's1'
    rerender(<ThinkEffortControl />)
    await waitFor(() => expect(mockedApi.setThinkingEffort).toHaveBeenCalledWith('s1', 'low'))
    // consumed once
    sessionMock.sessionId = 's2'
    rerender(<ThinkEffortControl />)
    await waitFor(() => expect(mockedApi.getSession).toHaveBeenCalledWith('s2'))
    expect(mockedApi.setThinkingEffort).toHaveBeenCalledTimes(1)
  })
})
