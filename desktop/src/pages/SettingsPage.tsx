import { useEffect, useState } from 'react'
import { Save, Loader2, Plus, Trash2, RefreshCw, Check, Search } from 'lucide-react'
import { api, type ProviderDraft, type ProviderInfo } from '../services/api'
import { ACCENT_PRESETS, applyAccent } from '../services/accent'
import { settingFields, type SettingField } from '../services/settingsFields'
import { useSettingsDraft, type PersonaDraft } from '../services/settingsDraft'
import InlineNotice from '../components/InlineNotice'

const emptyPersona: PersonaDraft = { agent_name: '', identity: '', tone: '', projects: [] }
const executionOptions = [
  ['auto', '自动选择'], ['native', '本机执行'], ['docker', 'Docker 容器'],
  ['disabled', '禁止执行'], ['local_unsafe', '本机直接执行（无隔离）'],
]

function providerDrafts(providers: ProviderInfo[]): ProviderDraft[] {
  return providers.map(provider => ({ ...provider, api_key: '', models: provider.models.map(model => ({ ...model })) }))
}

function providerPayload(providers: ProviderDraft[]) {
  return providers.map(provider => ({
    name: provider.name, base_url: provider.base_url, timeout: provider.timeout,
    ...(provider.api_key ? { api_key: provider.api_key } : {}), models: provider.models,
  }))
}

function parsePersona(value: unknown): PersonaDraft {
  if (typeof value !== 'object' || value === null) return emptyPersona
  const projects: PersonaDraft['projects'] = []
  if ('projects' in value && Array.isArray(value.projects)) {
    for (const project of value.projects) {
      if (typeof project === 'object' && project !== null && 'name' in project && typeof project.name === 'string'
        && 'focus' in project && typeof project.focus === 'string') projects.push({ name: project.name, focus: project.focus })
    }
  }
  return {
    agent_name: 'agent_name' in value && typeof value.agent_name === 'string' ? value.agent_name : '',
    identity: 'identity' in value && typeof value.identity === 'string' ? value.identity : '',
    tone: 'tone' in value && typeof value.tone === 'string' ? value.tone : '', projects,
  }
}

function failureMessage(error: unknown) {
  return error instanceof Error ? error.message : '请求失败，请重试。'
}

export default function SettingsPage() {
  const [config, setConfig] = useState<Record<string, unknown>>({})
  const [persona, setPersona] = useState<PersonaDraft>(emptyPersona)
  const [savedProviders, setSavedProviders] = useState<ProviderInfo[]>([])
  const [activeModel, setActiveModel] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({})
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [query, setQuery] = useState('')
  const [accent, setAccent] = useState(() => document.documentElement.dataset.accent ?? 'rose')
  const draft = useSettingsDraft()
  const personaDraft = draft.persona ?? persona
  const providersDraft = draft.providers ?? providerDrafts(savedProviders)
  const personaEdited = JSON.stringify(persona) !== JSON.stringify(personaDraft)
  const providersDirty = JSON.stringify(providerPayload(providerDrafts(savedProviders))) !== JSON.stringify(providerPayload(providersDraft))
  const dirty = Object.keys(draft.fields).length > 0 || personaEdited || providersDirty

  const load = async () => {
    setLoading(true)
    setLoadError(null)
    const results = await Promise.allSettled([api.getConfig(), api.getLlmProviders()])
    const failures: string[] = []
    const configuration = results[0]
    if (configuration.status === 'fulfilled') {
      setConfig(configuration.value)
      setPersona(parsePersona(configuration.value.persona))
    } else failures.push('设置加载失败：' + failureMessage(configuration.reason))
    const providers = results[1]
    if (providers.status === 'fulfilled') {
      setSavedProviders(providers.value.providers ?? [])
      setActiveModel(providers.value.active_model || providers.value.active || '')
      setConfig(current => ({ ...current, judge_model: providers.value.judge_model ?? '' }))
    } else failures.push('模型提供商加载失败：' + failureMessage(providers.reason))
    setLoadError(failures.length ? failures.join('\n') : null)
    setLoading(false)
  }

  useEffect(() => { void load() }, [])

  useEffect(() => {
    if (!dirty) return
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = '' }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  const change = (key: string, value: string) => {
    draft.setField(key, value)
    setFieldErrors(previous => { const next = { ...previous }; delete next[key]; return next })
    setError(null)
    setSuccess(null)
  }
  const changePersona = (update: (current: PersonaDraft) => PersonaDraft) => {
    draft.setPersona(update(useSettingsDraft.getState().persona ?? persona))
    setSuccess(null)
  }
  const changeProviders = (update: (current: ProviderDraft[]) => ProviderDraft[]) => {
    draft.setProviders(update(useSettingsDraft.getState().providers ?? providerDrafts(savedProviders)))
    setSuccess(null)
  }
  const discover = async (index: number) => {
    const provider = providersDraft[index]
    if (!provider) return
    setError(null)
    changeProviders(current => current.map((row, i) => i === index ? { ...row, discovering: true } : row))
    try {
      const response = await api.discoverLlmModels(provider.base_url, provider.api_key, provider.name)
      changeProviders(current => current.map((row, i) => i === index ? { ...row, discovered: response.models, discovering: false } : row))
    } catch (failure) {
      changeProviders(current => current.map((row, i) => i === index ? { ...row, discovering: false } : row))
      setError('拉取模型失败：' + failureMessage(failure))
    }
  }

  const save = async () => {
    const validation: Record<string, string> = {}
    for (const field of settingFields) {
      const value = draft.fields[field.key]
      if (value === undefined || field.kind !== 'number') continue
      const number = Number(value)
      if (!value.trim() || !Number.isFinite(number)) validation[field.key] = '请输入有效数字。'
      else if (field.step === 1 && !Number.isInteger(number)) validation[field.key] = '请输入整数。'
      else if (field.min !== undefined && number < field.min) validation[field.key] = '不能小于 ' + field.min + '。'
      else if (field.max !== undefined && number > field.max) validation[field.key] = '不能大于 ' + field.max + '。'
    }
    setFieldErrors(validation)
    if (Object.keys(validation).length) {
      setShowAdvanced(true)
      setError('有设置值超出允许范围，请修正标记的字段后保存。')
      return
    }
    setSaving(true)
    setError(null)
    setSuccess(null)
    const failures: string[] = []
    for (const [key, value] of Object.entries(draft.fields)) {
      try {
        await api.updateConfig(key, value)
        const field = settingFields.find(item => item.key === key)
        setConfig(previous => ({ ...previous, [key]: field?.kind === 'password' ? '****' : value }))
        if (useSettingsDraft.getState().fields[key] === value) draft.removeField(key)
      } catch (failure) {
        const message = failureMessage(failure)
        failures.push((settingFields.find(item => item.key === key)?.label ?? key) + '：' + message)
        setFieldErrors(previous => ({ ...previous, [key]: message }))
      }
    }
    if (personaEdited) {
      try {
        await api.updatePersona(personaDraft)
        setPersona(personaDraft)
        if (useSettingsDraft.getState().persona === personaDraft) draft.setPersona(null)
      } catch (failure) { failures.push('Agent 身份：' + failureMessage(failure)) }
    }
    if (providersDirty) {
      try {
        await api.updateLlmProviders(providerPayload(providersDraft))
        const response = await api.getLlmProviders()
        setSavedProviders(response.providers ?? [])
        setActiveModel(response.active_model || response.active || '')
        if (useSettingsDraft.getState().providers === providersDraft) draft.setProviders(null)
      } catch (failure) { failures.push('模型提供商：' + failureMessage(failure)) }
    }
    setSaving(false)
    if (failures.length) setError('部分更改未保存，待保存内容已保留。\n' + failures.join('\n'))
    else setSuccess('更改已保存。')
  }

  const displayValue = (field: SettingField) => draft.fields[field.key] ?? (field.kind === 'password' ? '' : String(config[field.key] ?? ''))
  const groups = [...new Set(settingFields.map(field => field.group))]
  const filtered = settingFields.filter(field => field.key in config && (
    query.trim() ? (field.label + ' ' + field.description + ' ' + field.key).toLowerCase().includes(query.trim().toLowerCase()) : showAdvanced || field.common || field.key in draft.fields
  ))
  const renderField = (field: SettingField) => {
    const id = 'setting-' + field.key
    const descriptionId = id + '-description'
    const errorId = id + '-error'
    const changed = field.key in draft.fields
    const value = displayValue(field)
    const constraints = field.kind === 'number'
      ? '默认 ' + String(field.defaultValue) + (field.unit ? ' ' + field.unit : '') + (field.min !== undefined ? '，最小 ' + field.min : '') + (field.max !== undefined ? '，最大 ' + field.max : '')
      : ''
    const describedBy = descriptionId + (fieldErrors[field.key] ? ' ' + errorId : '')
    let control
    if (!field.editable) {
      control = <output id={id} className="text-sm break-all" style={{ color: 'var(--fg-secondary)' }}>
        {Array.isArray(config[field.key]) ? JSON.stringify(config[field.key]) : value}
      </output>
    } else if (field.kind === 'boolean') {
      control = <button type="button" id={id} role="switch" aria-checked={value === 'true'} aria-label={field.label}
        aria-describedby={describedBy} onClick={() => change(field.key, value === 'true' ? 'false' : 'true')}
        className="relative w-9 h-5 rounded-full shrink-0" style={{ background: value === 'true' ? 'var(--accent)' : 'var(--surface-4)' }}>
        <span className="absolute top-0.5 w-4 h-4 rounded-full bg-white" style={{ left: value === 'true' ? 18 : 2 }} />
      </button>
    } else if (field.key === 'shell_execution_backend') {
      control = <select id={id} className="input-field min-w-0 w-full" value={value} aria-describedby={describedBy}
        onChange={event => change(field.key, event.target.value)}>
        {executionOptions.map(([key, label]) => <option key={key} value={key}>{label}</option>)}
      </select>
    } else {
      control = <input id={id} className="input-field min-w-0 w-full" type={field.kind === 'password' ? 'password' : field.kind === 'number' ? 'number' : 'text'}
        value={value} min={field.min} max={field.max} step={field.step} aria-invalid={Boolean(fieldErrors[field.key])}
        aria-describedby={describedBy} placeholder={field.kind === 'password' && config[field.key] ? '已保存，未修改时保持不变' : undefined}
        onChange={event => change(field.key, event.target.value)} />
    }
    return (
      <div key={field.key} className="grid gap-2 py-3 settings-field">
        <div>
          <label htmlFor={id} className="text-sm font-medium" style={{ color: 'var(--fg)' }}>{field.label}</label>
          {changed && <span className="ml-2 text-xs" style={{ color: 'var(--amber)' }}>未保存</span>}
          <p id={descriptionId} className="text-xs mt-1 leading-relaxed" style={{ color: 'var(--fg-secondary)' }}>{field.description}{constraints && ' ' + constraints}</p>
          {showAdvanced && <code className="text-xs" style={{ color: 'var(--fg-muted)' }}>{field.key}</code>}
        </div>
        <div className="min-w-0 self-center">
          {control}
          {fieldErrors[field.key] && <p id={errorId} role="alert" className="text-xs mt-1" style={{ color: 'var(--red)' }}>{fieldErrors[field.key]}</p>}
        </div>
      </div>
    )
  }
  const thinkingEnabled = (draft.fields.model_thinking ?? String(config.model_thinking)) === 'true'
  const thinkingMode = thinkingEnabled ? draft.fields.model_thinking_effort ?? String(config.model_thinking_effort ?? 'medium') : 'off'

  return (
    <div className="flex-1 min-w-0 flex flex-col overflow-hidden">
      <div className="px-6 py-4 shrink-0 space-y-3" style={{ borderBottom: '1px solid var(--border)' }}>
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div><h1 className="text-xl font-semibold" style={{ color: 'var(--fg)' }}>常规设置</h1>
            <p className="text-xs mt-1" style={{ color: 'var(--fg-secondary)' }}>外观立即生效，其余更改点击保存。离开页面会保留待保存内容。</p></div>
          <div className="flex items-center gap-2">
            {dirty && <button type="button" className="btn-ghost text-sm" disabled={saving} onClick={() => { draft.reset(); setError(null); setFieldErrors({}) }}>放弃更改</button>}
            <button type="button" className="btn-primary flex items-center gap-2 text-sm" disabled={!dirty || saving || loading || Boolean(loadError)} onClick={() => void save()}>
              {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}{saving ? '保存中…' : '保存更改'}
            </button>
          </div>
        </div>
        <InlineNotice message={loadError} onRetry={() => void load()} />
        <InlineNotice message={error} onDismiss={() => setError(null)} />
        <InlineNotice message={success} tone="success" onDismiss={() => setSuccess(null)} />
      </div>
      {loading ? <div role="status" className="p-6 text-sm">正在加载设置…</div> : (
      <fieldset disabled={saving} className="flex-1 min-h-0 min-w-0 overflow-y-auto px-6 py-4 space-y-5">
        <section className="space-y-3">
          <h2 className="text-base font-semibold">外观</h2>
          <div className="flex items-center gap-3 flex-wrap"><span className="text-sm">强调色</span>
            {ACCENT_PRESETS.map(preset => <button key={preset.id} type="button" aria-label={'强调色 ' + preset.label} aria-pressed={accent === preset.id}
              onClick={() => { applyAccent(preset.id); setAccent(preset.id) }} title={preset.label}
              className="relative rounded-full w-7 h-7" style={{ background: preset.dark[0], outline: accent === preset.id ? '2px solid var(--fg)' : undefined, outlineOffset: 3 }}>
              {accent === preset.id && <Check size={13} className="absolute inset-0 m-auto" style={{ color: '#fff' }} />}
            </button>)}
            <span className="text-xs" style={{ color: 'var(--fg-secondary)' }}>立即生效</span>
          </div>
        </section>
        <section className="space-y-3 pt-4" style={{ borderTop: '1px solid var(--border)' }}>
          <h2 className="text-base font-semibold">Agent 身份</h2>
          {([['agent_name', 'Agent 名称'], ['identity', '身份说明'], ['tone', '回复语气']] satisfies [keyof Omit<PersonaDraft, 'projects'>, string][]).map(([key, label]) => (
            <div key={key} className="settings-field grid gap-2"><label htmlFor={'persona-' + key} className="text-sm self-center">{label}</label>
              <input id={'persona-' + key} type="text" className="input-field min-w-0" value={personaDraft[key]}
                onChange={event => changePersona(current => ({ ...current, [key]: event.target.value }))} />
            </div>
          ))}
          <div className="flex items-center justify-between"><span className="text-sm">关注的项目</span>
            <button type="button" className="btn-ghost text-xs" onClick={() => changePersona(current => ({ ...current, projects: [...current.projects, { name: '', focus: '' }] }))}>添加项目</button>
          </div>
          {personaDraft.projects.map((project, index) => <div key={index} className="flex gap-2 flex-wrap">
            <input aria-label={'关注项目 ' + (index + 1) + ' 名称'} placeholder="项目名称" className="input-field flex-1 min-w-0" value={project.name}
              onChange={event => changePersona(current => ({ ...current, projects: current.projects.map((item, i) => i === index ? { ...item, name: event.target.value } : item) }))} />
            <input aria-label={'关注项目 ' + (index + 1) + ' 重点'} placeholder="关注重点" className="input-field flex-1 min-w-0" value={project.focus}
              onChange={event => changePersona(current => ({ ...current, projects: current.projects.map((item, i) => i === index ? { ...item, focus: event.target.value } : item) }))} />
            <button type="button" aria-label={'移除关注项目 ' + (index + 1)} title="移除关注项目" onClick={() => changePersona(current => ({ ...current, projects: current.projects.filter((_, i) => i !== index) }))}><Trash2 size={14} /></button>
          </div>)}
        </section>
        <section className="space-y-3 pt-4" style={{ borderTop: '1px solid var(--border)' }}>
          <div className="flex items-center justify-between gap-2"><h2 className="text-base font-semibold">模型提供商</h2>
            <button type="button" className="btn-ghost text-xs flex gap-1 items-center" onClick={() => changeProviders(current => [...current, { name: '', base_url: '', api_key: '', timeout: 60, models: [] }])}><Plus size={12} />添加提供商</button>
          </div>
          <p className="text-xs leading-relaxed" style={{ color: 'var(--fg-secondary)' }}>填写服务地址和 API 密钥，再拉取或添加模型。现有提供商的密钥留空时保持不变。</p>
          <div className="settings-field grid gap-2"><label htmlFor="default-thinking" className="text-sm self-center">新会话默认思考强度</label>
            <select id="default-thinking" className="input-field min-w-0" value={thinkingMode} onChange={event => {
              change('model_thinking', String(event.target.value !== 'off'))
              change('model_thinking_effort', event.target.value === 'off' ? 'none' : event.target.value)
            }}><option value="off">关闭</option><option value="low">低</option><option value="medium">中</option><option value="high">高</option></select>
          </div>
          <div className="settings-field grid gap-2"><label htmlFor="judge-model" className="text-sm self-center">评测模型</label>
            <select id="judge-model" className="input-field min-w-0" value={draft.fields.judge_model ?? String(config.judge_model ?? '')} onChange={event => change('judge_model', event.target.value)}>
              <option value="">跟随任务模型</option>{providersDraft.flatMap(provider => provider.models.map(model => <option key={provider.name + '/' + model.model_id} value={provider.name + '/' + model.model_id}>{provider.name} / {model.model_id}</option>))}
            </select>
          </div>
          {providersDraft.length === 0 && <p className="text-sm" style={{ color: 'var(--fg-secondary)' }}>尚未配置模型提供商。</p>}
          {providersDraft.map((provider, index) => <div key={index} className="p-3 rounded-lg space-y-3" style={{ border: '1px solid var(--border)' }}>
            <div className="grid gap-2 provider-fields">
              <input aria-label={'提供商 ' + (index + 1) + ' 名称'} placeholder="名称" className="input-field min-w-0" value={provider.name}
                onChange={event => changeProviders(current => current.map((row, i) => i === index ? { ...row, name: event.target.value } : row))} />
              <input aria-label={'提供商 ' + (index + 1) + ' 服务地址'} placeholder="服务地址" className="input-field min-w-0" value={provider.base_url}
                onChange={event => changeProviders(current => current.map((row, i) => i === index ? { ...row, base_url: event.target.value } : row))} />
              <input aria-label={'提供商 ' + (index + 1) + ' API 密钥'} type="password" placeholder={savedProviders.some(row => row.name === provider.name && row.api_key === '****') ? '已保存，留空保持不变' : 'API 密钥'} className="input-field min-w-0" value={provider.api_key}
                onChange={event => changeProviders(current => current.map((row, i) => i === index ? { ...row, api_key: event.target.value } : row))} />
            </div>
            <div className="flex items-center gap-3">
              <button type="button" className="btn-ghost text-xs flex gap-1 items-center" disabled={provider.discovering || !provider.base_url.trim()} onClick={() => void discover(index)}>
                {provider.discovering ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}拉取模型
              </button>
              <button type="button" className="btn-ghost text-xs" aria-label={'移除提供商 ' + (provider.name || index + 1)} onClick={() => changeProviders(current => current.filter((_, i) => i !== index))}>移除提供商</button>
            </div>
            {provider.discovered && <div className="max-h-44 overflow-y-auto text-sm">
              <p className="text-xs mb-1" style={{ color: 'var(--fg-secondary)' }}>点击加入或移除，已选 {provider.models.length} 个模型。</p>
              {provider.discovered.map(model => {
                const selected = provider.models.some(item => item.model_id === model.id)
                return <button key={model.id} type="button" aria-pressed={selected} className="w-full flex items-center gap-2 text-left py-1"
                  onClick={() => changeProviders(current => current.map((row, i) => i === index ? { ...row, models: selected ? row.models.filter(item => item.model_id !== model.id) : [...row.models, { model_id: model.id, override: null }] } : row))}>
                  <span className="w-4">{selected && <Check size={12} />}</span><span className="truncate flex-1">{model.id}</span>
                  {model.context_length !== null && <span className="text-xs">{Math.round(model.context_length / 1024)}K</span>}
                </button>
              })}
            </div>}
            {provider.models.map((model, modelIndex) => <div key={modelIndex} className="flex items-center gap-2">
              <input aria-label={'提供商 ' + (index + 1) + ' 模型 ' + (modelIndex + 1)} placeholder="模型名称" className="input-field flex-1 min-w-0" value={model.model_id}
                onChange={event => changeProviders(current => current.map((row, i) => i === index ? { ...row, models: row.models.map((item, j) => j === modelIndex ? { ...item, model_id: event.target.value } : item) } : row))} />
              {model.override && <span className="text-xs shrink-0" title="保留配置文件中的模型能力覆盖">自定义能力</span>}
              <button type="button" aria-label={'移除模型 ' + (model.model_id || modelIndex + 1)} title="移除模型" onClick={() => changeProviders(current => current.map((row, i) => i === index ? { ...row, models: row.models.filter((_, j) => j !== modelIndex) } : row))}><Trash2 size={14} /></button>
            </div>)}
            <button type="button" className="btn-ghost text-xs" onClick={() => changeProviders(current => current.map((row, i) => i === index ? { ...row, models: [...row.models, { model_id: '', override: null }] } : row))}>添加模型</button>
          </div>)}
          <p className="text-xs" style={{ color: 'var(--fg-secondary)' }}>当前任务模型：{activeModel || '默认配置'}</p>
        </section>
        <div className="flex items-center gap-3 pt-4 flex-wrap" style={{ borderTop: '1px solid var(--border)' }}>
          <div className="flex items-center gap-2 flex-1 min-w-0"><Search size={14} /><input aria-label="搜索设置" placeholder="搜索设置名称或用途" className="input-field flex-1 min-w-0" value={query} onChange={event => setQuery(event.target.value)} /></div>
          <button type="button" className="btn-ghost text-sm" aria-pressed={showAdvanced} onClick={() => setShowAdvanced(value => !value)}>{showAdvanced ? '仅显示常用设置' : '显示高级设置'}</button>
        </div>
        {groups.map(group => {
          const fields = filtered.filter(field => field.group === group)
          return fields.length ? <section key={group}><h2 className="text-base font-semibold">{group}</h2><div className="divide-y" style={{ borderColor: 'var(--border)' }}>{fields.map(renderField)}</div></section> : null
        })}
        {query.trim() && filtered.length === 0 && <p role="status" className="text-sm">没有匹配的设置，请尝试其他关键词。</p>}
      </fieldset>)}
    </div>
  )
}
