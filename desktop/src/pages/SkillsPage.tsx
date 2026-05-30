import { useState, useEffect, useRef } from 'react'
import { Zap, Loader2 } from 'lucide-react'
import { api } from '../services/api'
import { animateCardGrid } from '../utils/animations'

interface Skill { id: string; display_name: string; description: string; enabled: boolean }

export default function SkillsPage() {
  const [skills, setSkills] = useState<Skill[]>([])
  const [loading, setLoading] = useState(true)
  const [togglingId, setTogglingId] = useState<string | null>(null)

  useEffect(() => {
    api.listSkills().then(({ skills }) => setSkills(skills)).catch(console.error).finally(() => setLoading(false))
  }, [])

  const handleToggle = async (skill: Skill) => {
    setTogglingId(skill.id)
    try {
      if (skill.enabled) await api.disableSkill(skill.id)
      else await api.enableSkill(skill.id)
      setSkills(prev => prev.map(s => s.id === skill.id ? { ...s, enabled: !s.enabled } : s))
    } catch (e) { console.error('Toggle failed:', e) }
    finally { setTogglingId(null) }
  }

  const gridRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!gridRef.current || loading) return
    const cards = Array.from(gridRef.current.children)
    if (cards.length > 0) animateCardGrid(cards)
  }, [skills, loading])

  if (loading) return <div className="flex-1 flex items-center justify-center"><div className="w-6 h-6 border-2 border-t-transparent rounded-full animate-spin" style={{ borderColor: 'var(--fg-faint)', borderTopColor: 'transparent' }} /></div>

  return (
    <div className="flex-1 flex flex-col overflow-hidden p-5 gap-4">
      <div>
        <h1 className="text-lg font-semibold" style={{ color: 'var(--fg)' }}>Skills</h1>
        <p className="text-xs mt-0.5" style={{ color: 'var(--fg-muted)' }}>{skills.filter(s => s.enabled).length} of {skills.length} enabled</p>
      </div>

      <div ref={gridRef} className="flex-1 overflow-y-auto grid grid-cols-1 md:grid-cols-2 gap-3 content-start">
        {skills.map((skill) => (
          <div key={skill.id} className="surface-card p-4 transition-all duration-150 hover:border-[var(--border-strong)]" style={{ opacity: skill.enabled ? 1 : 0.6 }}>
            <div className="flex items-start justify-between mb-2.5">
              <div className="flex items-center gap-2.5 min-w-0">
                <div className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0" style={{ background: skill.enabled ? 'var(--accent-muted)' : 'var(--surface-3)' }}>
                  <Zap size={14} style={{ color: skill.enabled ? 'var(--accent)' : 'var(--fg-faint)' }} />
                </div>
                <h3 className="text-sm font-medium truncate" style={{ color: 'var(--fg)' }}>{skill.display_name}</h3>
              </div>
              <button onClick={() => handleToggle(skill)} disabled={togglingId === skill.id} className="relative w-9 h-5 rounded-full transition-colors duration-200 shrink-0 ml-2" style={{ background: skill.enabled ? 'var(--accent)' : 'var(--surface-4)' }}>
                <span className="absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform duration-200" style={{ left: skill.enabled ? '18px' : '2px' }} />
                {togglingId === skill.id && <Loader2 size={10} className="absolute inset-0 m-auto animate-spin" style={{ color: 'var(--fg-muted)' }} />}
              </button>
            </div>
            <p className="text-xs mb-2" style={{ color: 'var(--fg-secondary)' }}>{skill.description || 'No description'}</p>
            <p className="text-xs font-mono truncate" style={{ color: 'var(--fg-faint)' }}>{skill.id}</p>
          </div>
        ))}
      </div>
    </div>
  )
}
