import { useState, useEffect } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { MessageSquare, BookOpen, Zap, Brain, Settings, Plus, Server, Puzzle, BarChart3, Heart, Bell, FileText, FlaskConical, Network } from 'lucide-react'
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
  { path: '/wiki', icon: Network, label: 'Wiki' },
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
        borderRadius: '8px 0 0 8px',
        boxShadow: '1px 0 4px rgba(17,17,23,0.05)',
      }}
    >
      <div className="flex-1 overflow-y-auto px-3 py-3">
        {/* New Chat */}
        <button
          onClick={handleNewChat}
          className="w-full flex items-center gap-2 px-3 py-2 rounded-md transition-all duration-150 text-left mb-1"
          style={{
            background: 'var(--blue)',
            color: '#ffffff',
          }}
        >
          <Plus size={14} />
          <span className="text-[13px] font-medium">New Chat</span>
        </button>

        {/* Recent Sessions */}
        <div className="mb-2 mt-2">
          <span className="px-3 text-[10px] font-medium tracking-wider" style={{ color: 'var(--fg-muted)', fontFamily: 'var(--font-mono)' }}>
            RECENT
          </span>
        </div>

        {loading ? (
          <div className="px-3 py-3 flex justify-center">
            <div className="w-4 h-4 border-2 border-t-transparent rounded-full animate-spin" style={{ borderColor: 'var(--fg-faint)', borderTopColor: 'transparent' }} />
          </div>
        ) : recentSessions.length > 0 && (
          <div className="space-y-0.5">
            {recentSessions.map((session) => {
              const active = isActiveSession(session.session_id)
              return (
                <button
                  key={session.session_id}
                  onClick={() => handleSessionClick(session.session_id)}
                  className="w-full flex items-center gap-2 px-3 py-1.5 rounded-md transition-all duration-150 text-left"
                  style={{
                    color: active ? 'var(--fg)' : 'var(--fg-muted)',
                    background: active ? 'var(--surface-2)' : 'transparent',
                  }}
                >
                  <MessageSquare size={14} style={{ color: active ? 'var(--accent)' : 'var(--fg-faint)', flexShrink: 0 }} />
                  <span className="text-[12px] truncate flex-1" title={session.preview || 'New session'}>
                    {session.preview || 'New session'}
                  </span>
                  <span className="text-[10px] shrink-0" style={{ color: 'var(--fg-faint)', fontFamily: 'var(--font-mono)' }}>
                    {formatTime(session.last_message_at)}
                  </span>
                </button>
              )
            })}
          </div>
        )}

        {/* Nav Items */}
        <div className="mt-4 space-y-0.5">
          <div className="h-px my-2 mx-3" style={{ background: 'var(--border)' }} />
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
                  background: isActive ? 'var(--surface-2)' : 'transparent',
                }}
              >
                <Icon size={16} style={{ color: isActive ? 'var(--accent)' : undefined }} />
                <span className="text-[13px] truncate">{label}</span>
              </button>
            )
          })}
        </div>
      </div>
    </nav>
  )
}
