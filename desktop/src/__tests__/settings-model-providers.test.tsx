import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, within, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('../services/api', () => ({
  api: {
    getConfig: vi.fn().mockResolvedValue({ model_thinking_effort: 'high' }),
    getLlmProviders: vi.fn().mockResolvedValue({ providers: [], active: '', active_model: '', judge_model: '', legacy: { model_id: '', base_url: '', has_api_key: false } }),
    updateLlmProviders: vi.fn().mockResolvedValue({ status: 'updated', count: 0 }),
    discoverLlmModels: vi.fn().mockResolvedValue({ models: [] }),
    updateConfig: vi.fn().mockResolvedValue({ status: 'updated' }),
    updatePersona: vi.fn().mockResolvedValue({ status: 'updated' }),
  },
}))
vi.mock('../services/accent', () => ({
  ACCENT_PRESETS: [{ id: 'rose', label: 'Rose', light: ['#fda4af', '#fff'], dark: ['#e11d48', '#000'] }],
  applyAccent: vi.fn(),
}))

import { api } from '../services/api'
import SettingsPage from '../pages/SettingsPage'
import { useSettingsDraft } from '../services/settingsDraft'

const baseProvider = {
  name: 'openai',
  base_url: 'https://api.openai.com/v1',
  api_key: '****',
  timeout: 90,
  models: [
    { model_id: 'gpt-4o', override: { supports_vision: true, context_length: 128000 } },
    { model_id: 'o3-mini', override: null },
  ],
}

async function renderSettings() {
  render(<SettingsPage />)
  await waitFor(() => {
    expect(screen.getByText('模型提供商')).toBeInTheDocument()
  })
}

describe('SettingsPage · Model Providers (simplified)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useSettingsDraft.getState().reset()
    vi.mocked(api.getConfig).mockResolvedValue({ model_thinking_effort: 'high' })
    vi.mocked(api.getLlmProviders).mockResolvedValue({
      providers: [baseProvider],
      active: 'openai/gpt-4o',
      active_model: 'openai/gpt-4o',
      judge_model: '',
      legacy: { model_id: '', base_url: '', has_api_key: false },
    })
  })

  it('keeps provider edits made after returning while an earlier save is pending', async () => {
    let complete: (value: { status: string; count: number }) => void = () => { throw new Error('Save has not started') }
    const pending = new Promise<{ status: string; count: number }>(resolve => { complete = resolve })
    vi.mocked(api.updateLlmProviders).mockReturnValueOnce(pending)
    const user = userEvent.setup()
    const view = render(<SettingsPage />)
    const address = await screen.findByRole('textbox', { name: '提供商 1 服务地址' })
    await user.clear(address)
    await user.type(address, 'https://first.example/v1')
    await user.click(screen.getByRole('button', { name: '保存更改' }))
    await waitFor(() => expect(api.updateLlmProviders).toHaveBeenCalledTimes(1))
    view.unmount()
    await renderSettings()
    const later = screen.getByRole('textbox', { name: '提供商 1 服务地址' })
    await user.clear(later)
    await user.type(later, 'https://second.example/v1')
    await act(async () => { complete({ status: 'updated', count: 1 }); await pending })
    expect(later).toHaveValue('https://second.example/v1')
    expect(screen.getByRole('button', { name: '保存更改' })).toBeEnabled()
  })

  it('keeps persona edits made after returning while an earlier save is pending', async () => {
    let complete: (value: { status: string }) => void = () => { throw new Error('Save has not started') }
    const pending = new Promise<{ status: string }>(resolve => { complete = resolve })
    vi.mocked(api.updatePersona).mockReturnValueOnce(pending)
    const user = userEvent.setup()
    const view = render(<SettingsPage />)
    const name = await screen.findByRole('textbox', { name: 'Agent 名称' })
    await user.type(name, 'First')
    await user.click(screen.getByRole('button', { name: '保存更改' }))
    await waitFor(() => expect(api.updatePersona).toHaveBeenCalledTimes(1))
    view.unmount()
    await renderSettings()
    const later = screen.getByRole('textbox', { name: 'Agent 名称' })
    await user.clear(later)
    await user.type(later, 'Second')
    await act(async () => { complete({ status: 'updated' }); await pending })
    expect(later).toHaveValue('Second')
    expect(screen.getByRole('button', { name: '保存更改' })).toBeEnabled()
  })

  it('exposes a unified thinking mode select and maps it to model_thinking + effort', async () => {
    const user = userEvent.setup()
    await renderSettings()

    const select = screen.getByRole('combobox', { name: '新会话默认思考强度' })
    expect(select).toHaveValue('off')
    await user.selectOptions(select, 'high')
    await user.click(screen.getByRole('button', { name: '保存更改' }))
    await waitFor(() => {
      expect(api.updateConfig).toHaveBeenCalledWith('model_thinking', 'true')
      expect(api.updateConfig).toHaveBeenCalledWith('model_thinking_effort', 'high')
    })
    expect(select).toHaveValue('high')
    expect(await screen.findByText('更改已保存。')).toBeInTheDocument()
  })

  it('selecting off disables thinking and sets effort to none', async () => {
    const user = userEvent.setup()
    vi.mocked(api.getConfig).mockResolvedValue({ model_thinking: true, model_thinking_effort: 'medium' })
    await renderSettings()

    const select = screen.getByRole('combobox', { name: '新会话默认思考强度' })
    expect(select).toHaveValue('medium')
    await user.selectOptions(select, 'off')
    await user.click(screen.getByRole('button', { name: '保存更改' }))
    await waitFor(() => {
      expect(api.updateConfig).toHaveBeenCalledWith('model_thinking', 'false')
      expect(api.updateConfig).toHaveBeenCalledWith('model_thinking_effort', 'none')
    })
    expect(select).toHaveValue('off')
  })

  it('does not expose capability overrides, timeout, or Use buttons', async () => {
    await renderSettings()

    expect(screen.queryByText('视觉')).toBeNull()
    expect(screen.queryByText('工具调用')).toBeNull()
    expect(screen.queryByText('JSON Schema')).toBeNull()
    expect(screen.queryByText('上下文长度')).toBeNull()
    expect(screen.queryByRole('option', { name: '自动' })).toBeNull()
    expect(screen.queryByPlaceholderText('超时')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Use' })).toBeNull()
  })

  it('shows a YAML badge when a model has a config override, without editing it', async () => {
    await renderSettings()

    expect(screen.getByText('自定义能力')).toBeInTheDocument()
    expect(screen.getByDisplayValue('gpt-4o')).toBeInTheDocument()
    expect(screen.getByDisplayValue('o3-mini')).toBeInTheDocument()
  })

  it('toggles discovered models by click and preserves override/timeout on save', async () => {
    const user = userEvent.setup()
    vi.mocked(api.discoverLlmModels).mockResolvedValue({
      models: [
        { id: 'gpt-4o', context_length: 128000 },
        { id: 'gpt-4.1-mini', context_length: 1000000 },
      ],
    })

    await renderSettings()
    await user.click(screen.getByRole('button', { name: /拉取模型/ }))

    const discovered = await screen.findByText('gpt-4.1-mini')
    const list = discovered.closest('div')!.parentElement!
    await user.click(within(list as HTMLElement).getByText('gpt-4.1-mini'))

    await waitFor(() => {
      expect(screen.getByDisplayValue('gpt-4.1-mini')).toBeInTheDocument()
    })

    await user.click(screen.getByRole('button', { name: '保存更改' }))

    await waitFor(() => {
      expect(api.updateLlmProviders).toHaveBeenCalled()
    })
    const payload = vi.mocked(api.updateLlmProviders).mock.calls[0][0]
    expect(payload[0].timeout).toBe(90)
    expect(payload[0].models[0]).toEqual({
      model_id: 'gpt-4o',
      override: { supports_vision: true, context_length: 128000 },
    })
    expect(payload[0].models.some((m: { model_id: string }) => m.model_id === 'gpt-4.1-mini')).toBe(true)
  })

  it('removes a discovered model on second click', async () => {
    const user = userEvent.setup()
    vi.mocked(api.discoverLlmModels).mockResolvedValue({
      models: [{ id: 'gpt-4o', context_length: 128000 }],
    })

    await renderSettings()
    await user.click(screen.getByRole('button', { name: /拉取模型/ }))

    const row = await screen.findByText('gpt-4o')
    await user.click(row.closest('button')!)

    await waitFor(() => {
      expect(screen.queryByDisplayValue('gpt-4o')).toBeNull()
    })
  })
})
