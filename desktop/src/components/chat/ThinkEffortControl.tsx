import { useEffect, useRef, useState } from 'react'
import { api } from '../../services/api'
import { thinkingEffortArm } from '../../services/thinkingEffortArm'
import { useSessionOverride, type SessionOverrideConfig } from '../../hooks/useSessionOverride'
import { useSession } from '../session/SessionManager'

export type ThinkingEffort = 'off' | 'low' | 'medium' | 'high' | 'follow'

const TO_API: Record<Exclude<ThinkingEffort, 'follow'>, string> = {
  off: 'none',
  low: 'low',
  medium: 'medium',
  high: 'high',
}
const FROM_API: Record<string, ThinkingEffort> = {
  none: 'off',
  low: 'low',
  medium: 'medium',
  high: 'high',
}

const EFFORT_OVERRIDE: SessionOverrideConfig<ThinkingEffort, string | null> = {
  arm: thinkingEffortArm,
  initial: 'follow',
  toApi: (v) => (v === 'follow' ? null : TO_API[v]),
  fromApi: (v) => (v ? (FROM_API[v] ?? 'follow') : 'follow'),
  getServerValue: (sessionId) => api.getSession(sessionId).then(d => d.thinking_effort),
  setServerValue: (sessionId, v) => api.setThinkingEffort(sessionId, v).then(d => d.thinking_effort),
}

const LABELS: Record<ThinkingEffort, { en: string; cn: string; code: string }> = {
  follow: { en: 'FOLLOW', cn: '默认', code: 'AUTO' },
  off: { en: 'OFF', cn: '关', code: 'OFF' },
  low: { en: 'LOW', cn: '低', code: 'LOW' },
  medium: { en: 'MED', cn: '中', code: 'MED' },
  high: { en: 'HIGH', cn: '高', code: 'HIGH' },
}
const LEVEL_ORDER: ThinkingEffort[] = ['off', 'low', 'medium', 'high']

/** Thinking-effort chip + popover slider in the chat HUD.
 *
 *  Session override (none/low/medium/high) or follow Settings default.
 *  Mid-run switches apply on the next LLM call. Pre-session window stores
 *  an armed intent, mirroring PlanModeToggle. */
export default function ThinkEffortControl() {
  const { sessionId } = useSession()
  const { value: effort, busy, preview, commit } = useSessionOverride(sessionId, EFFORT_OVERRIDE)
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  /** Live preview during drag; commit on release / click. */
  const apply = (next: ThinkingEffort) => {
    preview(next)
    void commit(next)
  }

  const idx = effort === 'follow' ? 2 : LEVEL_ORDER.indexOf(effort)
  const fillPct = effort === 'follow' ? 66.6 : (idx / 3) * 100
  const active = effort !== 'follow' && effort !== 'medium'

  const chipColor = active || open ? 'var(--accent)' : 'var(--fg-muted)'
  const chipBg = active || open ? 'var(--accent-muted)' : 'transparent'

  return (
    <div ref={rootRef} className="relative shrink-0">
      <button
        onClick={() => setOpen(v => !v)}
        disabled={busy}
        aria-expanded={open}
        aria-haspopup="dialog"
        className="flex items-center gap-1.5 h-7 px-2.5 rounded-full transition-colors hover:bg-[var(--surface-3)] disabled:opacity-40"
        style={{ color: chipColor, background: chipBg, border: active || open ? '1px solid var(--border-active)' : '1px solid transparent' }}
        title="本会话思考强度；默认跟随 Settings"
      >
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" style={{ flexShrink: 0 }}>
          <path d="M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18Z" />
          <path d="M12 5a3 3 0 1 1 5.997.125 4 4 0 0 1 2.526 5.77 4 4 0 0 1-.556 6.588A4 4 0 1 1 12 18Z" />
          <path d="M12 5v13" />
        </svg>
        <span className="text-[12.5px] font-medium">思考</span>
        <span className="font-mono text-[10px] opacity-75 tracking-wider">{LABELS[effort].code}</span>
      </button>

      {open && (
        <div
          role="dialog"
          aria-label="思考强度"
          className="fixed z-50 animate-slide-up"
          style={{
            width: 280,
            background: 'var(--surface-2)',
            border: '1px solid var(--border-strong)',
            borderRadius: 12,
            boxShadow: 'var(--shadow-elevated)',
            left: rootRef.current?.getBoundingClientRect().left ?? 0,
            bottom: rootRef.current ? window.innerHeight - rootRef.current.getBoundingClientRect().top + 10 : 0,
            padding: '14px 14px 12px',
          }}
        >
          <div className="flex items-baseline justify-between mb-0.5">
            <h2 className="text-[13px] font-semibold" style={{ color: 'var(--fg)', letterSpacing: '-0.015em' }}>思考强度</h2>
            <div className="font-mono text-[11px]" style={{ color: 'var(--accent)' }}>{LABELS[effort].cn}</div>
          </div>
          <p className="text-[12px] mb-4" style={{ color: 'var(--fg-faint)' }}>会话覆盖，下一跳生效</p>

          {/* Continuous 12px rail + rounded-square thumb */}
          <div
            role="slider"
            tabIndex={0}
            aria-valuemin={0}
            aria-valuemax={3}
            aria-valuenow={idx}
            aria-valuetext={LABELS[effort].cn}
            aria-label="思考强度"
            className="relative flex items-center cursor-grab active:cursor-grabbing select-none"
            style={{ height: 48, touchAction: 'none' }}
            onKeyDown={(e) => {
              if (e.key === 'ArrowRight' || e.key === 'ArrowUp') {
                e.preventDefault()
                const next = effort === 'follow' ? 'low' : LEVEL_ORDER[Math.min(3, LEVEL_ORDER.indexOf(effort) + 1)]
                apply(next)
              } else if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') {
                e.preventDefault()
                const next = effort === 'follow' ? 'low' : LEVEL_ORDER[Math.max(0, LEVEL_ORDER.indexOf(effort) - 1)]
                apply(next)
              } else if (e.key === 'Home') {
                e.preventDefault(); apply('off')
              } else if (e.key === 'End') {
                e.preventDefault(); apply('high')
              }
            }}
            onPointerDown={(e) => {
              const el = e.currentTarget
              el.setPointerCapture(e.pointerId)
              const rect = el.getBoundingClientRect()
              let last: ThinkingEffort = effort
              const snap = (clientX: number): ThinkingEffort => {
                const r = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width))
                return LEVEL_ORDER[Math.round(r * 3)]
              }
              last = snap(e.clientX)
              preview(last)
              const onMove = (ev: PointerEvent) => {
                last = snap(ev.clientX)
                preview(last)
              }
              const onUp = () => {
                el.releasePointerCapture(e.pointerId)
                el.removeEventListener('pointermove', onMove)
                el.removeEventListener('pointerup', onUp)
                void commit(last)
              }
              el.addEventListener('pointermove', onMove)
              el.addEventListener('pointerup', onUp)
            }}
          >
            <div
              className="absolute left-0 right-0 overflow-hidden"
              style={{ height: 12, top: '50%', marginTop: -6, borderRadius: 999, background: 'var(--surface-4)' }}
            >
              {/* trail */}
              <div
                className="absolute left-0 top-0 bottom-0"
                style={{
                  width: `${Math.min(100, fillPct + 3)}%`,
                  borderRadius: 999,
                  background: 'linear-gradient(90deg, transparent 0%, var(--accent) 65%, var(--accent) 100%)',
                  opacity: effort === 'off' ? 0 : effort === 'high' ? 0.28 : effort === 'low' ? 0.12 : 0.18,
                  filter: 'blur(1px)',
                }}
              />
              {/* fill + intensity bars */}
              <div
                className="absolute left-0 top-0 bottom-0"
                style={{
                  width: `${fillPct}%`,
                  borderRadius: 999,
                  background: effort === 'off' ? 'var(--surface-3)' : 'var(--accent)',
                  transition: 'width 200ms cubic-bezier(0.16,1,0.3,1)',
                }}
              >
                <div
                  aria-hidden
                  className="absolute inset-0"
                  style={{
                    opacity: effort === 'off' ? 0 : effort === 'high' ? 0.42 : effort === 'low' ? 0.22 : 0.32,
                    background:
                      effort === 'high'
                        ? 'repeating-linear-gradient(90deg, transparent 0, transparent 3px, rgba(255,255,255,0.4) 3px, rgba(255,255,255,0.4) 4.5px)'
                        : effort === 'low'
                          ? 'repeating-linear-gradient(90deg, transparent 0, transparent 8px, rgba(255,255,255,0.3) 8px, rgba(255,255,255,0.3) 9.5px)'
                          : 'repeating-linear-gradient(90deg, transparent 0, transparent 5px, rgba(255,255,255,0.35) 5px, rgba(255,255,255,0.35) 6.5px)',
                  }}
                />
              </div>
            </div>
            {/* rounded-square thumb */}
            <div
              className="absolute grid place-items-center"
              style={{
                left: `${fillPct}%`,
                top: '50%',
                width: effort === 'high' ? 24 : 22,
                height: effort === 'high' ? 24 : 22,
                marginTop: effort === 'high' ? -12 : -11,
                marginLeft: effort === 'high' ? -12 : -11,
                borderRadius: effort === 'high' ? 8 : 7,
                background: effort === 'off' ? 'var(--surface-2)' : 'var(--surface-1)',
                border: `2px solid ${effort === 'off' ? 'var(--border-strong)' : 'var(--accent)'}`,
                boxShadow: effort === 'high' ? '0 0 0 3px var(--accent-subtle)' : undefined,
                transition: 'left 200ms cubic-bezier(0.16,1,0.3,1), width 150ms, height 150ms, margin 150ms',
                pointerEvents: 'none',
              }}
            >
              <div style={{ width: 8, height: 3, borderRadius: 2, background: effort === 'off' ? 'var(--fg-faint)' : 'var(--accent)' }} />
            </div>
          </div>

          {/* Faster ←→ Smarter */}
          <div className="flex justify-between mt-2.5 px-0.5">
            <span className="font-mono text-[10px] tracking-wider" style={{ color: idx <= 1 ? 'var(--accent)' : 'var(--fg-faint)' }}>FASTER</span>
            <span className="font-mono text-[10px] tracking-wider" style={{ color: idx >= 2 ? 'var(--accent)' : 'var(--fg-faint)' }}>SMARTER</span>
          </div>

          <div className="flex gap-1 mt-3.5">
            {(['follow', ...LEVEL_ORDER] as ThinkingEffort[]).map(v => (
              <button
                key={v}
                type="button"
                aria-pressed={effort === v}
                onClick={() => apply(v)}
                className="flex-1 rounded-lg py-1.5 text-center transition-colors"
                style={{
                  border: effort === v ? '1px solid var(--border-active)' : '1px solid transparent',
                  background: effort === v ? 'var(--accent-muted)' : 'transparent',
                  color: effort === v ? 'var(--accent)' : 'var(--fg-faint)',
                }}
              >
                <span className="block font-mono text-[10px] tracking-wider font-medium">{LABELS[v].code}</span>
                <span className="block text-[11px] font-medium mt-0.5">{LABELS[v].cn}</span>
              </button>
            ))}
          </div>

          <div className="flex items-center justify-between mt-3.5 pt-3" style={{ borderTop: '1px solid var(--border)' }}>
            <p className="text-[11px] leading-snug" style={{ color: 'var(--fg-faint)', maxWidth: '18ch' }}>拖到哪档算哪档</p>
            <button
              type="button"
              aria-pressed={effort === 'follow'}
              onClick={() => apply('follow')}
              className="h-[26px] px-2.5 rounded-md font-mono text-[10px] tracking-wider"
              style={{
                border: '1px solid var(--border)',
                background: effort === 'follow' ? 'var(--accent-muted)' : 'transparent',
                borderColor: effort === 'follow' ? 'var(--border-active)' : 'var(--border)',
                color: effort === 'follow' ? 'var(--accent)' : 'var(--fg-muted)',
              }}
            >
              AUTO
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
