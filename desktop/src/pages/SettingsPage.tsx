import { useState, useEffect } from 'react'
import { Save, Loader2, RotateCcw } from 'lucide-react'
import { api } from '../services/api'

const GROUPS: Record<string, string[]> = {
  'LLM': [
    'llm_model_id', 'llm_base_url', 'llm_api_key', 'llm_timeout',
    'model_tool_calling', 'model_json_mode', 'model_thinking', 'model_thinking_budget',
  ],
  'Judge LLM': ['judge_api_key', 'judge_model_id', 'judge_base_url'],
  'Agent': ['max_agent_steps', 'runtime_profile', 'trace_retention_days'],
  'Budget': ['budget_simple_max_tokens', 'budget_complex_max_tokens', 'budget_high_value_max_tokens', 'budget_exceed_strategy'],
  'RAG': [
    'enable_contextual_retrieval', 'enable_query_rewrite', 'enable_multi_query',
    'enable_hyde', 'hyde_question_only', 'enable_context_expansion',
    'rag_multi_query_count', 'rag_context_window', 'rag_context_max_chunks',
    'embedding_model', 'reranker_model', 'rag_default_namespace', 'rag_collection_prefix',
  ],
  'Memory': [
    'max_memories', 'memory_ttl_days',
    'autocompact_buffer_tokens', 'large_result_threshold',
    'offload_enabled', 'snip_enabled', 'time_microcompact_interval',
    'post_compact_max_files', 'post_compact_token_per_file', 'post_compact_token_budget',
    'transcript_enabled',
  ],
  'Code Execution': [
    'code_execution_backend', 'code_execution_timeout', 'code_execution_memory_mb',
    'code_execution_docker_image', 'code_execution_allow_unsafe_local',
  ],
  'Shell Execution': [
    'shell_enabled', 'shell_confirm', 'shell_timeout',
    'shell_execution_backend', 'shell_execution_memory_mb', 'shell_execution_docker_image',
    'shell_blacklist',
  ],
  'File Operations': ['file_read_max_mb'],
  'Skills': [
    'skills_default_namespace', 'default_skill',
    'skill_auto_route', 'skill_auto_route_llm_fallback',
    'skill_auto_route_min_score', 'skill_auto_route_margin',
  ],
  'Extensions & Plugins': ['extensions_enabled', 'extensions_dirs', 'plugins_auto_discover'],
  'MCP': ['mcp_enabled', 'mcp_startup_timeout'],
  'Browser Automation': [
    'browser_mode', 'browser_cdp_endpoint', 'browser_headless',
    'browser_viewport_width', 'browser_viewport_height',
    'browser_default_timeout', 'browser_networkidle_timeout',
    'browser_screenshot_dir', 'browser_context_ttl',
    'browser_allow_js_execution', 'browser_snapshot_max_nodes',
  ],
  'Desktop Automation': [
    'computer_use_enabled', 'computer_use_backend', 'computer_use_snapshot_max_nodes',
    'computer_use_allowed_apps', 'computer_use_blocked_apps',
  ],
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

  const displayValue = (key: string) => edited[key] ?? String(config[key] ?? '')
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
    return (
      <input
        type={isSecret(key) ? 'password' : 'text'}
        value={displayValue(key)}
        onChange={e => handleChange(key, e.target.value)}
        className="input-field flex-1 font-mono text-xs"
      />
    )
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="px-6 py-4" style={{ borderBottom: '1px solid var(--border)' }}>
        <h1 className="text-lg font-semibold" style={{ color: 'var(--fg)' }}>Settings</h1>
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
        {error && <div className="rounded-lg px-3 py-2 text-sm" style={{ background: 'var(--red-muted)', color: 'var(--red)' }}>{error}</div>}

        {Object.entries(GROUPS).map(([group, keys]) => {
          const visibleKeys = keys.filter(k => k in config)
          if (visibleKeys.length === 0) return null
          return (
            <div key={group} className="p-4 rounded-lg" style={{ background: 'var(--surface-1)', border: '1px solid var(--border)' }}>
              <h2 className="text-xs font-semibold uppercase tracking-wider mb-3" style={{ color: 'var(--accent)' }}>{group}</h2>
              <div className="space-y-2.5">
                {visibleKeys.map(key => (
                  <div key={key} className="flex items-center gap-3">
                    <label className="text-xs w-52 shrink-0 font-mono truncate" style={{ color: 'var(--fg-muted)' }} title={key}>{key}</label>
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
