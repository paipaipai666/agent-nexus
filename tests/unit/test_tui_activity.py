"""Session activity follows real execution while the user edits and navigates."""

import threading
from types import SimpleNamespace

import pytest
from textual.app import App
from textual.widgets import Tooltip

from agentnexus.agents.react_types import ReActEvent, ReActEventType
from agentnexus.tui.activity import Activity
from agentnexus.tui.app import AgentNexusTUI
from agentnexus.tui.widgets.activity import ActivityStatus
from agentnexus.tui.widgets.input_bar import Composer
from tests.unit.test_tui_workspace import settle
from tests.unit.test_tui_workspace import workspace_runtime as workspace_runtime


def activity_text(app):
    return str(app.screen.query_one("#agent-activity").render())


@pytest.mark.asyncio
@pytest.mark.parametrize("activity, expected", [
    (Activity("working", "准备"), "[/] 工作中 · 准备"),
    (Activity("working", "读取 [source].py", waiting=1, queued=2), "[/] 工作中 · 待答复 1 · 排队 2 · 读取 [source].py"),
    (Activity(), "[ ] 空闲"),
    (Activity("waiting", waiting=1), "[?] 等待回复 · 待答复 1"),
    (Activity("confirming"), "[?] 等待确认"),
    (Activity("unavailable", "状态[/]不可读取"), "[!] 状态不可用 · 状态[/]不可读取"),
])
async def test_activity_tooltip_preserves_literal_status_text(activity, expected):
    class ActivityApp(App):
        TOOLTIP_DELAY = 0.01

        def compose(self):
            yield ActivityStatus(id="status")

    app = ActivityApp()
    async with app.run_test(size=(80, 24), tooltips=True) as pilot:
        app.query_one(ActivityStatus).update_activity(activity)
        await pilot.hover("#status", offset=(1, 0))
        await settle(pilot, lambda: app.screen.query_one(Tooltip).display)
        assert str(app.screen.query_one(Tooltip).render()) == expected


@pytest.mark.asyncio
async def test_hovering_working_status_keeps_submitted_task_running(workspace_runtime):
    runtime, _ = workspace_runtime
    started, release = threading.Event(), threading.Event()

    def execute(*_args, **_kwargs):
        started.set()
        release.wait(10)
        return SimpleNamespace(answer="悬停后任务正常完成")

    runtime.agent.run.side_effect = execute
    app = AgentNexusTUI(runtime=runtime)
    app.TOOLTIP_DELAY = 0.01
    try:
        async with app.run_test(size=(80, 24), tooltips=True) as pilot:
            app.screen.query_one(Composer).value = "验证状态提示"
            await pilot.press("enter")
            await settle(pilot, started.is_set)
            await settle(pilot, lambda: "工作中" in activity_text(app))
            await pilot.hover("#agent-activity", offset=(1, 0))
            await settle(pilot, lambda: app.screen.query_one(Tooltip).display)
            assert str(app.screen.query_one(Tooltip).render()) == "[/] 工作中 · 准备"
            release.set()
            await settle(pilot, lambda: "空闲" in activity_text(app))
            assert runtime.chat.get_session_recovery("workspace")["answer"] == "悬停后任务正常完成"
    finally:
        release.set()


@pytest.mark.asyncio
async def test_inspector_tooltip_preserves_literal_storage_error(workspace_runtime, monkeypatch):
    runtime, _ = workspace_runtime

    def unavailable(_sid):
        raise OSError("无法读取 [/] source")

    monkeypatch.setattr(runtime.chat, "list_subagents", unavailable)
    app = AgentNexusTUI(runtime=runtime)
    app.TOOLTIP_DELAY = 0.01
    async with app.run_test(size=(80, 24), tooltips=True) as pilot:
        app.screen.action_task_record()
        await settle(pilot, lambda: bool(app.screen.query("#child-reply")))
        await pilot.hover("#child-reply", offset=(1, 0))
        await settle(pilot, lambda: app.screen.query_one(Tooltip).display)
        assert str(app.screen.query_one(Tooltip).render()) == "任务状态不可用 · 无法读取 [/] source"
        await pilot.press("escape")
        assert app.screen.query_one(Composer).has_focus


@pytest.mark.asyncio
async def test_child_work_and_pending_questions_are_not_reported_as_idle(workspace_runtime):
    runtime, _ = workspace_runtime
    waiting = runtime.chat._subagents.register("workspace", "parent", "验证", "reviewer", "检查结果")
    worker = runtime.chat._subagents.register("workspace", "parent", "检查", "reviewer", "检查界面")
    runtime.chat._subagents.update(waiting, status="waiting", waiting_questions=1)
    app = AgentNexusTUI(runtime=runtime)
    async with app.run_test(size=(60, 20)) as pilot:
        assert "工作中" in activity_text(app)
        assert "待答复 1" in activity_text(app)
        runtime.chat._subagents.update(worker, status="completed")
        await settle(pilot, lambda: "等待回复" in activity_text(app))
        runtime.chat._subagents.update(waiting, status="completed", waiting_questions=0)
        await settle(pilot, lambda: "空闲" in activity_text(app))


@pytest.mark.asyncio
async def test_concurrent_tools_keep_the_remaining_tool_visible(workspace_runtime):
    runtime, _ = workspace_runtime
    one_finished, release = threading.Event(), threading.Event()

    def execute(*_args, **_kwargs):
        for identity, name in [("read", "file_read"), ("shell", "shell_exec")]:
            runtime.agent.event_handler(ReActEvent(ReActEventType.TOOL_START, {"id": identity, "name": name}), None, None)
        runtime.agent.event_handler(ReActEvent(ReActEventType.TOOL_DONE, {"id": "read", "name": "file_read", "result": "source"}), None, None)
        one_finished.set()
        release.wait(10)
        return SimpleNamespace(answer="所有工具已结束")

    runtime.agent.run.side_effect = execute
    app = AgentNexusTUI(runtime=runtime)
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            app.screen.query_one(Composer).value = "并行检查"
            await pilot.press("enter")
            await settle(pilot, one_finished.is_set)
            await settle(pilot, lambda: "运行命令" in activity_text(app))
            await pilot.press("ctrl+c")
            await settle(pilot, lambda: "正在停止" in activity_text(app))
            assert "空闲" not in activity_text(app)
            release.set()
            await settle(pilot, lambda: "空闲" in activity_text(app))
    finally:
        release.set()


@pytest.mark.asyncio
async def test_status_data_failure_keeps_navigation_available(workspace_runtime, monkeypatch):
    runtime, _ = workspace_runtime

    def unavailable(_sid):
        raise OSError("协作数据暂不可读取")

    monkeypatch.setattr(runtime.chat, "list_subagents", unavailable)
    app = AgentNexusTUI(runtime=runtime)
    async with app.run_test(size=(80, 24)) as pilot:
        await settle(pilot, lambda: "状态不可用" in activity_text(app))
        await pilot.press("ctrl+p")
        assert app.screen.query_one("#palette-search").has_focus


@pytest.mark.asyncio
async def test_confirmation_wait_is_not_shown_as_active_work(workspace_runtime):
    runtime, _ = workspace_runtime
    approvals = []

    def execute(*_args, **_kwargs):
        approvals.append(runtime.agent.confirm_handler("仅预览，不执行外部操作"))
        return SimpleNamespace(answer="确认完成")

    runtime.agent.run.side_effect = execute
    app = AgentNexusTUI(runtime=runtime)
    async with app.run_test(size=(80, 24)) as pilot:
        screen = app.screen
        app.screen.query_one(Composer).value = "检查确认流程"
        await pilot.press("enter")
        await settle(pilot, lambda: bool(app.screen.query("#btn-confirm")))
        assert "等待确认" in str(screen.query_one("#agent-activity").render())
        await pilot.press("y")
        await settle(pilot, lambda: "空闲" in activity_text(app))
        assert approvals == [True]


@pytest.mark.asyncio
async def test_activity_tracks_reasoning_tool_and_completion(workspace_runtime):
    runtime, _ = workspace_runtime
    thinking, tools, release_thinking, release_tools = (threading.Event() for _ in range(4))

    def execute(*_args, **_kwargs):
        runtime.agent.event_handler(ReActEvent(ReActEventType.STREAM_REASONING, {"token": "检查请求去重。"}), None, None)
        runtime.agent.event_handler(ReActEvent(ReActEventType.STREAM_REASONING, {"token": "\n" + "\n".join(f"检查步骤 {i}" for i in range(30))}), None, None)
        thinking.set()
        release_thinking.wait(10)
        runtime.agent.event_handler(ReActEvent(ReActEventType.STREAM_REASONING, {"token": "\n追加的思考内容。"}), None, None)
        runtime.agent.event_handler(ReActEvent(ReActEventType.TOOL_START, {"id": "read", "name": "file_read"}), None, None)
        tools.set()
        release_tools.wait(10)
        runtime.agent.event_handler(ReActEvent(ReActEventType.TOOL_DONE, {"id": "read", "name": "file_read", "result": "完整文件"}), None, None)
        return SimpleNamespace(answer="检查完成。")

    runtime.agent.run.side_effect = execute
    app = AgentNexusTUI(runtime=runtime)
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            assert "空闲" in activity_text(app)
            app.screen.query_one(Composer).value = "检查请求"
            await pilot.press("enter")
            await settle(pilot, thinking.is_set)
            await settle(pilot, lambda: "思考" in activity_text(app))
            assert "工作中" in activity_text(app)
            await settle(pilot, lambda: app.screen.query_one(".reasoning-content").region.height == 31)
            area = app.screen.query_one("#chat-area")
            await settle(pilot, lambda: area.max_scroll_y > 0 and area.scroll_y == area.max_scroll_y)
            await pilot.press("pageup")
            release_thinking.set()
            await settle(pilot, tools.is_set)
            await settle(pilot, lambda: "读取文件" in activity_text(app))
            assert "空闲" not in activity_text(app)
            assert area.scroll_y < area.max_scroll_y
            assert app.screen.query_one("#new-output").display
            release_tools.set()
            await settle(pilot, lambda: "空闲" in activity_text(app))
            assert runtime.chat.get_session_recovery("workspace")["answer"] == "检查完成。"
    finally:
        release_thinking.set()
        release_tools.set()
