import { useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { ChevronUp, X } from 'lucide-react'
import type { ContextCategory, RuntimeStatus } from '../../services/api'

const categories: { key: ContextCategory; label: string; color: string }[] = [
  { key: 'system_prompt', label: 'System prompt', color: 'var(--fg-secondary)' },
  { key: 'tools', label: '工具定义', color: 'var(--blue)' },
  { key: 'mcp', label: 'MCP', color: 'var(--cyan)' },
  { key: 'skills', label: 'Skill', color: 'var(--purple)' },
  { key: 'memory', label: '记忆', color: 'var(--amber)' },
  { key: 'conversation', label: '对话', color: 'var(--accent)' },
  { key: 'tool_results', label: '工具结果', color: 'var(--green)' },
]

const tokens = (value: number) => value.toLocaleString('en-US')
const percent = (value: number, capacity: number) => capacity > 0 ? `${(value / capacity * 100).toFixed(1)}%` : '—'

function CompositionBar({ status, compact = false }: { status: RuntimeStatus; compact?: boolean }) {
  const usage = status.context_usage
  if (!usage) return null
  const scale = Math.max(status.ctx_max, usage.used_tokens, 1)
  return (
    <span aria-hidden="true" className={`flex overflow-hidden rounded-sm ${compact ? 'w-24 h-[5px]' : 'w-full h-2'}`}
      style={{ background: 'var(--surface-3)' }}>
      {categories.map(category => (
        <span key={category.key} style={{
          width: `${usage.categories[category.key] / scale * 100}%`, background: category.color,
        }} />
      ))}
    </span>
  )
}

export default function ContextMeter({ status }: { status: RuntimeStatus | null }) {
  const [open, setOpen] = useState(false)
  const trigger = useRef<HTMLButtonElement>(null)
  const panel = useRef<HTMLElement>(null)
  const panelId = useId()
  const capacity = status?.ctx_max ?? 0
  const usage = status?.context_usage ?? null
  const compaction = status?.compaction ?? null

  const close = () => { setOpen(false); trigger.current?.focus() }

  useEffect(() => {
    if (!open) return
    const outside = (event: PointerEvent) => {
      if (event.target instanceof Node && !panel.current?.contains(event.target) && !trigger.current?.contains(event.target)) {
        setOpen(false)
      }
    }
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        event.stopPropagation()
        setOpen(false)
        trigger.current?.focus()
      }
    }
    document.addEventListener('pointerdown', outside)
    document.addEventListener('keydown', escape)
    return () => {
      document.removeEventListener('pointerdown', outside)
      document.removeEventListener('keydown', escape)
    }
  }, [open])

  return (
    <>
      <button ref={trigger} type="button" aria-label="上下文用量" aria-expanded={open}
        aria-controls={open ? panelId : undefined} onClick={() => setOpen(!open)}
        className="flex items-center gap-2 h-6 px-1 rounded hover:bg-[var(--surface-3)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--accent)]"
        style={{ color: usage && capacity > 0 && usage.used_tokens > capacity ? 'var(--red)' : 'var(--fg-secondary)' }}>
        <span className="whitespace-nowrap">{usage ? `ctx ${percent(usage.used_tokens, capacity)}` : 'ctx 待测量'}</span>
        {status && <span className="hidden sm:flex"><CompositionBar status={status} compact /></span>}
        <ChevronUp size={12} aria-hidden="true" />
      </button>
      {open && createPortal(
        <aside ref={panel} id={panelId} role="dialog" aria-label="上下文用量明细"
          className="fixed z-50 rounded-lg p-4 font-sans text-[12px]"
          style={{
            right: 12, bottom: 34, width: 'min(360px, calc(100vw - 24px))',
            maxHeight: 'calc(100dvh - 58px)', overflowY: 'auto',
            background: 'var(--surface-1)', color: 'var(--fg-secondary)',
            border: '1px solid var(--border-strong)', boxShadow: 'var(--shadow-lg)',
          }}>
          <div className="flex items-start justify-between gap-3 mb-4">
            <div>
              <h2 className="font-semibold text-sm" style={{ color: 'var(--fg)' }}>上下文用量</h2>
              <p className="mt-1">最近一次模型请求 · 估算</p>
            </div>
            <button type="button" aria-label="关闭用量明细" onClick={close}
              className="p-1 rounded hover:bg-[var(--surface-3)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--accent)]">
              <X size={16} />
            </button>
          </div>
          {usage && status ? (
            <>
              <div className="flex justify-between gap-3 mb-2 font-mono" style={{ color: 'var(--fg)' }}>
                <span>{tokens(usage.used_tokens)} / {capacity > 0 ? tokens(capacity) : '未知'} tokens</span>
                <span>{percent(usage.used_tokens, capacity)}</span>
              </div>
              <CompositionBar status={status} />
              <table aria-label="上下文组成" className="w-full mt-3 border-collapse">
                <thead>
                  <tr><th className="text-left font-normal py-1">组成</th><th className="text-right font-normal">Tokens</th><th className="text-right font-normal">窗口占比</th></tr>
                </thead>
                <tbody>
                  {categories.map(category => (
                    <tr key={category.key}>
                      <td className="py-1.5"><span aria-hidden="true" className="inline-block w-2 h-2 rounded-sm mr-2" style={{ background: category.color }} />{category.label}</td>
                      <td className="text-right font-mono" style={{ color: 'var(--fg)' }}>{tokens(usage.categories[category.key])}</td>
                      <td className="text-right font-mono">{percent(usage.categories[category.key], capacity)}</td>
                    </tr>
                  ))}
                  <tr style={{ borderTop: '1px solid var(--border)' }}>
                    <td className="pt-2">空余</td>
                    <td className="text-right pt-2 font-mono" style={{ color: 'var(--fg)' }}>{tokens(usage.free_tokens)}</td>
                    <td className="text-right pt-2 font-mono">{percent(usage.free_tokens, capacity)}</td>
                  </tr>
                </tbody>
              </table>
              {capacity > 0 && usage.used_tokens > capacity && <p className="mt-3" style={{ color: 'var(--red)' }}>超出窗口 {tokens(usage.used_tokens - capacity)} tokens</p>}
              {usage.has_images && <p className="mt-3">图片 token 未纳入估算，实际用量可能更高。</p>}
            </>
          ) : (
            <p>尚无模型请求用量。发送消息后显示各部分占比。</p>
          )}
          {compaction && capacity > 0 && (
            <section aria-label="自动压缩" className="mt-4 pt-3" style={{ borderTop: '1px solid var(--border)' }}>
              <div className="flex justify-between gap-2 mb-2">
                <span style={{ color: 'var(--fg)' }}>自动压缩</span>
                <span>触发阈值 {percent(compaction.threshold_tokens, capacity)}</span>
              </div>
              <div role="meter" aria-label="短期记忆用量" aria-valuemin={0} aria-valuemax={capacity} aria-valuenow={Math.min(capacity, compaction.used_tokens)}
                className="relative h-2 rounded-sm" style={{ background: 'var(--surface-3)' }}>
                <div className="h-full rounded-sm" style={{ width: `${Math.min(100, compaction.used_tokens / capacity * 100)}%`, background: 'var(--amber)' }} />
                <span aria-hidden="true" className="absolute -top-1 h-4 border-l-2 border-dashed"
                  style={{ left: `${Math.max(0, Math.min(100, compaction.threshold_tokens / capacity * 100))}%`, borderColor: 'var(--fg)' }} />
              </div>
              <p className="mt-2 font-mono">短期记忆 {tokens(compaction.used_tokens)} / {tokens(compaction.threshold_tokens)} tokens</p>
              <p className="mt-1">达到短期记忆阈值时触发，完整输入占比单独统计。</p>
            </section>
          )}
        </aside>, document.body,
      )}
    </>
  )
}
