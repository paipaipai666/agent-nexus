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

  useEffect(() => {
    api.getConfig().then(setConfig).catch(console.error)
  }, [])

  const handleChange = (key: string, value: string) => {
    setEdited(prev => ({ ...prev, [key]: value }))
    setError(null)
  }

  const handleSave = async (key: string) => {
    setSaving(key)
    setError(null)
    try {
      await api.updateConfig(key, edited[key])
      setConfig(prev => ({ ...prev, [key]: edited[key] }))
      setEdited(prev => { const next = { ...prev }; delete next[key]; return next })
    } catch (e: any) {
      setError(`Failed to save ${key}: ${e.message}`)
    } finally {
      setSaving(null)
    }
  }

  const handleReset = (key: string) => {
    setEdited(prev => { const next = { ...prev }; delete next[key]; return next })
  }

  const displayValue = (key: string) => edited[key] ?? (config[key] ?? '')
  const isEdited = (key: string) => key in edited
  const isSecret = (key: string) => key.includes('key') || key.includes('secret') || key.includes('token')
  const isBoolean = (key: string) => {
    const val = edited[key] ?? config[key]
    return typeof val === 'boolean' || val === 'true' || val === 'false'
  }

  const renderInput = (key: string) => {
    const boolVal = isBoolean(key)
    if (boolVal) {
      const currentVal = (edited[key] ?? String(config[key])) === 'true'
      return (
        <button
          onClick={() => handleChange(key, String(!currentVal))}
          className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
            currentVal ? 'bg-accent-primary' : 'bg-bg-tertiary'
          }`}
        >
          <span
            className={`inline-block h-3.5 w-3.5 rounded-full bg-white transition-transform ${
              currentVal ? 'translate-x-4' : 'translate-x-1'
            }`}
          />
        </button>
      )
    }

    return (
      <input
        type={isSecret(key) ? 'password' : 'text'}
        value={displayValue(key)}
        onChange={e => handleChange(key, e.target.value)}
        className="flex-1 bg-bg-tertiary text-text-primary rounded px-2 py-1 text-sm border border-border-default focus:border-accent-primary focus:outline-none font-mono"
      />
    )
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden p-4 gap-4">
      <h1 className="text-lg font-semibold text-text-primary">Settings</h1>

      {error && (
        <div className="bg-status-error/10 border border-status-error/30 text-status-error text-sm rounded-md px-3 py-2">
          {error}
        </div>
      )}

      <div className="flex-1 overflow-y-auto space-y-6">
        {Object.entries(GROUPS).map(([group, keys]) => {
          const visibleKeys = keys.filter(k => k in config)
          if (visibleKeys.length === 0) return null
          return (
            <div key={group} className="bg-bg-secondary rounded-lg border border-border-default p-4">
              <h2 className="text-sm font-medium text-accent-primary mb-3">{group}</h2>
              <div className="space-y-3">
                {visibleKeys.map(key => (
                  <div key={key} className="flex items-center gap-2">
                    <label className="text-xs text-text-secondary w-48 shrink-0 font-mono" title={key}>
                      {key}
                    </label>
                    {renderInput(key)}
                    {isEdited(key) && (
                      <div className="flex items-center gap-1 shrink-0">
                        <button
                          onClick={() => handleSave(key)}
                          disabled={saving === key}
                          className="px-2 py-1 bg-accent-primary text-bg-primary rounded text-xs flex items-center gap-1"
                        >
                          {saving === key ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />}
                          Save
                        </button>
                        <button
                          onClick={() => handleReset(key)}
                          className="p-1 text-text-muted hover:text-text-primary rounded"
                          title="Revert"
                        >
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
