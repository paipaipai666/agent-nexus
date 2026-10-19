"""Terminal colors, transcript flow and color-independent navigation."""

import pytest
from rich.color import ColorType

from agentnexus.tui.app import AgentNexusTUI
from agentnexus.tui.widgets.input_bar import Composer
from agentnexus.tui.widgets.message import ReasoningBlock
from tests.unit.test_tui_workspace import workspace_runtime as workspace_runtime


@pytest.mark.asyncio
@pytest.mark.parametrize("theme", [None, "ansi-light"])
async def test_terminal_colors_and_prompt_follow_host_defaults(workspace_runtime, monkeypatch, theme):
    monkeypatch.delenv("NO_COLOR", raising=False)
    runtime, _ = workspace_runtime
    runtime.memory_manager.short_term.append("user", "保留 [方括号] 和缩进\n```python\n  x = 1\n```")
    runtime.memory_manager.short_term.append("assistant", "正文回答\n\n```python\nprint('hello')\n```")
    app = AgentNexusTUI(runtime=runtime)
    if theme:
        app.theme = theme
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        assert app.current_theme.ansi
        assert app.native_ansi_color
        assert app.screen.styles.background.ansi == -1
        rendered = [segment for strip in app.screen._compositor.render_strips() for segment in strip]
        text = "".join(segment.text for segment in rendered)
        assert "›" in text
        assert "保留 [方括号] 和缩进" in text
        assert "```python" in text
        assert "  x = 1" in text
        assert "正文回答" in text
        assert not app.screen.query(".user Label")
        assert all(color.type in {ColorType.DEFAULT, ColorType.STANDARD} for segment in rendered if segment.style for color in (segment.style.color, segment.style.bgcolor) if color)


@pytest.mark.asyncio
async def test_long_reasoning_uses_the_transcript_scroll(workspace_runtime):
    runtime, _ = workspace_runtime
    runtime.memory_manager.short_term.append("user", "检查完整思考")
    runtime.memory_manager.short_term.append("system", "[思考过程] " + "\n".join(f"推理步骤 {i}" for i in range(30)))
    app = AgentNexusTUI(runtime=runtime)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        reasoning = app.screen.query_one(ReasoningBlock)
        body = reasoning.query_one(".reasoning-content")
        assert body.region.height == 30
        assert body.max_scroll_y == 0
        area = app.screen.query_one("#chat-area")
        await pilot.press("pageup")
        assert area.scroll_y < area.max_scroll_y
        await pilot.press("ctrl+end", "tab", "enter")
        assert not body.display
        await pilot.press("enter")
        assert body.display
        assert app.screen.query_one(Composer).text == ""


@pytest.mark.asyncio
async def test_no_color_keeps_selection_and_activity_readable(workspace_runtime, monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    runtime, _ = workspace_runtime
    app = AgentNexusTUI(runtime=runtime)
    async with app.run_test(size=(60, 20)) as pilot:
        await pilot.press("ctrl+p", "down")
        rendered = [segment for strip in app.screen._compositor.render_strips() for segment in strip]
        text = "".join(segment.text for segment in rendered)
        assert "› 会话 · 切换" in text
        assert all(color.is_default for segment in rendered if segment.style for color in (segment.style.color, segment.style.bgcolor) if color)
        await pilot.press("escape")
        assert "[ ] 空闲" in str(app.screen.query_one("#agent-activity").render())
