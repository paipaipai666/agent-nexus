import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import ContextMeter from '../components/layout/ContextMeter'
import StatusBar from '../components/layout/StatusBar'
import SessionManager, { useSession } from '../components/session/SessionManager'
import type { RuntimeStatus } from '../services/api'
import { parseRuntimeStatus } from '../services/runtime-status'

const status: RuntimeStatus = {
  model_id: 'test/model', ctx_max: 10000, stm_tokens: 2000,
  total_usage: { input_tokens: 123, output_tokens: 45 }, step_count: 87, skill_id: null,
  context_usage: {
    used_tokens: 6000, free_tokens: 4000, estimated: true, measured_at: 1, has_images: false,
    categories: { system_prompt: 1000, tools: 500, mcp: 250, skills: 250, memory: 1000, conversation: 2000, tool_results: 1000 },
  },
  compaction: { basis: 'short_term_memory', used_tokens: 2000, threshold_tokens: 8000 },
}

afterEach(cleanup)

describe('Context composition', () => {
  it('parses measurements at the API boundary and rejects inconsistent counts', () => {
    expect(parseRuntimeStatus(status)).toEqual(status)
    expect(() => parseRuntimeStatus({})).toThrow('运行状态缺少模型标识')
    if (!status.context_usage) throw new Error('Missing test measurement')
    expect(() => parseRuntimeStatus({ ...status, context_usage: { ...status.context_usage, free_tokens: -1 } })).toThrow()
    expect(() => parseRuntimeStatus({ ...status, context_usage: { ...status.context_usage, used_tokens: 5999 } })).toThrow('上下文分类与总用量不一致')
  })

  it('shows each share and the separate short-term-memory compression threshold', () => {
    render(<ContextMeter status={status} />)
    expect(screen.getByText('ctx 60.0%')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '上下文用量' }))
    const table = screen.getByRole('table', { name: '上下文组成' })
    for (const [name, tokens, percent] of [
      ['System prompt', '1,000', '10.0%'], ['工具定义', '500', '5.0%'], ['MCP', '250', '2.5%'],
      ['Skill', '250', '2.5%'], ['记忆', '1,000', '10.0%'], ['对话', '2,000', '20.0%'],
      ['工具结果', '1,000', '10.0%'], ['空余', '4,000', '40.0%'],
    ]) {
      const row = within(table).getByText(name).closest('tr')
      expect(row).toHaveTextContent(tokens)
      expect(row).toHaveTextContent(percent)
    }
    expect(screen.getByText('触发阈值 80.0%')).toBeInTheDocument()
    expect(screen.getByText('短期记忆 2,000 / 8,000 tokens')).toBeInTheDocument()
    expect(screen.getByText('最近一次模型请求 · 估算')).toBeInTheDocument()
  })

  it('shows unavailable measurements without inventing a zero usage', () => {
    render(<ContextMeter status={{ ...status, context_usage: null }} />)
    expect(screen.getByText('ctx 待测量')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '上下文用量' }))
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    expect(screen.getByText('尚无模型请求用量。发送消息后显示各部分占比。')).toBeInTheDocument()
    expect(screen.getByText('触发阈值 80.0%')).toBeInTheDocument()
  })

  it('closes with Escape and returns focus to the trigger', () => {
    render(<ContextMeter status={status} />)
    const trigger = screen.getByRole('button', { name: '上下文用量' })
    fireEvent.click(trigger)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(trigger).toHaveFocus()
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
  })

  it('shows overflow and makes excluded image tokens explicit', () => {
    if (!status.context_usage) throw new Error('Missing test measurement')
    render(<ContextMeter status={{ ...status, ctx_max: 4000, context_usage: { ...status.context_usage, free_tokens: 0, has_images: true } }} />)
    expect(screen.getByText('ctx 150.0%')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '上下文用量' }))
    expect(screen.getByText('超出窗口 2,000 tokens')).toBeInTheDocument()
    expect(screen.getByText('图片 token 未纳入估算，实际用量可能更高。')).toBeInTheDocument()
  })

  it('removes steps and keeps late runtime responses in their owning session', () => {
    function Controls() {
      const { setSessionId, setRuntimeInfo } = useSession()
      return <>
        <button onClick={() => { setSessionId('a'); setRuntimeInfo('a', status) }}>Open A</button>
        <button onClick={() => { setSessionId('b'); setRuntimeInfo('b', { ...status, ctx_max: 20000 }) }}>Open B</button>
        <button onClick={() => setRuntimeInfo('a', { ...status, ctx_max: 12000 })}>Late A</button>
      </>
    }
    render(<SessionManager><Controls /><StatusBar /></SessionManager>)
    fireEvent.click(screen.getByText('Open A'))
    expect(screen.getByText('ctx 60.0%')).toBeInTheDocument()
    expect(screen.queryByText(/steps/i)).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '上下文用量' }))
    fireEvent.click(screen.getByText('Open B'))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByText('ctx 30.0%')).toBeInTheDocument()
    fireEvent.click(screen.getByText('Late A'))
    expect(screen.getByText('ctx 30.0%')).toBeInTheDocument()
  })
})
