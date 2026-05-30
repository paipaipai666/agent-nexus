import { useState, useEffect } from 'react'
import { Save, Loader2, RotateCcw } from 'lucide-react'
import { api } from '../services/api'

const GROUPS: Record<string, string[]> = {
  'LLM': ['llm_model_id', 'llm_base_url', 'llm_api_key', 'llm_timeout'],
  'Judge LLM': ['judge_api_key', 'judge_model_id'],
  'Agent': ['max_agent_steps'],
  'Code Execution': ['code_execution_backend', 'code_execution_timeout', 'code_execution_memory_mb', 'code_execution_docker_image', 'code_execution_allow_unsafe_local'],
  'Shell Execution': ['shell_execution_backend', 'shell_execution_memory_mb', 'shell_execution_docker_image'],
  'Skills': ['default_skill', 'skill_auto_route', 'skill_auto_route_llm_fallback', 'skill_auto_route_min_score', 'skill_auto_route_margin'],
  'RAG': ['enable_contextual_retrieval'],
  'MCP': ['mcp_enabled'],
  'External Services': ['tavily_api_key', 'e2b_api_key'],
}

export default function SettingsPage() {
  const [config, setConfig] = useState<Record<string, any>>({})
  const [edited, setEdited] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => { api.getConfig().then(setConfig).catch(console.error) }, [])

  const handleChange = (key: string, value: string) => { setEdited(prev => ({ ...prev, [key]: value })); setError(null) }
  const handleSave = async (key: string) => {
    setSaving(key); setError(null)
    try { await api.updateConfig(key, edited[key]); setConfig(prev => ({ ...prev, [key]: edited[key] })); setEdited(prev => { const next = { ...prev }; delete next[key]; return next }) }
    catch (e: any) { setError(`Failed to save ${key}: ${e.message}`) }
    finally { setSaving(null) }
  }
  const handleReset = (key: string) => { setEdited(prev => { const next = { ...prev }; delete next[key]; return next }) }

  const displayValue = (key: string) => edited[key] ?? (config[key] ?? '')
  const isEdited = (key: string) => key in edited
  const isSecret = (key: string) => key.includes('key') || key.includes('secret') || key.includes('token')
  const isBoolean = (key: string) => { const val = edited[key] ?? config[key]; return typeof val === 'boolean' || val === 'true' || val === 'false' }

  const renderInput = (key: string) => {
    if (isBoolean(key)) {
      const currentVal = (edited[key] ?? String(config[key])) === 'true'
      return (
        <button onClick={() => handleChange(key, String(!currentVal))} className="relative w-9 h-5 rounded-full transition-colors duration-200" style={{ background: currentVal ? 'var(--accent)' : 'var(--surface-4)' }}>
          <span className="absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform duration-200" style={{ left: currentVal ? '18px' : '2px' }} />
        </button>
      )
    }
    return <input type={isSecret(key) ? 'password' : 'text'} value={displayValue(key)} onChange={e => handleChange(key, e.target.value)} className="input-field flex-1 font-mono text-xs" />
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden p-5 gap-4">
      <h1 className="text-lg font-semibold" style={{ color: 'var(--fg)' }}>Settings</h1>

      {error && <div className="rounded-lg px-3 py-2 text-sm" style={{ background: 'var(--red-muted)', color: 'var(--red)' }}>{error}</div>}

      <div className="flex-1 overflow-y-auto space-y-4">
        {Object.entries(GROUPS).map(([group, keys]) => {
          const visibleKeys = keys.filter(k => k in config)
          if (visibleKeys.length === 0) return null
          return (
            <div key={group} className="surface-card p-4">
              <h2 className="text-xs font-semibold uppercase tracking-wider mb-3" style={{ color: 'var(--accent)' }}>{group}</h2>
              <div className="space-y-2.5">
                {visibleKeys.map(key => (
                  <div key={key} className="flex items-center gap-3">
                    <label className="text-xs w-48 shrink-0 font-mono truncate" style={{ color: 'var(--fg-muted)' }} title={key}>{key}</label>
                    {renderInput(key)}
                    {isEdited(key) && (
                      <div className="flex items-center gap-1 shrink-0">
                        <button onClick={() => handleSave(key)} disabled={saving === key} className="p-1.5 rounded-md transition-colors" style={{ background: 'var(--accent)', color: 'white' }}>
                          {saving === key ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />}
                        </button>
                        <button onClick={() => handleReset(key)} className="p-1.5 rounded-md transition-colors" style={{ color: 'var(--fg-faint)' }} onMouseEnter={e => e.currentTarget.style.background = 'var(--surface-3)'} onMouseLeave={e => e.currentTarget.style.background = 'transparent'}>
                          <RotateCcw size={12} />
                        </button>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
