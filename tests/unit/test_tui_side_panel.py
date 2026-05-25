"""Pure method tests for SidePanel widget rendering methods."""

from agentnexus.tui.widgets.side_panel import SidePanel


class TestSidePanelRendering:
    def test_render_version_default(self):
        panel = SidePanel()
        text = panel._render_version()
        assert "main" in text
        assert "---" in text

    def test_render_version_with_undo_redo(self):
        panel = SidePanel()
        panel._version_info = ("feature", "abc123456789", True, True)
        text = panel._render_version()
        assert "feature" in text
        assert "abc12345" in text
        assert "/undo" in text
        assert "/redo" in text

    def test_render_timeline_empty(self):
        panel = SidePanel()
        assert "No conversation summary" in panel._render_timeline()

    def test_render_timeline_keeps_last_eight(self):
        panel = SidePanel()
        panel.update_timeline([{"kind": "thought", "text": f"event {i}"} for i in range(10)])
        text = panel._render_timeline()
        assert "event 9" in text
        assert "event 2" in text
        assert "event 1" not in text

    def test_add_timeline_event(self):
        panel = SidePanel()
        panel.add_timeline_event("summary", "round one used search and answered")
        text = panel._render_timeline()
        assert "turn" in text
        assert "round one" in text

    def test_render_tools_empty(self):
        panel = SidePanel()
        assert "No tools registered" in panel._render_tools()

    def test_render_tools_as_available_tools(self):
        panel = SidePanel()
        panel.update_tools([
            {"name": "web_search", "risk": "low"},
            {"name": "file_write", "risk": "medium"},
            {"name": "shell_exec", "risk": "high"},
        ])
        text = panel._render_tools()
        assert "web_search" in text
        assert "file_write" in text
        assert "shell_exec" in text
        assert "low" in text
        assert "med" in text
        assert "high" in text

    def test_render_model_info(self):
        panel = SidePanel()
        panel.update_model("deepseek-v4-flash", "262k", "原生工具")
        text = panel._render_model()
        assert "deepseek-v4-flash" in text
        assert "262k" in text
        assert "原生工具" in text

    def test_render_mcp_disabled(self):
        panel = SidePanel()
        assert "disabled" in panel._render_mcp()

    def test_render_mcp_snapshot(self):
        panel = SidePanel()
        panel.update_mcp({
            "started": True,
            "connected_count": 2,
            "server_count": 3,
            "tool_count": 9,
            "failure_count": 1,
        })
        text = panel._render_mcp()
        assert "online" in text
        assert "2/3" in text
        assert "9" in text
        assert "1 fail" in text

    def test_render_skill_default(self):
        panel = SidePanel()
        text = panel._render_skill()
        assert "Skill" in text
        assert "default" in text
        assert "Workflow" in text

    def test_update_skill_sets_state(self):
        panel = SidePanel()
        panel.update_skill("review", "code_review", "active")
        assert panel._skill_info == {
            "skill": "review",
            "workflow": "code_review",
            "status": "active",
        }

    def test_update_memory_backward_compatibility(self):
        panel = SidePanel()
        panel.update_memory(["a", "b"])
        assert panel._skill_info["status"] == "2 memories"
