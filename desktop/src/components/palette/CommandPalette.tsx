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
  group: '会话' | '页面' | '操作'
  keywords?: string
  label: string
  hint?: string
  kbd?: string
  icon: LucideIcon
  run: () => void
}

export default function CommandPalette() {
  const navigate = useNavigate()
  const { theme, toggleTheme } = useTheme()
  const { paletteOpen, setPaletteOpen, togglePalette, toggleSidebar } = useUIStore()
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState(0)
  const [recents, setRecents] = useState<{ session_id: string; preview: string }[]>([])
  const inputRef = useRef<HTMLInputElement>(null)
  const dialogRef = useRef<HTMLDivElement>(null)
  const selectedRef = useRef<HTMLButtonElement>(null)
  const previousFocus = useRef<HTMLElement | null>(null)
  const [recentError, setRecentError] = useState<string | null>(null)
  const [recentLoading, setRecentLoading] = useState(false)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey)) return
      if (e.altKey || e.shiftKey || e.isComposing) return
      const k = e.key.toLowerCase()
      if (k === 'k') { e.preventDefault(); togglePalette() }
      else if (k === 'b') { e.preventDefault(); toggleSidebar() }
      else if (k === 'n') {
        e.preventDefault()
        setPaletteOpen(false)
        window.dispatchEvent(new Event('new-chat'))
        navigate('/')
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [togglePalette, toggleSidebar, navigate, setPaletteOpen])

  useEffect(() => {
    if (!paletteOpen) return
    setQuery('')
    setSelected(0)
    previousFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const timer = window.setTimeout(() => inputRef.current?.focus(), 0)
    return () => {
      window.clearTimeout(timer)
      if (previousFocus.current?.isConnected) previousFocus.current.focus()
    }
  }, [paletteOpen])

  useEffect(() => {
    if (!paletteOpen) return
    let cancelled = false
    setRecentError(null)
    setRecentLoading(true)
    const timer = window.setTimeout(() => {
      api.getRecentSessions(query.trim() ? 20 : 5, query)
        .then(({ sessions }) => {
          if (!cancelled) setRecents(sessions.filter(session => session.preview?.trim()).slice(0, query.trim() ? 20 : 4))
        })
        .catch(error => {
          if (!cancelled) { setRecents([]); setRecentError(`会话搜索失败：${error instanceof Error ? error.message : '请重试'}`) }
        })
        .finally(() => { if (!cancelled) setRecentLoading(false) })
    }, query.trim() ? 200 : 0)
    return () => { cancelled = true; window.clearTimeout(timer) }
  }, [paletteOpen, query])

  const items = useMemo<PaletteItem[]>(() => {
    const all: PaletteItem[] = [
      ...recents.map((s) => ({
        id: `recent-${s.session_id}`,
        group: '会话' as const,
        label: s.preview,
        icon: Clock,
        run: () => navigate(`/chat/${s.session_id}`),
      })),
      ...PRIMARY_NAV.map((n) => ({
        id: `nav-${n.id}`,
        group: '页面' as const,
        label: `打开${n.label}`,
        keywords: n.aliases?.join(' '),
        icon: n.icon,
        run: () => navigate(n.path),
      })),
      ...SETTINGS_NAV.map((n) => ({
        id: `nav-${n.id}`,
        group: '页面' as const,
        label: `打开${n.label}`,
        keywords: n.aliases?.join(' '),
        icon: n.icon,
        run: () => navigate(n.path),
      })),
      {
        id: 'act-new-chat', group: '操作', label: '新建聊天', keywords: 'new chat 对话', kbd: 'Ctrl N', icon: Plus,
        run: () => { window.dispatchEvent(new Event('new-chat')); navigate('/') },
      },
      {
        id: 'act-theme', group: '操作', label: theme === 'dark' ? '切换浅色主题' : '切换深色主题', keywords: 'theme light dark 主题',
        icon: theme === 'dark' ? Sun : Moon, run: toggleTheme,
      },
      {
        id: 'act-sidebar', group: '操作', label: '切换侧栏显示', keywords: 'sidebar 侧边栏', kbd: 'Ctrl B', icon: PanelLeft,
        run: toggleSidebar,
      },
    ]
    const q = query.trim().toLowerCase()
    return q ? all.filter(item => item.group === '会话' || `${item.label} ${item.keywords ?? ''}`.toLowerCase().includes(q)) : all
  }, [recents, query, navigate, theme, toggleTheme, toggleSidebar])

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
  useEffect(() => {
    setSelected(current => Math.min(current, Math.max(0, items.length - 1)))
    selectedRef.current?.scrollIntoView?.({ block: 'nearest' })
  }, [selected, items])

  if (!paletteOpen) return null

  const exec = (i: number) => {
    const target = items[i]
    if (!target) return
    setPaletteOpen(false)
    target?.run()
  }

  const onInputKey = (e: React.KeyboardEvent) => {
    if (e.nativeEvent.isComposing || e.keyCode === 229) return
    if (e.key === 'ArrowDown') { e.preventDefault(); setSelected((s) => Math.min(s + 1, Math.max(0, items.length - 1))) }
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
        ref={dialogRef}
        aria-label="搜索与命令"
        aria-modal="true"
        onKeyDown={event => {
          if (event.key === 'Escape') { event.preventDefault(); setPaletteOpen(false); return }
          if (event.key !== 'Tab') return
          const focusable = [...(dialogRef.current?.querySelectorAll<HTMLElement>('input, button, [tabindex="0"]') ?? [])]
            .filter(element => !element.hasAttribute('disabled'))
          const first = focusable[0]
          const last = focusable.at(-1)
          if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
          else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
        }}
        className="fixed left-1/2 z-50 w-[560px] max-w-[92vw] overflow-hidden"
        style={{
          top: '12vh',
          transform: 'translateX(-50%)',
          background: 'var(--surface-2)',
          border: '1px solid var(--border)',
          borderRadius: '16px',
          boxShadow: 'var(--shadow-elevated)',
          transformOrigin: 'top center',
          animation: 'pop-in-3d 0.25s var(--ease)',
        }}
      >
        <div
          className="flex items-center gap-2.5 px-4"
          style={{ height: 52, borderBottom: '1px solid var(--border-subtle)' }}
        >
          <Search size={14} style={{ color: 'var(--fg-muted)', flexShrink: 0 }} />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onInputKey}
            placeholder="搜索会话、页面或操作…"
            aria-label="搜索会话、页面或操作"
            maxLength={200}
            className="flex-1 bg-transparent outline-none text-[15px]"
            style={{ color: 'var(--fg)' }}
          />
          <kbd className="font-mono text-[11.5px] px-1.5 py-0.5 rounded" style={{ color: 'var(--fg-faint)', background: 'var(--surface-3)' }}>esc</kbd>
        </div>

        <div className="max-h-[320px] overflow-y-auto p-1.5">
          {recentLoading && <p role="status" className="px-3 py-2 text-xs" style={{ color: 'var(--fg-secondary)' }}>正在搜索会话…</p>}
          {recentError && <p role="alert" className="px-3 py-2 text-xs" style={{ color: 'var(--red)' }}>{recentError}</p>}
          {items.length === 0 && (
            <div className="py-6 text-center text-[12px]" style={{ color: 'var(--fg-faint)' }}>
              没有匹配的结果：{query}
            </div>
          )}
          {groups.map((g) => (
            <div key={g.name}>
              <div
                className="px-2.5 pt-2 pb-1 text-[11.5px] font-medium"
                style={{ color: 'var(--fg-muted)' }}
              >
                {g.name}
              </div>
              {g.items.map(({ item, index }) => {
                const Icon = item.icon
                const sel = index === selected
                return (
                  <button
                    ref={sel ? selectedRef : undefined}
                    key={item.id}
                    onClick={() => exec(index)}
                    onMouseEnter={() => setSelected(index)}
                    className="relative w-full flex items-center gap-2.5 px-2.5 py-[9px] rounded-lg text-left transition-colors"
                    style={{
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
                    <span className="text-[14px] truncate">{item.label}</span>
                    <span className="ml-auto flex items-center gap-1.5 shrink-0">
                      {item.hint && <span className="font-mono text-[11.5px]" style={{ color: 'var(--fg-faint)' }}>{item.hint}</span>}
                      {item.kbd && (
                        <kbd className="font-mono text-[11.5px] px-1.5 py-0.5 rounded" style={{ color: 'var(--fg-faint)', background: 'var(--surface-1)' }}>
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
          className="flex items-center gap-3.5 px-3.5 font-mono text-[11.5px]"
          style={{ height: 40, borderTop: '1px solid var(--border-subtle)', color: 'var(--fg-faint)' }}
        >
          <span>↑↓ 选择</span>
          <span>Enter 打开</span>
          <span className="ml-auto">Esc 关闭</span>
        </div>
      </div>
    </>
  )
}
