import { useEffect } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { SETTINGS_NAV } from './nav'

/**
 * Context-sidebar content for the Settings section (v2.2): sub-navigation for
 * all non-chat pages. Rendered by AppShell when the active section is Settings.
 * Esc (outside form fields) returns to chat, matching the back-button hint.
 */
export default function SettingsNav() {
  const navigate = useNavigate()

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || e.ctrlKey || e.metaKey || e.altKey) return
      const target = e.target
      if (target instanceof HTMLElement && target.closest('input, textarea, select, [role="dialog"]')) return
      navigate('/')
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [navigate])

  return (
    <div className="w-[232px] flex flex-col h-full shrink-0 overflow-y-auto py-3 px-2.5">
      {/* Back to chat — the only exit from the settings section */}
      <button
        onClick={() => navigate('/')}
        className="flex items-center gap-2 px-2.5 mb-2 rounded-lg transition-all"
        style={{
          height: 32,
          color: 'var(--fg-secondary)',
          transitionDuration: '150ms',
          transitionTimingFunction: 'var(--ease)',
        }}
        onMouseEnter={e => { e.currentTarget.style.background = 'var(--surface-3)'; e.currentTarget.style.color = 'var(--fg)' }}
        onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'var(--fg-secondary)' }}
      >
        <ArrowLeft size={14} style={{ flexShrink: 0 }} />
        <span className="text-[12px] font-medium">Back to Chat</span>
        <kbd
          className="ml-auto font-mono text-[10px] px-1 rounded"
          style={{ color: 'var(--fg-faint)', background: 'var(--surface-2)' }}
        >
          Esc
        </kbd>
      </button>
      <div
        className="px-2.5 pb-2 text-[11px] font-medium uppercase"
        style={{ color: 'var(--fg-muted)', letterSpacing: '0.06em' }}
      >
        Settings
      </div>
      <div className="flex flex-col" style={{ gap: 2 }}>
        {SETTINGS_NAV.map((item) => {
          const Icon = item.icon
          return (
            <NavLink
              key={item.id}
              to={item.path}
              end={item.path === '/settings'}
              className="flex items-center gap-2.5 px-2.5 rounded-lg transition-all"
              style={({ isActive }) => ({
                height: 32,
                color: isActive ? 'var(--fg)' : 'var(--fg-secondary)',
                background: isActive ? 'var(--accent-muted)' : 'transparent',
                boxShadow: isActive ? 'var(--glow-accent)' : 'none',
                transitionDuration: '150ms',
                transitionTimingFunction: 'var(--ease)',
              })}
            >
              {({ isActive }) => (
                <>
                  <Icon size={14} style={{ color: isActive ? 'var(--accent)' : 'var(--fg-faint)', flexShrink: 0 }} />
                  <span className="text-[12px] font-medium flex-1">{item.label}</span>
                </>
              )}
            </NavLink>
          )
        })}
      </div>
    </div>
  )
}
