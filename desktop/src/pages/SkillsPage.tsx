import { useState, useEffect } from 'react'
import { Zap, Loader2 } from 'lucide-react'
import { api } from '../services/api'

interface Skill {
  id: string
  display_name: string
  description: string
  enabled: boolean
}

export default function SkillsPage() {
  const [skills, setSkills] = useState<Skill[]>([])
  const [loading, setLoading] = useState(true)
  const [togglingId, setTogglingId] = useState<string | null>(null)

  useEffect(() => {
    api.listSkills()
      .then(({ skills }) => setSkills(skills))
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [])

  const handleToggle = async (skill: Skill) => {
    setTogglingId(skill.id)
    try {
      if (skill.enabled) {
        await api.disableSkill(skill.id)
      } else {
        await api.enableSkill(skill.id)
      }
      setSkills(prev =>
        prev.map(s => s.id === skill.id ? { ...s, enabled: !s.enabled } : s)
      )
    } catch (e) {
      console.error('Toggle failed:', e)
    } finally {
      setTogglingId(null)
    }
  }

  if (loading) {
    return <div className="flex-1 flex items-center justify-center text-text-muted">Loading...</div>
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden p-4 gap-4">
      <h1 className="text-lg font-semibold text-text-primary">Skills</h1>

      <div className="flex-1 overflow-y-auto grid grid-cols-1 md:grid-cols-2 gap-3">
        {skills.map((skill) => (
          <div
            key={skill.id}
            className={`bg-bg-secondary rounded-lg p-4 border transition-colors ${
              skill.enabled
                ? 'border-accent-primary/30 hover:border-accent-primary/50'
                : 'border-border-default hover:border-border-hover opacity-70'
            }`}
          >
            <div className="flex items-start justify-between mb-2">
              <div className="flex items-center gap-2 min-w-0">
                <Zap size={16} className={skill.enabled ? 'text-accent-primary' : 'text-text-muted'} />
                <h3 className="text-sm font-medium text-text-primary truncate">{skill.display_name}</h3>
              </div>
              <button
                onClick={() => handleToggle(skill)}
                disabled={togglingId === skill.id}
                className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors shrink-0 ml-2 ${
                  skill.enabled ? 'bg-accent-primary' : 'bg-bg-tertiary'
                } disabled:opacity-50`}
                title={skill.enabled ? 'Disable' : 'Enable'}
              >
                <span
                  className={`inline-block h-3.5 w-3.5 rounded-full bg-white transition-transform ${
                    skill.enabled ? 'translate-x-4' : 'translate-x-1'
                  }`}
                />
                {togglingId === skill.id && (
                  <Loader2 size={10} className="absolute animate-spin text-text-muted" />
                )}
              </button>
            </div>
            <p className="text-xs text-text-secondary mb-2">{skill.description || 'No description'}</p>
            <p className="text-xs text-text-muted font-mono truncate">{skill.id}</p>
          </div>
        ))}
      </div>
    </div>
  )
}
