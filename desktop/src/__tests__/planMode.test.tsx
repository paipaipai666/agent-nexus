import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import PlanModeToggle from '../components/chat/PlanModeToggle'
import PlanReviewCard from '../components/chat/PlanReviewCard'
import { api } from '../services/api'

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

  it('is disabled and makes no requests without a session', () => {
    render(<PlanModeToggle sessionId={null} />)
    const btn = screen.getByRole('button')
    expect(btn).toBeDisabled()
    expect(mockedApi.getSession).not.toHaveBeenCalled()
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
