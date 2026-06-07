import { useState, useEffect } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { MessageSquare, BookOpen, Zap, Brain, Settings, BarChart3, Plus, Server, Puzzle, Heart, Bell, FileText, FlaskConical } from 'lucide-react'
import { api } from '../../services/api'

interface RecentSession {
  session_id: string
  created_at: string
  updated_at: string
  last_message_at: string
  preview: string
  profile: string | null
}

const navItems = [
  { path: '/knowledge', icon: BookOpen, label: 'Knowledge' },
  { path: '/skills', icon: Zap, label: 'Skills' },
  { path: '/mcp', icon: Server, label: 'MCP' },
  { path: '/memory', icon: Brain, label: 'Memory' },
  { path: '/plugins', icon: Puzzle, label: 'Plugins' },
  { divider: true },
  { path: '/settings', icon: Settings, label: 'Settings' },
  { path: '/stats', icon: BarChart3, label: 'Stats' },
  { path: '/health', icon: Heart, label: 'Health' },
  { path: '/alerts', icon: Bell, label: 'Alerts' },
  { path: '/audit', icon: FileText, label: 'Audit' },
  { path: '/eval', icon: FlaskConical, label: 'Eval' },
]

export default function Sidebar() {
  const location = useLocation()
  const navigate = useNavigate()
  const [recentSessions, setRecentSessions] = useState<RecentSession[]>([])
  const [loading, setLoading] = useState(false)

  const isChatActive = location.pathname === '/' || location.pathname.startsWith('/chat/')

  useEffect(() => {
    loadRecentSessions()
  }, [])

  useEffect(() => {
    if (isChatActive) loadRecentSessions()
  }, [location.pathname])

  const loadRecentSessions = async () => {
    setLoading(true)
    try {
      const { sessions } = await api.getRecentSessions(8)
      setRecentSessions(sessions)
    } catch (error) {
      console.error('Failed to load recent sessions:', error)
    } finally {
      setLoading(false)
    }
  }

  const handleNewChat = () => navigate('/')
  const handleSessionClick = (sessionId: string) => navigate(`/chat/${sessionId}`)

  const formatTime = (dateStr: string) => {
    const date = new Date(dateStr)
    const now = new Date()
    const diffMs = now.getTime() - date.getTime()
    const diffMins = Math.floor(diffMs / 60000)
    const diffHours = Math.floor(diffMs / 3600000)
    const diffDays = Math.floor(diffMs / 86400000)
    if (diffMins < 1) return 'now'
    if (diffMins < 60) return `${diffMins}m`
    if (diffHours < 24) return `${diffHours}h`
    if (diffDays < 7) return `${diffDays}d`
    return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
  }

  const isActiveSession = (sid: string) => location.pathname === `/chat/${sid}`

  return (
    <nav
      className="w-[220px] flex flex-col shrink-0"
      style={{
        background: 'var(--surface-1)',
        borderRight: '1px solid var(--border)',
      }}
    >
      <div className="flex-1 overflow-y-auto px-3 py-3">
        {/* New Chat */}
        <button
          onClick={handleNewChat}
          className="w-full flex items-center gap-2.5 px-3 py-2 rounded-md transition-all duration-150 text-left mb-1"
          style={{
            color: isChatActive && location.pathname === '/' ? 'var(--fg)' : 'var(--fg-muted)',
            background: isChatActive && location.pathname === '/' ? 'var(--surface-3)' : 'transparent',
          }}
          onMouseEnter={e => {
            if (!(isChatActive && location.pathname === '/')) {
              e.currentTarget.style.color = 'var(--fg-secondary)'
              e.currentTarget.style.background = 'var(--surface-2)'
            }
          }}
          onMouseLeave={e => {
            if (!(isChatActive && location.pathname === '/')) {
              e.currentTarget.style.color = 'var(--fg-muted)'
              e.currentTarget.style.background = 'transparent'
            }
          }}
        >
          <Plus size={16} strokeWidth={1.5} style={{ color: isChatActive && location.pathname === '/' ? 'var(--accent)' : undefined }} />
          <span className="text-[13px] truncate">New Chat</span>
        </button>

        {/* Recent Sessions */}
        {loading ? (
          <div className="px-3 py-3 flex justify-center">
            <div className="w-4 h-4 border-2 border-t-transparent rounded-full animate-spin" style={{ borderColor: 'var(--fg-faint)', borderTopColor: 'transparent' }} />
          </div>
        ) : recentSessions.length > 0 && (
          <div className="mb-2 space-y-0.5">
            {recentSessions.map((session) => {
              const active = isActiveSession(session.session_id)
              return (
                <button
                  key={session.session_id}
                  onClick={() => handleSessionClick(session.session_id)}
                  className="w-full flex items-center gap-2 px-3 py-1.5 rounded-md transition-all duration-150 text-left group"
                  style={{
                    color: active ? 'var(--fg)' : 'var(--fg-muted)',
                    background: active ? 'var(--surface-3)' : 'transparent',
                  }}
                  onMouseEnter={e => {
                    if (!active) {
                      e.currentTarget.style.color = 'var(--fg-secondary)'
                      e.currentTarget.style.background = 'var(--surface-2)'
                    }
                  }}
                  onMouseLeave={e => {
                    if (!active) {
                      e.currentTarget.style.color = 'var(--fg-muted)'
                      e.currentTarget.style.background = 'transparent'
                    }
                  }}
                >
                  <MessageSquare size={14} strokeWidth={active ? 2 : 1.5} style={{ color: active ? 'var(--accent)' : undefined, flexShrink: 0 }} />
                  <span className="text-[12px] truncate flex-1" title={session.preview || 'New session'}>
                    {session.preview || 'New session'}
                  </span>
                  <span className="text-[10px] shrink-0" style={{ color: 'var(--fg-faint)' }}>
                    {formatTime(session.last_message_at)}
                  </span>
                </button>
              )
            })}
          </div>
        )}

        {/* Nav Items */}
        <div className="space-y-0.5">
          {navItems.map((item, idx) => {
            if ('divider' in item) {
              return <div key={`div-${idx}`} className="h-px my-2 mx-3" style={{ background: 'var(--border)' }} />
            }
            const { path, icon: Icon, label } = item
            const isActive = location.pathname === path
            return (
              <button
                key={path}
                onClick={() => navigate(path)}
                className="w-full flex items-center gap-2.5 px-3 py-2 rounded-md transition-all duration-150 text-left"
                style={{
                  color: isActive ? 'var(--fg)' : 'var(--fg-muted)',
                  background: isActive ? 'var(--surface-3)' : 'transparent',
                }}
                onMouseEnter={e => {
                  if (!isActive) {
                    e.currentTarget.style.color = 'var(--fg-secondary)'
                    e.currentTarget.style.background = 'var(--surface-2)'
                  }
                }}
                onMouseLeave={e => {
                  if (!isActive) {
                    e.currentTarget.style.color = 'var(--fg-muted)'
                    e.currentTarget.style.background = 'transparent'
                  }
                }}
              >
                <Icon size={16} strokeWidth={isActive ? 2 : 1.5} style={{ color: isActive ? 'var(--accent)' : undefined }} />
                <span className="text-[13px] truncate">{label}</span>
              </button>
            )
          })}
        </div>
      </div>
    </nav>
  )
}
