import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { api } from '../services/api'
import SubagentPage from '../pages/SubagentPage'

afterEach(() => { cleanup(); vi.restoreAllMocks() })

it('shows a trajectory failure and retries the same subagent', async () => {
  Element.prototype.scrollIntoView = vi.fn()
  vi.spyOn(api, 'restoreSession').mockResolvedValue({ session_id: 's', restored: true })
  vi.spyOn(api, 'getSubagents').mockResolvedValue({ session_id: 's', subagents: [] })
  const events = vi.spyOn(api, 'getSessionEvents').mockRejectedValue(new Error('offline'))
  render(<MemoryRouter initialEntries={['/chat/s/subagent/a']}><Routes><Route path="/chat/:sessionId/subagent/:subagentId" element={<SubagentPage />} /></Routes></MemoryRouter>)
  await screen.findByText(/子任务过程加载失败/)
  expect(screen.queryByText(/未找到该子代理/)).not.toBeInTheDocument()
  events.mockResolvedValue({ session_id: 's', events: [], last_id: 0, count: 0 })
  await userEvent.click(screen.getByRole('button', { name: '重试子任务过程' }))
  await screen.findByText(/未找到该子代理/)
  expect(events.mock.calls.at(-1)).toEqual(['s', 0, 'a', 'trajectory'])
})
