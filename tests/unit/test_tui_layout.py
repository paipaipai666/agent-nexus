"""Responsive terminal layouts verified together with their input workflows."""

import xml.etree.ElementTree as ET
from sqlite3 import OperationalError

import pytest
from rich.cells import cell_len

from agentnexus.tui.app import AgentNexusTUI
from agentnexus.tui.widgets.input_bar import Composer
from tests.unit.test_tui_workspace import settle
from tests.unit.test_tui_workspace import workspace_runtime as workspace_runtime


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(60, 20), (80, 24), (120, 32)])
async def test_task_status_shares_the_three_row_composer(workspace_runtime, size):
    runtime, _ = workspace_runtime
    child = runtime.chat._subagents.register("workspace", "parent", "交互检查", "reviewer", "检查按键路径")
    runtime.chat._subagents.update(child, status="waiting", waiting_questions=1)
    app = AgentNexusTUI(runtime=runtime)
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        assert app.screen.query_one("#input-area").region.height == 3
        composer = app.screen.query_one("#input-area")
        assert composer.styles.border_top[0] == "solid"
        assert app.screen.query_one("#input-row").region.y == composer.region.y + 1
        assert not app.screen.query("#composer-actions")
        assert app.screen.query_one("#chat-area").region.height >= size[1] - 4
        activity = app.screen.query_one("#agent-activity")
        assert "待答复 1" in str(activity.render())
        assert activity.region.right <= app.screen.query_one("#composer-hints").region.x
        assert "F3 任务" in str(app.screen.query_one("#composer-hints").render())
        assert "F1 帮助" in str(app.screen.query_one("#composer-hints").render())


@pytest.mark.asyncio
async def test_clicking_the_send_hint_submits_the_same_task(workspace_runtime):
    runtime, _ = workspace_runtime
    app = AgentNexusTUI(runtime=runtime)
    async with app.run_test(size=(80, 24)) as pilot:
        app.screen.query_one(Composer).value = "从底栏发送任务"
        await pilot.pause()
        await pilot.click("#composer-hints", offset=(3, 0))
        await settle(pilot, lambda: runtime.chat.get_session_recovery("workspace") is not None and not runtime.chat.is_processing)
        assert runtime.chat.get_session_recovery("workspace")["question"] == "从底栏发送任务"
        assert runtime.agent.run.call_count == 1


@pytest.mark.asyncio
async def test_model_summary_and_activity_are_pointer_shortcuts(workspace_runtime):
    runtime, _ = workspace_runtime
    app = AgentNexusTUI(runtime=runtime)
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause()
        assert "test-model" in app.screen.query_one("#hud-text").content
        await pilot.click("#hud-text")
        await settle(pilot, lambda: bool(app.screen.query("#palette-search")))
        await pilot.press("escape")
        await pilot.click("#agent-activity")
        assert app.screen.query_one("#task-detail")
        await pilot.press("escape")
        assert app.screen.query_one(Composer).has_focus


@pytest.mark.asyncio
async def test_action_picker_keeps_the_task_visible_above_the_choices(workspace_runtime):
    runtime, _ = workspace_runtime
    runtime.memory_manager.short_term.append("user", "保留当前任务上下文")
    app = AgentNexusTUI(runtime=runtime)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("ctrl+p")
        rendered = "".join(ET.fromstring(app.export_screenshot()).itertext()).replace("\xa0", " ")
        assert "保留当前任务上下文" in rendered
        assert "› 会话 · 新建" in rendered
        await pilot.press("escape")
        assert app.screen.query_one(Composer).has_focus


@pytest.mark.asyncio
@pytest.mark.parametrize("width", [60, 120, 180])
async def test_task_details_keep_a_single_column(workspace_runtime, width):
    runtime, _ = workspace_runtime
    child = runtime.chat._subagents.register("workspace", "parent", "检查任务", "reviewer", "完整任务说明")
    runtime.chat._subagents.update(child, status="thinking")
    app = AgentNexusTUI(runtime=runtime)
    async with app.run_test(size=(width, 32)) as pilot:
        await pilot.press("f3")
        tasks, detail = app.screen.query_one("#child-list"), app.screen.query_one("#child-detail")
        assert tasks.region.x == detail.region.x
        assert tasks.region.width == detail.region.width
        assert tasks.region.bottom <= detail.region.y
        assert "完整任务说明" in detail.text


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [OSError, OperationalError])
async def test_unavailable_task_data_keeps_navigation_and_disables_mutations(workspace_runtime, monkeypatch, error_type):
    runtime, _ = workspace_runtime

    def unavailable(_sid):
        raise error_type("任务数据暂不可读取")

    monkeypatch.setattr(runtime.chat, "get_session_recovery", unavailable)
    app = AgentNexusTUI(runtime=runtime)
    async with app.run_test(size=(60, 20)) as pilot:
        await pilot.press("f3", "r", "f1")
        assert "任务数据暂不可读取" in app.screen.query_one("#record-content").text
        await pilot.press("escape", "escape", "ctrl+p")
        assert app.screen.query_one("#palette-search").has_focus


@pytest.mark.asyncio
async def test_compact_controls_keep_a_visible_content_row(workspace_runtime):
    runtime, _ = workspace_runtime
    app = AgentNexusTUI(runtime=runtime)
    async with app.run_test(size=(60, 20)) as pilot:
        await pilot.press("ctrl+p")
        assert app.screen.query_one("#palette-search").content_region.height == 1
        await pilot.press("escape", "f3")
        button = app.screen.query_one("#task-record")
        assert button.content_region.height >= 1
        assert button.region.bottom <= app.screen.query_one("#task-controls").region.bottom


@pytest.mark.asyncio
async def test_task_updates_preserve_the_readers_position(workspace_runtime):
    runtime, _ = workspace_runtime
    child = runtime.chat._subagents.register("workspace", "parent", "检查日志", "reviewer", "任务详情\n" * 80)
    runtime.chat._subagents.update(child, status="thinking", latest_progress="初始进度")
    app = AgentNexusTUI(runtime=runtime)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("f3", "tab", "pagedown", "pagedown")
        detail = app.screen.query_one("#child-detail")
        before = detail.scroll_y
        assert before > 0
        runtime.chat._subagents.update(child, latest_progress="后续进度")
        await settle(pilot, lambda: "后续进度" in detail.text)
        await pilot.pause()
        assert detail.scroll_y == before


@pytest.mark.asyncio
async def test_draft_io_failure_keeps_input_and_command_navigation_usable(workspace_runtime, monkeypatch):
    import os
    from pathlib import Path

    runtime, _ = workspace_runtime
    replace = os.replace

    def unavailable(source, target):
        if Path(target).parent.name == "tui-drafts":
            raise PermissionError("草稿目录暂不可写")
        return replace(source, target)

    app = AgentNexusTUI(runtime=runtime)
    async with app.run_test(size=(80, 24)) as pilot:
        monkeypatch.setattr(os, "replace", unavailable)
        await pilot.press("保", "留", "草", "稿", "ctrl+p", "escape")
        assert app.screen.query_one(Composer).text == "保留草稿"
        await pilot.press("enter")
        await settle(pilot, lambda: runtime.chat.get_session_recovery("workspace") is not None and not runtime.chat.is_processing)
        assert runtime.chat.get_session_recovery("workspace")["question"] == "保留草稿"


@pytest.mark.asyncio
@pytest.mark.parametrize("width", [60, 80])
async def test_record_full_text_hint_stays_clickable_at_normal_terminal_widths(workspace_runtime, width):
    runtime, _ = workspace_runtime
    runtime.memory_manager.short_term.append("assistant", "可复制的完整结果")
    app = AgentNexusTUI(runtime=runtime)
    async with app.run_test(size=(width, 24)) as pilot:
        await pilot.press("tab")
        hints = str(app.screen.query_one("#composer-hints").render())
        assert "D 全文" in hints
        offset = max(0, app.screen.query_one("#composer-hints").size.width - cell_len(hints)) + cell_len(hints.partition("D 全文")[0]) + 1
        await pilot.click("#composer-hints", offset=(offset, 0))
        assert app.screen.query_one("#record-content").text == "可复制的完整结果"
