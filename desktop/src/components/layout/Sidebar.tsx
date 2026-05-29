import { useLocation, useNavigate } from 'react-router-dom'
import { MessageSquare, BookOpen, Zap, Brain, Settings, BarChart3 } from 'lucide-react'

const navItems = [
  { path: '/', icon: MessageSquare, label: 'Chat' },
  { path: '/knowledge', icon: BookOpen, label: 'Knowledge' },
  { path: '/skills', icon: Zap, label: 'Skills' },
  { path: '/memory', icon: Brain, label: 'Memory' },
  { path: '/settings', icon: Settings, label: 'Settings' },
  { path: '/stats', icon: BarChart3, label: 'Stats' },
]

export default function Sidebar() {
  const location = useLocation()
  const navigate = useNavigate()

  return (
    <nav className="w-14 bg-bg-secondary border-r border-border-default flex flex-col items-center py-3 gap-1 shrink-0">
      {navItems.map(({ path, icon: Icon, label }) => {
        const isActive = location.pathname === path
        return (
          <button
            key={path}
            onClick={() => navigate(path)}
            title={label}
            className={`w-10 h-10 flex items-center justify-center rounded-md transition-colors ${
              isActive
                ? 'bg-accent-primary/20 text-accent-primary'
                : 'text-text-muted hover:text-text-primary hover:bg-bg-tertiary'
            }`}
          >
            <Icon size={20} />
          </button>
        )
      })}
    </nav>
  )
}
