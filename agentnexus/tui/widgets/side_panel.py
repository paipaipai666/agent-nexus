"""Right side panel — task timeline, tools, MCP, and skill status."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Label, Static


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
        self._version_info = ("main", "---", False, False)
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
        }

    def update_version(self, branch: str, head: str, can_undo: bool, can_redo: bool):
        self._version_info = (branch, head, can_undo, can_redo)
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

    def update_skill(self, skill: str = "default", workflow: str = "default", status: str = "idle"):
        self._skill_info = {"skill": skill or "default", "workflow": workflow or "default", "status": status or "idle"}
        self._refresh_section("skill-card", self._render_skill())

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
        branch, head, can_undo, can_redo = self._version_info
        head_short = head[:8] if head and head not in {"---", "—"} else head
        actions = []
        if can_undo:
            actions.append("/undo")
        if can_redo:
            actions.append("/redo")
        action_text = f"\n[dim]{'  '.join(actions)}[/]" if actions else ""
        return f"[#6ba5f2]{branch}[/] [dim]@ {head_short}[/]{action_text}"

    def _render_timeline(self) -> str:
        if not self._timeline_items:
            return "[dim]No conversation summary yet[/]"
        lines = []
        for item in self._timeline_items[-8:]:
            kind = str(item.get("kind", "event")).lower()
            text = _truncate(str(item.get("text", "")), 56)
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
            name = _truncate(str(item.get("name", "tool")), 22)
            risk = str(item.get("risk", "low")).lower()
            badge = {
                "low": "[#75c990]low[/]",
                "medium": "[#d6a25d]med[/]",
                "high": "[#e06c75]high[/]",
            }.get(risk, "[dim]tool[/]")
            lines.append(f"{badge} {name}")
        return "\n".join(lines)

    def _render_model(self) -> str:
        model = _truncate(str(self._model_info.get("model", "unknown")), 30)
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
        return f"[dim]Skill[/] {skill}\n[dim]Workflow[/] {workflow}\n[dim]Status[/] {status}"

    def compose(self) -> ComposeResult:
        yield Label("RUN", classes="panel-eyebrow")
        yield Label("Model", classes="section-title")
        yield Static(self._render_model(), id="model-card", classes="section-body")
        yield Label("Task Timeline", classes="section-title")
        yield Static(self._render_timeline(), id="timeline-card", classes="section-body")
        yield Label("Available Tools", classes="section-title")
        yield Static(self._render_tools(), id="tool-card", classes="section-body")
        yield Label("MCP", classes="section-title")
        yield Static(self._render_mcp(), id="mcp-card", classes="section-body")
        yield Label("Skill", classes="section-title")
        yield Static(self._render_skill(), id="skill-card", classes="section-body")
        yield Label("Session", classes="section-title")
        yield Static(self._render_version(), id="version-card", classes="section-body")


def _truncate(text: str, limit: int) -> str:
    clean = " ".join((text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 1)] + "…"
