import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import PlanModeToggle from '../components/chat/PlanModeToggle'
import PlanReviewCard from '../components/chat/PlanReviewCard'
import { api } from '../services/api'
import { planModeArm } from '../services/planModeArm'

vi.mock('../services/api', () => ({
  api: {
    getSession: vi.fn(),
    setPlanMode: vi.fn(),
  },
}))

const mockedApi = vi.mocked(api)

describe('PlanModeToggle', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    planModeArm.setArmed(false)
  })

  it('reflects initial plan_mode=true from getSession', async () => {
    mockedApi.getSession.mockResolvedValue({ session_id: 's1', skill: null, profile: null, workspace: null, plan_mode: true })
    render(<PlanModeToggle sessionId="s1" />)
    const btn = await screen.findByRole('button')
    await waitFor(() => expect(btn).toHaveAttribute('aria-pressed', 'true'))
  })

  it('toggles off via setPlanMode and updates pressed state', async () => {
    mockedApi.getSession.mockResolvedValue({ session_id: 's1', skill: null, profile: null, workspace: null, plan_mode: true })
    mockedApi.setPlanMode.mockResolvedValue({ session_id: 's1', plan_mode: false })
    render(<PlanModeToggle sessionId="s1" />)
    const btn = await screen.findByRole('button')
    await waitFor(() => expect(btn).toHaveAttribute('aria-pressed', 'true'))
    await userEvent.click(btn)
    await waitFor(() => expect(btn).toHaveAttribute('aria-pressed', 'false'))
    expect(mockedApi.setPlanMode).toHaveBeenCalledWith('s1', false)
  })

  it('arms without a session and applies it once a session appears', async () => {
    mockedApi.setPlanMode.mockResolvedValue({ session_id: 's1', plan_mode: true })
    const { rerender } = render(<PlanModeToggle sessionId={null} />)
    const btn = screen.getByRole('button')
    expect(btn).not.toBeDisabled() // pre-session arming must be possible
    await userEvent.click(btn)
    expect(btn).toHaveAttribute('aria-pressed', 'true')
    expect(mockedApi.getSession).not.toHaveBeenCalled()
    expect(mockedApi.setPlanMode).not.toHaveBeenCalled()
    // session created on first send → armed intent applied before any run
    rerender(<PlanModeToggle sessionId="s1" />)
    await waitFor(() => expect(mockedApi.setPlanMode).toHaveBeenCalledWith('s1', true))
    await waitFor(() => expect(btn).toHaveAttribute('aria-pressed', 'true'))
    // consumed exactly once
    rerender(<PlanModeToggle sessionId="s2" />)
    await waitFor(() => expect(mockedApi.getSession).toHaveBeenCalledWith('s2'))
    expect(mockedApi.setPlanMode).toHaveBeenCalledTimes(1)
  })

  it('disarmed pre-session stays off when a session appears', async () => {
    mockedApi.getSession.mockResolvedValue({ session_id: 's1', skill: null, profile: null, workspace: null, plan_mode: false })
    const { rerender } = render(<PlanModeToggle sessionId={null} />)
    rerender(<PlanModeToggle sessionId="s1" />)
    await waitFor(() => expect(mockedApi.getSession).toHaveBeenCalledWith('s1'))
    expect(mockedApi.setPlanMode).not.toHaveBeenCalled()
    expect(screen.getByRole('button')).toHaveAttribute('aria-pressed', 'false')
  })
})

describe('PlanReviewCard', () => {
  it('renders the plan markdown and both actions', () => {
    const onApprove = vi.fn()
    const onDeny = vi.fn()
    render(<PlanReviewCard plan={'# 实施计划\n\n1. 改 A\n2. 改 B'} onApprove={onApprove} onDeny={onDeny} />)
    expect(screen.getByText('实施计划')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /批准/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /拒绝/ })).toBeInTheDocument()
  })

  it('invokes onApprove / onDeny', async () => {
    const onApprove = vi.fn()
    const onDeny = vi.fn()
    render(<PlanReviewCard plan="plan" onApprove={onApprove} onDeny={onDeny} />)
    await userEvent.click(screen.getByRole('button', { name: /批准/ }))
    expect(onApprove).toHaveBeenCalledTimes(1)
    await userEvent.click(screen.getByRole('button', { name: /拒绝/ }))
    expect(onDeny).toHaveBeenCalledTimes(1)
  })
})
