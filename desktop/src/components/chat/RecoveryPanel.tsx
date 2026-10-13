import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useSession } from '../session/SessionManager'
import { getChatDraft, saveChatDraft } from '../../services/chatDrafts'

const reasons: Record<string, string> = {
  client_disconnected: '连接断开，任务已中断', server_shutdown: '后端关闭，任务已中断',
  server_restarted: '后端已重启，请核对任务完成情况', cancelled: '任务已停止', user_cancelled: '任务已停止',
}

export default function RecoveryPanel() {
  const { sessionId, pendingSubmission, recovery, recoveryNotice, stopState, cancelRun, retrySubmission, refreshRecovery, retrySave } = useSession()
  const navigate = useNavigate()
  const [copyNotice, setCopyNotice] = useState<string | null>(null)
  const needsRecovery = recovery && (recovery.status !== 'finished' || !recovery.persisted)
  if (!pendingSubmission && !needsRecovery && !recoveryNotice) return null

  const copy = async (text: string) => {
    try { await navigator.clipboard.writeText(text); setCopyNotice('已复制') }
    catch { setCopyNotice('复制失败，请从下方原文手动复制') }
  }
  const prepareDraft = (text: string) => {
    if (!sessionId) return
    const key = `session:${sessionId}`
    const current = getChatDraft(key)
    saveChatDraft(key, { ...current, text: [current.text, text].filter(Boolean).join('\n\n') })
    setCopyNotice('请求已放入输入框，请检查后发送')
  }
  const exportResult = () => {
    if (!recovery) return
    const url = URL.createObjectURL(new Blob([JSON.stringify(recovery, null, 2)], { type: 'application/json' }))
    const link = document.createElement('a')
    link.href = url
    link.download = `${recovery.run_id}.json`
    link.click()
    URL.revokeObjectURL(url)
  }

  return <div className="space-y-2 rounded-lg p-3 text-sm" style={{ background: 'var(--surface-2)', color: 'var(--fg-secondary)' }}>
    {pendingSubmission && <>
      <p role="status">{pendingSubmission.phase === 'waiting_connection' ? '正在等待连接…' : pendingSubmission.phase === 'waiting_ack' ? '正在等待发送回执…' : pendingSubmission.message}</p>
      <div className="flex flex-wrap gap-2">
        <button className="btn-ghost" onClick={refreshRecovery}>查询发送状态</button>
        {(pendingSubmission.phase === 'unknown' || pendingSubmission.phase === 'rejected') && <button className="btn-ghost" onClick={retrySubmission}>重试原提交</button>}
        <button className="btn-ghost" onClick={() => void copy([pendingSubmission.text, ...pendingSubmission.attachments.map(file => file.path)].join('\n'))}>复制原请求</button>
        <button className="btn-ghost" disabled={stopState === 'requesting'} onClick={cancelRun}>取消待确认请求</button>
      </div>
      <details><summary>查看待确认的原请求</summary><pre className="whitespace-pre-wrap break-words mt-2 max-h-40 overflow-auto">{pendingSubmission.text}</pre>
        {pendingSubmission.attachments.map(file => <p key={file.path}>{file.name} · {file.path}</p>)}</details>
    </>}
    {needsRecovery && <>
      <p role="status">{recovery.status === 'finished' ? '任务已完成，结果尚未保存' : reasons[recovery.reason] || recovery.reason || (recovery.status === 'empty_answer' ? '任务没有产生最终答案' : '任务执行失败')}</p>
      <p>{recovery.persisted ? '会话结果已保存。' : '结果尚未保存，请保留副本。'}{recovery.journal.length ? ` 本轮记录了 ${recovery.journal.length} 项活动。` : ''}</p>
      {recovery.next_action && <p>{recovery.next_action}</p>}
      {(recovery.status === 'interrupted' || recovery.status === 'failed') && <p>文件修改或外部操作可能已完成。继续前请核对时间线；会话撤销只恢复对话上下文。</p>}
      <div className="flex flex-wrap gap-2">
        {!recovery.persisted && recovery.started && <button className="btn-ghost" onClick={retrySave}>重试保存结果</button>}
        <button className="btn-ghost" onClick={refreshRecovery}>查询任务状态</button>
        {(recovery.failure_code === 'provider_auth' || recovery.failure_code === 'provider_rate_limit') && <button className="btn-ghost" onClick={() => navigate('/settings')}>打开提供商设置</button>}
        <button className="btn-ghost" onClick={() => void copy(recovery.answer)}>复制结果</button>
        <button className="btn-ghost" onClick={exportResult}>导出恢复记录</button>
        <button className="btn-ghost" onClick={() => navigate(`/chat/${sessionId}/timeline`)}>核对已执行步骤</button>
        {!recovery.execution_active && <button className="btn-ghost" onClick={() => prepareDraft(`请先核对上一轮已完成的操作和文件修改，再从未完成的步骤继续，避免重复执行。原请求：${recovery.question}`)}>准备继续请求</button>}
      </div>
      <details><summary>查看恢复记录</summary><pre className="whitespace-pre-wrap break-words mt-2 max-h-48 overflow-auto">{[recovery.answer, recovery.detail, recovery.save_error, ...recovery.journal].filter(Boolean).join('\n')}</pre></details>
    </>}
    {recoveryNotice && <p role="alert">{recoveryNotice}</p>}
    {recoveryNotice && !pendingSubmission && !needsRecovery && <button className="btn-ghost" onClick={refreshRecovery}>查询任务状态</button>}
    {copyNotice && <p role="status">{copyNotice}</p>}
  </div>
}
