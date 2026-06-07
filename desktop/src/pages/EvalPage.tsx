import { useState, useEffect, useCallback } from 'react'
import {
  FlaskConical, Play, CheckCircle, XCircle, BarChart3, Layers, ChevronDown, ChevronRight,
  RefreshCw, Save, GitCompare, AlertTriangle, Clock, Zap, Target,
} from 'lucide-react'
import { api } from '../services/api'

interface EvalTask {
  id: string
  description: string
  category: string
  difficulty: string
  eval_type: string
  tags: string[]
  grader_count: number
}

interface EvalSuite {
  name: string
  eval_type: string
  description: string
  task_count: number
}

interface TaskResult {
  task_id: string
  passed: boolean
  avg_score: number
  n_trials: number
  pass_at_k: Record<string, number>
}

interface SuiteResult {
  suite_name: string
  eval_type: string
  passed: boolean
  aggregate: {
    total_tasks: number
    passed_tasks: number
    pass_rate: number
    avg_score: number
    total_trials: number
    total_duration_ms: number
  }
  task_reports: TaskResult[]
}

type TabType = 'tasks' | 'suites' | 'results' | 'baselines'

export default function EvalPage() {
  const [activeTab, setActiveTab] = useState<TabType>('tasks')
  const [tasks, setTasks] = useState<EvalTask[]>([])
  const [suites, setSuites] = useState<EvalSuite[]>([])
  const [validation, setValidation] = useState<{ valid: boolean; errors: string[]; stats: any } | null>(null)
  const [suiteResult, setSuiteResult] = useState<SuiteResult | null>(null)
  const [baselines, setBaselines] = useState<any[]>([])
  const [regression, setRegression] = useState<any>(null)
  const [loading, setLoading] = useState<string | null>(null)
  const [filterCategory, setFilterCategory] = useState<string>('')
  const [filterDifficulty, setFilterDifficulty] = useState<string>('')
  const [expandedTask, setExpandedTask] = useState<string | null>(null)
  const [taskDetail, setTaskDetail] = useState<any>(null)
  const [nTrials, setNTrials] = useState(1)

  const loadTasks = useCallback(async () => {
    try {
      const { tasks } = await api.listEvalTasks(filterCategory || undefined, filterDifficulty || undefined)
      setTasks(tasks)
    } catch (e) { console.error(e) }
  }, [filterCategory, filterDifficulty])

  const loadSuites = useCallback(async () => {
    try {
      const { suites } = await api.listEvalSuites()
      setSuites(suites)
    } catch (e) { console.error(e) }
  }, [])

  const loadBaselines = useCallback(async () => {
    try {
      const { suites } = await api.listEvalSuites()
      const results: any[] = []
      for (const s of suites) {
        try {
          const bl = await api.getEvalBaseline(s.name)
          results.push({ suite: s.name, ...bl })
        } catch { /* no baseline */ }
      }
      setBaselines(results)
    } catch (e) { console.error(e) }
  }, [])

  useEffect(() => {
    loadTasks()
    loadSuites()
    loadBaselines()
  }, [loadTasks, loadSuites, loadBaselines])

  const handleValidate = async () => {
    setLoading('validate')
    try {
      const result = await api.validateEvalTasks()
      setValidation(result)
    } catch (e) { console.error(e) }
    setLoading(null)
  }

  const handleRunSuite = async (suiteName: string) => {
    setLoading(`suite:${suiteName}`)
    setSuiteResult(null)
    setRegression(null)
    try {
      const result = await api.runEvalSuite(suiteName, nTrials)
      setSuiteResult(result)
      setActiveTab('results')
    } catch (e) { console.error(e) }
    setLoading(null)
  }

  const handleSaveBaseline = async (suiteName: string) => {
    setLoading(`baseline:${suiteName}`)
    try {
      await api.saveEvalBaseline(suiteName)
      await loadBaselines()
    } catch (e) { console.error(e) }
    setLoading(null)
  }

  const handleCompareBaseline = async (suiteName: string) => {
    setLoading(`compare:${suiteName}`)
    setRegression(null)
    try {
      const result = await api.compareEvalBaseline(suiteName)
      setRegression(result)
    } catch (e) { console.error(e) }
    setLoading(null)
  }

  const handleShowTaskDetail = async (taskId: string) => {
    if (expandedTask === taskId) {
      setExpandedTask(null)
      setTaskDetail(null)
      return
    }
    setExpandedTask(taskId)
    try {
      const detail = await api.getEvalTask(taskId)
      setTaskDetail(detail)
    } catch { setTaskDetail(null) }
  }

  const difficultyColor = (d: string) => {
    if (d === 'easy') return 'var(--green)'
    if (d === 'hard') return 'var(--red)'
    return 'var(--amber)'
  }

  const categoryIcon = (c: string) => {
    if (c === 'coding') return '💻'
    if (c === 'tool_use') return '🔧'
    if (c === 'reasoning') return '🧠'
    if (c === 'conversation') return '💬'
    if (c === 'rag') return '📚'
    return '📋'
  }

  const tabs = [
    { id: 'tasks' as TabType, label: 'Tasks', icon: Layers },
    { id: 'suites' as TabType, label: 'Suites', icon: Target },
    { id: 'results' as TabType, label: 'Results', icon: BarChart3 },
    { id: 'baselines' as TabType, label: 'Baselines', icon: GitCompare },
  ]

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Header */}
      <div className="px-6 py-4 flex items-center justify-between" style={{ borderBottom: '1px solid var(--border)' }}>
        <div className="flex items-center gap-2">
          <FlaskConical size={20} style={{ color: 'var(--accent)' }} />
          <h1 className="text-lg font-semibold" style={{ color: 'var(--fg)' }}>Evaluation</h1>
        </div>
        <div className="flex gap-2">
          <button className="btn-ghost text-xs flex items-center gap-1" onClick={handleValidate} disabled={loading === 'validate'}>
            <CheckCircle size={14} /> Validate
          </button>
          <button className="btn-ghost text-xs flex items-center gap-1" onClick={() => { loadTasks(); loadSuites(); loadBaselines() }}>
            <RefreshCw size={14} /> Refresh
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="px-6 flex gap-1" style={{ borderBottom: '1px solid var(--border)' }}>
        {tabs.map(t => (
          <button
            key={t.id}
            className="flex items-center gap-1.5 px-3 py-2.5 text-sm transition-colors"
            style={{
              color: activeTab === t.id ? 'var(--accent)' : 'var(--fg-muted)',
              borderBottom: activeTab === t.id ? '2px solid var(--accent)' : '2px solid transparent',
            }}
            onClick={() => setActiveTab(t.id)}
          >
            <t.icon size={14} /> {t.label}
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-6 py-4 space-y-3">
        {/* Validation Result */}
        {validation && (
          <div className="p-3 rounded-lg text-sm" style={{ background: validation.valid ? 'var(--green-muted)' : 'var(--red-muted)', border: '1px solid var(--border)' }}>
            {validation.valid ? (
              <span style={{ color: 'var(--green)' }}>✓ Dataset valid — {validation.stats?.total || 0} tasks</span>
            ) : (
              <div>
                <span style={{ color: 'var(--red)' }}>✗ {validation.errors.length} errors</span>
                {validation.errors.slice(0, 5).map((e, i) => (
                  <div key={i} className="text-xs mt-1" style={{ color: 'var(--fg-muted)' }}>• {e}</div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Tasks Tab */}
        {activeTab === 'tasks' && (
          <>
            <div className="flex gap-2 mb-2">
              <select className="input-field text-xs" value={filterCategory} onChange={e => setFilterCategory(e.target.value)}>
                <option value="">All Categories</option>
                <option value="coding">Coding</option>
                <option value="tool_use">Tool Use</option>
                <option value="reasoning">Reasoning</option>
                <option value="conversation">Conversation</option>
                <option value="rag">RAG</option>
              </select>
              <select className="input-field text-xs" value={filterDifficulty} onChange={e => setFilterDifficulty(e.target.value)}>
                <option value="">All Difficulties</option>
                <option value="easy">Easy</option>
                <option value="medium">Medium</option>
                <option value="hard">Hard</option>
              </select>
            </div>
            {tasks.length === 0 ? (
              <div className="text-center py-8" style={{ color: 'var(--fg-muted)' }}>No tasks found</div>
            ) : tasks.map(task => (
              <div key={task.id} className="p-3 rounded-lg cursor-pointer" style={{ background: 'var(--surface-1)', border: '1px solid var(--border)' }} onClick={() => handleShowTaskDetail(task.id)} onMouseEnter={e => e.currentTarget.style.background = 'var(--surface-2)'} onMouseLeave={e => e.currentTarget.style.background = 'var(--surface-1)'}>
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span>{categoryIcon(task.category)}</span>
                    <span className="font-medium text-sm" style={{ color: 'var(--fg)' }}>{task.id}</span>
                    <span className="text-xs px-1.5 py-0.5 rounded" style={{ background: task.difficulty === 'easy' ? 'var(--green-muted)' : task.difficulty === 'hard' ? 'var(--red-muted)' : 'var(--amber-muted)', color: difficultyColor(task.difficulty) }}>
                      {task.difficulty}
                    </span>
                    <span className="text-xs px-1.5 py-0.5 rounded" style={{ background: 'var(--accent-muted)', color: 'var(--accent)' }}>
                      {task.eval_type}
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-xs" style={{ color: 'var(--fg-muted)' }}>{task.grader_count} graders</span>
                    {expandedTask === task.id ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                  </div>
                </div>
                <div className="text-xs mt-1" style={{ color: 'var(--fg-muted)' }}>{task.description}</div>
                {expandedTask === task.id && taskDetail && (
                  <div className="mt-3 p-2 rounded text-xs" style={{ background: 'var(--surface-3)' }}>
                    <div className="mb-2"><strong>Graders:</strong></div>
                    {taskDetail.graders?.map((g: any, i: number) => (
                      <div key={i} className="ml-2 mb-1">• {g.type} (weight: {g.weight})</div>
                    ))}
                    {taskDetail.reference_solution && (
                      <div className="mt-2"><strong>Reference:</strong> <code className="text-xs">{taskDetail.reference_solution.slice(0, 100)}</code></div>
                    )}
                  </div>
                )}
              </div>
            ))}
          </>
        )}

        {/* Suites Tab */}
        {activeTab === 'suites' && (
          <>
            <div className="flex items-center gap-2 mb-2">
              <label className="text-xs" style={{ color: 'var(--fg-muted)' }}>Trials:</label>
              <select className="input-field text-xs w-20" value={nTrials} onChange={e => setNTrials(Number(e.target.value))}>
                {[1, 3, 5, 10].map(n => <option key={n} value={n}>{n}</option>)}
              </select>
            </div>
            {suites.length === 0 ? (
              <div className="text-center py-8" style={{ color: 'var(--fg-muted)' }}>No suites found</div>
            ) : suites.map(suite => (
              <div key={suite.name} className="p-4 rounded-lg" style={{ background: 'var(--surface-1)', border: '1px solid var(--border)' }}>
                <div className="flex items-center justify-between mb-2">
                  <div>
                    <span className="font-medium" style={{ color: 'var(--fg)' }}>{suite.name}</span>
                    <span className="text-xs ml-2 px-1.5 py-0.5 rounded" style={{ background: 'var(--accent-muted)', color: 'var(--accent)' }}>
                      {suite.eval_type}
                    </span>
                  </div>
                  <span className="text-xs" style={{ color: 'var(--fg-muted)' }}>{suite.task_count} tasks</span>
                </div>
                <div className="text-xs mb-3" style={{ color: 'var(--fg-muted)' }}>{suite.description}</div>
                <div className="flex gap-2">
                  <button
                    className="btn-primary text-xs flex items-center gap-1"
                    onClick={() => handleRunSuite(suite.name)}
                    disabled={loading === `suite:${suite.name}`}
                  >
                    <Play size={12} /> {loading === `suite:${suite.name}` ? 'Running...' : 'Run'}
                  </button>
                  <button
                    className="btn-ghost text-xs flex items-center gap-1"
                    onClick={() => handleSaveBaseline(suite.name)}
                    disabled={loading === `baseline:${suite.name}`}
                  >
                    <Save size={12} /> Save Baseline
                  </button>
                  <button
                    className="btn-ghost text-xs flex items-center gap-1"
                    onClick={() => handleCompareBaseline(suite.name)}
                    disabled={loading === `compare:${suite.name}`}
                  >
                    <GitCompare size={12} /> Compare
                  </button>
                </div>
              </div>
            ))}
          </>
        )}

        {/* Results Tab */}
        {activeTab === 'results' && (
          <>
            {suiteResult ? (
              <div className="space-y-3">
                {/* Summary Card */}
                <div className="p-4 rounded-lg" style={{ background: 'var(--surface-1)', border: '1px solid var(--border)' }}>
                  <div className="flex items-center justify-between mb-3">
                    <h3 className="font-medium" style={{ color: 'var(--fg)' }}>{suiteResult.suite_name} ({suiteResult.eval_type})</h3>
                    <span className="text-sm font-medium px-2 py-0.5 rounded" style={{
                      background: suiteResult.passed ? 'var(--green-muted)' : 'var(--red-muted)',
                      color: suiteResult.passed ? 'var(--green)' : 'var(--red)',
                    }}>
                      {suiteResult.passed ? 'PASSED' : 'FAILED'}
                    </span>
                  </div>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                    <MetricCard icon={<Target size={16} />} label="Pass Rate" value={`${(suiteResult.aggregate.pass_rate * 100).toFixed(1)}%`} />
                    <MetricCard icon={<Zap size={16} />} label="Avg Score" value={suiteResult.aggregate.avg_score.toFixed(2)} />
                    <MetricCard icon={<Layers size={16} />} label="Tasks" value={`${suiteResult.aggregate.passed_tasks}/${suiteResult.aggregate.total_tasks}`} />
                    <MetricCard icon={<Clock size={16} />} label="Duration" value={`${(suiteResult.aggregate.total_duration_ms / 1000).toFixed(1)}s`} />
                  </div>
                </div>

                {/* Per-Task Results */}
                <h3 className="text-sm font-medium" style={{ color: 'var(--fg)' }}>Task Results</h3>
                {suiteResult.task_reports?.map((tr: any) => (
                  <div key={tr.task_id} className="p-3 rounded-lg" style={{ background: 'var(--surface-1)', border: '1px solid var(--border)' }}>
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        {tr.passed ? <CheckCircle size={14} style={{ color: 'var(--green)' }} /> : <XCircle size={14} style={{ color: 'var(--red)' }} />}
                        <span className="text-sm font-medium" style={{ color: 'var(--fg)' }}>{tr.task_id}</span>
                      </div>
                      <div className="flex items-center gap-3 text-xs" style={{ color: 'var(--fg-muted)' }}>
                        <span>Score: {tr.avg_score?.toFixed(2)}</span>
                        <span>Trials: {tr.n_trials}</span>
                      </div>
                    </div>
                  </div>
                ))}

                {/* Regression Result */}
                {regression && (
                  <div className="p-4 rounded-lg" style={{ background: regression.has_regression ? 'var(--red-muted)' : 'var(--green-muted)', border: '1px solid var(--border)' }}>
                    <h3 className="font-medium mb-2" style={{ color: 'var(--fg)' }}>Regression Report</h3>
                    <div className="text-sm" style={{ color: 'var(--fg-muted)' }}>
                      <div>Pass Rate Change: <span style={{ color: regression.pass_rate_diff >= 0 ? 'var(--green)' : 'var(--red)' }}>{(regression.pass_rate_diff * 100).toFixed(1)}%</span></div>
                      <div>Score Change: <span style={{ color: regression.avg_score_diff >= 0 ? 'var(--green)' : 'var(--red)' }}>{regression.avg_score_diff.toFixed(3)}</span></div>
                      {regression.regressions?.length > 0 && (
                        <div className="mt-2" style={{ color: 'var(--red)' }}>
                          <AlertTriangle size={12} className="inline mr-1" />
                          Regressions: {regression.regressions.join(', ')}
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>
            ) : (
              <div className="text-center py-8" style={{ color: 'var(--fg-muted)' }}>
                <FlaskConical size={32} className="mx-auto mb-2 opacity-30" />
                <p>Run a suite to see results</p>
              </div>
            )}
          </>
        )}

        {/* Baselines Tab */}
        {activeTab === 'baselines' && (
          <>
            {baselines.length === 0 ? (
              <div className="text-center py-8" style={{ color: 'var(--fg-muted)' }}>
                <GitCompare size={32} className="mx-auto mb-2 opacity-30" />
                <p>No baselines saved yet</p>
              </div>
            ) : baselines.map(bl => (
              <div key={bl.suite} className="p-4 rounded-lg" style={{ background: 'var(--surface-1)', border: '1px solid var(--border)' }}>
                <div className="flex items-center justify-between mb-2">
                  <span className="font-medium" style={{ color: 'var(--fg)' }}>{bl.suite}</span>
                  <span className="text-xs" style={{ color: 'var(--fg-muted)' }}>{bl.timestamp?.slice(0, 19)}</span>
                </div>
                <div className="flex gap-4 text-sm" style={{ color: 'var(--fg-muted)' }}>
                  <span>Pass Rate: <strong>{((bl.pass_rate || 0) * 100).toFixed(1)}%</strong></span>
                  <span>Avg Score: <strong>{(bl.avg_score || 0).toFixed(2)}</strong></span>
                </div>
              </div>
            ))}
          </>
        )}
      </div>
    </div>
  )
}

function MetricCard({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return (
    <div className="p-3 rounded-lg" style={{ background: 'var(--surface-3)' }}>
      <div className="flex items-center gap-1.5 mb-1" style={{ color: 'var(--fg-muted)' }}>
        {icon}
        <span className="text-xs">{label}</span>
      </div>
      <div className="text-lg font-semibold" style={{ color: 'var(--fg)' }}>{value}</div>
    </div>
  )
}
