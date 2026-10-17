"""Compact collaboration summaries from the shared child-task registry."""

from rich.text import Text
from textual.widget import Widget
from textual.widgets import Button, Static

STATUS_LABELS = {
    "thinking": "运行中", "tool_calling": "调用工具", "waiting": "待答复",
    "cancelling": "停止中", "interrupted": "已中断", "completed": "已完成", "failed": "失败",
}


def child_label(child):
    """Show the actual child state and its latest progress on one row."""
    state = STATUS_LABELS.get(child.get("status"), str(child.get("status", "")))
    waiting = child.get("waiting_questions", 0)
    if waiting:
        state = f"待答复 {waiting}"
    detail = str(child.get("current_tool") or child.get("latest_progress") or "").replace("\n", " ")
    return f"{child.get('name', child['subagent_id'])} · {state}" + (f" · {detail}" if detail else "")


class CollaborationTree(Widget):
    """Keep named child tasks visible without opening a permanent side panel."""

    DEFAULT_CSS = """
    CollaborationTree { height: auto; margin-bottom: 1; }
    CollaborationTree Static { height: auto; max-height: 6; text-overflow: ellipsis; }
    CollaborationTree Button { height: 1; min-width: 0; border: none; padding: 0; color: $text-muted; background: transparent; }
    """

    def compose(self):
        yield Static("", id="collaboration-summary")
        yield Button("F3 查看协作与任务", id="open-collaboration")

    def update_children(self, children):
        self.display = bool(children)
        text = Text("协作 · 主代理", style="bold")
        for index, child in enumerate(children[:4]):
            text.append("\n" + ("└─ " if index == len(children) - 1 else "├─ "))
            text.append(child_label(child), "yellow" if child.get("waiting_questions") else "dim")
        if len(children) > 4:
            text.append(f"\n└─ 还有 {len(children) - 4} 个任务 · F3 查看", "dim")
        self.query_one("#collaboration-summary", Static).update(text)
