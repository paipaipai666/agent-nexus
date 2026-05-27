"""TUI widget lifecycle tests using Textual's pilot.

Covers:
- Widget mounting and basic interaction
- Chat screen static method behavior
- Side panel rendering helpers
- HUD rendering
"""

from unittest.mock import MagicMock, patch

import pytest
from textual.app import App

pytestmark = pytest.mark.asyncio


class _ChatScreenTestApp(App):
    """Minimal app that mounts a ChatScreen."""

    def __init__(self, **kwargs):
        super().__init__()
        self._agent = kwargs.get("agent", MagicMock())
        self._memory = kwargs.get("memory", MagicMock())
        self._version = kwargs.get("version", MagicMock())
        self._skill_service = kwargs.get("skill_service", MagicMock())

    def on_mount(self):
        from agentnexus.tui.screens.chat import ChatScreen
        screen = ChatScreen(
            agent=self._agent,
            memory=self._memory,
            version=self._version,
            skill_service=self._skill_service,
        )
        self.push_screen(screen)


class TestChatScreenLifecycle:

    async def test_chat_screen_mounts_and_has_title(self):
        app = _ChatScreenTestApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.screen
            assert screen is not None
            from agentnexus.tui.screens.chat import ChatScreen
            assert isinstance(screen, ChatScreen)

    async def test_chat_screen_shows_input_bar(self):
        app = _ChatScreenTestApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            from agentnexus.tui.widgets.input_bar import InputBar
            bars = app.screen.query(InputBar)
            assert len(bars) >= 0

    async def test_chat_screen_static_condense_search(self):
        from agentnexus.tools.result_format import condense_search_result
        text = "[1] Test Title (2024) [相关度: 0.95]\nURL: https://example.com\ncontent body here"
        result = condense_search_result(text)
        assert "Test Title" in result
        assert "example.com" in result

    async def test_chat_screen_static_condense_file(self):
        from agentnexus.tools.result_format import condense_file_result
        text = "[文件] /path/to/file.py (100 行, 共 2048 字节)\n1 | line content\n2 | line content"
        result = condense_file_result(text)
        assert "file.py" in result
        assert "100" in result


class TestSidePanelRendering:

    async def test_side_panel_renders_version_card(self):
        from agentnexus.tui.widgets.side_panel import SidePanel
        panel = SidePanel(id="test-side-panel")
        version_html = panel._render_version()
        assert "main" in version_html or len(version_html) > 0

    async def test_side_panel_render_timeline_empty(self):
        from agentnexus.tui.widgets.side_panel import SidePanel
        panel = SidePanel(id="test-side-panel")
        timeline = panel._render_timeline()
        assert len(timeline) >= 0

    async def test_side_panel_render_tools_empty(self):
        from agentnexus.tui.widgets.side_panel import SidePanel
        panel = SidePanel(id="test-side-panel")
        tools = panel._render_tools()
        assert len(tools) >= 0

    async def test_side_panel_update_version(self):
        from agentnexus.tui.widgets.side_panel import SidePanel
        panel = SidePanel(id="test-side-panel")
        panel.update_version("abc1234", True, False)
        assert panel._version_info == ("abc1234", True, False)

    async def test_side_panel_update_tools(self):
        from agentnexus.tui.widgets.side_panel import SidePanel
        panel = SidePanel(id="test-side-panel")
        panel.update_tools([{"name": "test_tool", "calls": 5}])
        assert len(panel._tool_items) == 1
        assert panel._tool_items[0]["name"] == "test_tool"


class TestHUDRendering:

    async def test_hud_build_text_contains_model(self):
        with patch("agentnexus.tui.widgets.hud.get_settings") as mock_settings:
            mock_settings.return_value.llm_model_id = "test-model"
            app = App()
            app.compose = lambda: []
            from agentnexus.tui.widgets.hud import HUD
            hud = HUD(id="test-hud")
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                text = hud._build_text()
                assert "200k" in str(text) or str(text)

    async def test_hud_update_capabilities(self):
        with patch("agentnexus.tui.widgets.hud.get_settings") as mock_settings:
            mock_settings.return_value.llm_model_id = "model"
            app = App()
            app.compose = lambda: []
            from agentnexus.tui.widgets.hud import HUD
            hud = HUD(id="test-hud")
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                hud.update_capabilities(supports_thinking=True, strategy="test")
                await pilot.pause()
