import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Check, ChevronDown, Cpu, Settings2 } from 'lucide-react'
import { api } from '../../services/api'

interface ProviderInfo {
  name: string
  model_id: string
  base_url: string
  api_key: string
  timeout: number
}

interface ModelPickerProps {
  /** Currently active model id (from runtime status) — displayed on the chip. */
  currentModel: string | null
  /** Called after a successful switch, with the new active model id. */
  onSwitched: (modelId: string) => void
}

/** Model selector living in the chat input's HUD row. Lists the configured
 *  provider profiles plus the legacy flat-config default; switching is live
 *  (the backend hot-swaps the shared LLM client). */
export default function ModelPicker({ currentModel, onSwitched }: ModelPickerProps) {
  const [open, setOpen] = useState(false)
  const [providers, setProviders] = useState<ProviderInfo[]>([])
  const [active, setActive] = useState('')
  const [legacyModel, setLegacyModel] = useState<string | null>(null)
  const [switching, setSwitching] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const rootRef = useRef<HTMLDivElement>(null)
  const navigate = useNavigate()
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
        setActive(d.active || '')
        setLegacyModel(d.legacy?.model_id ?? null)
      }).catch(() => {})
    }
  }

  const handleSelect = async (name: string) => {
    if (name === active || switching) { setOpen(false); return }
    setSwitching(name)
    setError(null)
    try {
      const res = await api.setActiveLlmProvider(name)
      setActive(name)
      onSwitched(res.model_id)
      setOpen(false)
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
        className="flex items-center gap-1 px-1.5 py-0.5 rounded transition-colors hover:bg-[var(--surface-2)]"
        style={{ color: 'var(--fg-muted)' }}
        title="切换模型（立即生效）"
      >
        <Cpu size={10} style={{ color: 'var(--fg-faint)', flexShrink: 0 }} />
        <span>{currentModel ? (currentModel.split('/').pop() ?? currentModel) : 'model'}</span>
        <ChevronDown size={9} style={{ color: 'var(--fg-faint)' }} />
      </button>

      {open && (
        <div
          className="fixed w-64 rounded-lg overflow-hidden z-50 animate-slide-up"
          style={{
            background: 'var(--surface-2)', border: '1px solid var(--border-strong)', boxShadow: 'var(--shadow-lg)',
            left: anchorRect?.left ?? 0,
            bottom: anchorRect ? window.innerHeight - anchorRect.top + 6 : 0,
          }}
        >
          <div className="px-3 pt-2 pb-1 text-[9px] uppercase tracking-wider" style={{ color: 'var(--fg-faint)' }}>
            Model
          </div>

          {/* Legacy flat-config default */}
          {legacyModel && (
            <button
              onClick={() => handleSelect('')}
              className="w-full flex items-center gap-2 px-3 py-1.5 text-left transition-colors hover:bg-[var(--surface-3)]"
            >
              <span className="w-3 shrink-0 text-center" style={{ color: 'var(--green)' }}>
                {active === '' && <Check size={10} />}
              </span>
              <span className="flex-1 min-w-0">
                <span className="block text-[11px] truncate" style={{ color: 'var(--fg)' }}>
                  默认 · {legacyModel.split('/').pop() ?? legacyModel}
                </span>
                <span className="block text-[9px] truncate" style={{ color: 'var(--fg-faint)' }}>{legacyModel}</span>
              </span>
            </button>
          )}

          {providers.map((p) => (
            <button
              key={p.name}
              onClick={() => handleSelect(p.name)}
              disabled={switching !== null}
              className="w-full flex items-center gap-2 px-3 py-1.5 text-left transition-colors hover:bg-[var(--surface-3)] disabled:opacity-50"
            >
              <span className="w-3 shrink-0 text-center" style={{ color: 'var(--green)' }}>
                {active === p.name && <Check size={10} />}
              </span>
              <span className="flex-1 min-w-0">
                <span className="block text-[11px] truncate" style={{ color: 'var(--fg)' }}>
                  {p.name} · {p.model_id.split('/').pop() ?? p.model_id}
                </span>
                <span className="block text-[9px] truncate" style={{ color: 'var(--fg-faint)' }}>{p.model_id}</span>
              </span>
              {switching === p.name && (
                <span className="w-3 h-3 border-2 border-t-transparent rounded-full animate-spin shrink-0" style={{ borderColor: 'var(--fg-faint)', borderTopColor: 'transparent' }} />
              )}
            </button>
          ))}

          {error && (
            <div className="px-3 py-1.5 text-[10px]" style={{ color: 'var(--red)' }}>{error}</div>
          )}

          <button
            onClick={() => { setOpen(false); navigate('/settings/general') }}
            className="w-full flex items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-[var(--surface-3)]"
            style={{ borderTop: '1px solid var(--border)', color: 'var(--fg-muted)' }}
          >
            <Settings2 size={10} style={{ flexShrink: 0 }} />
            <span className="text-[10px]">管理模型提供商…</span>
          </button>
        </div>
      )}
    </div>
  )
}
