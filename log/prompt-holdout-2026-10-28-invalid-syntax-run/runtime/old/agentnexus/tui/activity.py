"""Current terminal activity derived from execution and collaboration state."""

from dataclasses import dataclass, field
from typing import Literal

from agentnexus.services.subagents import TERMINAL_STATUSES

TOOL_LABELS = {
    "file_read": "读取文件", "file_write": "写入文件", "file_edit": "修改文件",
    "file_search": "搜索文件", "code_search": "搜索代码", "shell_exec": "运行命令",
    "web_search": "搜索网页", "web_fetch": "读取网页", "subagent_run": "委派任务",
    "subagent_wait": "等待子任务",
}


def tool_label(name: str) -> str:
    """Use a short user-facing name while preserving unknown tool identities."""
    return TOOL_LABELS.get(name, name)


@dataclass
class RunActivity:
    """Progress of one running attempt, including overlapping tool calls."""

    run_id: str = ""
    phase: Literal["starting", "thinking", "answering", "saving"] = "starting"
    tools: dict[str, str] = field(default_factory=dict)
    confirming: bool = False

    def observe(self, event):
        if self.run_id and event.run_id != self.run_id:
            return
        payload = event.payload
        if event.type == "stream_reasoning":
            self.phase = "thinking"
        elif event.type == "stream_token":
            self.phase = "answering"
        elif event.type == "tool_start":
            name = payload.get("name", "tool")
            self.tools[payload.get("id") or name] = name
        elif event.type == "tool_done":
            self.tools.pop(payload.get("id") or payload.get("name", "tool"), None)
        elif event.type in {"run_finished", "run_failed", "run_interrupted"}:
            self.tools.clear()
            self.phase = "saving"

    @property
    def detail(self):
        if self.tools:
            first = tool_label(next(iter(self.tools.values())))
            return first if len(self.tools) == 1 else f"{first}等 {len(self.tools)} 项"
        return {"starting": "准备", "thinking": "思考", "answering": "输出回答", "saving": "保存结果"}[self.phase]


@dataclass(frozen=True)
class Activity:
    """Visible session state, with attention independent of ongoing work."""

    mode: Literal["idle", "working", "waiting", "confirming", "unavailable"] = "idle"
    detail: str = ""
    waiting: int = 0
    queued: int = 0

    def label(self, frame=0):
        marker = f"[{('/','-',chr(92),'|')[frame % 4]}]" if self.mode == "working" else {"idle": "[ ]", "waiting": "[?]", "confirming": "[?]", "unavailable": "[!]"}[self.mode]
        title = {"idle": "空闲", "working": "工作中", "waiting": "等待回复", "confirming": "等待确认", "unavailable": "状态不可用"}[self.mode]
        text = f"{marker} {title}"
        if self.waiting:
            text += f" · 待答复 {self.waiting}"
        if self.queued:
            text += f" · 排队 {self.queued}"
        if self.detail:
            text += f" · {self.detail}"
        return text


def session_activity(*, active, progress=None, children=(), recovery=None, failure="", compacting=False):
    """Keep known active work visible even when other status data is unavailable."""
    live = [child for child in children if child.get("status") not in TERMINAL_STATUSES]
    waiting = sum(child.get("waiting_questions", 0) for child in live)
    working = [child for child in live if child.get("status") != "waiting"]
    if active:
        if progress is not None and progress.confirming:
            return Activity("confirming", waiting=waiting)
        detail = "正在停止" if recovery and recovery.get("status") == "interrupted" else progress.detail if progress else "执行任务"
        return Activity("working", detail, waiting)
    if working:
        detail = "正在停止" if all(child.get("status") == "cancelling" for child in working) else f"子任务 {len(working)}"
        return Activity("working", detail, waiting)
    if live:
        return Activity("waiting", waiting=waiting)
    if compacting:
        return Activity("working", "压缩上下文")
    if failure:
        return Activity("unavailable")
    return Activity()
