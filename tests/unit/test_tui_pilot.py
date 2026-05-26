"""Pilot-based asyncio tests for TUI widgets using Textual's app.run_test()."""

from unittest.mock import MagicMock, patch

import pytest
from textual.app import App
from textual.widgets import Button, Label, Static

pytestmark = pytest.mark.asyncio


# ── ConfirmDialog tests ──────────────────────────────────────────────


class ConfirmTestApp(App):
    """Minimal app that pushes ConfirmDialog and captures the dismiss result."""

    def __init__(self, tool_name, params, risk_level="high"):
        super().__init__()
        self._tool_name = tool_name
        self._params = params
        self._risk_level = risk_level
        self.result = None

    def on_mount(self):
        from agentnexus.tui.widgets.confirm_dialog import ConfirmDialog
        dialog = ConfirmDialog(self._tool_name, self._params, self._risk_level)
        self.push_screen(dialog, callback=lambda r: setattr(self, "result", r))


class TestConfirmDialogPilot:

    async def test_compose_buttons_present(self):
        """Both confirm and cancel buttons are rendered."""
        app = ConfirmTestApp("test_tool", "some params")
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            screen = app.screen
            confirm_btn = screen.query_one("#btn-confirm", Button)
            cancel_btn = screen.query_one("#btn-cancel", Button)
            assert confirm_btn is not None
            assert cancel_btn is not None

    async def test_click_confirm_returns_true(self):
        """Clicking the confirm button dismisses with True."""
        app = ConfirmTestApp("test_tool", "some params")
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.click("#btn-confirm")
            await pilot.pause()
            assert app.result is True

    async def test_click_cancel_returns_false(self):
        """Clicking the cancel button dismisses with False."""
        app = ConfirmTestApp("test_tool", "some params")
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.click("#btn-cancel")
            await pilot.pause()
            assert app.result is False

    async def test_press_y_returns_true(self):
        """Pressing 'y' dismisses with True."""
        app = ConfirmTestApp("test_tool", "some params")
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.press("y")
            await pilot.pause()
            assert app.result is True

    async def test_press_n_returns_false(self):
        """Pressing 'n' dismisses with False."""
        app = ConfirmTestApp("test_tool", "some params")
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.press("n")
            await pilot.pause()
            assert app.result is False

    async def test_press_escape_returns_false(self):
        """Pressing escape dismisses with False."""
        app = ConfirmTestApp("test_tool", "some params")
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert app.result is False

    async def test_shows_risk_level_medium(self):
        """Risk level label displays MEDIUM for medium risk."""
        app = ConfirmTestApp("test_tool", "some params", risk_level="medium")
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            labels = app.screen.query(Label)
            assert any("MEDIUM" in label.content for label in labels)

    async def test_shows_risk_level_low(self):
        """Risk level label displays LOW for low risk."""
        app = ConfirmTestApp("test_tool", "some params", risk_level="low")
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            labels = app.screen.query(Label)
            assert any("LOW" in label.content for label in labels)

    async def test_tool_name_visible(self):
        """Tool name appears in the dialog."""
        app = ConfirmTestApp("deploy-tool", "params")
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            labels = app.screen.query(Label)
            assert any("deploy-tool" in label.content for label in labels)

    async def test_params_summary_visible(self):
        """Params summary appears in the preview Static."""
        app = ConfirmTestApp("t", "param content here")
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            preview = app.screen.query_one("#confirm-preview", Static)
            assert "param content here" in preview.content

    async def test_title_label_present(self):
        """Title label with id confirm-title is rendered."""
        app = ConfirmTestApp("t", "p")
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            title = app.screen.query_one("#confirm-title", Label)
            assert title is not None


# ── HUD widget tests ────────────────────────────────────────────────


class HUDTestApp(App):
    """Minimal app that mounts a HUD widget."""

    def compose(self):
        from agentnexus.tui.widgets.hud import HUD
        yield HUD(id="test-hud")


class TestHUDWidgetPilot:

    async def test_compose_shows_model_name(self):
        """HUD displays the short model name from settings."""
        with patch("agentnexus.tui.widgets.hud.get_settings") as mock_settings:
            mock_settings.return_value.llm_model_id = "provider/my-model"
            app = HUDTestApp()
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                from agentnexus.tui.widgets.hud import HUD
                hud = app.query_one("#test-hud", HUD)
                assert hud._display_model == "my-model"

    async def test_update_capabilities_reflects_in_text(self):
        """Calling update_capabilities changes the rendered HUD text."""
        with patch("agentnexus.tui.widgets.hud.get_settings") as mock_settings:
            mock_settings.return_value.llm_model_id = "model"
            app = HUDTestApp()
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                from agentnexus.tui.widgets.hud import HUD
                hud = app.query_one("#test-hud", HUD)
                hud.update_capabilities(supports_thinking=True, strategy="原生工具")
                await pilot.pause()
                text = app.query_one("#hud-text", Static)
                assert "原生工具" in text.content
                assert "🧠" in text.content

    async def test_update_context_updates_display(self):
        """Calling update_context changes token display."""
        with patch("agentnexus.tui.widgets.hud.get_settings") as mock_settings:
            mock_settings.return_value.llm_model_id = "model"
            app = HUDTestApp()
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                from agentnexus.tui.widgets.hud import HUD
                hud = app.query_one("#test-hud", HUD)
                hud.update_context(current_tokens=5000)
                await pilot.pause()
                text = app.query_one("#hud-text", Static)
                assert "5.0k" in text.content

    async def test_compacting_indicator_shows(self):
        """Setting compacting flag shows the gear indicator."""
        with patch("agentnexus.tui.widgets.hud.get_settings") as mock_settings:
            mock_settings.return_value.llm_model_id = "model"
            app = HUDTestApp()
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                from agentnexus.tui.widgets.hud import HUD
                hud = app.query_one("#test-hud", HUD)
                hud.set_compacting(True)
                await pilot.pause()
                text = app.query_one("#hud-text", Static)
                assert "⚙" in text.content

    async def test_version_info_updates(self):
        """update_version changes the version segment in rendered text."""
        with patch("agentnexus.tui.widgets.hud.get_settings") as mock_settings:
            mock_settings.return_value.llm_model_id = "model"
            app = HUDTestApp()
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                from agentnexus.tui.widgets.hud import HUD
                hud = app.query_one("#test-hud", HUD)
                hud.update_version("feature", "deadbeef1234", can_undo=True, can_redo=False)
                await pilot.pause()
                text = app.query_one("#hud-text", Static)
                rendered = text.content
                assert "feature" in rendered
                assert "deadbeef" in rendered
                assert "undo" in rendered


# ── AgentNexusTUI main app tests ────────────────────────────────────


def _make_mock_version():
    v = MagicMock()
    v.status.return_value = {
        "branch": "main",
        "head": {"id": "abc123"},
        "can_undo": False,
        "can_redo": False,
    }
    return v


def _make_mock_agent():
    a = MagicMock()
    a.model_id = "v4-flash"
    a.llm_client.capabilities.supports_thinking = True
    a.llm_client.capabilities.supports_tool_calling = True
    return a


class TestAgentNexusTUIPilot:

    async def test_app_launch_sets_title(self):
        """App TITLE is AgentNexus and ChatScreen is pushed on mount."""
        with patch("agentnexus.tui.widgets.hud.get_settings") as mock_settings:
            mock_settings.return_value.llm_model_id = "n/a"
            from agentnexus.tui.app import AgentNexusTUI
            app = AgentNexusTUI(
                agent=_make_mock_agent(),
                memory=MagicMock(),
                version=_make_mock_version(),
            )
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                assert app.TITLE == "AgentNexus"
                from agentnexus.tui.screens.chat import ChatScreen
                assert isinstance(app.screen, ChatScreen)

    async def test_app_has_chat_area(self):
        """Chat area widget is present after launch."""
        with patch("agentnexus.tui.widgets.hud.get_settings") as mock_settings:
            mock_settings.return_value.llm_model_id = "n/a"
            from agentnexus.tui.app import AgentNexusTUI
            app = AgentNexusTUI(
                agent=_make_mock_agent(),
                memory=MagicMock(),
                version=_make_mock_version(),
            )
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                assert app.screen.query_one("#chat-area") is not None

    async def test_app_has_input_bar(self):
        """Input bar with chat-input is present after launch."""
        with patch("agentnexus.tui.widgets.hud.get_settings") as mock_settings:
            mock_settings.return_value.llm_model_id = "n/a"
            from agentnexus.tui.app import AgentNexusTUI
            app = AgentNexusTUI(
                agent=_make_mock_agent(),
                memory=MagicMock(),
                version=_make_mock_version(),
            )
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                from textual.containers import VerticalScroll
                from textual.widgets import Input
                chat_input = app.screen.query_one("#chat-input", Input)
                assert chat_input is not None
                assert chat_input.styles.height.value == 1
                assert app.screen.query_one("#input-area").styles.height.value == 3
                assert app.screen.query_one("#command-palette", VerticalScroll) is not None

    async def test_app_has_hud(self):
        """HUD widget is present after launch."""
        with patch("agentnexus.tui.widgets.hud.get_settings") as mock_settings:
            mock_settings.return_value.llm_model_id = "n/a"
            from agentnexus.tui.app import AgentNexusTUI
            app = AgentNexusTUI(
                agent=_make_mock_agent(),
                memory=MagicMock(),
                version=_make_mock_version(),
            )
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                from agentnexus.tui.widgets.hud import HUD
                assert app.screen.query_one("#hud", HUD) is not None

    async def test_hud_is_below_input_bar(self):
        """HUD should render after the input bar at the bottom."""
        with patch("agentnexus.tui.widgets.hud.get_settings") as mock_settings:
            mock_settings.return_value.llm_model_id = "n/a"
            from agentnexus.tui.app import AgentNexusTUI
            app = AgentNexusTUI(
                agent=_make_mock_agent(),
                memory=MagicMock(),
                version=_make_mock_version(),
            )
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                input_bar = app.screen.query_one("#input-area")
                hud = app.screen.query_one("#hud")
                assert input_bar.region.y < hud.region.y

    async def test_app_quit_action_is_disabled(self):
        """Default Textual quit shortcut must not close the TUI."""
        with patch("agentnexus.tui.widgets.hud.get_settings") as mock_settings:
            mock_settings.return_value.llm_model_id = "n/a"
            from agentnexus.tui.app import AgentNexusTUI
            app = AgentNexusTUI(
                agent=_make_mock_agent(),
                memory=MagicMock(),
                version=_make_mock_version(),
            )
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                app.notify = MagicMock()
                app.action_quit()
                assert not app._exit
                app.notify.assert_called_once()

    async def test_exit_command_closes_tui(self):
        """The supported TUI exit path is the explicit /exit command."""
        with patch("agentnexus.tui.widgets.hud.get_settings") as mock_settings:
            mock_settings.return_value.llm_model_id = "n/a"
            from agentnexus.tui.app import AgentNexusTUI
            from agentnexus.tui.widgets.input_bar import InputBar
            app = AgentNexusTUI(
                agent=_make_mock_agent(),
                memory=MagicMock(),
                version=_make_mock_version(),
            )
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                app.screen.on_input_bar_app_submit(InputBar.AppSubmit("/exit"))
                await pilot.pause()
                assert app._exit

    async def test_app_has_side_panel(self):
        """Runtime side panel is present after launch."""
        with patch("agentnexus.tui.widgets.hud.get_settings") as mock_settings:
            mock_settings.return_value.llm_model_id = "n/a"
            from agentnexus.tui.app import AgentNexusTUI
            app = AgentNexusTUI(
                agent=_make_mock_agent(),
                memory=MagicMock(),
                version=_make_mock_version(),
            )
            async with app.run_test(size=(100, 24)) as pilot:
                await pilot.pause()
                from agentnexus.tui.widgets.side_panel import SidePanel
                panel = app.screen.query_one("#side-panel", SidePanel)
                assert "default" in panel._render_skill()
                assert "mock" in panel._render_model() or "v4-flash" in panel._render_model()

    async def test_app_renders_top_bar(self):
        """Top bar is rendered with welcome message."""
        with patch("agentnexus.tui.widgets.hud.get_settings") as mock_settings:
            mock_settings.return_value.llm_model_id = "n/a"
            from agentnexus.tui.app import AgentNexusTUI
            app = AgentNexusTUI(
                agent=_make_mock_agent(),
                memory=MagicMock(),
                version=_make_mock_version(),
            )
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                top = app.screen.query_one("#top-bar", Static)
                assert "AgentNexus" in top.content
