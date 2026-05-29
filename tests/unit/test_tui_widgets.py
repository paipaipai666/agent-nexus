"""Tests for TUI widgets — pure functions and widget initialization."""

from unittest.mock import MagicMock, patch

import pytest


# ── hud._format_k ────────────────────────────────────────────────


class TestFormatK:
    def test_zero(self):
        from agentnexus.tui.widgets.hud import _format_k

        assert _format_k(0) == "0"

    def test_small_number(self):
        from agentnexus.tui.widgets.hud import _format_k

        assert _format_k(512) == "512"

    def test_thousands_one_decimal(self):
        from agentnexus.tui.widgets.hud import _format_k

        assert _format_k(1500) == "1.5k"

    def test_ten_thousands_no_decimal(self):
        from agentnexus.tui.widgets.hud import _format_k

        assert _format_k(15000) == "15k"

    def test_millions(self):
        from agentnexus.tui.widgets.hud import _format_k

        assert _format_k(1_500_000) == "1.5m"

    def test_exactly_thousand(self):
        from agentnexus.tui.widgets.hud import _format_k

        assert _format_k(1000) == "1.0k"

    def test_exactly_ten_thousand(self):
        from agentnexus.tui.widgets.hud import _format_k

        assert _format_k(10_000) == "10k"

    def test_float_input(self):
        from agentnexus.tui.widgets.hud import _format_k

        assert _format_k(1234.5) == "1.2k"

    def test_exactly_one_million(self):
        from agentnexus.tui.widgets.hud import _format_k

        assert _format_k(1_000_000) == "1.0m"


# ── message._safe ────────────────────────────────────────────────


class TestSafe:
    def test_normal_text(self):
        from agentnexus.tui.widgets.message import _safe

        result = _safe("hello world")
        assert str(result) == "hello world"

    def test_empty_string(self):
        from agentnexus.tui.widgets.message import _safe

        result = _safe("")
        assert str(result) == ""

    def test_none_input(self):
        from agentnexus.tui.widgets.message import _safe

        result = _safe(None)
        assert str(result) == ""

    def test_markup_chars_preserved(self):
        from agentnexus.tui.widgets.message import _safe

        result = _safe("[bold]text[/bold]")
        # _safe wraps in Rich Text, preventing markup parsing
        assert "[bold]" in str(result)

    def test_bracket_chars(self):
        from agentnexus.tui.widgets.message import _safe

        result = _safe("arr[0] = 1")
        assert "arr[0]" in str(result)


# ── message.render_diff_with_colors ──────────────────────────────


class TestRenderDiffWithColors:
    def test_added_lines_green(self):
        from agentnexus.tui.widgets.message import render_diff_with_colors

        result = render_diff_with_colors("+new line")
        assert "on green" in result
        assert "+new line" in result

    def test_removed_lines_red(self):
        from agentnexus.tui.widgets.message import render_diff_with_colors

        result = render_diff_with_colors("-old line")
        assert "on red" in result
        assert "-old line" in result

    def test_hunk_header_cyan(self):
        from agentnexus.tui.widgets.message import render_diff_with_colors

        result = render_diff_with_colors("@@ -1,3 +1,4 @@")
        assert "dark_cyan" in result

    def test_file_header_bold(self):
        from agentnexus.tui.widgets.message import render_diff_with_colors

        result = render_diff_with_colors("--- a/file.py")
        assert "bold" in result

        result2 = render_diff_with_colors("+++ b/file.py")
        assert "bold" in result2

    def test_context_lines_unchanged(self):
        from agentnexus.tui.widgets.message import render_diff_with_colors

        result = render_diff_with_colors(" context line")
        assert "context line" in result
        assert "on red" not in result
        assert "on green" not in result

    def test_empty_input(self):
        from agentnexus.tui.widgets.message import render_diff_with_colors

        result = render_diff_with_colors("")
        assert result == ""

    def test_multiple_lines(self):
        from agentnexus.tui.widgets.message import render_diff_with_colors

        diff = "--- a/test.py\n+++ b/test.py\n@@ -1 +1 @@\n-old\n+new\n context"
        result = render_diff_with_colors(diff)
        lines = result.split("\n")
        assert len(lines) == 6
        assert "bold" in lines[0]
        assert "on red" in lines[3]
        assert "on green" in lines[4]

    def test_rich_markup_escaped(self):
        from agentnexus.tui.widgets.message import render_diff_with_colors

        result = render_diff_with_colors("+[bold]text[/bold]")
        # Square brackets should be escaped with backslash
        assert "\\[bold\\]" in result


# ── side_panel._truncate ─────────────────────────────────────────


class TestTruncate:
    def test_short_text_unchanged(self):
        from agentnexus.tui.widgets.side_panel import _truncate

        assert _truncate("hello", 10) == "hello"

    def test_long_text_truncated(self):
        from agentnexus.tui.widgets.side_panel import _truncate

        result = _truncate("hello world this is long", 10)
        assert len(result) <= 10
        assert result.endswith("…")

    def test_exact_limit(self):
        from agentnexus.tui.widgets.side_panel import _truncate

        assert _truncate("12345", 5) == "12345"

    def test_collapses_whitespace(self):
        from agentnexus.tui.widgets.side_panel import _truncate

        result = _truncate("hello   world", 20)
        assert result == "hello world"

    def test_none_input(self):
        from agentnexus.tui.widgets.side_panel import _truncate

        assert _truncate(None, 10) == ""

    def test_empty_string(self):
        from agentnexus.tui.widgets.side_panel import _truncate

        assert _truncate("", 10) == ""

    def test_zero_limit(self):
        from agentnexus.tui.widgets.side_panel import _truncate

        result = _truncate("hello", 0)
        # limit=0 → clean[:max(0,-1)] + "…" = "" + "…" = "…"
        assert result == "…"


# ── ConfirmDialog initialization ─────────────────────────────────


class TestConfirmDialog:
    def test_init_truncates_params(self):
        from agentnexus.tui.widgets.confirm_dialog import ConfirmDialog

        long_params = "x" * 1000
        dialog = ConfirmDialog("tool", long_params, "high")
        assert len(dialog._params_summary) == 500

    def test_init_stores_risk_level(self):
        from agentnexus.tui.widgets.confirm_dialog import ConfirmDialog

        dialog = ConfirmDialog("tool", "params", "medium")
        assert dialog._risk_level == "medium"

    def test_init_stores_tool_name(self):
        from agentnexus.tui.widgets.confirm_dialog import ConfirmDialog

        dialog = ConfirmDialog("my_tool", "summary", "low")
        assert dialog._tool_name == "my_tool"


# ── InputBar messages ────────────────────────────────────────────


class TestInputBarMessages:
    def test_app_submit_has_text(self):
        from agentnexus.tui.widgets.input_bar import InputBar

        msg = InputBar.AppSubmit("hello")
        assert msg.text == "hello"

    def test_app_input_changed_has_text(self):
        from agentnexus.tui.widgets.input_bar import InputBar

        msg = InputBar.AppInputChanged("typed")
        assert msg.text == "typed"


# ── ChatMessage initialization ───────────────────────────────────


class TestChatMessage:
    def test_stores_content(self):
        from agentnexus.tui.widgets.message import ChatMessage

        msg = ChatMessage("user", "hello world")
        assert msg.content == "hello world"
        assert msg._rich_markup is False

    def test_markup_flag(self):
        from agentnexus.tui.widgets.message import ChatMessage

        msg = ChatMessage("system", "[bold]text[/bold]", markup=True)
        assert msg._rich_markup is True

    def test_role_sets_css_class(self):
        from agentnexus.tui.widgets.message import ChatMessage

        msg = ChatMessage("assistant", "reply")
        assert "assistant" in msg.classes


# ── ToolCall initialization ──────────────────────────────────────


class TestToolCall:
    def test_stores_attributes(self):
        from agentnexus.tui.widgets.message import ToolCall

        tc = ToolCall("search", result="found", duration_ms=123.4)
        assert tc.tool_name == "search"
        assert tc.result == "found"
        assert tc.duration_ms == 123.4

    def test_defaults(self):
        from agentnexus.tui.widgets.message import ToolCall

        tc = ToolCall("tool")
        assert tc.result == ""
        assert tc.duration_ms == 0


# ── SidePanel initialization ─────────────────────────────────────


class TestSidePanel:
    def test_default_state(self):
        from agentnexus.tui.widgets.side_panel import SidePanel

        panel = SidePanel()
        assert panel._version_info == ("---", False, False)
        assert panel._timeline_items == []
        assert panel._tool_items == []
        assert panel._mcp_snapshot is None
        assert panel._todo_items == []

    def test_update_version_stores_info(self):
        from agentnexus.tui.widgets.side_panel import SidePanel

        panel = SidePanel()
        panel.update_version("abc1234", True, False)
        assert panel._version_info == ("abc1234", True, False)

    def test_update_timeline_caps_at_8(self):
        from agentnexus.tui.widgets.side_panel import SidePanel

        panel = SidePanel()
        items = [{"kind": "event", "text": f"item{i}"} for i in range(20)]
        panel.update_timeline(items)
        assert len(panel._timeline_items) == 8

    def test_add_timeline_event_caps_at_8(self):
        from agentnexus.tui.widgets.side_panel import SidePanel

        panel = SidePanel()
        for i in range(20):
            panel.add_timeline_event("event", f"item{i}")
        assert len(panel._timeline_items) == 8
        assert panel._timeline_items[-1]["text"] == "item19"

    def test_update_tools_stores_items(self):
        from agentnexus.tui.widgets.side_panel import SidePanel

        panel = SidePanel()
        tools = [{"name": "search", "risk": "low"}, {"name": "write", "risk": "high"}]
        panel.update_tools(tools)
        assert len(panel._tool_items) == 2

    def test_add_tool_event_caps_at_8(self):
        from agentnexus.tui.widgets.side_panel import SidePanel

        panel = SidePanel()
        for i in range(20):
            panel.add_tool_event(f"tool{i}", "done")
        assert len(panel._tool_items) == 8

    def test_update_mcp_stores_snapshot(self):
        from agentnexus.tui.widgets.side_panel import SidePanel

        panel = SidePanel()
        snapshot = {"started": True, "connected_count": 2, "server_count": 3, "tool_count": 5, "failure_count": 0}
        panel.update_mcp(snapshot)
        assert panel._mcp_snapshot == snapshot

    def test_update_model_stores_info(self):
        from agentnexus.tui.widgets.side_panel import SidePanel

        panel = SidePanel()
        panel.update_model("gpt-4", "128k", "react")
        assert panel._model_info == {"model": "gpt-4", "ctx": "128k", "strategy": "react"}

    def test_update_skill_stores_info(self):
        from agentnexus.tui.widgets.side_panel import SidePanel

        panel = SidePanel()
        panel.update_skill(skill="coding", workflow="default", status="running")
        assert panel._skill_info["skill"] == "coding"
        assert panel._skill_info["status"] == "running"

    def test_update_skill_with_runtime(self):
        from agentnexus.tui.widgets.side_panel import SidePanel

        panel = SidePanel()
        runtime = {"status": "running", "steps": 5, "ok": 3, "errors": 1}
        panel.update_skill(runtime=runtime)
        assert panel._skill_info["runtime"]["steps"] == 5

    def test_update_skill_with_available(self):
        from agentnexus.tui.widgets.side_panel import SidePanel

        panel = SidePanel()
        available = [("id1", "Skill 1", "desc1"), ("id2", "Skill 2", "desc2")]
        panel.update_skill(available=available)
        assert len(panel._skill_info["available"]) == 2

    def test_update_todo_stores_items(self):
        from agentnexus.tui.widgets.side_panel import SidePanel

        panel = SidePanel()
        items = [{"status": "done", "description": "task 1"}, {"status": "pending", "description": "task 2"}]
        panel.update_todo(items)
        assert len(panel._todo_items) == 2

    def test_update_memory_backward_compat(self):
        from agentnexus.tui.widgets.side_panel import SidePanel

        panel = SidePanel()
        panel.update_memory(["mem1", "mem2"])
        assert "2 memories" in panel._skill_info["status"]

    def test_update_memory_empty(self):
        from agentnexus.tui.widgets.side_panel import SidePanel

        panel = SidePanel()
        panel.update_memory([])
        assert panel._skill_info["status"] == "idle"


# ── SidePanel render methods ─────────────────────────────────────


class TestSidePanelRender:
    def test_render_version_with_actions(self):
        from agentnexus.tui.widgets.side_panel import SidePanel

        panel = SidePanel()
        panel._version_info = ("abc12345def", True, True)
        result = panel._render_version()
        assert "abc12345" in result
        assert "/undo" in result
        assert "/redo" in result

    def test_render_version_no_actions(self):
        from agentnexus.tui.widgets.side_panel import SidePanel

        panel = SidePanel()
        panel._version_info = ("abc12345", False, False)
        result = panel._render_version()
        assert "abc12345" in result
        assert "/undo" not in result

    def test_render_timeline_empty(self):
        from agentnexus.tui.widgets.side_panel import SidePanel

        panel = SidePanel()
        result = panel._render_timeline()
        assert "No conversation" in result

    def test_render_timeline_with_items(self):
        from agentnexus.tui.widgets.side_panel import SidePanel

        panel = SidePanel()
        panel._timeline_items = [
            {"kind": "thought", "text": "thinking..."},
            {"kind": "tool_start", "text": "search"},
            {"kind": "error", "text": "failed"},
            {"kind": "run", "text": "running"},
            {"kind": "summary", "text": "done"},
        ]
        result = panel._render_timeline()
        assert "think" in result
        assert "tool" in result
        assert "err" in result
        assert "run" in result
        assert "turn" in result

    def test_render_tools_empty(self):
        from agentnexus.tui.widgets.side_panel import SidePanel

        panel = SidePanel()
        result = panel._render_tools()
        assert "No tools" in result

    def test_render_tools_with_items(self):
        from agentnexus.tui.widgets.side_panel import SidePanel

        panel = SidePanel()
        panel._tool_items = [
            {"name": "search", "risk": "low"},
            {"name": "write_file", "risk": "medium"},
            {"name": "delete_all", "risk": "high"},
        ]
        result = panel._render_tools()
        assert "low" in result
        assert "med" in result
        assert "high" in result
        assert "search" in result

    def test_render_model(self):
        from agentnexus.tui.widgets.side_panel import SidePanel

        panel = SidePanel()
        panel._model_info = {"model": "gpt-4", "ctx": "128k", "strategy": "react"}
        result = panel._render_model()
        assert "gpt-4" in result
        assert "128k" in result
        assert "react" in result

    def test_render_model_no_strategy(self):
        from agentnexus.tui.widgets.side_panel import SidePanel

        panel = SidePanel()
        panel._model_info = {"model": "gpt-4", "ctx": "128k", "strategy": ""}
        result = panel._render_model()
        assert "gpt-4" in result
        assert "Strategy" not in result

    def test_render_mcp_disabled(self):
        from agentnexus.tui.widgets.side_panel import SidePanel

        panel = SidePanel()
        panel._mcp_snapshot = None
        result = panel._render_mcp()
        assert "disabled" in result

    def test_render_mcp_online(self):
        from agentnexus.tui.widgets.side_panel import SidePanel

        panel = SidePanel()
        panel._mcp_snapshot = {
            "started": True,
            "connected_count": 2,
            "server_count": 3,
            "tool_count": 5,
            "failure_count": 0,
        }
        result = panel._render_mcp()
        assert "online" in result
        assert "2/3" in result
        assert "5" in result

    def test_render_mcp_with_failures(self):
        from agentnexus.tui.widgets.side_panel import SidePanel

        panel = SidePanel()
        panel._mcp_snapshot = {
            "started": True,
            "connected_count": 1,
            "server_count": 3,
            "tool_count": 2,
            "failure_count": 2,
        }
        result = panel._render_mcp()
        assert "2 fail" in result

    def test_render_skill_basic(self):
        from agentnexus.tui.widgets.side_panel import SidePanel

        panel = SidePanel()
        panel._skill_info = {"skill": "coding", "workflow": "default", "status": "running", "available": []}
        result = panel._render_skill()
        assert "coding" in result
        assert "running" in result

    def test_render_skill_with_runtime(self):
        from agentnexus.tui.widgets.side_panel import SidePanel

        panel = SidePanel()
        panel._skill_info = {
            "skill": "test",
            "workflow": "wf",
            "status": "active",
            "available": [],
            "runtime": {"status": "running", "steps": 10, "ok": 8, "errors": 1},
        }
        result = panel._render_skill()
        assert "running" in result
        assert "8/10" in result
        assert "1 err" in result

    def test_render_skill_with_available(self):
        from agentnexus.tui.widgets.side_panel import SidePanel

        panel = SidePanel()
        panel._skill_info = {
            "skill": "default",
            "workflow": "default",
            "status": "idle",
            "available": [("id1", "Skill 1", ""), ("id2", "Skill 2", "")],
        }
        result = panel._render_skill()
        assert "Available" in result
        assert "id1" in result

    def test_render_todo_empty(self):
        from agentnexus.tui.widgets.side_panel import SidePanel

        panel = SidePanel()
        result = panel._render_todo()
        assert "无任务" in result

    def test_render_todo_with_items(self):
        from agentnexus.tui.widgets.side_panel import SidePanel

        panel = SidePanel()
        panel._todo_items = [
            {"status": "done", "description": "task 1"},
            {"status": "in_progress", "description": "task 2"},
            {"status": "pending", "description": "task 3"},
        ]
        result = panel._render_todo()
        assert "✓" in result
        assert "→" in result
        assert "·" in result


# ── HUD initialization ───────────────────────────────────────────


def _make_hud_settings(model_id="test-model", base_url=""):
    settings = MagicMock()
    settings.llm_model_id = model_id
    settings.llm_base_url = base_url
    return settings


class TestHUD:
    @patch("agentnexus.tui.widgets.hud.resolve_ctx_max", return_value=128000)
    @patch("agentnexus.tui.widgets.hud.get_settings")
    def test_init_stores_model(self, mock_get_settings, mock_resolve):
        from agentnexus.tui.widgets.hud import HUD

        mock_get_settings.return_value = _make_hud_settings("deepseek/deepseek-v4-flash")
        hud = HUD()
        assert hud.model == "deepseek/deepseek-v4-flash"
        assert hud._display_model == "deepseek-v4-flash"

    @patch("agentnexus.tui.widgets.hud.resolve_ctx_max", return_value=128000)
    @patch("agentnexus.tui.widgets.hud.get_settings")
    def test_init_model_without_slash(self, mock_get_settings, mock_resolve):
        from agentnexus.tui.widgets.hud import HUD

        mock_get_settings.return_value = _make_hud_settings("gpt-4")
        hud = HUD()
        assert hud._display_model == "gpt-4"

    @patch("agentnexus.tui.widgets.hud.resolve_ctx_max", return_value=128000)
    @patch("agentnexus.tui.widgets.hud.get_settings")
    def test_update_context(self, mock_get_settings, mock_resolve):
        from agentnexus.tui.widgets.hud import HUD

        mock_get_settings.return_value = _make_hud_settings()
        hud = HUD()
        hud.update_context(50000, 100000, 20000)
        assert hud.current_tokens == 50000
        assert hud.total_input == 100000
        assert hud.total_output == 20000

    @patch("agentnexus.tui.widgets.hud.resolve_ctx_max", return_value=128000)
    @patch("agentnexus.tui.widgets.hud.get_settings")
    def test_update_tokens(self, mock_get_settings, mock_resolve):
        from agentnexus.tui.widgets.hud import HUD

        mock_get_settings.return_value = _make_hud_settings()
        hud = HUD()
        hud.update_tokens(5000, 1000)
        assert hud.total_input == 5000
        assert hud.total_output == 1000

    @patch("agentnexus.tui.widgets.hud.resolve_ctx_max", return_value=128000)
    @patch("agentnexus.tui.widgets.hud.get_settings")
    def test_set_compacting(self, mock_get_settings, mock_resolve):
        from agentnexus.tui.widgets.hud import HUD

        mock_get_settings.return_value = _make_hud_settings()
        hud = HUD()
        assert hud._compacting is False
        hud.set_compacting(True)
        assert hud._compacting is True

    @patch("agentnexus.tui.widgets.hud.resolve_ctx_max", return_value=128000)
    @patch("agentnexus.tui.widgets.hud.get_settings")
    def test_update_capabilities(self, mock_get_settings, mock_resolve):
        from agentnexus.tui.widgets.hud import HUD

        mock_get_settings.return_value = _make_hud_settings()
        hud = HUD()
        hud.update_capabilities(True, "react")
        assert hud._supports_thinking is True
        assert hud._strategy == "react"

    @patch("agentnexus.tui.widgets.hud.resolve_ctx_max", return_value=128000)
    @patch("agentnexus.tui.widgets.hud.get_settings")
    def test_update_version(self, mock_get_settings, mock_resolve):
        from agentnexus.tui.widgets.hud import HUD

        mock_get_settings.return_value = _make_hud_settings()
        hud = HUD()
        hud.update_version("abc12345", True, False)
        assert hud._head == "abc12345"
        assert hud._can_undo is True
        assert hud._can_redo is False


# ── HUD._build_text ──────────────────────────────────────────────


class TestHUDBuildText:
    @patch("agentnexus.tui.widgets.hud.resolve_ctx_max", return_value=128000)
    @patch("agentnexus.tui.widgets.hud.get_settings")
    def test_build_text_with_ctx(self, mock_get_settings, mock_resolve):
        from agentnexus.tui.widgets.hud import HUD

        mock_get_settings.return_value = _make_hud_settings("test-model")
        hud = HUD()
        hud.current_tokens = 50000
        hud.total_input = 100000
        hud.total_output = 20000
        text = hud._build_text()
        assert "test-model" in text
        assert "ctx" in text
        assert "in:" in text
        assert "out:" in text

    @patch("agentnexus.tui.widgets.hud.resolve_ctx_max", return_value=None)
    @patch("agentnexus.tui.widgets.hud.get_settings")
    def test_build_text_no_ctx_max(self, mock_get_settings, mock_resolve):
        from agentnexus.tui.widgets.hud import HUD

        mock_get_settings.return_value = _make_hud_settings()
        hud = HUD()
        hud.current_tokens = 1000
        text = hud._build_text()
        assert "ctx" in text
        assert "?" in text

    @patch("agentnexus.tui.widgets.hud.resolve_ctx_max", return_value=128000)
    @patch("agentnexus.tui.widgets.hud.get_settings")
    def test_build_text_with_thinking(self, mock_get_settings, mock_resolve):
        from agentnexus.tui.widgets.hud import HUD

        mock_get_settings.return_value = _make_hud_settings()
        hud = HUD()
        hud._supports_thinking = True
        text = hud._build_text()
        assert "🧠" in text

    @patch("agentnexus.tui.widgets.hud.resolve_ctx_max", return_value=128000)
    @patch("agentnexus.tui.widgets.hud.get_settings")
    def test_build_text_with_compacting(self, mock_get_settings, mock_resolve):
        from agentnexus.tui.widgets.hud import HUD

        mock_get_settings.return_value = _make_hud_settings()
        hud = HUD()
        hud._compacting = True
        text = hud._build_text()
        assert "compact" in text

    @patch("agentnexus.tui.widgets.hud.resolve_ctx_max", return_value=128000)
    @patch("agentnexus.tui.widgets.hud.get_settings")
    def test_build_text_with_version(self, mock_get_settings, mock_resolve):
        from agentnexus.tui.widgets.hud import HUD

        mock_get_settings.return_value = _make_hud_settings()
        hud = HUD()
        hud._head = "abc12345"
        hud._can_undo = True
        hud._can_redo = True
        text = hud._build_text()
        assert "abc12345" in text
        assert "undo" in text
        assert "redo" in text


# ── AgentNexusTUI initialization ─────────────────────────────────


class TestAgentNexusTUI:
    def test_init_stores_params(self):
        from agentnexus.tui.app import AgentNexusTUI

        agent = MagicMock()
        memory = MagicMock()
        version = MagicMock()
        mcp = MagicMock()
        skill = MagicMock()
        cap = MagicMock()

        app = AgentNexusTUI(agent, memory, version, mcp, skill_service=skill, capability_runtime=cap)
        assert app._agent is agent
        assert app._memory is memory
        assert app._version is version
        assert app._mcp_manager is mcp
        assert app._skill_service is skill
        assert app._capability_runtime is cap

    def test_init_defaults(self):
        from agentnexus.tui.app import AgentNexusTUI

        app = AgentNexusTUI(MagicMock(), MagicMock(), MagicMock())
        assert app._mcp_manager is None
        assert app._skill_service is None
        assert app._capability_runtime is None

    def test_title(self):
        from agentnexus.tui.app import AgentNexusTUI

        assert AgentNexusTUI.TITLE == "AgentNexus"

    def test_css_paths_defined(self):
        from agentnexus.tui.app import AgentNexusTUI

        assert len(AgentNexusTUI.CSS_PATH) == 7
        for path in AgentNexusTUI.CSS_PATH:
            assert path.endswith(".tcss")
