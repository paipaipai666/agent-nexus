import { useState, useEffect } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { MessageSquare, BookOpen, Zap, Brain, Settings, BarChart3, Plus, Clock, Server, Puzzle } from 'lucide-react'
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
  { path: '/', icon: MessageSquare, label: 'Chat', isChat: true },
  { path: '/knowledge', icon: BookOpen, label: 'Knowledge' },
  { path: '/skills', icon: Zap, label: 'Skills' },
  { path: '/mcp', icon: Server, label: 'MCP' },
  { path: '/memory', icon: Brain, label: 'Memory' },
  { path: '/plugins', icon: Puzzle, label: 'Plugins' },
  { path: '/settings', icon: Settings, label: 'Settings' },
  { path: '/stats', icon: BarChart3, label: 'Stats' },
]

export default function Sidebar() {
  const location = useLocation()
  const navigate = useNavigate()
  const [showSessionList, setShowSessionList] = useState(false)
  const [recentSessions, setRecentSessions] = useState<RecentSession[]>([])
  const [loading, setLoading] = useState(false)

  const isChatActive = location.pathname === '/' || location.pathname.startsWith('/chat/')

  useEffect(() => {
    if (showSessionList) loadRecentSessions()
  }, [showSessionList])

  const loadRecentSessions = async () => {
    setLoading(true)
    try {
      const { sessions } = await api.getRecentSessions(5)
      setRecentSessions(sessions)
    } catch (error) {
      console.error('Failed to load recent sessions:', error)
    } finally {
      setLoading(false)
    }
  }

  const handleChatClick = () => setShowSessionList(!showSessionList)
  const handleNewChat = () => { setShowSessionList(false); navigate('/') }
  const handleSessionClick = (sessionId: string) => { setShowSessionList(false); navigate(`/chat/${sessionId}`) }

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

  return (
    <nav
      className="w-[52px] flex flex-col items-center py-3 gap-0.5 shrink-0 relative"
      style={{
        background: 'var(--surface-1)',
        borderRight: '1px solid var(--border)',
      }}
    >
      {navItems.map(({ path, icon: Icon, label, isChat }) => {
        const isActive = isChat ? isChatActive : location.pathname === path
        return (
          <div key={path} className="relative">
            <button
              onClick={isChat ? handleChatClick : () => navigate(path)}
              title={label}
              className="w-9 h-9 flex items-center justify-center rounded-lg transition-all duration-150"
              style={{
                color: isActive ? 'var(--accent)' : 'var(--fg-muted)',
                background: isActive ? 'var(--accent-subtle)' : 'transparent',
              }}
              onMouseEnter={e => {
                if (!isActive) {
                  e.currentTarget.style.color = 'var(--fg-secondary)'
                  e.currentTarget.style.background = 'var(--surface-3)'
                }
              }}
              onMouseLeave={e => {
                if (!isActive) {
                  e.currentTarget.style.color = 'var(--fg-muted)'
                  e.currentTarget.style.background = 'transparent'
                }
              }}
            >
              <Icon size={18} strokeWidth={isActive ? 2 : 1.5} />
            </button>

            {/* Session List Dropdown */}
            {isChat && showSessionList && (
              <div
                className="absolute left-12 top-0 z-50 w-72 animate-scale-in"
                style={{
                  background: 'var(--surface-3)',
                  border: '1px solid var(--border-strong)',
                  borderRadius: '12px',
                  boxShadow: '0 16px 64px rgba(0,0,0,0.5)',
                }}
              >
                <button
                  onClick={handleNewChat}
                  className="w-full flex items-center gap-3 px-4 py-3 transition-colors"
                  style={{ borderBottom: '1px solid var(--border)' }}
                  onMouseEnter={e => e.currentTarget.style.background = 'var(--surface-4)'}
                  onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                >
                  <div className="w-7 h-7 rounded-lg flex items-center justify-center" style={{ background: 'var(--accent-muted)' }}>
                    <Plus size={14} style={{ color: 'var(--accent)' }} />
                  </div>
                  <span className="text-sm font-medium" style={{ color: 'var(--fg)' }}>New Chat</span>
                </button>

                <div className="max-h-80 overflow-y-auto py-1">
                  {loading ? (
                    <div className="px-4 py-6 text-center">
                      <div className="w-5 h-5 border-2 border-t-transparent rounded-full animate-spin mx-auto" style={{ borderColor: 'var(--fg-faint)', borderTopColor: 'transparent' }} />
                    </div>
                  ) : recentSessions.length === 0 ? (
                    <div className="px-4 py-6 text-sm text-center" style={{ color: 'var(--fg-muted)' }}>No recent sessions</div>
                  ) : (
                    recentSessions.map((session) => (
                      <button
                        key={session.session_id}
                        onClick={() => handleSessionClick(session.session_id)}
                        className="w-full flex flex-col gap-1 px-4 py-2.5 transition-colors text-left"
                        onMouseEnter={e => e.currentTarget.style.background = 'var(--surface-4)'}
                        onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                      >
                        <div className="flex items-center justify-between">
                          <span className="text-sm truncate flex-1" style={{ color: 'var(--fg)' }}>
                            {session.preview || 'New session'}
                          </span>
                          <span className="text-xs ml-2 flex items-center gap-1 shrink-0" style={{ color: 'var(--fg-faint)' }}>
                            <Clock size={10} />
                            {formatTime(session.last_message_at)}
                          </span>
                        </div>
                        <span className="text-xs font-mono truncate" style={{ color: 'var(--fg-faint)' }}>
                          {session.session_id.slice(0, 16)}
                        </span>
                      </button>
                    ))
                  )}
                </div>
              </div>
            )}
          </div>
        )
      })}

      {/* Click outside to close */}
      {showSessionList && (
        <div className="fixed inset-0 z-40" onClick={() => setShowSessionList(false)} />
      )}
    </nav>
  )
}
