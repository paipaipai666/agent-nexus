import { useState, useEffect, useCallback } from 'react'
import { Save, Loader2, RotateCcw, Plus, Trash2, RefreshCw } from 'lucide-react'
import { api, ProviderDraft, ProviderInfo, ModelOverrideDraft } from '../services/api'

interface PersonaProject {
  name: string
  focus: string
}

interface PersonaData {
  agent_name: string
  identity: string
  tone: string
  projects: PersonaProject[]
}

const GROUPS: Record<string, string[]> = {
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

const EMPTY_PERSONA: PersonaData = { agent_name: '', identity: '', tone: '', projects: [] }

const OVERRIDE_FIELDS: Array<{ key: keyof ModelOverrideDraft; label: string; kind: 'bool' | 'int' }> = [
  { key: 'supports_vision', label: '视觉', kind: 'bool' },
  { key: 'supports_tool_calling', label: '工具调用', kind: 'bool' },
  { key: 'supports_json_mode', label: 'JSON 模式', kind: 'bool' },
  { key: 'supports_json_schema', label: 'JSON Schema', kind: 'bool' },
  { key: 'supports_thinking', label: '思考', kind: 'bool' },
  { key: 'supports_parallel_tool_calls', label: '并行工具', kind: 'bool' },
  { key: 'context_length', label: '上下文长度', kind: 'int' },
  { key: 'max_output_tokens', label: '最大输出', kind: 'int' },
]

function TriSelect({ value, onChange }: { value: boolean | null | undefined; onChange: (v: boolean | null) => void }) {
  return (
    <select
      value={value === true ? 'y' : value === false ? 'n' : ''}
      onChange={e => onChange(e.target.value === '' ? null : e.target.value === 'y')}
      className="input-field text-xs"
    >
      <option value="">自动</option>
      <option value="y">是</option>
      <option value="n">否</option>
    </select>
  )
}

export default function SettingsPage() {
  const [config, setConfig] = useState<Record<string, any>>({})
  const [edited, setEdited] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Persona state
  const [persona, setPersona] = useState<PersonaData>(EMPTY_PERSONA)
  const [personaDraft, setPersonaDraft] = useState<PersonaData>(EMPTY_PERSONA)
  const [personaSaving, setPersonaSaving] = useState(false)
  const personaEdited = JSON.stringify(persona) !== JSON.stringify(personaDraft)
  // Model providers state (multi-provider, multi-model switchable profiles)
  const [savedProviders, setSavedProviders] = useState<ProviderInfo[]>([])
  const [providersDraft, setProvidersDraft] = useState<ProviderDraft[]>([])
  const [providersDirty, setProvidersDirty] = useState(false)
  const [providersSaving, setProvidersSaving] = useState(false)
  const [activeModel, setActiveModel] = useState('')
  const [judgeModel, setJudgeModel] = useState('')

  useEffect(() => {
    api.getConfig().then((cfg) => {
      setConfig(cfg)
      if (cfg.persona) {
        const p: PersonaData = {
          agent_name: cfg.persona.agent_name || '',
          identity: cfg.persona.identity || '',
          tone: cfg.persona.tone || '',
          projects: cfg.persona.projects || [],
        }
        setPersona(p)
        setPersonaDraft(p)
      }
    }).catch(console.error)
    api.getLlmProviders().then((d) => {
      setSavedProviders(d.providers || [])
      setProvidersDraft((d.providers || []).map(p => ({
        name: p.name,
        base_url: p.base_url,
        api_key: '',
        timeout: String(p.timeout ?? 60),
        models: (p.models || []).map(m => ({ model_id: m.model_id, override: m.override || null })),
      })))
      setActiveModel(d.active_model || d.active || '')
      setJudgeModel(d.judge_model || '')
    }).catch(() => {})
  }, [])

  const handlePersonaField = useCallback((field: keyof Omit<PersonaData, 'projects'>, value: string) => {
    setPersonaDraft(prev => ({ ...prev, [field]: value }))
  }, [])

  const handleProjectChange = useCallback((index: number, field: keyof PersonaProject, value: string) => {
    setPersonaDraft(prev => {
      const projects = [...prev.projects]
      projects[index] = { ...projects[index], [field]: value }
      return { ...prev, projects }
    })
  }, [])

  const addProject = useCallback(() => {
    setPersonaDraft(prev => ({
      ...prev,
      projects: [...prev.projects, { name: '', focus: '进行中' }],
    }))
  }, [])

  const removeProject = useCallback((index: number) => {
    setPersonaDraft(prev => ({
      ...prev,
      projects: prev.projects.filter((_, i) => i !== index),
    }))
  }, [])

  const handlePersonaSave = useCallback(async () => {
    setPersonaSaving(true); setError(null)
    try {
      await api.updatePersona(personaDraft)
      setPersona(personaDraft)
    } catch (e: any) {
      setError(`Failed to save persona: ${e.message}`)
    } finally {
      setPersonaSaving(false)
    }
  }, [personaDraft])

  const handlePersonaReset = useCallback(() => {
    setPersonaDraft(persona)
  }, [persona])
  const handleProviderField = (index: number, field: 'name' | 'base_url' | 'api_key' | 'timeout', value: string) => {
    setProvidersDraft(prev => {
      const next = [...prev]
      next[index] = { ...next[index], [field]: value }
      return next
    })
    setProvidersDirty(true)
  }

  const addProviderRow = () => {
    setProvidersDraft(prev => [...prev, { name: '', base_url: '', api_key: '', timeout: '60', models: [] }])
    setProvidersDirty(true)
  }

  const removeProviderRow = (index: number) => {
    setProvidersDraft(prev => prev.filter((_, i) => i !== index))
    setProvidersDirty(true)
  }

  const handleModelId = (pIdx: number, mIdx: number, value: string) => {
    setProvidersDraft(prev => {
      const next = [...prev]
      const models = [...next[pIdx].models]
      models[mIdx] = { ...models[mIdx], model_id: value }
      next[pIdx] = { ...next[pIdx], models }
      return next
    })
    setProvidersDirty(true)
  }

  const addModelRow = (pIdx: number, modelId = '') => {
    setProvidersDraft(prev => {
      const next = [...prev]
      next[pIdx] = { ...next[pIdx], models: [...next[pIdx].models, { model_id: modelId, override: null }] }
      return next
    })
    setProvidersDirty(true)
  }

  const removeModelRow = (pIdx: number, mIdx: number) => {
    setProvidersDraft(prev => {
      const next = [...prev]
      next[pIdx] = { ...next[pIdx], models: next[pIdx].models.filter((_, i) => i !== mIdx) }
      return next
    })
    setProvidersDirty(true)
  }

  const handleOverride = (pIdx: number, mIdx: number, field: keyof ModelOverrideDraft, raw: string, kind: 'bool' | 'int') => {
    setProvidersDraft(prev => {
      const next = [...prev]
      const models = [...next[pIdx].models]
      const override: ModelOverrideDraft = { ...(models[mIdx].override || {}) }
      if (raw === '') {
        delete override[field]
      } else {
        const value = kind === 'bool' ? raw === 'y' : parseInt(raw, 10)
        if (typeof value === 'number' && !Number.isFinite(value)) return prev
        ;(override as Record<string, boolean | number | null | undefined>)[field] = value
      }
      models[mIdx] = { ...models[mIdx], override: Object.keys(override).length ? override : null }
      next[pIdx] = { ...next[pIdx], models }
      return next
    })
    setProvidersDirty(true)
  }

  const handleDiscover = async (pIdx: number) => {
    const row = providersDraft[pIdx]
    setError(null)
    setProvidersDraft(prev => prev.map((p, i) => i === pIdx ? { ...p, discovering: true } : p))
    try {
      const d = await api.discoverLlmModels(row.base_url, row.api_key, row.name)
      setProvidersDraft(prev => prev.map((p, i) => i === pIdx ? {
        ...p,
        discovering: false,
        discovered: d.models.map(m => ({ ...m, checked: false })),
      } : p))
    } catch (e) {
      setProvidersDraft(prev => prev.map((p, i) => i === pIdx ? { ...p, discovering: false } : p))
      setError(`拉取模型列表失败：${e instanceof Error ? e.message : String(e)}`)
    }
  }

  const toggleDiscovered = (pIdx: number, id: string) => {
    setProvidersDraft(prev => prev.map((p, i) => i === pIdx ? {
      ...p,
      discovered: (p.discovered || []).map(m => m.id === id ? { ...m, checked: !m.checked } : m),
    } : p))
  }

  const importDiscovered = (pIdx: number) => {
    setProvidersDraft(prev => {
      const next = [...prev]
      const row = next[pIdx]
      const existing = new Set(row.models.map(m => m.model_id))
      const picked = (row.discovered || []).filter(m => m.checked && !existing.has(m.id))
      next[pIdx] = {
        ...row,
        models: [...row.models, ...picked.map(m => ({ model_id: m.id, override: null }))],
        discovered: undefined,
      }
      return next
    })
    setProvidersDirty(true)
  }

  const handleProvidersSave = async () => {
    setProvidersSaving(true); setError(null)
    try {
      await api.updateLlmProviders(providersDraft.map(r => ({
        name: r.name,
        base_url: r.base_url,
        ...(r.api_key ? { api_key: r.api_key } : {}),
        timeout: parseInt(r.timeout, 10) || 60,
        models: r.models,
      })))
      setProvidersDirty(false)
      // Reload so key masking + normalized values come from the server
      const d = await api.getLlmProviders()
      setSavedProviders(d.providers || [])
      setProvidersDraft((d.providers || []).map(p => ({
        name: p.name,
        base_url: p.base_url,
        api_key: '',
        timeout: String(p.timeout ?? 60),
        models: (p.models || []).map(m => ({ model_id: m.model_id, override: m.override || null })),
      })))
      setActiveModel(d.active_model || d.active || '')
    } catch (e) {
      setError(`Failed to save providers: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setProvidersSaving(false)
    }
  }

  const handleUseModel = async (selector: string) => {
    setError(null)
    try {
      await api.setActiveLlmProvider(selector)
      setActiveModel(selector)
    } catch (e) {
      setError(`切换模型失败：${e instanceof Error ? e.message : String(e)}`)
    }
  }

  const handleJudgeModelChange = async (selector: string) => {
    setError(null)
    try {
      await api.updateConfig('judge_model', selector)
      setJudgeModel(selector)
    } catch (e) {
      setError(`保存 judge 模型失败：${e instanceof Error ? e.message : String(e)}`)
    }
  }

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
      <div className="px-6 py-4">
        <h1 className="text-xl uppercase tracking-wider" style={{ color: 'var(--fg)', fontFamily: 'var(--font-display)' }}>General</h1>
        <p className="text-xs mt-0.5" style={{ color: 'var(--fg-muted)' }}>Core configuration for LLM, agent, RAG, and execution</p>
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
        {error && <div className="rounded-lg px-3 py-2 text-sm" style={{ background: 'var(--red-muted)', color: 'var(--red)' }}>{error}</div>}

        {/* ── Persona Section ─────────────────────────────────── */}
        <div className="p-4 rounded-lg" style={{ background: 'var(--surface-1)', border: '1px solid var(--border)' }}>
          <h2 className="text-xs font-semibold uppercase tracking-wider mb-3" style={{ color: 'var(--accent)' }}>Persona</h2>
          <div className="space-y-2.5">
            {/* Simple fields */}
            {([['agent_name', 'Agent Name'], ['identity', 'Identity'], ['tone', 'Tone']] as const).map(([field, label]) => (
              <div key={field} className="flex items-center gap-3">
                <label className="text-xs w-52 shrink-0 font-mono truncate" style={{ color: 'var(--fg-muted)' }}>{label}</label>
                <input
                  type="text"
                  value={personaDraft[field]}
                  onChange={e => handlePersonaField(field, e.target.value)}
                  className="input-field flex-1 font-mono text-xs"
                  placeholder={field === 'tone' ? '直接、简洁' : field === 'identity' ? '开发搭档' : 'Nexus'}
                />
              </div>
            ))}

            {/* Projects list */}
            <div className="pt-2">
              <div className="flex items-center justify-between mb-2">
                <label className="text-xs font-mono" style={{ color: 'var(--fg-muted)' }}>Projects</label>
                <button
                  onClick={addProject}
                  className="flex items-center gap-1 px-2 py-1 rounded text-xs transition-colors"
                  style={{ background: 'var(--surface-3)', color: 'var(--fg)' }}
                >
                  <Plus size={12} /> Add
                </button>
              </div>
              {personaDraft.projects.length === 0 && (
                <p className="text-xs italic" style={{ color: 'var(--fg-faint)' }}>No projects configured</p>
              )}
              {personaDraft.projects.map((project, idx) => (
                <div key={idx} className="flex items-center gap-2 mb-1.5">
                  <input
                    type="text"
                    value={project.name}
                    onChange={e => handleProjectChange(idx, 'name', e.target.value)}
                    className="input-field flex-1 font-mono text-xs"
                    placeholder="Project name"
                  />
                  <input
                    type="text"
                    value={project.focus}
                    onChange={e => handleProjectChange(idx, 'focus', e.target.value)}
                    className="input-field flex-1 font-mono text-xs"
                    placeholder="Focus"
                  />
                  <button
                    onClick={() => removeProject(idx)}
                    className="p-1.5 rounded-md transition-colors"
                    style={{ color: 'var(--fg-faint)' }}
                    onMouseEnter={e => { e.currentTarget.style.background = 'var(--red-muted)'; e.currentTarget.style.color = 'var(--red)' }}
                    onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'var(--fg-faint)' }}
                  >
                    <Trash2 size={12} />
                  </button>
                </div>
              ))}
            </div>

            {/* Save / Reset */}
            {personaEdited && (
              <div className="flex items-center gap-2 pt-2">
                <button
                  onClick={handlePersonaSave}
                  disabled={personaSaving}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors"
                  style={{ background: 'var(--accent)', color: 'white' }}
                >
                  {personaSaving ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />}
                  Save Persona
                </button>
                <button
                  onClick={handlePersonaReset}
                  className="p-1.5 rounded-md transition-colors"
                  style={{ color: 'var(--fg-faint)' }}
                  onMouseEnter={e => e.currentTarget.style.background = 'var(--surface-3)'}
                  onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                >
                  <RotateCcw size={12} />
                </button>
              </div>
            )}
          </div>
        </div>

        {/* ── Model Providers ───────────────────────────────── */}
        <div className="p-4 rounded-lg" style={{ background: 'var(--surface-1)', border: '1px solid var(--border)' }}>
          <div className="flex items-center justify-between mb-2">
            <h2 className="text-xs font-semibold uppercase tracking-wider" style={{ color: 'var(--accent)' }}>Model Providers</h2>
            <button
              onClick={addProviderRow}
              className="flex items-center gap-1 px-2 py-1 rounded text-xs transition-colors"
              style={{ background: 'var(--surface-3)', color: 'var(--fg)' }}
            >
              <Plus size={12} /> Add Provider
            </button>
          </div>
          <p className="text-[11px] mb-3" style={{ color: 'var(--fg-faint)' }}>
            每个供应商可挂多个模型；能力默认值自动检测，可逐项覆盖。聊天输入框左下角随时切换任务模型。
          </p>

          {/* Judge model picker */}
          <div className="flex items-center gap-3 mb-4">
            <label className="text-xs w-52 shrink-0 font-mono" style={{ color: 'var(--fg-muted)' }}>Judge 模型</label>
            <select
              value={judgeModel}
              onChange={e => handleJudgeModelChange(e.target.value)}
              className="input-field flex-1 font-mono text-xs"
            >
              <option value="">跟随任务模型</option>
              {providersDraft.flatMap(p =>
                p.models.map(m => (
                  <option key={`${p.name}/${m.model_id}`} value={`${p.name}/${m.model_id}`}>
                    {p.name} / {m.model_id}
                  </option>
                ))
              )}
            </select>
          </div>

          {providersDraft.length === 0 && (
            <p className="text-xs italic" style={{ color: 'var(--fg-faint)' }}>No providers configured</p>
          )}

          {providersDraft.map((p, pIdx) => (
            <div key={pIdx} className="mb-3 p-3 rounded-md" style={{ background: 'var(--surface-2)', border: '1px solid var(--border)' }}>
              {/* provider endpoint row */}
              <div className="flex items-center gap-2 mb-2">
                <input type="text" value={p.name} onChange={e => handleProviderField(pIdx, 'name', e.target.value)} className="input-field w-28 shrink-0 font-mono text-xs" placeholder="名称" />
                <input type="text" value={p.base_url} onChange={e => handleProviderField(pIdx, 'base_url', e.target.value)} className="input-field flex-1 min-w-0 font-mono text-xs" placeholder="base_url" />
                <input
                  type="password"
                  value={p.api_key}
                  onChange={e => handleProviderField(pIdx, 'api_key', e.target.value)}
                  className="input-field w-28 shrink-0 font-mono text-xs"
                  placeholder={savedProviders.find(s => s.name === p.name)?.api_key === '****' ? '****（留空保持不变）' : 'api_key'}
                />
                <input type="text" value={p.timeout} onChange={e => handleProviderField(pIdx, 'timeout', e.target.value)} className="input-field w-14 shrink-0 font-mono text-xs" placeholder="超时" title="timeout（秒）" />
                <button
                  onClick={() => handleDiscover(pIdx)}
                  disabled={p.discovering || !p.base_url.trim()}
                  className="flex items-center gap-1 px-2 py-1 rounded text-xs transition-colors shrink-0 disabled:opacity-40"
                  style={{ background: 'var(--surface-3)', color: 'var(--fg)' }}
                  title="从供应商 /v1/models 拉取模型列表"
                >
                  {p.discovering ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />} 拉取模型
                </button>
                <button
                  onClick={() => removeProviderRow(pIdx)}
                  className="p-1.5 rounded-md transition-colors shrink-0"
                  style={{ color: 'var(--fg-faint)' }}
                  onMouseEnter={e => { e.currentTarget.style.background = 'var(--red-muted)'; e.currentTarget.style.color = 'var(--red)' }}
                  onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'var(--fg-faint)' }}
                >
                  <Trash2 size={12} />
                </button>
              </div>

              {/* discovered models (checkbox import) */}
              {p.discovered && p.discovered.length > 0 && (
                <div className="mb-2 p-2 rounded max-h-44 overflow-y-auto" style={{ background: 'var(--surface-1)', border: '1px solid var(--border)' }}>
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-[10px]" style={{ color: 'var(--fg-faint)' }}>勾选导入（{p.discovered.filter(m => m.checked).length} 选中）</span>
                    <button
                      onClick={() => importDiscovered(pIdx)}
                      disabled={!p.discovered.some(m => m.checked)}
                      className="px-2 py-0.5 rounded text-[10px] transition-colors disabled:opacity-40"
                      style={{ background: 'var(--accent)', color: 'white' }}
                    >
                      导入选中
                    </button>
                  </div>
                  {p.discovered.map(m => (
                    <label key={m.id} className="flex items-center gap-2 text-[11px] py-0.5 cursor-pointer" style={{ color: 'var(--fg-muted)' }}>
                      <input type="checkbox" checked={m.checked} onChange={() => toggleDiscovered(pIdx, m.id)} />
                      <span className="font-mono">{m.id}</span>
                      {m.context_length && <span className="text-[10px]" style={{ color: 'var(--fg-faint)' }}>{(m.context_length / 1024).toFixed(0)}k ctx</span>}
                    </label>
                  ))}
                </div>
              )}

              {/* models table */}
              {p.models.map((m, mIdx) => {
                const selector = `${p.name}/${m.model_id}`
                const isActive = selector === activeModel
                return (
                  <div key={mIdx} className="mb-1.5">
                    <div className="flex items-center gap-2">
                      {isActive ? (
                        <span className="text-[10px] px-1.5 py-0.5 rounded shrink-0 w-12 text-center" style={{ background: 'var(--green-muted)', color: 'var(--green)' }}>Active</span>
                      ) : (
                        <button
                          onClick={() => handleUseModel(selector)}
                          disabled={providersDirty || !p.name.trim() || !m.model_id.trim()}
                          className="text-[10px] px-1.5 py-0.5 rounded shrink-0 w-12 transition-colors disabled:opacity-40"
                          style={{ background: 'var(--surface-3)', color: 'var(--fg-muted)' }}
                          title={providersDirty ? '先保存再切换' : '设为任务模型'}
                        >
                          Use
                        </button>
                      )}
                      <input type="text" value={m.model_id} onChange={e => handleModelId(pIdx, mIdx, e.target.value)} className="input-field flex-1 min-w-0 font-mono text-xs" placeholder="model_id" />
                      <button
                        onClick={() => removeModelRow(pIdx, mIdx)}
                        className="p-1 rounded-md transition-colors shrink-0"
                        style={{ color: 'var(--fg-faint)' }}
                        onMouseEnter={e => { e.currentTarget.style.background = 'var(--red-muted)'; e.currentTarget.style.color = 'var(--red)' }}
                        onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'var(--fg-faint)' }}
                      >
                        <Trash2 size={11} />
                      </button>
                    </div>
                    {/* capability overrides — empty = auto-detect */}
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mt-1 ml-14">
                      {OVERRIDE_FIELDS.map(f => (
                        <label key={f.key} className="flex items-center gap-1 text-[10px]" style={{ color: 'var(--fg-faint)' }}>
                          {f.label}
                          {f.kind === 'bool' ? (
                            <TriSelect
                              value={typeof m.override?.[f.key] === 'boolean' ? m.override[f.key] as boolean : null}
                              onChange={v => handleOverride(pIdx, mIdx, f.key, v === null ? '' : v ? 'y' : 'n', 'bool')}
                            />
                          ) : (
                            <input
                              type="number"
                              value={typeof m.override?.[f.key] === 'number' ? m.override[f.key] as number : ''}
                              onChange={e => handleOverride(pIdx, mIdx, f.key, e.target.value, 'int')}
                              className="input-field w-20 text-[10px] font-mono"
                              placeholder="自动"
                            />
                          )}
                        </label>
                      ))}
                    </div>
                  </div>
                )
              })}
              <button
                onClick={() => addModelRow(pIdx)}
                className="flex items-center gap-1 px-2 py-0.5 rounded text-[10px] transition-colors mt-1"
                style={{ background: 'var(--surface-3)', color: 'var(--fg-muted)' }}
              >
                <Plus size={10} /> 添加模型
              </button>
            </div>
          ))}

          <div className="flex items-center gap-2 pt-1">
            {providersDirty && (
              <button
                onClick={handleProvidersSave}
                disabled={providersSaving}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors"
                style={{ background: 'var(--accent)', color: 'white' }}
              >
                {providersSaving ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />}
                Save Providers
              </button>
            )}
            <span className="text-[10px] ml-auto" style={{ color: 'var(--fg-faint)' }}>
              当前任务模型：{activeModel || '默认（llm_* 配置）'}
            </span>
          </div>
        </div>

        {/* ── Flat Config Groups ──────────────────────────────── */}
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
