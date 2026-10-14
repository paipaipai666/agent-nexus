import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft, Bot, GitBranch, MessageSquare, Send, Square } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api } from '../services/api'
import { useSubagentWorkspace } from '../services/useSubagentWorkspace'
import { useCollaborationStore } from '../services/collaborationStore'
import { isActive, statusLabels, type Communication, type SubagentEvent } from '../services/subagentWorkspace'
import { useSession } from '../components/session/SessionManager'
import ToolCard from '../components/chat/ToolCard'

const deliveryLabels = { queued: '已排队', applied: '已进入上下文', rejected: '投递失败' }
const kindLabels = { question: '问题', reply: '答复', instruction: '追加指令', progress: '进展', result: '结果' }
const errorLabels: Record<string, string> = { metadata: '子任务信息', trajectory: '子任务过程', communication: '通信记录', live: '实时事件' }
type Tab = 'communication' | 'execution' | 'results'

export default function SubagentPage() {
  const { sessionId = '', subagentId = '' } = useParams()
  return <Workspace key={`${sessionId}:${subagentId}`} sessionId={sessionId} subagentId={subagentId} />
}

function Workspace({ sessionId, subagentId }: { sessionId: string; subagentId: string }) {
  const navigate = useNavigate()
  const { setSessionId } = useSession()
  const { workspace, errors, loaded, enabled, reload } = useSubagentWorkspace(sessionId, subagentId)
  const [tab, setTab] = useState<Tab>('communication')
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState('all')
  const [showList, setShowList] = useState(false)
  const [draft, setDraft] = useState('')
  const [replyTo, setReplyTo] = useState<number | null>(null)
  const [operation, setOperation] = useState<'idle' | 'sending' | 'cancelling'>('idle')
  const [operationError, setOperationError] = useState('')
  const request = useRef<{ id: string; content: string; question: number | null } | null>(null)
  const viewport = useRef<HTMLDivElement>(null)
  const follow = useRef(true)
  const [unread, setUnread] = useState(false)
  const composer = useRef<HTMLTextAreaElement>(null)
  const agent = workspace.agents.get(subagentId)
  const events = workspace.events.get(subagentId) ?? []
  const messages = workspace.messages.get(subagentId) ?? []
  const questions = messages.filter(m => m.kind === 'question' && m.question_state === 'open')
  const active = agent ? isActive(agent.status) : false
  const canSend = enabled && agent?.collaborative === true && active && agent.status !== 'cancelling' && Boolean(agent.child_run_id)
  const [now, setNow] = useState(Date.now())

  useEffect(() => {
    void api.restoreSession(sessionId).catch(() => {})
    setSessionId(sessionId)
  }, [sessionId, setSessionId])

  useEffect(() => {
    if (!active) return
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [active])

  useEffect(() => {
    if (follow.current && viewport.current) viewport.current.scrollTop = viewport.current.scrollHeight
    else setUnread(true)
  }, [events.length, messages.length, tab])

  const send = async () => {
    if (!canSend || !agent?.child_run_id || !draft.trim() || operation !== 'idle') return
    const question = questions.some(m => m.id === replyTo) ? replyTo : null
    if (!request.current || request.current.content !== draft || request.current.question !== question) request.current = { id: crypto.randomUUID(), content: draft, question }
    setOperation('sending')
    setOperationError('')
    try {
      const receipt = await api.sendSubagentMessage(sessionId, subagentId, { content: draft, request_id: request.current.id, child_run_id: agent.child_run_id, ...(question ? { question_id: question } : {}) })
      useCollaborationStore.getState().receive(sessionId, subagentId, [receipt])
      if (receipt.delivery === 'rejected') throw new Error(receipt.reason || '接收者已结束，消息无法投递')
      setDraft('')
      setReplyTo(null)
      request.current = null
    } catch (error) { setOperationError(error instanceof Error ? error.message : '消息发送失败，请重试') }
    finally { setOperation('idle') }
  }

  const cancel = async () => {
    setOperation('cancelling')
    setOperationError('')
    try {
      const receipt = await api.cancelSubagent(sessionId, subagentId)
      if (!receipt.accepted) throw new Error('子任务已经结束或无法中断，请刷新状态')
      if (receipt.subagent) useCollaborationStore.getState().reconcile(sessionId, [receipt.subagent])
      else reload()
    } catch (error) { setOperationError(error instanceof Error ? error.message : '中断请求失败，请重试') }
    finally { setOperation('idle') }
  }

  const agents = [...workspace.agents.values()].filter(a => `${a.name} ${a.task}`.toLowerCase().includes(query.toLowerCase()) && (filter === 'all' || (filter === 'active' ? isActive(a.status) : !isActive(a.status))))
  const elapsed = agent?.started_at ? Math.max(0, Math.floor(((agent.finished_at ? agent.finished_at * 1000 : now) - agent.started_at * 1000) / 1000)) : 0
  const result = messages.filter(m => m.kind === 'result').at(-1)?.content ?? events.filter(e => e.kind === 'finished').map(e => e.kind === 'finished' ? e.summary : '').at(-1) ?? ''

  return <div className="collaboration-workspace flex-1 flex flex-col min-w-0 overflow-hidden" style={{ background: 'var(--surface-0)', color: 'var(--fg)' }}>
    <header className="flex items-center gap-3 px-5 py-3 border-b" style={{ borderColor: 'var(--border)' }}>
      <Link to={`/chat/${sessionId}`} aria-label="返回主对话" className="btn-ghost p-2"><ArrowLeft size={16} /></Link>
      <Bot size={18} style={{ color: 'var(--accent)' }} />
      <h1 className="text-base font-semibold">协作工作区</h1>
      <span className="text-xs" style={{ color: 'var(--fg-secondary)' }}>{workspace.agents.size} 个子任务</span>
      <button className="btn-ghost ml-auto md:hidden text-xs" onClick={() => setShowList(value => !value)}>{showList ? '查看详情' : '代理列表'}</button>
    </header>
    <div className="flex flex-1 min-h-0">
      <aside aria-label="代理列表" className={`${showList ? 'flex' : 'hidden'} md:flex flex-col w-full md:w-[232px] shrink-0 border-r overflow-y-auto p-3 gap-3`} style={{ borderColor: 'var(--border)', background: 'var(--surface-1)' }}>
        <input aria-label="搜索子任务" value={query} onChange={e => setQuery(e.target.value)} placeholder="搜索子任务" className="w-full rounded-lg px-3 py-2 text-xs border" style={{ background: 'var(--surface-0)', borderColor: 'var(--border)' }} />
        <select aria-label="筛选子任务" value={filter} onChange={e => setFilter(e.target.value)} className="rounded-lg px-2 py-1.5 text-xs" style={{ background: 'var(--surface-2)' }}>
          <option value="all">全部任务</option><option value="active">正在执行</option><option value="finished">已结束</option>
        </select>
        <nav className="space-y-1.5">{agents.map(item => {
          const pending = workspace.messages.get(item.subagent_id)?.filter(m => m.kind === 'question' && m.question_state === 'open').length ?? item.waiting_questions ?? 0
          return <Link key={item.subagent_id} to={`/chat/${sessionId}/subagent/${item.subagent_id}`} aria-current={item.subagent_id === subagentId ? 'page' : undefined} className="block rounded-lg p-3 border" style={{ background: item.subagent_id === subagentId ? 'var(--accent-muted)' : 'transparent', borderColor: item.subagent_id === subagentId ? 'var(--accent)' : 'transparent' }}>
            <div className="flex justify-between gap-2 text-sm font-medium"><span className="truncate">{item.name}</span><span className="text-[11px] shrink-0 font-normal">{statusLabels[item.status]}</span></div>
            <p className="text-xs mt-1.5 line-clamp-2" style={{ color: 'var(--fg-secondary)' }}>{item.latest_progress || item.task}</p>
            <div className="text-[11px] mt-2 flex justify-between"><span>{item.role}</span>{pending > 0 && <span style={{ color: 'var(--accent)' }}>{pending} 个待答问题</span>}</div>
          </Link>
        })}</nav>
        {agents.length === 0 && <p className="text-xs py-4">{query ? '没有匹配的子任务' : '暂无子任务'}</p>}
      </aside>
      <section className={`${showList ? 'hidden' : 'flex'} md:flex flex-1 flex-col min-w-0 overflow-hidden`} aria-label="子任务详情">
        {agent && <div className="px-5 pt-5 pb-4 border-b" style={{ borderColor: 'var(--border)' }}>
          <div className="flex flex-wrap items-center gap-2"><h2 className="text-lg font-semibold">{agent.name}</h2><span className="text-xs px-2 py-1 rounded-full" style={{ background: 'var(--surface-2)' }}>{statusLabels[agent.status]}</span>
            <button className="btn-ghost ml-auto text-xs flex items-center gap-1" onClick={() => navigate(`/chat/${sessionId}/subagent/${subagentId}/timeline`)}><GitBranch size={13} />Timeline</button>
            {active && <button onClick={() => { void cancel() }} disabled={operation !== 'idle' || agent.status === 'cancelling'} className="btn-ghost text-xs flex items-center gap-1 disabled:opacity-50" style={{ color: 'var(--red)' }}><Square size={12} />{agent.status === 'cancelling' ? '取消中' : operation === 'cancelling' ? '正在请求' : '中断'}</button>}
          </div>
          <p className="mt-2 text-sm leading-relaxed max-w-[70ch] whitespace-pre-wrap">{agent.task}</p>
          <p className="mt-3 text-xs flex flex-wrap gap-x-4 gap-y-1" style={{ color: 'var(--fg-secondary)' }}><span>角色 {agent.role}</span><span>{agent.steps_used} 步</span><span>{Math.floor(elapsed / 60)} 分 {elapsed % 60} 秒</span>{agent.current_tool && <span>当前工具 {agent.current_tool}</span>}</p>
        </div>}
        {questions.length > 0 && <div className="px-5 py-3 border-b" style={{ borderColor: 'var(--border)', background: 'var(--accent-muted)' }}>
          <p className="text-xs font-semibold mb-2 flex items-center gap-2"><MessageSquare size={13} />待答问题 · {questions.length}</p>
          {questions.map(question => <div key={question.id} className="flex items-start gap-3 py-1 text-sm"><p className="flex-1 whitespace-pre-wrap">{question.content}</p>{canSend && <button className="btn-ghost text-xs shrink-0" onClick={() => { setReplyTo(question.id); setTab('communication'); composer.current?.focus() }}>补充答复</button>}</div>)}
        </div>}
        <div role="group" aria-label="详情视图" className="flex px-5 gap-5 border-b" style={{ borderColor: 'var(--border)' }}>
          {([{ id: 'communication', label: '通信记录' }, { id: 'execution', label: '执行过程' }, { id: 'results', label: '结果' }] satisfies { id: Tab; label: string }[]).map(item => <button key={item.id} aria-pressed={tab === item.id} onClick={() => { setTab(item.id); follow.current = true; setUnread(false) }} className="py-3 text-sm border-b-2" style={{ borderColor: tab === item.id ? 'var(--accent)' : 'transparent', color: tab === item.id ? 'var(--fg)' : 'var(--fg-secondary)' }}>{item.label}</button>)}
        </div>
        <div ref={viewport} className="flex-1 overflow-y-auto p-5" onScroll={event => { const el = event.currentTarget; follow.current = el.scrollHeight - el.scrollTop - el.clientHeight < 64; if (follow.current) setUnread(false) }}>
          <div className="max-w-[760px] mx-auto space-y-4">
            {Object.entries(errors).map(([scope, error]) => <div key={scope} role="alert" className="text-xs space-y-2 p-3 rounded-lg border" style={{ borderColor: 'var(--border)' }}><p>{errorLabels[scope]}加载失败：{error}。已读取的内容保留。</p><button className="btn-ghost" onClick={reload}>重试{errorLabels[scope]}</button></div>)}
            {!loaded && <p role="status" className="text-sm">正在读取协作记录…</p>}
            {!agent && loaded && !errors.metadata && !errors.trajectory && <p className="text-sm py-8 text-center">未找到该子代理</p>}
            {tab === 'communication' && messages.map(message => <CommunicationCard key={message.id} message={message} childName={agent?.name || subagentId} />)}
            {tab === 'communication' && agent && messages.length === 0 && !errors.communication && <div className="py-8 text-center"><MessageSquare size={24} className="mx-auto mb-3" style={{ color: 'var(--accent)' }} /><p className="text-sm">暂无协作消息</p><p className="text-xs mt-2" style={{ color: 'var(--fg-secondary)' }}>{active ? '子代理的进展、问题和主代理的答复会显示在这里。' : '可切换到执行过程查看历史轨迹。'}</p></div>}
            {tab === 'execution' && <Execution events={events} />}
            {tab === 'results' && <><Markdown text={result || (active ? '子任务仍在执行，结果会在结束后显示。' : '没有可用的结果。')} />{agent?.error && <p role="alert" style={{ color: 'var(--red)' }}>{agent.error}</p>}</>}
          </div>
        </div>
        {unread && <button className="btn-ghost mx-auto my-2 text-xs" onClick={() => { follow.current = true; if (viewport.current) viewport.current.scrollTop = viewport.current.scrollHeight; setUnread(false) }}>查看新内容 ↓</button>}
        {operationError && <p role="alert" className="px-5 py-2 text-xs" style={{ color: 'var(--red)' }}>{operationError}</p>}
        {tab === 'communication' && <form className="border-t p-4" style={{ borderColor: 'var(--border)', background: 'var(--surface-1)' }} onSubmit={event => { event.preventDefault(); void send() }}>
          {replyTo && questions.some(q => q.id === replyTo) && <div className="text-xs mb-2 flex justify-between"><span>答复问题 #{replyTo}</span><button type="button" onClick={() => setReplyTo(null)}>改为追加指令</button></div>}
          <div className="flex items-end gap-3"><textarea ref={composer} aria-label="补充要求或答复" value={draft} onChange={event => setDraft(event.target.value)} maxLength={16000} rows={2} disabled={!canSend || operation === 'sending'} placeholder={canSend ? '补充要求，或选择待答问题后回复…' : '当前子任务无法接收新消息'} className="flex-1 min-w-0 resize-none rounded-lg border px-3 py-2 text-sm disabled:opacity-70" style={{ background: 'var(--surface-0)', borderColor: 'var(--border)' }} /><button type="submit" disabled={!canSend || !draft.trim() || operation !== 'idle'} className="px-3 py-2.5 rounded-lg text-xs flex items-center gap-2 disabled:opacity-40" style={{ background: 'var(--fg)', color: 'var(--surface-0)' }}><Send size={13} />{operation === 'sending' ? '发送中' : '发送'}</button></div>
          <p className="mt-2 text-[11px]" style={{ color: 'var(--fg-secondary)' }}>{canSend ? '消息保存后排队，代理在当前操作结束后读取。' : agent?.status === 'cancelling' ? '正在等待子任务退出，无法追加消息。' : active ? '此子任务未启用双向协作。' : '执行已结束，通信记录保留。'}</p>
        </form>}
      </section>
    </div>
  </div>
}

function Markdown({ text }: { text: string }) {
  return <div className="markdown-body text-sm max-w-[70ch] break-words" style={{ color: 'var(--fg)' }}><ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown></div>
}

function CommunicationCard({ message, childName }: { message: Communication; childName: string }) {
  const sender = message.sender === 'parent' ? '主代理' : message.sender === 'user' ? '你' : childName
  return <article className="py-3 border-b" style={{ borderColor: 'var(--border)' }}>
    <div className="flex flex-wrap items-center gap-2 text-xs mb-2"><span className="font-medium">{sender} → {message.recipient === 'parent' ? '主代理' : childName}</span><span style={{ color: 'var(--accent)' }}>{kindLabels[message.kind]}</span><time className="ml-auto" style={{ color: 'var(--fg-secondary)' }}>{new Date(message.ts * 1000).toLocaleTimeString('zh-CN')}</time></div>
    <Markdown text={message.content} />
    <p className="text-[11px] mt-2" style={{ color: message.delivery === 'rejected' ? 'var(--red)' : 'var(--fg-secondary)' }}>{deliveryLabels[message.delivery]}{message.kind === 'question' ? ` · 问题 #${message.id} · ${{ open: '待答', answered: '已答复', expired: '已超时', closed: '已关闭' }[message.question_state]}` : message.kind === 'reply' ? ` · 答复 #${message.question_id}` : ''}{message.reason && ` · ${message.reason}`}</p>
  </article>
}

function Execution({ events }: { events: SubagentEvent[] }) {
  return <div className="space-y-2">{events.map((event, index) => {
    if (event.kind === 'tool_call') {
      const result = events.slice(index + 1).find(e => e.kind === 'tool_result' && e.toolCallId === event.toolCallId && e.childRunId === event.childRunId)
      return <div key={event.key} className="rounded-lg border" style={{ borderColor: 'var(--border)' }}><ToolCard msg={{ id: event.key, role: 'tool', content: event.args, toolName: event.toolName, toolCallId: event.toolCallId, toolStatus: result ? 'done' : 'running', toolResult: result?.kind === 'tool_result' ? result.result : '', toolDurationMs: result?.kind === 'tool_result' ? result.duration : 0, timestamp: new Date(event.ts * 1000) }} /></div>
    }
    if (event.kind === 'thinking' || event.kind === 'retry') return event.content ? <div key={event.key} className="text-sm rounded-lg p-3" style={{ background: 'var(--surface-2)' }}>{event.kind === 'retry' ? '重试：' : ''}{event.content}</div> : null
    if (event.kind === 'reasoning') return <details key={event.key} className="text-sm rounded-lg p-3" style={{ background: 'var(--surface-2)' }}><summary className="cursor-pointer">推理片段 · 第 {event.step} 步</summary><div className="mt-3 whitespace-pre-wrap">{event.content}</div></details>
    if (event.kind === 'token') return <div key={event.key} className="py-2"><Markdown text={event.content} /></div>
    if (event.kind === 'finished') return <div key={event.key} className="py-3"><Markdown text={event.summary || event.error} /></div>
    return null
  })}</div>
}
