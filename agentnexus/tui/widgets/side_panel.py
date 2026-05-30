"""Right side panel — task timeline, tools, MCP, and skill status."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Label, Static

from agentnexus.core.text_utils import collapse_and_truncate


class SidePanel(Widget):
    """Dense IDE-style runtime summary panel."""

    DEFAULT_CSS = """
    SidePanel {
        height: 100%;
        overflow-y: auto;
    }
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._version_info = ("---", False, False)
        self._timeline_items: list[dict] = []
        self._tool_items: list[dict] = []
        self._model_info = {
            "model": "unknown",
            "ctx": "unknown",
            "strategy": "",
        }
        self._mcp_snapshot: dict | None = None
        self._skill_info = {
            "skill": "default",
            "workflow": "default",
            "status": "idle",
            "available": [],
        }
        self._todo_items: list[dict] = []

    def update_version(self, head: str, can_undo: bool, can_redo: bool):
        self._version_info = (head, can_undo, can_redo)
        self._refresh_section("version-card", self._render_version())

    def update_timeline(self, items: list[dict]):
        self._timeline_items = list(items)[-8:]
        self._refresh_section("timeline-card", self._render_timeline())

    def add_timeline_event(self, kind: str, text: str):
        self._timeline_items.append({"kind": kind, "text": text})
        self._timeline_items = self._timeline_items[-8:]
        self._refresh_section("timeline-card", self._render_timeline())

    def update_tools(self, items: list[dict]):
        self._tool_items = list(items)
        self._refresh_section("tool-card", self._render_tools())

    def add_tool_event(self, name: str, status: str = "running", detail: str = "", ms: float = 0):
        self._tool_items.append({"name": name, "status": status, "detail": detail, "ms": ms})
        self._tool_items = self._tool_items[-8:]
        self._refresh_section("tool-card", self._render_tools())

    def update_mcp(self, snapshot: dict | None):
        self._mcp_snapshot = snapshot
        self._refresh_section("mcp-card", self._render_mcp())

    def update_model(self, model: str, ctx: str = "unknown", strategy: str = ""):
        self._model_info = {"model": model or "unknown", "ctx": ctx or "unknown", "strategy": strategy or ""}
        self._refresh_section("model-card", self._render_model())

    def update_skill(
        self,
        skill: str = "default",
        workflow: str = "default",
        status: str = "idle",
        runtime: dict | None = None,
        available: list[tuple[str, str, str]] | None = None,
    ):
        self._skill_info = {
            "skill": skill or "default",
            "workflow": workflow or "default",
            "status": status or "idle",
            "available": list(available or self._skill_info.get("available", [])),
        }
        if runtime:
            self._skill_info["runtime"] = runtime
        self._refresh_section("skill-card", self._render_skill())

    def update_todo(self, items: list[dict]):
        self._todo_items = list(items)
        self._refresh_section("todo-card", self._render_todo())

    # Backward-compatible shim for old tests/callers.
    def update_memory(self, items: list[str]):
        if items:
            self.update_skill(status=f"{len(items)} memories")
        else:
            self.update_skill()

    def _refresh_section(self, section_id: str, content: str):
        try:
            section = self.query_one(f"#{section_id}", Static)
            section.update(content)
        except Exception:
            pass

    def _render_version(self) -> str:
        head, can_undo, can_redo = self._version_info
        head_short = head[:8] if head and head not in {"---", "—"} else head
        actions = []
        if can_undo:
            actions.append("/undo")
        if can_redo:
            actions.append("/redo")
        action_text = f"\n[dim]{'  '.join(actions)}[/]" if actions else ""
        return f"[dim]@ {head_short}[/]{action_text}"

    def _render_timeline(self) -> str:
        if not self._timeline_items:
            return "[dim]No conversation summary yet[/]"
        lines = []
        for item in self._timeline_items[-8:]:
            kind = str(item.get("kind", "event")).lower()
            text = collapse_and_truncate(str(item.get("text", "")), 56)
            marker = {
                "thought": "[#a78bfa]think[/]",
                "tool_start": "[#fab283]tool[/]",
                "tool_done": "[#7fd88f]done[/]",
                "error": "[#e06c75]err[/]",
                "run": "[#6ba5f2]run[/]",
                "summary": "[#6ba5f2]turn[/]",
            }.get(kind, "[dim]event[/]")
            lines.append(f"{marker} {text}")
        return "\n".join(lines)

    def _render_tools(self) -> str:
        if not self._tool_items:
            return "[dim]No tools registered[/]"
        lines = []
        for item in self._tool_items[:14]:
            name = collapse_and_truncate(str(item.get("name", "tool")), 22)
            risk = str(item.get("risk", "low")).lower()
            badge = {
                "low": "[#75c990]low[/]",
                "medium": "[#d6a25d]med[/]",
                "high": "[#e06c75]high[/]",
            }.get(risk, "[dim]tool[/]")
            lines.append(f"{badge} {name}")
        return "\n".join(lines)

    def _render_model(self) -> str:
        model = collapse_and_truncate(str(self._model_info.get("model", "unknown")), 30)
        ctx = self._model_info.get("ctx", "unknown")
        strategy = self._model_info.get("strategy", "")
        strategy_line = f"\n[dim]Strategy[/] {strategy}" if strategy else ""
        return f"[#70a6e8]{model}[/]\n[dim]Context[/] {ctx}{strategy_line}"

    def _render_mcp(self) -> str:
        if not self._mcp_snapshot:
            return "[dim]disabled[/]"
        started = self._mcp_snapshot.get("started", False)
        connected = self._mcp_snapshot.get("connected_count", 0)
        servers = self._mcp_snapshot.get("server_count", 0)
        tools = self._mcp_snapshot.get("tool_count", 0)
        failures = self._mcp_snapshot.get("failure_count", 0)
        state = "[#7fd88f]online[/]" if started and connected else "[dim]offline[/]"
        fail_text = f" [#e06c75]{failures} fail[/]" if failures else ""
        return f"{state}\n[dim]servers[/] {connected}/{servers}  [dim]tools[/] {tools}{fail_text}"

    def _render_skill(self) -> str:
        skill = self._skill_info.get("skill", "default")
        workflow = self._skill_info.get("workflow", "default")
        status = self._skill_info.get("status", "idle")
        runtime = self._skill_info.get("runtime", {}) or {}
        available = self._skill_info.get("available", []) or []
        run_status = runtime.get("status", "")
        steps = runtime.get("steps", 0)
        ok = runtime.get("ok", 0)
        errors = runtime.get("errors", 0)
        scripts = runtime.get("scripts", 0)
        references = runtime.get("references", 0)
        assets = runtime.get("assets", 0)
        auto_reason = runtime.get("auto_reason", "")
        auto_source = runtime.get("auto_source", "")
        runtime_line = ""
        if run_status:
            runtime_line = f"\n[dim]Run[/] {run_status}  [dim]steps[/] {ok}/{steps}"
            if errors:
                runtime_line += f"  [#e06c75]{errors} err[/]"
        resources = []
        if scripts:
            resources.append(f"scripts {scripts}")
        if references:
            resources.append(f"refs {references}")
        if assets:
            resources.append(f"assets {assets}")
        if resources:
            runtime_line += "\n[dim]Resources[/] " + ", ".join(resources)
        if auto_reason:
            label = f"Auto {auto_source}" if auto_source else "Auto"
            runtime_line += f"\n[dim]{label}[/] " + collapse_and_truncate(auto_reason, 80)
        available_line = ""
        if available:
            lines = ["\n[dim]Available[/]"]
            for item in available[:6]:
                skill_id = collapse_and_truncate(str(item[0]), 24)
                name = collapse_and_truncate(str(item[1]), 26)
                lines.append(f"{skill_id} [dim]{name}[/]")
            if len(available) > 6:
                lines.append(f"[dim]... {len(available) - 6} more[/]")
            available_line = "\n".join(lines)
        return (
            f"[dim]Skill[/] {skill}\n[dim]Workflow[/] {workflow}\n"
            f"[dim]Status[/] {status}{runtime_line}{available_line}"
        )

    def _render_todo(self) -> str:
        if not self._todo_items:
            return "[dim]无任务[/]"
        lines = []
        for item in self._todo_items:
            status = str(item.get("status", "pending"))
            desc = collapse_and_truncate(str(item.get("description", "")), 28)
            marker = {
                "done": "[#7fd88f]✓[/]",
                "in_progress": "[#fab283]→[/]",
                "pending": "[dim]·[/]",
            }.get(status, "[dim]·[/]")
            lines.append(f"{marker} {desc}")
        return "\n".join(lines)

    def compose(self) -> ComposeResult:
        yield Label("RUN", classes="panel-eyebrow")
        yield Label("Model", classes="section-title")
        yield Static(self._render_model(), id="model-card", classes="section-body")
        yield Label("Task Timeline", classes="section-title")
        yield Static(self._render_timeline(), id="timeline-card", classes="section-body")
        yield Label("Todo List", classes="section-title")
        yield Static(self._render_todo(), id="todo-card", classes="section-body")
        yield Label("Available Tools", classes="section-title")
        yield Static(self._render_tools(), id="tool-card", classes="section-body")
        yield Label("MCP", classes="section-title")
        yield Static(self._render_mcp(), id="mcp-card", classes="section-body")
        yield Label("Skill", classes="section-title")
        yield Static(self._render_skill(), id="skill-card", classes="section-body")
        yield Label("Session", classes="section-title")
        yield Static(self._render_version(), id="version-card", classes="section-body")
