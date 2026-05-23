import asyncio
from unittest.mock import patch

from agentnexus.memory.short_term import ShortTermMemory
from agentnexus.tui.app import AgentNexusTUI
from agentnexus.tui.widgets.hud import HUD


class TestHUDVersionDisplay:
    def test_build_text_includes_cwd_and_version(self):
        with patch("agentnexus.tui.widgets.hud.Path.cwd") as mock_cwd:
            mock_cwd.return_value.resolve.return_value = type("Resolved", (), {"__str__": lambda self: "D:/code/AgentNexus"})()
            hud = HUD()

        hud.update_version("feature", "1234567890abcdef", True, False)
        text = hud._build_text()

        assert "cwd:D:/code/AgentNexus" in text
        assert "feature@12345678" in text
        assert "undo" in text


class TestChatLayout:
    def test_chat_screen_has_no_sidebar_and_hud_shows_version(self):
        class FakeCaps:
            supports_thinking = True
            supports_tool_calling = False

        class FakeLLM:
            capabilities = FakeCaps()
            model = "fake-model"

        class FakeAgent:
            def __init__(self):
                self.llm_client = FakeLLM()
                self.total_usage = {"input_tokens": 0, "output_tokens": 0}
                self._on_event = None
                self._confirm = None

            @property
            def model_id(self):
                return "fake-model"

        class FakeMemory:
            def __init__(self):
                self.short_term = ShortTermMemory()
                self._on_compact = None

            def estimate_stm_tokens(self):
                return 0

        class FakeVersion:
            def status(self):
                return {
                    "branch": "feature",
                    "head": {"id": "abcdef123456"},
                    "can_undo": True,
                    "can_redo": False,
                }

        async def scenario():
            with patch("agentnexus.tui.widgets.hud.Path.cwd") as mock_cwd:
                mock_cwd.return_value.resolve.return_value = type("Resolved", (), {"__str__": lambda self: "D:/code/AgentNexus"})()
                app = AgentNexusTUI(agent=FakeAgent(), memory=FakeMemory(), version=FakeVersion())
                async with app.run_test():
                    await asyncio.sleep(0.2)
                    screen = app.screen
                    assert len(list(screen.query("#side-panel"))) == 0
                    hud_text = screen.query_one("#hud-text").content
                    hud_str = str(hud_text)
                    assert "feature@abcdef12" in hud_str
                    assert "cwd:D:/code/AgentNexus" in hud_str

        asyncio.run(scenario())
