"""AgentNexus TUI — Textual-based terminal-native chat interface.

Catppuccin Mocha theme, powered by real ReActAgent backend.

Usage: nexus tui
"""

from pathlib import Path

from textual.app import App

_CSS = str(Path(__file__).parent / "styles" / "catppuccin.tcss")


class AgentNexusTUI(App):
    """AgentNexus terminal-native chat app."""

    CSS_PATH = _CSS
    TITLE = "AgentNexus"

    def __init__(self, agent, memory, version):
        super().__init__()
        self._agent = agent
        self._memory = memory
        self._version = version

    def on_mount(self):
        from agentnexus.tui.screens.chat import ChatScreen
        self.push_screen(ChatScreen(self._agent, self._memory, self._version))
