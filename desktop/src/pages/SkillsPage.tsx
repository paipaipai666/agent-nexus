import { useState, useEffect } from 'react'
import { Zap } from 'lucide-react'
import { api } from '../services/api'

export default function SkillsPage() {
  const [skills, setSkills] = useState<any[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.listSkills()
      .then(({ skills }) => setSkills(skills))
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [])

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
            className="bg-bg-secondary rounded-lg p-4 border border-border-default hover:border-border-hover transition-colors"
          >
            <div className="flex items-start justify-between mb-2">
              <div className="flex items-center gap-2">
                <Zap size={16} className={skill.enabled ? 'text-accent-primary' : 'text-text-muted'} />
                <h3 className="text-sm font-medium text-text-primary">{skill.display_name}</h3>
              </div>
              <span className={`text-xs px-2 py-0.5 rounded-full ${
                skill.enabled
                  ? 'bg-status-success/20 text-status-success'
                  : 'bg-bg-tertiary text-text-muted'
              }`}>
                {skill.enabled ? 'Enabled' : 'Disabled'}
              </span>
            </div>
            <p className="text-xs text-text-secondary mb-2">{skill.description || 'No description'}</p>
            <p className="text-xs text-text-muted font-mono">{skill.id}</p>
          </div>
        ))}
      </div>
    </div>
  )
}
