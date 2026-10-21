import { beforeEach, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { api } from '../services/api'
import type { ModelCapabilities } from '../services/modelCapabilities'
import ModelPicker from '../components/chat/ModelPicker'

vi.mock('thinking-orbs', () => ({ ThinkingOrb: () => null }))
vi.mock('../services/api', () => ({ api: {
  getLlmProviders: vi.fn(), getModelCapabilities: vi.fn(), refreshModelCapabilities: vi.fn(),
} }))

const unknown: ModelCapabilities = {
  model: 'fixture', base_url: 'http://localhost/v1', source: 'unknown', discovery_status: 'probing',
  tool_calling: false, json_mode: false, json_schema: false, thinking: false, vision: false,
  parallel_tool_calls: false, max_context_tokens: 0, max_output_tokens: 32768, session_disabled: [],
}

beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(api.getLlmProviders).mockResolvedValue({ providers: [], active: '', active_model: '', judge_model: '', legacy: { model_id: '', base_url: '', has_api_key: false } })
  vi.mocked(api.refreshModelCapabilities).mockResolvedValue(unknown)
  vi.mocked(api.getModelCapabilities).mockResolvedValue(unknown)
})

it('shows unknown capabilities while discovery runs and then displays its result', async () => {
  const user = userEvent.setup()
  render(<MemoryRouter><ModelPicker currentModel="fixture" onSwitched={() => {}} /></MemoryRouter>)
  await user.click(screen.getByRole('button', { name: '选择模型' }))
  expect(await screen.findByText('检测中…')).toBeInTheDocument()
  expect(screen.getByLabelText('原生工具：未知')).toBeInTheDocument()
  expect(screen.getByText('上下文 未知 · 输出 33K')).toBeInTheDocument()
  vi.mocked(api.getModelCapabilities).mockResolvedValue({ ...unknown, source: 'probe', discovery_status: 'ready', tool_calling: true })
  await waitFor(() => expect(screen.getByText('实时探测')).toBeInTheDocument())
  expect(screen.getByLabelText('原生工具：支持')).toBeInTheDocument()
})

it('offers a retry when discovery failed without marking unknown flags as unsupported', async () => {
  vi.mocked(api.refreshModelCapabilities).mockResolvedValue({ ...unknown, discovery_status: 'failed' })
  const user = userEvent.setup()
  render(<MemoryRouter><ModelPicker currentModel="fixture" onSwitched={() => {}} /></MemoryRouter>)
  await user.click(screen.getByRole('button', { name: '选择模型' }))
  expect(await screen.findByText('检测暂不可用')).toBeInTheDocument()
  expect(screen.getByLabelText('原生工具：未知')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '重试能力检测' })).toBeEnabled()
})
