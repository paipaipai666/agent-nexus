"""AgentNexus terminal workspace backed by the shared application runtime."""

from dataclasses import replace
from pathlib import Path

from textual.app import App
from textual.theme import BUILTIN_THEMES, Theme

_STYLES = Path(__file__).parent / "styles"


class AgentNexusTUI(App):
    """AgentNexus terminal-native chat app."""

    CSS_PATH = [
        str(_STYLES / "base.tcss"),
        str(_STYLES / "top_bar.tcss"),
        str(_STYLES / "layout.tcss"),
        str(_STYLES / "message.tcss"),
        str(_STYLES / "input_area.tcss"),
        str(_STYLES / "side_panel.tcss"),
        str(_STYLES / "hud.tcss"),
    ]
    TITLE = "AgentNexus"
    ENABLE_COMMAND_PALETTE = False

    def __init__(self, agent=None, memory=None, version=None, mcp_manager=None, skill_service=None, capability_runtime=None, *, runtime=None):
        super().__init__(ansi_color=True)
        for name in ("ansi-dark", "ansi-light"):
            theme = BUILTIN_THEMES[name]
            self.register_theme(replace(theme, variables={**theme.variables,
                "text-muted": "ansi_default", "text-disabled": "ansi_default",
                "scrollbar-background": "ansi_default", "scrollbar-background-hover": "ansi_default",
                "scrollbar-background-active": "ansi_default", "scrollbar-corner-color": "ansi_default",
                "input-cursor-background": "ansi_default", "input-cursor-foreground": "ansi_default",
                "input-cursor-text-style": "reverse",
            }))
        self.register_theme(Theme(
            name="nexus-dark", primary="#dda0b0", secondary="#8bc4d6", accent="#dda0b0",
            background="#141414", surface="#1b1b1b", panel="#202020", foreground="#e4e4e4",
            success="#8bc69c", warning="#e1bd84", error="#ef8996", dark=True,
            variables={"text-muted": "#a3a3a3"},
        ))
        self.register_theme(Theme(
            name="nexus-light", primary="#953a56", secondary="#246578", accent="#953a56",
            background="#fafafa", surface="#eeeeee", panel="#e5e5e5", foreground="#242424",
            success="#286a40", warning="#805307", error="#a52838", dark=False,
            variables={"text-muted": "#626262"},
        ))
        self.theme = "ansi-dark"
        self._runtime = runtime
        if runtime is not None:
            agent, memory, version = runtime.agent, runtime.memory_manager, runtime.version_manager
            mcp_manager, skill_service = runtime.mcp_manager, runtime.skill
            capability_runtime = runtime.capability_runtime
            self._subagent_confirm = getattr(runtime, "subagent_confirm", None)
        self._agent = agent
        self._memory = memory
        self._version = version
        self._mcp_manager = mcp_manager
        self._skill_service = skill_service
        self._capability_runtime = capability_runtime

    def on_mount(self):
        from agentnexus.tui.screens.chat import ChatScreen
        self.push_screen(ChatScreen(
            self._agent,
            self._memory,
            self._version,
            self._mcp_manager,
            skill_service=self._skill_service,
            capability_runtime=self._capability_runtime,
            chat_service=self._runtime.chat if self._runtime is not None else None,
            session_id=self._runtime.session_id if self._runtime is not None else None,
            runtime=self._runtime,
        ))

    def action_quit(self):
        """Disable Textual's default quit shortcut; use /exit from the input box."""
        try:
            self.notify("请输入 /exit 退出 TUI")
        except Exception:
            pass
