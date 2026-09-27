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
    <div
      className="rounded-2xl overflow-hidden"
      style={{ background: 'var(--surface-1)', border: '1px solid var(--border-strong)', boxShadow: 'var(--shadow-float)' }}
    >
      <div className="flex items-center gap-2.5 px-5 pt-4 pb-3" style={{ borderBottom: '1px solid var(--border-subtle)' }}>
        <span className="w-2 h-2 rounded-full animate-pulse shrink-0" style={{ background: 'var(--amber)' }} />
        <span className="text-[14px] font-semibold" style={{ color: 'var(--fg)' }}>计划审批 Plan Review</span>
        <span className="text-[12px] ml-auto text-right" style={{ color: 'var(--fg-faint)' }}>
          批准后将退出计划模式并允许写入操作；拒绝则 agent 继续调研或修订计划。
        </span>
      </div>
      <div
        className="markdown-body px-5 py-4 max-h-[50vh] overflow-auto text-[14px]"
        style={{ color: 'var(--fg-secondary)', lineHeight: 1.65 }}
      >
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{plan}</ReactMarkdown>
      </div>
      <div className="px-5 pb-4 flex gap-2.5">
        <button onClick={onApprove} className="btn-primary">批准并执行</button>
        <button onClick={onDeny} className="btn-ghost">拒绝</button>
      </div>
    </div>
  )
}
