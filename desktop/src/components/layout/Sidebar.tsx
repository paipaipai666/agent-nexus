import { useState, useEffect } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { MessageSquare, Settings, Plus } from 'lucide-react'
import { api } from '../../services/api'
import { useSession } from '../session/SessionProvider'

interface RecentSession {
  session_id: string
  created_at: string
  updated_at: string
  last_message_at: string
  preview: string
  profile: string | null
}

export default function Sidebar() {
  const location = useLocation()
  const navigate = useNavigate()
  const { isSessionRunning, activateSession, sessions } = useSession()
  const [recentSessions, setRecentSessions] = useState<RecentSession[]>([])
  const [loading, setLoading] = useState(false)

  const isChatActive = location.pathname === '/' || location.pathname.startsWith('/chat/')
  const isSettingsActive = location.pathname.startsWith('/settings')

  useEffect(() => {
    loadRecentSessions()
  }, [])

  useEffect(() => {
    if (isChatActive) loadRecentSessions()
  }, [location.pathname])

  // Refresh sidebar when a session is updated (e.g., first message sent)
  useEffect(() => {
    const handleSessionUpdated = () => loadRecentSessions()
    window.addEventListener('session-updated', handleSessionUpdated)
    return () => window.removeEventListener('session-updated', handleSessionUpdated)
  }, [])

  const loadRecentSessions = async () => {
    setLoading(true)
    try {
      const { sessions } = await api.getRecentSessions(8)
      // Filter out empty sessions (no preview = never had a user message).
      // These are created eagerly on mount and clutter the sidebar.
      setRecentSessions(sessions.filter(s => s.preview && s.preview.trim()))
    } catch (error) {
      console.error('Failed to load recent sessions:', error)
    } finally {
      setLoading(false)
    }
  }

  const handleNewChat = () => {
    // Dispatch event so ChatPage can reset session state even when already on '/'
    window.dispatchEvent(new Event('new-chat'))
    navigate('/')
  }
  const handleSessionClick = (sessionId: string) => {
    activateSession(sessionId)
    navigate(`/chat/${sessionId}`)
  }

  const formatTime = (dateStr: string) => {
    const date = new Date(dateStr + 'Z')
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

  const parseTime = (dateStr: string) => new Date(dateStr + 'Z').getTime()

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
            {[...recentSessions].sort((a, b) => parseTime(b.last_message_at) - parseTime(a.last_message_at)).map((session) => {
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
                  {isSessionRunning(session.session_id) && (
                    <span
                      className="w-2 h-2 rounded-full shrink-0 animate-pulse"
                      style={{ background: 'var(--green, #22c55e)' }}
                      title="Running"
                    />
                  )}
                  {/* R5: pending confirm badge — orange pulsing "!" */}
                  {sessions.get(session.session_id)?.pendingConfirm && (
                    <span
                      className="text-[10px] font-bold px-1.5 py-0.5 rounded-full shrink-0 animate-pulse"
                      style={{ background: '#f59e0b', color: '#fff' }}
                      title="等待工具确认"
                    >
                      !
                    </span>
                  )}
                  {/* Unread count badge */}
                  {sessions.get(session.session_id)?.unreadCount ? (
                    <span
                      className="text-[9px] px-1 py-0.5 rounded-full shrink-0"
                      style={{ background: 'var(--accent)', color: '#fff' }}
                    >
                      {sessions.get(session.session_id)!.unreadCount}
                    </span>
                  ) : null}
                  <span className="text-[10px] shrink-0" style={{ color: 'var(--fg-faint)', fontFamily: 'var(--font-mono)' }}>
                    {formatTime(session.last_message_at)}
                  </span>
                </button>
              )
            })}
          </div>
        )}

        {/* Settings */}
        <div className="mt-4">
          <div className="h-px my-2 mx-3" style={{ background: 'var(--border)' }} />
          <button
            onClick={() => navigate('/settings/general')}
            className="w-full flex items-center gap-2.5 px-3 py-2 rounded-md transition-all duration-150 text-left"
            style={{
              color: isSettingsActive ? 'var(--fg)' : 'var(--fg-muted)',
              background: isSettingsActive ? 'var(--surface-2)' : 'transparent',
            }}
          >
            <Settings size={16} style={{ color: isSettingsActive ? 'var(--accent)' : undefined }} />
            <span className="text-[13px] truncate">Settings</span>
          </button>
        </div>
      </div>
    </nav>
  )
}
