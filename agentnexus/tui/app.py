"""AgentNexus TUI — Textual-based terminal-native chat interface.

Catppuccin Mocha theme, powered by real ReActAgent backend.

Usage: nexus tui
"""

from pathlib import Path

from textual.app import App

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

    def __init__(self, agent, memory, version, mcp_manager=None, skill_service=None):
        super().__init__()
        self._agent = agent
        self._memory = memory
        self._version = version
        self._mcp_manager = mcp_manager
        self._skill_service = skill_service

    def on_mount(self):
        from agentnexus.tui.screens.chat import ChatScreen
        self.push_screen(ChatScreen(
            self._agent,
            self._memory,
            self._version,
            self._mcp_manager,
            skill_service=self._skill_service,
        ))
