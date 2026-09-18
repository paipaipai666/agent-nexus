import {
  MessageSquare, Sparkles, Plug, Brain, BookOpen, Library, Package,
  BarChart3, Activity, Bell, ScrollText, FlaskConical, Settings,
  type LucideIcon,
} from 'lucide-react'

export interface NavItem {
  /** stable id, also used for route matching */
  id: string
  label: string
  path: string
  icon: LucideIcon
  /** match extra path prefixes (e.g. chat matches /chat/:id) */
  match?: (pathname: string) => boolean
}

export interface NavGroup {
  id: 'conversation' | 'capabilities' | 'observe' | 'system'
  items: NavItem[]
}

/** v2 information architecture — designs/mockups/v2-ui.yaml */
export const NAV_GROUPS: NavGroup[] = [
  {
    id: 'conversation',
    items: [
      {
        id: 'chat', label: 'Chat', path: '/', icon: MessageSquare,
        match: (p) => p === '/' || p.startsWith('/chat'),
      },
    ],
  },
  {
    id: 'capabilities',
    items: [
      { id: 'skills', label: 'Skills', path: '/skills', icon: Sparkles },
      { id: 'mcp', label: 'MCP', path: '/mcp', icon: Plug },
      { id: 'memory', label: 'Memory', path: '/memory', icon: Brain },
      { id: 'knowledge', label: 'Knowledge', path: '/knowledge', icon: BookOpen },
      { id: 'wiki', label: 'Wiki', path: '/wiki', icon: Library },
      { id: 'plugins', label: 'Plugins', path: '/plugins', icon: Package },
    ],
  },
  {
    id: 'observe',
    items: [
      { id: 'stats', label: 'Stats', path: '/stats', icon: BarChart3 },
      { id: 'health', label: 'Health', path: '/health', icon: Activity },
      { id: 'alerts', label: 'Alerts', path: '/alerts', icon: Bell },
      { id: 'audit', label: 'Audit', path: '/audit', icon: ScrollText },
      { id: 'eval', label: 'Eval', path: '/eval', icon: FlaskConical },
    ],
  },
  {
    id: 'system',
    items: [
      { id: 'settings', label: 'Settings', path: '/settings', icon: Settings },
    ],
  },
]

export const NAV_ITEMS: NavItem[] = NAV_GROUPS.flatMap((g) => g.items)

export function navItemForPath(pathname: string): NavItem | undefined {
  return NAV_ITEMS.find((i) => (i.match ? i.match(pathname) : pathname.startsWith(i.path)))
}
