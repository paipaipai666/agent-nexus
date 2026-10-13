import { useEffect, useRef, useState } from 'react'
import { checkBackendHealth } from '../services/backend'

export default function BackendRecoveryControls({ port, onRecovered }: { port: number; onRecovered: () => void }) {
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const generation = useRef(0)
  useEffect(() => () => { generation.current++ }, [])
  const recover = async (restart: boolean) => {
    if (busy) return
    const operation = ++generation.current
    setBusy(true)
    setNotice(null)
    try {
      const status = restart ? await window.electronAPI?.restartBackend?.() : null
      const ready = status?.ready ?? await checkBackendHealth(port)
      if (operation !== generation.current) return
      if (ready) onRecovered()
      else setNotice(status?.error || '仍无法连接后端，请检查诊断信息后重试。')
    } catch (error) {
      if (operation === generation.current) setNotice(error instanceof Error ? error.message : '恢复后端失败，请重试。')
    } finally { if (operation === generation.current) setBusy(false) }
  }
  const copyDiagnostics = async () => {
    const operation = generation.current
    try {
      const status = await window.electronAPI?.getBackendStatus()
      await navigator.clipboard.writeText(JSON.stringify({ port, ...status, message: notice }, null, 2))
      if (operation === generation.current) setNotice('诊断信息已复制')
    } catch { if (operation === generation.current) setNotice(`复制失败。后端端口：${port}，请检查后端是否运行。`) }
  }
  return <div className="space-y-2 text-sm">
    <div className="flex flex-wrap justify-center gap-2">
      <button className="btn-ghost" disabled={busy} onClick={() => void recover(false)}>重试连接</button>
      {window.electronAPI?.restartBackend && <button className="btn-ghost" disabled={busy} onClick={() => void recover(true)}>重启后端</button>}
      <button className="btn-ghost" onClick={() => void copyDiagnostics()}>复制诊断信息</button>
    </div>
    {busy && <p role="status">正在恢复后端连接…</p>}
    {notice && <p role="status">{notice}</p>}
  </div>
}
