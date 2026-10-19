"""Terminal workflows reached through real keyboard input."""

import threading
from types import SimpleNamespace

import pytest

from agentnexus.tui.app import AgentNexusTUI
from agentnexus.tui.widgets.input_bar import Composer
from agentnexus.tui.widgets.message import ChatMessage
from tests.unit.test_tui_workspace import settle
from tests.unit.test_tui_workspace import workspace_runtime as workspace_runtime


@pytest.mark.asyncio
async def test_copying_selected_draft_does_not_stop_a_task(workspace_runtime):
    runtime, _ = workspace_runtime
    app = AgentNexusTUI(runtime=runtime)
    async with app.run_test(size=(80, 24)) as pilot:
        composer = app.screen.query_one(Composer)
        composer.value = "需要复制的草稿"
        await pilot.press("home", "shift+end", "ctrl+c")
        assert app.clipboard == "需要复制的草稿"
        assert not app.screen.query(ChatMessage)
        await pilot.press("end", "ctrl+c")
        assert composer.text == "需要复制的草稿"
        assert not app.screen.query(ChatMessage)


@pytest.mark.asyncio
@pytest.mark.parametrize("history_size", [0, 10])
async def test_tab_cycles_three_regions_independent_of_history(workspace_runtime, history_size):
    runtime, _ = workspace_runtime
    for _ in range(history_size):
        runtime.memory_manager.short_term.append("system", "[思考过程] 检查操作路径。")
        runtime.memory_manager.short_term.append("tool", "工具的完整结果")
    if history_size:
        runtime.memory_manager.short_term.append("assistant", "已完成路径检查。")
    app = AgentNexusTUI(runtime=runtime)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("tab")
        assert app.focused.id == "chat-area"
        if history_size:
            await pilot.press("enter")
            assert app.screen.query_one("#record-content").text == "已完成路径检查。"
            await pilot.press("escape")
            assert app.focused.id == "chat-area"
        await pilot.press("tab")
        assert app.focused.id == "choose-model"
        await pilot.press("right")
        assert app.focused.id == "choose-thinking"
        await pilot.press("tab")
        assert app.focused.id == "chat-input"
        await pilot.press("shift+tab")
        assert app.focused.id == "choose-thinking"
        await pilot.press("escape")
        assert app.focused.id == "chat-input"


@pytest.mark.asyncio
async def test_page_keys_read_history_without_moving_the_draft(workspace_runtime):
    runtime, _ = workspace_runtime
    runtime.memory_manager.short_term.append("assistant", "\n\n".join(f"历史段落 {i}" for i in range(60)))
    app = AgentNexusTUI(runtime=runtime)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        area = app.screen.query_one("#chat-area")
        composer = app.screen.query_one(Composer)
        composer.value = "保留第一行\n保留第二行"
        await pilot.pause()
        before = area.scroll_y
        assert before > 0
        await pilot.press("pageup")
        assert area.scroll_y < before
        assert composer.text == "保留第一行\n保留第二行"
        await pilot.press("ctrl+end")
        await pilot.pause()
        assert area.scroll_y == area.max_scroll_y
        assert composer.has_focus


@pytest.mark.asyncio
async def test_context_help_and_direct_action_restore_the_same_draft(workspace_runtime):
    runtime, _ = workspace_runtime
    app = AgentNexusTUI(runtime=runtime)
    async with app.run_test(size=(80, 24)) as pilot:
        composer = app.screen.query_one(Composer)
        composer.value = "继续处理这个任务"
        await pilot.press("f1")
        help_text = app.screen.query_one("#record-content").text
        assert "Alt+M" in help_text
        assert "PageUp" in help_text
        await pilot.press("escape", "alt+t")
        assert app.screen.query_one("#palette-search").has_focus
        await pilot.press("escape")
        assert composer.has_focus
        assert composer.text == "继续处理这个任务"


@pytest.mark.asyncio
async def test_escape_returns_to_parent_menu_with_filter_and_selection(workspace_runtime):
    runtime, _ = workspace_runtime
    app = AgentNexusTUI(runtime=runtime)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("ctrl+p", "资", "料", "enter")
        assert "资料与检索" in str(app.screen.query_one("Label").render())
        await pilot.press("escape")
        assert app.screen.query_one("#palette-search").value == "资料"
        await pilot.press("escape")
        assert app.focused.id == "chat-input"


@pytest.mark.asyncio
async def test_ctrl_c_copies_selection_then_stops_the_active_run(workspace_runtime):
    runtime, _ = workspace_runtime
    release = threading.Event()
    started = threading.Event()

    def execute(*_args, **_kwargs):
        started.set()
        release.wait(8)
        return SimpleNamespace(answer="结束")

    runtime.agent.run.side_effect = execute
    app = AgentNexusTUI(runtime=runtime)
    async with app.run_test(size=(80, 24)) as pilot:
        composer = app.screen.query_one(Composer)
        composer.value = "执行任务"
        await pilot.press("enter")
        try:
            await settle(pilot, started.is_set)
            composer.value = "复制这段文字"
            await pilot.press("home", "shift+end", "ctrl+c")
            assert app.clipboard == "复制这段文字"
            assert not any("已请求停止当前任务" in item.content for item in app.screen.query(ChatMessage))
            await pilot.press("end", "ctrl+c")
            assert any("已请求停止当前任务" in item.content for item in app.screen.query(ChatMessage))
            assert composer.text == "复制这段文字"
        finally:
            release.set()
        await settle(pilot, lambda: not runtime.chat.is_processing)
        assert runtime.chat.get_session_recovery("workspace")["status"] == "interrupted"
