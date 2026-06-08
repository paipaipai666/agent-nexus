import { NavLink, Outlet, Navigate, useLocation } from 'react-router-dom'
import {
  Settings, BookOpen, Network, Zap, Server, Brain, Puzzle,
  BarChart3, Heart, Bell, FileText, FlaskConical,
} from 'lucide-react'

interface SettingsTab {
  path: string
  label: string
  icon: any
}

const settingsTabs: SettingsTab[] = [
  { path: '/settings/general', label: 'General', icon: Settings },
  { path: '/settings/knowledge', label: 'Knowledge', icon: BookOpen },
  { path: '/settings/wiki', label: 'Wiki', icon: Network },
  { path: '/settings/skills', label: 'Skills', icon: Zap },
  { path: '/settings/mcp', label: 'MCP', icon: Server },
  { path: '/settings/memory', label: 'Memory', icon: Brain },
  { path: '/settings/plugins', label: 'Plugins', icon: Puzzle },
  { path: '/settings/stats', label: 'Stats', icon: BarChart3 },
  { path: '/settings/health', label: 'Health', icon: Heart },
  { path: '/settings/alerts', label: 'Alerts', icon: Bell },
  { path: '/settings/audit', label: 'Audit', icon: FileText },
  { path: '/settings/eval', label: 'Eval', icon: FlaskConical },
]

export default function SettingsLayout() {
  const location = useLocation()

  // Redirect /settings → /settings/general
  if (location.pathname === '/settings' || location.pathname === '/settings/') {
    return <Navigate to="/settings/general" replace />
  }

  return (
    <div className="flex-1 flex overflow-hidden">
      {/* Sub-navigation */}
      <nav
        className="w-[160px] shrink-0 overflow-y-auto py-3 px-2"
        style={{ borderRight: '1px solid var(--border)' }}
      >
        <div className="space-y-0.5">
          {settingsTabs.map(tab => {
            const Icon = tab.icon
            return (
              <NavLink
                key={tab.path}
                to={tab.path}
                className={({ isActive }) =>
                  `flex items-center gap-2 px-2.5 py-1.5 rounded-md text-[12px] transition-colors ${
                    isActive ? 'font-medium' : ''
                  }`
                }
                style={({ isActive }) => ({
                  color: isActive ? 'var(--fg)' : 'var(--fg-muted)',
                  background: isActive ? 'var(--surface-2)' : 'transparent',
                })}
              >
                <Icon size={14} />
                {tab.label}
              </NavLink>
            )
          })}
        </div>
      </nav>

      {/* Content area */}
      <div className="flex-1 overflow-y-auto">
        <Outlet />
      </div>
    </div>
  )
}
