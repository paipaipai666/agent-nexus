"""Terminal workspace behavior exercised through the running Textual application."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from textual.widgets import Input

from agentnexus.tui.app import AgentNexusTUI


@pytest.fixture
def workspace_runtime(tmp_path):
    from agentnexus.core.config import get_settings
    from agentnexus.memory.short_term import ShortTermMemory
    from agentnexus.memory.versioned import ConversationVersionManager
    from agentnexus.services.chat import ChatService, SessionHandle

    agents = {}

    def agent_factory(sid):
        agent = MagicMock()
        agent.agent_id = "main"
        agent.max_steps = 5
        agent.model_id = "test-model"
        agent.llm_client.model = "test-model"
        agent.llm_client.base_url = ""
        agent.llm_client.capabilities.max_context_tokens = 128000
        agent.llm_client.capabilities.supports_thinking = True
        agent.llm_client.capabilities.supports_tool_calling = True
        agent._on_event = None
        agent._todo_list = None
        agent._total_usage = {}
        agent.total_usage = {}
        agent._step_count = 0
        agent.run.return_value = SimpleNamespace(answer="Completed the requested task.")
        agents[sid] = agent
        return agent

    def memory_factory(_sid):
        return lambda: SimpleNamespace(short_term=ShortTermMemory(), estimate_stm_tokens=lambda: 0)

    agent, memory = agent_factory("workspace"), memory_factory("workspace")()
    version = ConversationVersionManager("workspace", get_settings().memory_db_path, workspace_path=str(tmp_path))
    chat = ChatService(agent_factory, memory_factory, version_manager=version)
    chat.attach_session(SessionHandle("workspace", workspace=str(tmp_path)), agent=agent, memory=memory, version=version)
    return SimpleNamespace(agent=agent, memory_manager=memory, version_manager=version, chat=chat,
                           session_id="workspace", mcp_manager=None, skill=None, capability_runtime=None,
                           llm=agent.llm_client), agents


async def settle(pilot, predicate):
    for _ in range(200):
        if predicate():
            return
        await pilot.pause(0.02)
    assert predicate()


@pytest.mark.asyncio
async def test_empty_session_shows_project_logo_and_full_width_chat(workspace_runtime):
    runtime, _ = workspace_runtime
    app = AgentNexusTUI(runtime=runtime)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        welcome = app.screen.query_one("#welcome")
        assert welcome.display
        assert "AgentNexus" in str(welcome.render())
        assert "▟" in str(welcome.render())
        assert not app.screen.query_one("#side-panel").display
        assert app.screen.query_one("#chat-area").region.width == 80
        assert app.screen.query_one("#chat-input", Input).has_focus


@pytest.mark.asyncio
async def test_first_submission_hides_welcome_and_persists_the_answer(workspace_runtime):
    runtime, _ = workspace_runtime
    app = AgentNexusTUI(runtime=runtime)
    async with app.run_test(size=(80, 24)) as pilot:
        app.screen.query_one("#chat-input", Input).value = "first task"
        await pilot.press("enter")
        await settle(pilot, lambda: runtime.chat.get_session_recovery("workspace") is not None and not runtime.chat.is_processing)
        assert not app.screen.query_one("#welcome").display
        assert runtime.chat.get_session_recovery("workspace")["answer"] == "Completed the requested task."


@pytest.mark.asyncio
async def test_restored_history_skips_the_large_welcome_logo(workspace_runtime):
    runtime, _ = workspace_runtime
    runtime.memory_manager.short_term.append("user", "previous request")
    runtime.memory_manager.estimate_stm_tokens = lambda: 4000
    runtime.agent.total_usage = {"input_tokens": 1234, "output_tokens": 567}
    runtime.agent._todo_list = SimpleNamespace(list_items=lambda: [SimpleNamespace(id="restored", status="pending", description="恢复的任务")])
    app = AgentNexusTUI(runtime=runtime)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        assert not app.screen.query_one("#welcome").display
        assert "ctx 4.0k/" in app.screen.query_one("#hud-text").content
        assert "in:1.2k out:567" in app.screen.query_one("#hud-text").content
        assert "恢复的任务" in str(app.screen.query_one("#todo-card").render())


@pytest.mark.asyncio
async def test_tool_output_expands_with_a_keyboard_action():
    from textual.app import App

    from agentnexus.tui.widgets.message import ToolCall

    class ToolApp(App):
        def compose(self):
            yield ToolCall("file_read", "source line\nsecond line", duration_ms=120)

    app = ToolApp()
    async with app.run_test() as pilot:
        tool = app.query_one(ToolCall)
        assert not tool.query_one("#tool-result").display
        tool.focus()
        await pilot.press("enter")
        assert tool.query_one("#tool-result").display
        assert "source line" in str(tool.query_one("#tool-result").render())
        await pilot.press("enter")
        assert not tool.query_one("#tool-result").display


@pytest.mark.asyncio
async def test_multiline_editor_preserves_newlines_and_sends_once(workspace_runtime):
    from textual.widgets import TextArea

    runtime, agents = workspace_runtime
    app = AgentNexusTUI(runtime=runtime)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("f4")
        editor = app.screen.query_one("#message-editor", TextArea)
        editor.load_text("first line\nsecond line")
        await pilot.press("f4")
        await settle(pilot, lambda: runtime.chat.get_session_recovery("workspace") is not None and not runtime.chat.is_processing)
        assert runtime.chat.get_session_recovery("workspace")["question"] == "first line\nsecond line"
        assert agents["workspace"].run.call_count == 1


@pytest.mark.asyncio
async def test_command_menu_filters_and_executes_a_selected_action(workspace_runtime):
    runtime, _ = workspace_runtime
    app = AgentNexusTUI(runtime=runtime)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("ctrl+p")
        app.screen.query_one("#palette-search", Input).value = "计划模式"
        await pilot.pause()
        await pilot.press("down", "enter")
        await settle(pilot, lambda: runtime.chat.is_plan_mode("workspace"))
        assert runtime.chat.is_plan_mode("workspace") is True


@pytest.mark.asyncio
async def test_multiline_paste_opens_editor_without_sending(workspace_runtime):
    from textual.events import Paste
    from textual.widgets import TextArea

    runtime, agents = workspace_runtime
    app = AgentNexusTUI(runtime=runtime)
    async with app.run_test(size=(80, 24)) as pilot:
        app.screen.query_one("#chat-input", Input).post_message(Paste("中文任务\n第二行"))
        await pilot.pause()
        assert app.screen.query_one("#message-editor", TextArea).text == "中文任务\n第二行"
        await pilot.press("escape")
        assert agents["workspace"].run.call_count == 0
        await pilot.press("f4")
        assert app.screen.query_one("#message-editor", TextArea).text == "中文任务\n第二行"


@pytest.mark.asyncio
async def test_inspector_adapts_to_terminal_width_and_resize(workspace_runtime):
    runtime, _ = workspace_runtime
    app = AgentNexusTUI(runtime=runtime)
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.press("f3")
        assert app.screen.query_one("#side-panel").display
        await pilot.resize_terminal(80, 24)
        assert not app.screen.query_one("#side-panel").display
        assert app.screen.query_one("#chat-area").region.width == 80
        await pilot.press("f3")
        assert app.screen.query_one("#task-detail") is not None
        await pilot.press("escape")
        assert app.screen.query_one("#chat-input", Input).has_focus


@pytest.mark.asyncio
async def test_collaboration_tree_displays_actual_progress_and_waiting_state(workspace_runtime):
    runtime, _ = workspace_runtime
    child = runtime.chat._subagents.register("workspace", "parent", "检查源码", "explorer", "检查入口")
    runtime.chat._subagents.update(child, status="waiting", latest_progress="找到两个入口", waiting_questions=1, child_run_id="attempt")
    app = AgentNexusTUI(runtime=runtime)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        tree = app.screen.query_one("#collaboration-tree")
        assert tree.display
        text = str(tree.query_one("#collaboration-summary").render())
        assert "检查源码" in text
        assert "待答复" in text
        assert "找到两个入口" in text
        await pilot.press("f3")
        assert "检查入口" in app.screen.query_one("#child-detail").text


@pytest.mark.asyncio
async def test_new_output_does_not_move_a_reader_away_from_history(workspace_runtime):
    runtime, _ = workspace_runtime
    app = AgentNexusTUI(runtime=runtime)
    async with app.run_test(size=(80, 24)) as pilot:
        area = app.screen.query_one("#chat-area")
        for index in range(40):
            area.add_message("assistant", f"history {index}\nsecond line")
        await pilot.pause()
        area.scroll_home(animate=False)
        await pilot.pause()
        before = area.scroll_y
        area.add_message("assistant", "new output")
        await pilot.pause()
        assert area.scroll_y == before
        assert app.screen.query_one("#new-output").display
        await pilot.click("#new-output")
        await pilot.pause()
        assert area.scroll_y == area.max_scroll_y
        area.scroll_home(animate=False)
        await pilot.pause()
        area.add_message("assistant", "another pending update")
        await pilot.pause()
        assert app.screen.query_one("#new-output").display
        await pilot.press("f2")
        await settle(pilot, lambda: bool(app.screen.query("#palette-search")))
        await pilot.press("enter")
        await settle(pilot, lambda: hasattr(app.screen, "_chat_session") and app.screen._chat_session.id != "workspace")
        await pilot.pause()
        assert not app.screen.query_one("#new-output").display
        assert app.screen._chat_area.following_output


@pytest.mark.asyncio
async def test_session_menu_switches_and_returns_to_empty_welcome(workspace_runtime):
    runtime, _ = workspace_runtime
    app = AgentNexusTUI(runtime=runtime)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("f2")
        await settle(pilot, lambda: bool(app.screen.query("#palette-search")))
        await pilot.press("enter")
        await settle(pilot, lambda: runtime.chat._sessions.keys() != {"workspace"} and hasattr(app.screen, "_chat_session") and app.screen._chat_session.id != "workspace")
        await pilot.pause()
        assert app.screen.query_one("#welcome").display


@pytest.mark.asyncio
async def test_inspector_replies_to_the_selected_child_question_and_shows_receipt(workspace_runtime, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    from textual.widgets import TextArea

    from agentnexus.observability.timeline import get_timeline_store
    from agentnexus.services.collaboration import ChildTask, CollaborationRun
    from agentnexus.services.subagents import SubagentRunContext

    runtime, _ = workspace_runtime
    store = get_timeline_store()
    pool = ThreadPoolExecutor(1)
    context = SubagentRunContext(runtime.chat, "workspace", "parent")
    run = CollaborationRun(context, store, pool, lambda: False)
    runtime.chat._collaborations["parent"] = run
    entry = context.started("源码检查", "explorer", "检查入口")
    runtime.chat._subagents.update(entry, status="waiting", child_run_id="attempt", collaborative=True)
    child = ChildTask(entry, "attempt")
    run.children[entry.subagent_id] = child
    question = run._send(child, entry.subagent_id, "parent", "question", "输出 Markdown 吗？", "question")
    app = AgentNexusTUI(runtime=runtime)
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.press("f3")
            assert "输出 Markdown 吗？" in app.screen.query_one("#child-detail", TextArea).text
            await pilot.click("#child-reply")
            app.screen.query_one("#message-editor", TextArea).load_text("是的，使用 Markdown。")
            await pilot.press("f4")
            await settle(pilot, lambda: len(store.list_collaboration_messages("workspace", entry.subagent_id)) == 2)
            reply = store.list_collaboration_messages("workspace", entry.subagent_id)[-1]
            assert reply["question_id"] == question["id"]
            assert reply["child_run_id"] == "attempt"
            assert reply["content"] == "是的，使用 Markdown。"
            assert reply["delivery"] == "queued"
            assert "queued" in app.screen.query_one("#child-detail", TextArea).text
            await pilot.click("#child-send")
            app.screen.query_one("#message-editor", TextArea).load_text("尚未发送的补充")
            await pilot.press("escape", "escape", "f3")
            await pilot.click("#child-send")
            assert app.screen.query_one("#message-editor", TextArea).text == "尚未发送的补充"
            await pilot.press("escape")
            for count in (3, 4):
                await pilot.click("#child-send")
                app.screen.query_one("#message-editor", TextArea).load_text("继续检查")
                await pilot.press("f4")
                await settle(pilot, lambda: len(store.list_collaboration_messages("workspace", entry.subagent_id)) == count)
            instructions = store.list_collaboration_messages("workspace", entry.subagent_id)[-2:]
            assert [message["content"] for message in instructions] == ["继续检查", "继续检查"]
            assert instructions[0]["request_id"] != instructions[1]["request_id"]
            send = runtime.chat.send_subagent_message
            attempts = []

            def uncertain_send(*args):
                attempts.append(args[3])
                receipt = send(*args)
                if len(attempts) == 1:
                    raise OSError("receipt lost after durable acceptance")
                return receipt

            monkeypatch.setattr(runtime.chat, "send_subagent_message", uncertain_send)
            await pilot.click("#child-send")
            app.screen.query_one("#message-editor", TextArea).load_text("只投递一次")
            await pilot.press("f4")
            await settle(pilot, lambda: len(store.list_collaboration_messages("workspace", entry.subagent_id)) == 5)
            await pilot.press("escape", "f3")
            await pilot.click("#child-send")
            assert app.screen.query_one("#message-editor", TextArea).text == "只投递一次"
            await pilot.press("f4")
            await settle(pilot, lambda: len(attempts) == 2)
            assert attempts[0] == attempts[1]
            assert len(store.list_collaboration_messages("workspace", entry.subagent_id)) == 5
    finally:
        child.ended = True
        run.close("finished")
        pool.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("theme", ["nexus-light", "ansi-dark", "ansi-light"])
async def test_theme_selection_preserves_a_usable_narrow_composer(workspace_runtime, theme):
    runtime, _ = workspace_runtime
    app = AgentNexusTUI(runtime=runtime)
    async with app.run_test(size=(60, 20)) as pilot:
        app.screen.query_one("#chat-input", Input).value = f"/theme {theme}"
        await pilot.press("enter")
        assert app.theme == theme
        assert app.screen.query_one("#chat-area").region.width == 60
        assert app.screen.query_one("#chat-input", Input).region.width > 40
        await pilot.press("f4")
        assert app.screen.query_one("#message-editor").region.height >= 8


@pytest.mark.asyncio
async def test_tool_details_can_show_the_complete_unshortened_result():
    from textual.app import App

    from agentnexus.tui.widgets.message import ToolCall

    class ToolApp(App):
        def compose(self):
            yield ToolCall("file_read", "preview", raw_result="first\n" + "line\n" * 300 + "last line")

    app = ToolApp()
    async with app.run_test(size=(80, 24)) as pilot:
        app.query_one(ToolCall).focus()
        await pilot.press("d")
        assert app.screen.query_one("#record-content").text.endswith("last line")


@pytest.mark.asyncio
async def test_session_switch_replaces_timeline_todos_usage_and_capabilities(workspace_runtime):
    runtime, _ = workspace_runtime
    runtime.agent.total_usage = {"input_tokens": 1234, "output_tokens": 567}
    runtime.agent.llm_client.capabilities.supports_thinking = False
    runtime.agent._todo_list = SimpleNamespace(list_items=lambda: [SimpleNamespace(id="one", status="pending", description="旧会话任务")])
    runtime.memory_manager.estimate_stm_tokens = lambda: 128
    app = AgentNexusTUI(runtime=runtime)
    async with app.run_test(size=(120, 32)) as pilot:
        app.screen.query_one("#chat-input", Input).value = "old session task"
        await pilot.press("enter")
        await settle(pilot, lambda: runtime.chat.get_session_recovery("workspace") is not None and not runtime.chat.is_processing)
        await pilot.pause()
        await pilot.press("f3")
        assert "旧会话任务" in str(app.screen.query_one("#todo-card").render())
        assert "in:1.2k" in app.screen.query_one("#hud-text").content
        await pilot.press("f2")
        await settle(pilot, lambda: bool(app.screen.query("#palette-search")))
        await pilot.press("enter")
        await settle(pilot, lambda: hasattr(app.screen, "_chat_session") and app.screen._chat_session.id != "workspace")
        await pilot.pause()
        assert "旧会话任务" not in str(app.screen.query_one("#todo-card").render())
        assert "old session task" not in str(app.screen.query_one("#timeline-card").render())
        text = app.screen.query_one("#hud-text").content
        assert "ctx 0/" in text
        assert "in:0 out:0" in text
        assert "思考" in text
