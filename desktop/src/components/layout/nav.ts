import {
  MessageSquare, Settings, BookOpen, Library, Sparkles, Plug, Brain,
  Package, BarChart3, Activity, Bell, ScrollText, FlaskConical,
  type LucideIcon,
} from 'lucide-react'

export interface NavItem {
  /** stable id */
  id: string
  label: string
  path: string
  icon: LucideIcon
  /** match extra path prefixes (e.g. chat matches /chat/:id) */
  match?: (pathname: string) => boolean
}

/** v2.3 — two primary sections only; everything else lives under Settings. */
export const PRIMARY_NAV: NavItem[] = [
  {
    id: 'chat', label: 'Chat', path: '/', icon: MessageSquare,
    match: (p) => p === '/' || p.startsWith('/chat'),
  },
  {
    id: 'settings', label: 'Settings', path: '/settings', icon: Settings,
    match: (p) => p === '/settings' || p.startsWith('/settings/'),
  },
]

/** Settings sub-navigation — rendered in the context sidebar under /settings/*. */
export const SETTINGS_NAV: NavItem[] = [
  { id: 'general', label: 'General', path: '/settings', icon: Settings },
  { id: 'knowledge', label: 'Knowledge', path: '/settings/knowledge', icon: BookOpen },
  { id: 'wiki', label: 'Wiki', path: '/settings/wiki', icon: Library },
  { id: 'skills', label: 'Skills', path: '/settings/skills', icon: Sparkles },
  { id: 'mcp', label: 'MCP', path: '/settings/mcp', icon: Plug },
  { id: 'memory', label: 'Memory', path: '/settings/memory', icon: Brain },
  { id: 'plugins', label: 'Plugins', path: '/settings/plugins', icon: Package },
  { id: 'stats', label: 'Stats', path: '/settings/stats', icon: BarChart3 },
  { id: 'health', label: 'Health', path: '/settings/health', icon: Activity },
  { id: 'alerts', label: 'Alerts', path: '/settings/alerts', icon: Bell },
  { id: 'audit', label: 'Audit', path: '/settings/audit', icon: ScrollText },
  { id: 'eval', label: 'Eval', path: '/settings/eval', icon: FlaskConical },
]

export function navItemForPath(pathname: string): NavItem | undefined {
  return PRIMARY_NAV.find((i) => (i.match ? i.match(pathname) : pathname.startsWith(i.path)))
}

/** Sub-page label for the titlebar breadcrumb (e.g. "Settings / Stats"). */
export function settingsItemForPath(pathname: string): NavItem | undefined {
  return SETTINGS_NAV.find((i) =>
    i.path === '/settings' ? pathname === '/settings' : pathname.startsWith(i.path),
  )
}
