import { useState, useEffect } from 'react'
import { Save } from 'lucide-react'
import { api } from '../services/api'

const GROUPS: Record<string, string[]> = {
  'LLM': ['llm_model_id', 'llm_base_url', 'llm_api_key', 'llm_timeout'],
  'Code Execution': ['code_execution_backend', 'code_execution_timeout', 'code_execution_memory_mb'],
  'Skills': ['default_skill', 'skill_auto_route', 'skill_auto_route_llm_fallback'],
  'MCP': ['mcp_enabled'],
  'External': ['tavily_api_key', 'e2b_api_key'],
}

export default function SettingsPage() {
  const [config, setConfig] = useState<Record<string, any>>({})
  const [edited, setEdited] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    api.getConfig().then(setConfig).catch(console.error)
  }, [])

  const handleChange = (key: string, value: string) => {
    setEdited(prev => ({ ...prev, [key]: value }))
  }

  const handleSave = async (key: string) => {
    setSaving(true)
    try {
      await api.updateConfig(key, edited[key])
      setConfig(prev => ({ ...prev, [key]: edited[key] }))
      setEdited(prev => { const next = { ...prev }; delete next[key]; return next })
    } catch (e) {
      console.error(e)
    } finally {
      setSaving(false)
    }
  }

  const displayValue = (key: string) => edited[key] ?? (config[key] ?? '')
  const isEdited = (key: string) => key in edited
  const isSecret = (key: string) => key.includes('key') || key.includes('secret') || key.includes('token')

  return (
    <div className="flex-1 flex flex-col overflow-hidden p-4 gap-4">
      <h1 className="text-lg font-semibold text-text-primary">Settings</h1>

      <div className="flex-1 overflow-y-auto space-y-6">
        {Object.entries(GROUPS).map(([group, keys]) => (
          <div key={group} className="bg-bg-secondary rounded-lg border border-border-default p-4">
            <h2 className="text-sm font-medium text-accent-primary mb-3">{group}</h2>
            <div className="space-y-3">
              {keys.filter(k => k in config).map(key => (
                <div key={key} className="flex items-center gap-2">
                  <label className="text-xs text-text-secondary w-40 shrink-0 font-mono">{key}</label>
                  <input
                    type={isSecret(key) ? 'password' : 'text'}
                    value={displayValue(key)}
                    onChange={e => handleChange(key, e.target.value)}
                    className="flex-1 bg-bg-tertiary text-text-primary rounded px-2 py-1 text-sm border border-border-default focus:border-accent-primary focus:outline-none"
                  />
                  {isEdited(key) && (
                    <button
                      onClick={() => handleSave(key)}
                      disabled={saving}
                      className="px-2 py-1 bg-accent-primary text-bg-primary rounded text-xs flex items-center gap-1"
                    >
                      <Save size={12} />
                      Save
                    </button>
                  )}
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
