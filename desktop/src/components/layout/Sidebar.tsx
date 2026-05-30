import { useState, useEffect } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { MessageSquare, BookOpen, Zap, Brain, Settings, BarChart3, Plus, Clock } from 'lucide-react'
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
  { path: '/memory', icon: Brain, label: 'Memory' },
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
    if (showSessionList) {
      loadRecentSessions()
    }
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

  const handleChatClick = () => {
    setShowSessionList(!showSessionList)
  }

  const handleNewChat = () => {
    setShowSessionList(false)
    navigate('/')
  }

  const handleSessionClick = (sessionId: string) => {
    setShowSessionList(false)
    navigate(`/chat/${sessionId}`)
  }

  const formatTime = (dateStr: string) => {
    const date = new Date(dateStr)
    const now = new Date()
    const diffMs = now.getTime() - date.getTime()
    const diffMins = Math.floor(diffMs / 60000)
    const diffHours = Math.floor(diffMs / 3600000)
    const diffDays = Math.floor(diffMs / 86400000)

    if (diffMins < 1) return '刚刚'
    if (diffMins < 60) return `${diffMins}分钟前`
    if (diffHours < 24) return `${diffHours}小时前`
    if (diffDays < 7) return `${diffDays}天前`
    return date.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })
  }

  return (
    <nav className="w-14 bg-bg-secondary border-r border-border-default flex flex-col items-center py-3 gap-1 shrink-0 relative">
      {navItems.map(({ path, icon: Icon, label, isChat }) => {
        const isActive = isChat ? isChatActive : location.pathname === path
        return (
          <div key={path} className="relative">
            <button
              onClick={isChat ? handleChatClick : () => navigate(path)}
              title={label}
              className={`w-10 h-10 flex items-center justify-center rounded-md transition-colors ${
                isActive
                  ? 'bg-accent-primary/20 text-accent-primary'
                  : 'text-text-muted hover:text-text-primary hover:bg-bg-tertiary'
              }`}
            >
              <Icon size={20} />
            </button>

            {/* Session List Dropdown */}
            {isChat && showSessionList && (
              <div className="absolute left-12 top-0 z-50 w-72 bg-bg-secondary border border-border-default rounded-lg shadow-xl">
                {/* New Chat Button */}
                <button
                  onClick={handleNewChat}
                  className="w-full flex items-center gap-3 px-4 py-3 hover:bg-bg-tertiary transition-colors border-b border-border-default"
                >
                  <Plus size={18} className="text-accent-primary" />
                  <span className="text-sm text-text-primary font-medium">开启新对话</span>
                </button>

                {/* Recent Sessions */}
                <div className="max-h-80 overflow-y-auto">
                  {loading ? (
                    <div className="px-4 py-3 text-sm text-text-muted text-center">
                      加载中...
                    </div>
                  ) : recentSessions.length === 0 ? (
                    <div className="px-4 py-3 text-sm text-text-muted text-center">
                      暂无历史会话
                    </div>
                  ) : (
                    recentSessions.map((session) => (
                      <button
                        key={session.session_id}
                        onClick={() => handleSessionClick(session.session_id)}
                        className="w-full flex flex-col gap-1 px-4 py-3 hover:bg-bg-tertiary transition-colors text-left border-b border-border-default last:border-b-0"
                      >
                        <div className="flex items-center justify-between">
                          <span className="text-sm text-text-primary truncate flex-1">
                            {session.preview || '新会话'}
                          </span>
                          <span className="text-xs text-text-muted ml-2 flex items-center gap-1">
                            <Clock size={10} />
                            {formatTime(session.last_message_at)}
                          </span>
                        </div>
                        <span className="text-xs text-text-muted truncate">
                          {session.session_id}
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
        <div
          className="fixed inset-0 z-40"
          onClick={() => setShowSessionList(false)}
        />
      )}
    </nav>
  )
}
