import {
  MessageSquare, Settings, BookOpen, Library, Sparkles, Plug, Brain,
  Package, BarChart3, Activity, Bell, ScrollText, FlaskConical,
  type LucideIcon,
} from 'lucide-react'

export interface NavItem {
  id: string
  label: string
  path: string
  icon: LucideIcon
  match?: (pathname: string) => boolean
  aliases?: string[]
}

export const PRIMARY_NAV: NavItem[] = [
  {
    id: 'chat', label: '聊天', aliases: ['chat', '对话'], path: '/', icon: MessageSquare,
    match: (p) => p === '/' || p.startsWith('/chat'),
  },
  {
    id: 'settings', label: '设置', aliases: ['settings', '配置'], path: '/settings', icon: Settings,
    match: (p) => p === '/settings' || p.startsWith('/settings/'),
  },
]

export const SETTINGS_NAV: NavItem[] = [
  { id: 'general', label: '常规设置', aliases: ['general'], path: '/settings', icon: Settings },
  { id: 'knowledge', label: '知识库', aliases: ['knowledge', '文档'], path: '/settings/knowledge', icon: BookOpen },
  { id: 'wiki', label: 'Wiki', aliases: ['知识页面'], path: '/settings/wiki', icon: Library },
  { id: 'skills', label: '技能', aliases: ['skills', 'skill'], path: '/settings/skills', icon: Sparkles },
  { id: 'mcp', label: 'MCP', path: '/settings/mcp', icon: Plug },
  { id: 'memory', label: '记忆', aliases: ['memory'], path: '/settings/memory', icon: Brain },
  { id: 'plugins', label: '插件', aliases: ['plugins', 'extensions'], path: '/settings/plugins', icon: Package },
  { id: 'stats', label: '用量统计', aliases: ['stats', '统计'], path: '/settings/stats', icon: BarChart3 },
  { id: 'health', label: '运行状态', aliases: ['health', '健康'], path: '/settings/health', icon: Activity },
  { id: 'alerts', label: '告警', aliases: ['alerts'], path: '/settings/alerts', icon: Bell },
  { id: 'audit', label: '操作审计', aliases: ['audit', '审计'], path: '/settings/audit', icon: ScrollText },
  { id: 'eval', label: '评测', aliases: ['eval', 'evaluation'], path: '/settings/eval', icon: FlaskConical },
]

export function navItemForPath(pathname: string): NavItem | undefined {
  return PRIMARY_NAV.find((i) => (i.match ? i.match(pathname) : pathname.startsWith(i.path)))
}

export function settingsItemForPath(pathname: string): NavItem | undefined {
  return SETTINGS_NAV.find((i) =>
    i.path === '/settings' ? pathname === '/settings' : pathname.startsWith(i.path),
  )
}
