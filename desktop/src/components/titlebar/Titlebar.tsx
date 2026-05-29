import { Minus, Square, X, Search, Sun, Moon } from 'lucide-react'
import { useTheme } from '../theme/ThemeProvider'

export default function Titlebar() {
  const { theme, toggleTheme } = useTheme()

  return (
    <div className="h-9 bg-bg-secondary border-b border-border-default flex items-center select-none shrink-0"
         style={{ WebkitAppRegion: 'drag' } as React.CSSProperties}>
      <div className="flex items-center px-3 gap-2">
        <div className="w-4 h-4 rounded bg-accent-primary" />
        <span className="text-sm font-medium text-text-primary">AgentNexus</span>
      </div>

      <div className="flex-1 flex justify-center">
        <div className="flex items-center bg-bg-tertiary rounded-md px-2 py-1 gap-1 text-text-muted text-xs"
             style={{ WebkitAppRegion: 'no-drag' } as React.CSSProperties}>
          <Search size={12} />
          <span>Ctrl+K</span>
        </div>
      </div>

      <div className="flex items-center h-full gap-0"
           style={{ WebkitAppRegion: 'no-drag' } as React.CSSProperties}>
        <button
          onClick={toggleTheme}
          className="w-9 h-full flex items-center justify-center hover:bg-bg-tertiary text-text-muted hover:text-text-primary transition-colors"
          title={theme === 'dark' ? 'Switch to light' : 'Switch to dark'}
        >
          {theme === 'dark' ? <Sun size={14} /> : <Moon size={14} />}
        </button>
        <button
          onClick={() => window.electronAPI?.minimize()}
          className="w-11 h-full flex items-center justify-center hover:bg-bg-tertiary text-text-muted hover:text-text-primary transition-colors"
        >
          <Minus size={14} />
        </button>
        <button
          onClick={() => window.electronAPI?.maximize()}
          className="w-11 h-full flex items-center justify-center hover:bg-bg-tertiary text-text-muted hover:text-text-primary transition-colors"
        >
          <Square size={12} />
        </button>
        <button
          onClick={() => window.electronAPI?.close()}
          className="w-11 h-full flex items-center justify-center hover:bg-status-error hover:text-white text-text-muted transition-colors"
        >
          <X size={14} />
        </button>
      </div>
    </div>
  )
}
