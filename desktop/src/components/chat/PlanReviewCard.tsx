import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

interface PlanReviewCardProps {
  /** Full markdown plan document submitted via exit_plan_mode. */
  plan: string
  onApprove: () => void
  onDeny: () => void
}

/** Plan review surface: renders the agent's final plan document in full for
 *  user approval. Both actions must always be present (a review UI without an
 *  accept control strands the agent in plan mode). */
export default function PlanReviewCard({ plan, onApprove, onDeny }: PlanReviewCardProps) {
  return (
    <div className="rounded-lg p-4" style={{ background: 'var(--amber-muted)', border: '1px solid var(--amber-muted)' }}>
      <div className="text-sm font-medium mb-1" style={{ color: 'var(--amber)' }}>计划审批 Plan Review</div>
      <div className="text-xs mb-3" style={{ color: 'var(--fg-muted)' }}>
        批准后将退出计划模式并允许写入操作；拒绝则 agent 继续调研或修订计划。
      </div>
      <div className="markdown-body max-h-[50vh] overflow-auto rounded-lg p-3 mb-3" style={{ background: 'var(--surface-1)', color: 'var(--fg-secondary)' }}>
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{plan}</ReactMarkdown>
      </div>
      <div className="flex gap-2">
        <button onClick={onApprove} className="btn-primary text-sm">批准并执行</button>
        <button onClick={onDeny} className="btn-ghost text-sm">拒绝</button>
      </div>
    </div>
  )
}
