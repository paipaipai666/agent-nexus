import { Minus, Square, X, Sun, Moon } from 'lucide-react'
import { useTheme } from '../theme/ThemeProvider'
import { useSession } from '../session/SessionProvider'

export default function Titlebar() {
  const { theme, toggleTheme } = useTheme()
  const { sessionId, modelName, contextUsed } = useSession()

  return (
    <div
      className="h-10 flex items-center select-none shrink-0 relative z-[100]"
      style={{
        background: 'var(--surface-1)',
        borderBottom: '1px solid var(--border)',
        WebkitAppRegion: 'drag',
      } as React.CSSProperties}
    >
      {/* Spacer for sidebar width */}
      <div className="w-[200px] shrink-0" />

      {/* Session Info */}
      {sessionId && (
        <div className="flex items-center gap-2 font-mono text-xs" style={{ color: 'var(--fg-muted)' }}>
          <span
            className="w-1.5 h-1.5 rounded-full"
            style={{ background: 'var(--green)', boxShadow: '0 0 6px var(--green-muted)' }}
          />
          <span className="truncate max-w-[180px]">{sessionId.slice(0, 16)}</span>
          {modelName && (
            <span className="text-[11px]" style={{ color: 'var(--fg-faint)' }}>
              {modelName}
            </span>
          )}
        </div>
      )}

      <div className="flex-1" />

      {/* Context Bar */}
      {contextUsed != null && (
        <div className="flex items-center gap-1.5 mr-4 font-mono text-[11px]" style={{ color: 'var(--fg-muted)' }}>
          <span>ctx</span>
          <div className="w-12 h-1 rounded-full overflow-hidden" style={{ background: 'var(--surface-3)' }}>
            <div
              className="h-full rounded-full"
              style={{
                width: `${Math.min(contextUsed, 100)}%`,
                background: 'var(--accent)',
                boxShadow: '0 0 6px var(--accent-glow)',
              }}
            />
          </div>
          <span>{contextUsed}%</span>
        </div>
      )}

      {/* Window Controls */}
      <div className="flex items-center h-full" style={{ WebkitAppRegion: 'no-drag' } as React.CSSProperties}>
        <button
          onClick={toggleTheme}
          className="w-10 h-full flex items-center justify-center transition-colors"
          style={{ color: 'var(--fg-muted)' }}
          onMouseEnter={e => { e.currentTarget.style.background = 'var(--surface-3)'; e.currentTarget.style.color = 'var(--accent)' }}
          onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'var(--fg-muted)' }}
          title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
        >
          {theme === 'dark' ? <Sun size={14} /> : <Moon size={14} />}
        </button>
        <button
          onClick={() => window.electronAPI?.minimize()}
          className="w-11 h-full flex items-center justify-center transition-colors"
          style={{ color: 'var(--fg-muted)' }}
          onMouseEnter={e => { e.currentTarget.style.background = 'var(--surface-3)'; e.currentTarget.style.color = 'var(--fg)' }}
          onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'var(--fg-muted)' }}
        >
          <Minus size={14} />
        </button>
        <button
          onClick={() => window.electronAPI?.maximize()}
          className="w-11 h-full flex items-center justify-center transition-colors"
          style={{ color: 'var(--fg-muted)' }}
          onMouseEnter={e => { e.currentTarget.style.background = 'var(--surface-3)'; e.currentTarget.style.color = 'var(--fg)' }}
          onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'var(--fg-muted)' }}
        >
          <Square size={12} />
        </button>
        <button
          onClick={() => window.electronAPI?.close()}
          className="w-11 h-full flex items-center justify-center transition-colors"
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
