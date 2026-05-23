"""Pure method tests for SidePanel widget rendering methods."""

from agentnexus.tui.widgets.side_panel import SidePanel


class TestSidePanelRendering:
    def test_render_version_default(self):
        """Default version info shows 'main' branch and em-dash HEAD."""
        panel = SidePanel()
        text = panel._render_version()
        assert "分支:" in text
        assert "main" in text
        assert "—" in text

    def test_render_version_with_undo_redo(self):
        """Version display shows undo/redo actions when both are available."""
        panel = SidePanel()
        panel._version_info = ("feature", "abc1234", True, True)
        text = panel._render_version()
        assert "feature" in text
        assert "abc1234" in text
        assert "/undo" in text
        assert "/redo" in text

    def test_render_version_with_undo_only(self):
        """Version display shows only undo when only undo is available."""
        panel = SidePanel()
        panel._version_info = ("main", "abc1234", True, False)
        text = panel._render_version()
        assert "/undo" in text
        assert "/redo" not in text

    def test_render_version_with_redo_only(self):
        """Version display shows only redo when only redo is available."""
        panel = SidePanel()
        panel._version_info = ("main", "abc1234", False, True)
        text = panel._render_version()
        assert "/redo" in text
        assert "/undo" not in text

    def test_render_version_no_actions(self):
        """No action labels when both undo and redo are unavailable."""
        panel = SidePanel()
        panel._version_info = ("main", "abc1234", False, False)
        text = panel._render_version()
        assert "/undo" not in text
        assert "/redo" not in text

    def test_render_memory_empty(self):
        """Empty memory shows placeholder text."""
        panel = SidePanel()
        assert panel._render_memory() == "[dim]暂无记忆[/]"

    def test_render_memory_with_items(self):
        """Only the first 5 items are rendered."""
        panel = SidePanel()
        panel._ltm_items = ["item1", "item2", "item3", "item4", "item5", "item6"]
        text = panel._render_memory()
        assert "item1" in text
        assert "item5" in text
        assert "item6" not in text

    def test_render_memory_single_item(self):
        """Single item renders correctly."""
        panel = SidePanel()
        panel._ltm_items = ["唯一记忆"]
        text = panel._render_memory()
        assert "唯一记忆" in text
        assert "·" in text

    def test_render_tools_empty(self):
        """Empty tool list shows placeholder text."""
        panel = SidePanel()
        assert panel._render_tools() == "[dim]暂无调用[/]"

    def test_render_tools_with_items(self):
        """Tool items show name, status mark, and duration."""
        panel = SidePanel()
        panel._tool_items = [
            {"name": "web_search", "ok": True, "ms": 150.0},
            {"name": "file_read", "ok": False, "ms": 200.0},
        ]
        text = panel._render_tools()
        assert "web_search" in text
        assert "file_read" in text
        assert "150ms" in text
        assert "200ms" in text
        assert "✓" in text
        assert "✗" in text

    def test_render_tools_only_last_five(self):
        """Only the last 5 tool items are displayed."""
        panel = SidePanel()
        panel._tool_items = [
            {"name": f"tool_{i}", "ok": True, "ms": float(i * 10)}
            for i in range(10)
        ]
        text = panel._render_tools()
        assert "tool_9" in text
        assert "tool_5" in text
        assert "tool_4" not in text  # item index 4 is the 5th item from start

    def test_update_version_sets_state(self):
        """update_version stores values in _version_info."""
        panel = SidePanel()
        panel.update_version("new-branch", "new-head", True, False)
        assert panel._version_info == ("new-branch", "new-head", True, False)

    def test_update_memory_sets_state(self):
        """update_memory stores items in _ltm_items."""
        panel = SidePanel()
        panel.update_memory(["a", "b"])
        assert panel._ltm_items == ["a", "b"]

    def test_update_tools_sets_state(self):
        """update_tools stores items in _tool_items."""
        panel = SidePanel()
        panel.update_tools([{"name": "search", "ok": True, "ms": 100}])
        assert panel._tool_items == [{"name": "search", "ok": True, "ms": 100}]
