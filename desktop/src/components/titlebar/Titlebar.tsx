import { Minus, Square, X, Sun, Moon, Search } from 'lucide-react'
import { useLocation } from 'react-router-dom'
import { useTheme } from '../theme/ThemeProvider'
import { useUIStore } from '../../services/ui'
import { navItemForPath } from '../layout/nav'

/**
 * v2 titlebar (36px, frameless drag region):
 * logo + wordmark · route breadcrumb · command-palette trigger / theme / window controls.
 */
export default function Titlebar() {
  const { theme, toggleTheme } = useTheme()
  const location = useLocation()
  const setPaletteOpen = useUIStore((s) => s.setPaletteOpen)
  const current = navItemForPath(location.pathname)

  return (
    <div
      className="h-9 flex items-center select-none shrink-0 relative z-10"
      style={{
        background: 'color-mix(in srgb, var(--surface-0) 80%, transparent)',
        backdropFilter: 'blur(16px) saturate(1.4)',
        WebkitBackdropFilter: 'blur(16px) saturate(1.4)',
        borderBottom: '1px solid var(--border-subtle)',
        WebkitAppRegion: 'drag',
      } as React.CSSProperties}
    >
      {/* App identity */}
      <div className="w-[232px] shrink-0 px-3 flex items-center gap-2">
        <span
          className="grid place-items-center w-[18px] h-[18px] rounded-[5px] font-mono font-bold text-[10px]"
          style={{ background: 'var(--accent-gradient)', color: '#fff', boxShadow: '0 1px 4px var(--accent-glow)' }}
        >
          N
        </span>
        <span className="text-[12px] font-semibold tracking-tight" style={{ color: 'var(--fg-secondary)' }}>
          AgentNexus
        </span>
      </div>

      {/* Breadcrumb */}
      <div
        className="absolute left-1/2 -translate-x-1/2 flex items-center gap-1.5 text-[12px]"
        style={{ color: 'var(--fg-muted)' }}
      >
        <span>{current?.label ?? 'Chat'}</span>
        {location.pathname.startsWith('/chat/') && (
          <>
            <span style={{ color: 'var(--fg-faint)' }}>/</span>
            <span className="font-mono text-[11px]" style={{ color: 'var(--fg-secondary)' }}>
              {location.pathname.split('/').pop()?.slice(0, 8)}
            </span>
          </>
        )}
      </div>

      {/* Controls */}
      <div className="flex items-center h-full ml-auto" style={{ WebkitAppRegion: 'no-drag' } as React.CSSProperties}>
        <button
          onClick={() => setPaletteOpen(true)}
          className="flex items-center gap-2 h-6 px-2.5 mr-2 rounded-lg transition-colors font-mono text-[11px]"
          style={{
            background: 'var(--surface-2)',
            border: '1px solid var(--border-subtle)',
            color: 'var(--fg-muted)',
            minWidth: 140,
          }}
          onMouseEnter={e => { e.currentTarget.style.borderColor = 'var(--border-strong)'; e.currentTarget.style.color = 'var(--fg-secondary)' }}
          onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border-subtle)'; e.currentTarget.style.color = 'var(--fg-muted)' }}
        >
          <Search size={11} />
          <span>Search</span>
          <span className="ml-auto" style={{ color: 'var(--fg-faint)' }}>Ctrl K</span>
        </button>
        <button
          onClick={toggleTheme}
          className="w-10 h-full grid place-items-center transition-colors"
          style={{ color: 'var(--fg-muted)' }}
          onMouseEnter={e => { e.currentTarget.style.background = 'var(--surface-3)'; e.currentTarget.style.color = 'var(--fg)' }}
          onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'var(--fg-muted)' }}
          title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
        >
          {theme === 'dark' ? <Sun size={14} /> : <Moon size={14} />}
        </button>
        <button
          onClick={() => window.electronAPI?.minimize()}
          className="w-10 h-full grid place-items-center transition-colors"
          style={{ color: 'var(--fg-muted)' }}
          onMouseEnter={e => { e.currentTarget.style.background = 'var(--surface-3)'; e.currentTarget.style.color = 'var(--fg)' }}
          onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'var(--fg-muted)' }}
        >
          <Minus size={14} />
        </button>
        <button
          onClick={() => window.electronAPI?.maximize()}
          className="w-10 h-full grid place-items-center transition-colors"
          style={{ color: 'var(--fg-muted)' }}
          onMouseEnter={e => { e.currentTarget.style.background = 'var(--surface-3)'; e.currentTarget.style.color = 'var(--fg)' }}
          onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'var(--fg-muted)' }}
        >
          <Square size={12} />
        </button>
        <button
          onClick={() => window.electronAPI?.close()}
          className="w-10 h-full grid place-items-center transition-colors"
          style={{ color: 'var(--fg-muted)' }}
          onMouseEnter={e => { e.currentTarget.style.background = 'var(--red)'; e.currentTarget.style.color = 'white' }}
          onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'var(--fg-muted)' }}
        >
          <X size={14} />
        </button>
      </div>
    </div>
  )
}
