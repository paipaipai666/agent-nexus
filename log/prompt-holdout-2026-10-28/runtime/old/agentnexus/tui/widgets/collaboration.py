"""Compact collaboration summaries from the shared child-task registry."""


STATUS_LABELS = {
    "thinking": "运行中", "tool_calling": "调用工具", "waiting": "待答复",
    "cancelling": "停止中", "interrupted": "已中断", "completed": "已完成", "failed": "失败",
}


def child_label(child, *, include_progress=True):
    """Show the actual child state and its latest progress on one row."""
    state = STATUS_LABELS.get(child.get("status"), str(child.get("status", "")))
    waiting = child.get("waiting_questions", 0)
    if waiting:
        state = f"待答复 {waiting}"
    detail = str(child.get("current_tool") or child.get("latest_progress") or "").replace("\n", " ") if include_progress else ""
    return f"{child.get('name', child['subagent_id'])} · {state}" + (f" · {detail}" if detail else "")
