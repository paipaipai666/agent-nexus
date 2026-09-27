import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Check, ChevronDown, Cpu, Settings2, X } from 'lucide-react'
import { api, ProviderInfo } from '../../services/api'

interface ModelCapabilities {
  model: string
  base_url: string
  source: 'registry' | 'probe' | 'config'
  tool_calling: boolean
  json_mode: boolean
  json_schema: boolean
  thinking: boolean
  vision: boolean
  parallel_tool_calls: boolean
  max_context_tokens: number
  max_output_tokens: number
  session_disabled: string[]
}

interface ModelPickerProps {
  /** Currently active model id (from runtime status) — displayed on the chip. */
  currentModel: string | null
  /** Called after a successful switch, with the new active model id. */
  onSwitched: (modelId: string) => void
}

/** Model selector living in the chat input's HUD row. Lists every model of
 *  every configured provider, grouped by provider; switching is live (the
 *  backend hot-swaps the shared LLM client). */
export default function ModelPicker({ currentModel, onSwitched }: ModelPickerProps) {
  const [open, setOpen] = useState(false)
  const [providers, setProviders] = useState<ProviderInfo[]>([])
  const [active, setActive] = useState('')
  const [legacyModel, setLegacyModel] = useState<string | null>(null)
  const [switching, setSwitching] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [caps, setCaps] = useState<ModelCapabilities | null>(null)
  const rootRef = useRef<HTMLDivElement>(null)
  const navigate = useNavigate()

  const refreshCaps = () => {
    api.getModelCapabilities().then(setCaps).catch(() => setCaps(null))
  }
  // Anchored with position:fixed — the HUD row has overflow-x-auto, which
  // would clip an absolutely-positioned dropdown.
  const anchorRect = open ? rootRef.current?.getBoundingClientRect() : undefined

  // Close on outside click
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  const handleToggle = () => {
    const next = !open
    setOpen(next)
    if (next) {
      setError(null)
      api.getLlmProviders().then((d) => {
        setProviders(d.providers || [])
        setActive(d.active_model || d.active || '')
        setLegacyModel(d.legacy?.model_id ?? null)
      }).catch(() => {})
      refreshCaps()
    }
  }

  const handleSelect = async (selector: string) => {
    if (selector === active || switching) { setOpen(false); return }
    setSwitching(selector)
    setError(null)
    try {
      const res = await api.setActiveLlmProvider(selector)
      setActive(selector)
      onSwitched(res.model_id)
      setOpen(false)
      setCaps(null)
      refreshCaps()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSwitching(null)
    }
  }

  return (
    <div ref={rootRef} className="relative shrink-0">
      <button
        onClick={handleToggle}
        className="flex items-center gap-1.5 h-7 px-3 rounded-full transition-colors hover:bg-[var(--surface-3)] font-mono text-[12px]"
        style={{ color: 'var(--fg-muted)' }}
        title="切换模型（立即生效）"
      >
        <Cpu size={12} style={{ color: 'var(--fg-faint)', flexShrink: 0 }} />
        <span>{currentModel ? (currentModel.split('/').pop() ?? currentModel) : 'model'}</span>
        <ChevronDown size={11} style={{ color: 'var(--fg-faint)' }} />
      </button>

      {open && (
        <div
          className="fixed w-64 rounded-[14px] overflow-hidden z-50 animate-slide-up max-h-[70vh] flex flex-col"
          style={{
            background: 'var(--surface-2)', border: '1px solid var(--border-strong)', boxShadow: 'var(--shadow-elevated)',
            left: anchorRect?.left ?? 0,
            bottom: anchorRect ? window.innerHeight - anchorRect.top + 6 : 0,
          }}
        >
          <div className="px-3 pt-2 pb-1 text-[11.5px] shrink-0" style={{ color: 'var(--fg-faint)' }}>
            Model
          </div>

          <div className="overflow-y-auto flex-1">
            {/* Legacy flat-config default */}
            {legacyModel && (
              <button
                onClick={() => handleSelect('')}
                className="w-full flex items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-[var(--surface-3)]"
              >
                <span className="w-3 shrink-0 text-center" style={{ color: 'var(--accent)' }}>
                  {active === '' && <Check size={12} />}
                </span>
                <span className="flex-1 min-w-0">
                  <span className="block text-[13px] truncate" style={{ color: 'var(--fg)' }}>
                    默认 · {legacyModel.split('/').pop() ?? legacyModel}
                  </span>
                  <span className="block text-[12px] truncate" style={{ color: 'var(--fg-faint)' }}>{legacyModel}</span>
                </span>
              </button>
            )}

            {/* Providers grouped with their models */}
            {providers.map((p) => (
              <div key={p.name}>
                <div className="px-3 pt-1.5 pb-0.5 text-[11.5px]" style={{ color: 'var(--fg-faint)' }}>
                  {p.name}
                </div>
                {p.models.map((m) => {
                  const selector = `${p.name}/${m.model_id}`
                  return (
                    <button
                      key={selector}
                      onClick={() => handleSelect(selector)}
                      disabled={switching !== null}
                      className="w-full flex items-center gap-2 px-3 py-2 pl-6 text-left transition-colors hover:bg-[var(--surface-3)] disabled:opacity-50"
                    >
                      <span className="w-3 shrink-0 text-center" style={{ color: 'var(--accent)' }}>
                        {active === selector && <Check size={12} />}
                      </span>
                      <span className="flex-1 min-w-0">
                        <span className="block text-[13px] truncate" style={{ color: 'var(--fg)' }}>
                          {m.model_id.split('/').pop() ?? m.model_id}
                        </span>
                        <span className="block text-[12px] truncate" style={{ color: 'var(--fg-faint)' }}>{m.model_id}</span>
                      </span>
                      {switching === selector && (
                        <span className="w-3 h-3 border-2 border-t-transparent rounded-full animate-spin shrink-0" style={{ borderColor: 'var(--fg-faint)', borderTopColor: 'transparent' }} />
                      )}
                    </button>
                  )
                })}
              </div>
            ))}
          </div>

          {error && (
            <div className="px-3 py-1.5 text-[12px] shrink-0" style={{ color: 'var(--red)' }}>{error}</div>
          )}

          {/* Capability detection results for the active model */}
          <div className="px-3 pt-2 pb-2.5 shrink-0" style={{ borderTop: '1px solid var(--border)' }}>
            <div className="flex items-center justify-between mb-1.5">
              <span className="text-[11.5px]" style={{ color: 'var(--fg-faint)' }}>能力检测</span>
              <span className="text-[12px]" style={{ color: 'var(--fg-faint)' }}>
                {!caps ? '检测中…'
                  : caps.source === 'probe' ? '实时探测'
                  : caps.source === 'config' ? '配置指定'
                  : '注册表'}
              </span>
            </div>
            {caps ? (
              <>
                <div className="flex flex-wrap gap-1">
                  {(() => {
                    // Mutually exclusive fallback ladder (backend decisions.py):
                    // native tools → JSON Mode → JSON Schema. Show whichever
                    // mechanism is active; all-red shows 原生工具 as failed.
                    const chain = [
                      { key: 'tool_calling', label: '原生工具' },
                      { key: 'json_mode', label: 'JSON Mode' },
                      { key: 'json_schema', label: 'JSON Schema' },
                    ] as const
                    const active = chain.find(c => caps[c.key]) ?? chain[0]
                    return (
                      <CapChip
                        label={active.label}
                        ok={caps[active.key]}
                        disabled={caps.session_disabled.includes(active.key)}
                      />
                    )
                  })()}
                  <CapChip label="Thinking" ok={caps.thinking} disabled={caps.session_disabled.includes('thinking')} />
                  <CapChip label="视觉" ok={caps.vision} />
                  <CapChip label="并行工具" ok={caps.parallel_tool_calls} disabled={caps.session_disabled.includes('parallel_tool_calls')} />
                </div>
                <div className="mt-1.5 text-[12px]" style={{ color: 'var(--fg-faint)' }}>
                  上下文 {(caps.max_context_tokens / 1000).toFixed(0)}K · 输出 {(caps.max_output_tokens / 1000).toFixed(0)}K
                </div>
              </>
            ) : (
              <div className="flex flex-wrap gap-1">
                {[0, 1, 2, 3, 4].map(i => (
                  <span key={i} className="h-4 w-14 rounded animate-pulse" style={{ background: 'var(--surface-3)' }} />
                ))}
              </div>
            )}
          </div>

          <button
            onClick={() => { setOpen(false); navigate('/settings') }}
            className="w-full flex items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-[var(--surface-3)] shrink-0"
            style={{ borderTop: '1px solid var(--border)', color: 'var(--fg-muted)' }}
          >
            <Settings2 size={12} style={{ flexShrink: 0 }} />
            <span className="text-[12px]">管理模型提供商…</span>
          </button>
        </div>
      )}
    </div>
  )
}

function CapChip({ label, ok, disabled = false }: { label: string; ok: boolean; disabled?: boolean }) {
  return (
    <span
      className="inline-flex items-center gap-0.5 px-1.5 rounded text-[10.5px] leading-4"
      title={disabled ? `${label}：运行时被禁用（API 报错后自动降级）` : label}
      style={
        disabled
          ? { background: 'var(--surface-3)', color: 'var(--amber)' }
          : ok
            ? { background: 'var(--surface-3)', color: 'var(--green)' }
            : { background: 'var(--surface-3)', color: 'var(--fg-faint)' }
      }
    >
      {ok && !disabled ? <Check size={8} /> : (!ok && !disabled) ? <X size={8} /> : <span className="w-1.5 h-1.5 rounded-full" style={{ background: 'currentColor' }} />}
      {label}
    </span>
  )
}
