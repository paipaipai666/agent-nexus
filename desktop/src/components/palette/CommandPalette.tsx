import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Search, Clock, Plus, Sun, Moon, PanelLeft, type LucideIcon,
} from 'lucide-react'
import { PRIMARY_NAV, SETTINGS_NAV } from '../layout/nav'
import { useUIStore } from '../../services/ui'
import { useTheme } from '../theme/ThemeProvider'
import { api } from '../../services/api'

interface PaletteItem {
  id: string
  group: 'Recent' | 'Navigation' | 'Actions'
  label: string
  hint?: string
  kbd?: string
  icon: LucideIcon
  run: () => void
}

/**
 * Ctrl+K command palette (v2): grouped results, live filter, full keyboard
 * control. Overlay surface = surface-2 + border + elevated shadow.
 */
export default function CommandPalette() {
  const navigate = useNavigate()
  const { theme, toggleTheme } = useTheme()
  const { paletteOpen, setPaletteOpen, togglePalette, toggleSidebar } = useUIStore()
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState(0)
  const [recents, setRecents] = useState<{ session_id: string; preview: string }[]>([])
  const inputRef = useRef<HTMLInputElement>(null)

  // Global shortcuts: Ctrl+K palette, Ctrl+B sidebar
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey)) return
      const k = e.key.toLowerCase()
      if (k === 'k') { e.preventDefault(); togglePalette() }
      else if (k === 'b') { e.preventDefault(); toggleSidebar() }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [togglePalette, toggleSidebar])

  // Lazy-load recent sessions each time the palette opens
  useEffect(() => {
    if (!paletteOpen) return
    setQuery('')
    setSelected(0)
    setTimeout(() => inputRef.current?.focus(), 50)
    api.getRecentSessions(5)
      .then(({ sessions }) => setRecents(sessions.filter((s) => s.preview?.trim()).slice(0, 4)))
      .catch(() => setRecents([]))
  }, [paletteOpen])

  const items = useMemo<PaletteItem[]>(() => {
    const all: PaletteItem[] = [
      ...recents.map((s) => ({
        id: `recent-${s.session_id}`,
        group: 'Recent' as const,
        label: s.preview,
        icon: Clock,
        run: () => navigate(`/chat/${s.session_id}`),
      })),
      ...PRIMARY_NAV.map((n) => ({
        id: `nav-${n.id}`,
        group: 'Navigation' as const,
        label: `Go to ${n.label}`,
        icon: n.icon,
        run: () => navigate(n.path),
      })),
      ...SETTINGS_NAV.map((n) => ({
        id: `nav-${n.id}`,
        group: 'Navigation' as const,
        label: `Go to ${n.label}`,
        icon: n.icon,
        run: () => navigate(n.path),
      })),
      {
        id: 'act-new-chat', group: 'Actions', label: 'New Chat', kbd: 'Ctrl N', icon: Plus,
        run: () => { window.dispatchEvent(new Event('new-chat')); navigate('/') },
      },
      {
        id: 'act-theme', group: 'Actions', label: theme === 'dark' ? 'Switch to Light Theme' : 'Switch to Dark Theme',
        icon: theme === 'dark' ? Sun : Moon, run: toggleTheme,
      },
      {
        id: 'act-sidebar', group: 'Actions', label: 'Toggle Sidebar', kbd: 'Ctrl B', icon: PanelLeft,
        run: toggleSidebar,
      },
    ]
    const q = query.trim().toLowerCase()
    return q ? all.filter((i) => i.label.toLowerCase().includes(q)) : all
  }, [recents, query, navigate, theme, toggleTheme, toggleSidebar])

  // Grouped view preserving order
  const groups = useMemo(() => {
    const out: { name: string; items: { item: PaletteItem; index: number }[] }[] = []
    items.forEach((item, index) => {
      const g = out.find((x) => x.name === item.group)
      if (g) g.items.push({ item, index })
      else out.push({ name: item.group, items: [{ item, index }] })
    })
    return out
  }, [items])

  useEffect(() => { setSelected(0) }, [query])

  if (!paletteOpen) return null

  const exec = (i: number) => {
    const target = items[i]
    setPaletteOpen(false)
    target?.run()
  }

  const onInputKey = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setSelected((s) => Math.min(s + 1, items.length - 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setSelected((s) => Math.max(s - 1, 0)) }
    else if (e.key === 'Enter') { e.preventDefault(); exec(selected) }
    else if (e.key === 'Escape') { e.preventDefault(); setPaletteOpen(false) }
  }

  return (
    <>
      <div
        className="fixed inset-0 z-40"
        style={{ background: 'var(--scrim)', backdropFilter: 'blur(2px)', animation: 'rise-in 0.15s var(--ease)' }}
        onClick={() => setPaletteOpen(false)}
      />
      <div
        role="dialog"
        aria-label="Command palette"
        className="fixed left-1/2 z-50 w-[560px] max-w-[92vw] overflow-hidden"
        style={{
          top: '12vh',
          transform: 'translateX(-50%)',
          background: 'var(--surface-2)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--radius-lg)',
          boxShadow: 'var(--shadow-elevated)',
          transformOrigin: 'top center',
          animation: 'pop-in-3d 0.25s var(--ease)',
        }}
      >
        <div
          className="flex items-center gap-2.5 px-4"
          style={{ height: 46, borderBottom: '1px solid var(--border-subtle)' }}
        >
          <Search size={14} style={{ color: 'var(--fg-muted)', flexShrink: 0 }} />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onInputKey}
            placeholder="Type a command or search…"
            className="flex-1 bg-transparent outline-none text-[14px]"
            style={{ color: 'var(--fg)' }}
          />
          <kbd className="font-mono text-[10px] px-1.5 py-0.5 rounded" style={{ color: 'var(--fg-faint)', background: 'var(--surface-3)' }}>esc</kbd>
        </div>

        <div className="max-h-[320px] overflow-y-auto p-1.5">
          {items.length === 0 && (
            <div className="py-6 text-center text-[12px]" style={{ color: 'var(--fg-faint)' }}>
              No results for “{query}”
            </div>
          )}
          {groups.map((g) => (
            <div key={g.name}>
              <div
                className="px-2.5 pt-2 pb-1 text-[11px] font-medium uppercase"
                style={{ color: 'var(--fg-faint)', letterSpacing: '0.06em' }}
              >
                {g.name}
              </div>
              {g.items.map(({ item, index }) => {
                const Icon = item.icon
                const sel = index === selected
                return (
                  <button
                    key={item.id}
                    onClick={() => exec(index)}
                    onMouseEnter={() => setSelected(index)}
                    className="relative w-full flex items-center gap-2.5 px-2.5 rounded-lg text-left transition-colors"
                    style={{
                      height: 36,
                      background: sel ? 'var(--surface-3)' : 'transparent',
                      color: sel ? 'var(--fg)' : 'var(--fg-secondary)',
                      transitionDuration: '120ms',
                    }}
                  >
                    {sel && (
                      <span
                        className="absolute left-0 top-2 bottom-2 w-[2px] rounded-full"
                        style={{ background: 'var(--accent)' }}
                      />
                    )}
                    <Icon size={14} style={{ color: sel ? 'var(--fg-secondary)' : 'var(--fg-muted)', flexShrink: 0 }} />
                    <span className="text-[13px] truncate">{item.label}</span>
                    <span className="ml-auto flex items-center gap-1.5 shrink-0">
                      {item.hint && <span className="font-mono text-[10px]" style={{ color: 'var(--fg-faint)' }}>{item.hint}</span>}
                      {item.kbd && (
                        <kbd className="font-mono text-[10px] px-1.5 py-0.5 rounded" style={{ color: 'var(--fg-faint)', background: 'var(--surface-1)' }}>
                          {item.kbd}
                        </kbd>
                      )}
                    </span>
                  </button>
                )
              })}
            </div>
          ))}
        </div>

        <div
          className="flex items-center gap-3.5 px-3.5 font-mono text-[10px]"
          style={{ height: 32, borderTop: '1px solid var(--border-subtle)', color: 'var(--fg-faint)' }}
        >
          <span>↑↓ navigate</span>
          <span>↵ open</span>
          <span className="ml-auto">esc close</span>
        </div>
      </div>
    </>
  )
}
