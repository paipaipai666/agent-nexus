import { useState, useEffect, useMemo } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { MessageSquare, Search, Plus, FolderOpen, FolderPlus, X, ChevronDown, ChevronRight } from 'lucide-react'
import { api } from '../../services/api'
import { useSession } from '../session/SessionProvider'
import { useProjects, selectProject, addProject, removeProject, pickAndAddProject, workspaceKey } from '../../services/projects'

interface RecentSession {
  session_id: string
  created_at: string
  updated_at: string
  last_message_at: string
  preview: string
  profile: string | null
  workspace_path: string
}

interface ProjectGroup {
  key: string
  path: string
  explicit: boolean
  sessions: RecentSession[]
}
const parseTime = (dateStr: string) => new Date(dateStr + 'Z').getTime()

function sortByRecency(list: RecentSession[]) {
  return [...list].sort((a, b) => parseTime(b.last_message_at) - parseTime(a.last_message_at))
}

const formatTime = (dateStr: string) => {
  const diffMs = Date.now() - parseTime(dateStr)
  const diffMins = Math.floor(diffMs / 60000)
  const diffHours = Math.floor(diffMs / 3600000)
  const diffDays = Math.floor(diffMs / 86400000)
  if (diffMins < 1) return 'now'
  if (diffMins < 60) return `${diffMins}m`
  if (diffHours < 24) return `${diffHours}h`
  if (diffDays < 7) return `${diffDays}d`
  return new Date(dateStr + 'Z').toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

/**
 * Context sidebar for the Chat section (v2: 232px, luminance-step surfaces).
 * Projects group their sessions Codex-style; the New Chat button binds to the
 * currently selected project folder.
 */
export default function Sidebar() {
  const location = useLocation()
  const navigate = useNavigate()
  const { isSessionRunning, activateSession, sessions } = useSession()
  const [recentSessions, setRecentSessions] = useState<RecentSession[]>([])
  const [loading, setLoading] = useState(false)
  const [query, setQuery] = useState('')
  const { projects, selected } = useProjects()
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})

  const isChatActive = location.pathname === '/' || location.pathname.startsWith('/chat/')

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
      const { sessions } = await api.getRecentSessions(50)
      // Filter out empty sessions (no preview = never had a user message).
      // These are created eagerly on mount and clutter the sidebar.
      setRecentSessions(sessions.filter(s => s.preview && s.preview.trim()))
    } catch (error) {
      console.error('Failed to load recent sessions:', error)
    } finally {
      setLoading(false)
    }
  }

  // Group sessions under their project folder (Codex-style). Explicit projects
  // come first in stored order; sessions whose folder was never added render as
  // implicit groups ordered by recency.
  const groups = useMemo<ProjectGroup[]>(() => {
    const byKey = new Map<string, RecentSession[]>()
    const q = query.trim().toLowerCase()
    for (const s of recentSessions) {
      if (!s.workspace_path) continue
      if (q && !s.preview.toLowerCase().includes(q)) continue
      const key = workspaceKey(s.workspace_path)
      const list = byKey.get(key)
      if (list) list.push(s)
      else byKey.set(key, [s])
    }
    const claimed = new Set<string>()
    const explicit: ProjectGroup[] = projects.map((p) => {
      const key = workspaceKey(p)
      claimed.add(key)
      return { key, path: p, explicit: true, sessions: sortByRecency(byKey.get(key) ?? []) }
    })
    const orphans: ProjectGroup[] = [...byKey.entries()]
      .filter(([key]) => !claimed.has(key))
      .map(([key, list]) => ({ key, path: list[0].workspace_path, explicit: false, sessions: sortByRecency(list) }))
      .sort((a, b) => parseTime(b.sessions[0].last_message_at) - parseTime(a.sessions[0].last_message_at))
    return [...explicit, ...orphans]
  }, [recentSessions, projects, query])

  const handleNewChat = () => {
    // Dispatch event so ChatPage can reset session state even when already on '/'.
    // The new chat binds to the currently selected project folder.
    window.dispatchEvent(new Event('new-chat'))
    navigate('/')
  }
  const handleAddProject = async () => {
    await pickAndAddProject(selected)
  }
  const handleSessionClick = (sessionId: string) => {
    activateSession(sessionId)
    navigate(`/chat/${sessionId}`)
  }

  const isActiveSession = (sid: string) => location.pathname === `/chat/${sid}`
  const selectedKey = selected ? workspaceKey(selected) : null

  return (
    <div className="w-[232px] flex flex-col h-full shrink-0" role="complementary" aria-label="Chat sessions">
      {/* New Chat + search */}
      <div className="px-2.5 pt-2.5 pb-1.5 shrink-0">
        <button
          onClick={handleNewChat}
          className="btn-primary w-full flex items-center justify-center gap-1.5"
          style={{ height: 30 }}
        >
          <Plus size={13} />
          New Chat
        </button>
        <div
          className="mt-2 flex items-center gap-1.5 h-7 px-2.5 rounded-lg transition-all"
          style={{
            background: 'var(--surface-2)',
            border: '1px solid var(--border-subtle)',
            transitionDuration: '200ms',
            transitionTimingFunction: 'var(--ease)',
          }}
          onFocusCapture={(e) => {
            e.currentTarget.style.borderColor = 'var(--accent-ring)'
            e.currentTarget.style.boxShadow = '0 0 0 3px var(--accent-glow)'
          }}
          onBlurCapture={(e) => {
            e.currentTarget.style.borderColor = 'var(--border-subtle)'
            e.currentTarget.style.boxShadow = 'none'
          }}
        >
          <Search size={12} style={{ color: 'var(--fg-faint)', flexShrink: 0 }} />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search sessions…"
            className="flex-1 min-w-0 bg-transparent outline-none text-[12px]"
            style={{ color: 'var(--fg)' }}
          />
        </div>
      </div>

      {/* Projects */}
      <div className="flex-1 overflow-y-auto px-2.5 pb-2.5">
        <div className="flex items-center justify-between pr-1 pt-2 pb-0.5">
          <span
            className="px-1 text-[11px] font-medium uppercase"
            style={{ color: 'var(--fg-muted)', letterSpacing: '0.06em' }}
          >
            Projects
          </span>
          <button
            onClick={handleAddProject}
            className="p-1 rounded-md transition-colors"
            style={{ color: 'var(--fg-faint)' }}
            onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--surface-3)'; e.currentTarget.style.color = 'var(--fg)' }}
            onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'var(--fg-faint)' }}
            title="Add project folder"
          >
            <FolderPlus size={12} />
          </button>
        </div>

        {loading ? (
          <div className="px-3 py-3 flex justify-center">
            <div className="w-4 h-4 border-2 border-t-transparent rounded-full animate-spin" style={{ borderColor: 'var(--fg-faint)', borderTopColor: 'transparent' }} />
          </div>
        ) : groups.length === 0 ? (
          <button
            onClick={handleAddProject}
            className="w-full flex items-center gap-2 px-3 py-2 rounded-lg transition-colors text-left"
            style={{ color: 'var(--fg-faint)' }}
            onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--surface-3)' }}
            onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}
          >
            <FolderPlus size={13} style={{ flexShrink: 0 }} />
            <span className="text-[12px]">Add a project folder…</span>
          </button>
        ) : (
          <div className="space-y-0.5">
            {groups.map((group) => {
              const isSelected = selectedKey === group.key
              const isCollapsed = !!collapsed[group.key]
              return (
                <div key={group.key}>
                  {/* Project header */}
                  <div
                    className="group/proj flex items-center gap-0.5 px-1 py-1 rounded-lg transition-colors"
                    style={{ background: isSelected ? 'var(--surface-3)' : 'transparent', transitionDuration: '150ms' }}
                  >
                    <button
                      onClick={() => setCollapsed(c => ({ ...c, [group.key]: !c[group.key] }))}
                      className="p-0.5 shrink-0 transition-opacity"
                      style={{ color: 'var(--fg-faint)' }}
                      title={isCollapsed ? 'Expand' : 'Collapse'}
                    >
                      {isCollapsed ? <ChevronRight size={11} /> : <ChevronDown size={11} />}
                    </button>
                    <button
                      onClick={() => selectProject(group.path)}
                      className="flex items-center gap-1.5 flex-1 min-w-0 text-left"
                      title={`${group.path}\nNew chats are created in this folder`}
                    >
                      <FolderOpen size={12} style={{ color: isSelected ? 'var(--accent)' : 'var(--fg-faint)', flexShrink: 0 }} />
                      <span
                        className="text-[12px] font-medium truncate"
                        style={{ color: isSelected ? 'var(--fg)' : 'var(--fg-secondary)', fontFamily: 'var(--font-mono)' }}
                      >
                        {group.path.split(/[\\/]/).filter(Boolean).pop() ?? group.path}
                      </span>
                    </button>
                    {group.explicit ? (
                      <button
                        onClick={() => removeProject(group.path)}
                        className="p-0.5 shrink-0 rounded opacity-0 group-hover/proj:opacity-100 transition-opacity"
                        style={{ color: 'var(--fg-faint)' }}
                        title="Remove from projects (sessions are kept)"
                      >
                        <X size={11} />
                      </button>
                    ) : (
                      <button
                        onClick={() => addProject(group.path)}
                        className="p-0.5 shrink-0 rounded opacity-0 group-hover/proj:opacity-100 transition-opacity"
                        style={{ color: 'var(--fg-faint)' }}
                        title="Add as project"
                      >
                        <Plus size={11} />
                      </button>
                    )}
                  </div>

                  {/* Sessions under this project */}
                  {!isCollapsed && (
                    <div className="ml-3.5 space-y-0.5">
                      {group.sessions.length === 0 ? (
                        <div className="px-3 py-1 text-[10px]" style={{ color: 'var(--fg-faint)' }}>No chats yet</div>
                      ) : group.sessions.map((session) => {
                        const active = isActiveSession(session.session_id)
                        return (
                          <button
                            key={session.session_id}
                            onClick={() => handleSessionClick(session.session_id)}
                            className="relative w-full flex items-center gap-2 px-2.5 py-1.5 rounded-lg transition-all text-left"
                            style={{
                              color: active ? 'var(--fg)' : 'var(--fg-secondary)',
                              background: active ? 'var(--accent-muted)' : 'transparent',
                              boxShadow: active ? 'var(--glow-accent)' : 'none',
                              transitionDuration: '150ms',
                              transitionTimingFunction: 'var(--ease)',
                            }}
                          >
                            {active && (
                              <span
                                className="absolute left-0 top-1/2 -translate-y-1/2 w-[3px] h-3.5 rounded-full"
                                style={{ background: 'var(--accent)' }}
                              />
                            )}
                            <MessageSquare size={12} style={{ color: active ? 'var(--accent)' : 'var(--fg-faint)', flexShrink: 0 }} />
                            <span className="text-[12px] truncate flex-1" title={session.preview || 'New session'}>
                              {session.preview || 'New session'}
                            </span>
                            {isSessionRunning(session.session_id) && (
                              <span
                                className="w-1.5 h-1.5 rounded-full shrink-0 animate-pulse"
                                style={{ background: 'var(--green)' }}
                                title="Running"
                              />
                            )}
                            {/* pending confirm badge — pulsing "!" */}
                            {sessions.get(session.session_id)?.pendingConfirm && (
                              <span
                                className="text-[10px] font-bold px-1.5 py-0.5 rounded-full shrink-0 animate-pulse"
                                style={{ background: 'var(--amber)', color: '#0a0b0d' }}
                                title="Waiting for tool confirmation"
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
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
