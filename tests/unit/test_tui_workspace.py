"""Terminal workspace behavior exercised through the running Textual application."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from textual.widgets import Input

from agentnexus.tui.app import AgentNexusTUI
from agentnexus.tui.widgets.input_bar import Composer


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


async def click_ready(app, pilot, selector):
    await settle(pilot, lambda: not app.screen.query_one(selector).has_class("-active"))
    await pilot.click(selector)


@pytest.mark.asyncio
async def test_reasoning_stream_is_visible_and_keeps_tool_order(workspace_runtime):
    import threading

    from agentnexus.agents.react_types import ReActEvent, ReActEventType
    from agentnexus.tui.widgets.message import ReasoningBlock, ToolCall

    runtime, _ = workspace_runtime
    release = threading.Event()

    def execute(*_args, **_kwargs):
        def emit(kind, **payload):
            runtime.agent._on_event(ReActEvent(kind, payload), None, None)

        emit(ReActEventType.STREAM_REASONING, token="先检查 [入口]，")
        emit(ReActEventType.STREAM_REASONING, token="再验证行为。")
        release.wait(5)
        emit(ReActEventType.TOOL_START, id="read", name="file_read", arguments={"path": "app.py"})
        emit(ReActEventType.TOOL_DONE, id="read", name="file_read", result="source", duration_ms=10)
        emit(ReActEventType.STREAM_REASONING, token="工具结果已确认。")
        emit(ReActEventType.STREAM_TOKEN, token="完成。")
        return SimpleNamespace(answer="完成。")

    runtime.agent.run.side_effect = execute
    app = AgentNexusTUI(runtime=runtime)
    async with app.run_test(size=(80, 24)) as pilot:
        app.screen.query_one("#chat-input", Composer).value = "检查入口"
        await pilot.press("enter")
        try:
            await settle(pilot, lambda: bool(app.screen.query(ReasoningBlock)))
            reasoning = app.screen.query_one(ReasoningBlock)
            await settle(pilot, lambda: "先检查 [入口]，再验证行为。" in str(reasoning.query_one(".reasoning-content").render()))
            assert reasoning.query_one(".reasoning-content").display
            assert "先检查 [入口]，再验证行为。" in str(reasoning.query_one(".reasoning-content").render())
            reasoning.focus()
            await pilot.press("enter")
            assert not reasoning.query_one(".reasoning-content").display
            await pilot.press("enter")
            assert reasoning.query_one(".reasoning-content").display
            await pilot.press("d")
            assert "先检查 [入口]，再验证行为。" in app.screen.query_one("#record-content").text
            await pilot.press("escape")
        finally:
            release.set()
        await settle(pilot, lambda: not runtime.chat.is_processing)
        await pilot.pause()
        blocks = list(app.screen.query("ReasoningBlock, ToolCall"))
        assert [type(block) for block in blocks] == [ReasoningBlock, ToolCall, ReasoningBlock]
        assert "工具结果已确认。" in str(blocks[-1].query_one(".reasoning-content").render())
        assert "思考结束" in str(reasoning.query_one(".reasoning-heading").render())


@pytest.mark.asyncio
async def test_history_restores_reasoning_and_final_answer_as_distinct_content(workspace_runtime):
    from agentnexus.tui.widgets.message import ChatMessage, ReasoningBlock

    runtime, _ = workspace_runtime
    runtime.memory_manager.short_term.append("user", "历史任务")
    runtime.memory_manager.short_term.append("system", "[思考过程] 保留历史思考。")
    runtime.memory_manager.short_term.append("system", "[最终答案] **历史结果**")
    app = AgentNexusTUI(runtime=runtime)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        block = app.screen.query_one(ReasoningBlock)
        assert "保留历史思考。" in str(block.query_one(".reasoning-content").render())
        answers = app.screen.query("ChatMessage.assistant")
        assert len(answers) == 1
        assert answers.first(ChatMessage).content == "**历史结果**"


@pytest.mark.asyncio
async def test_running_reasoning_survives_switching_sessions(workspace_runtime):
    import threading

    from agentnexus.agents.react_types import ReActEvent, ReActEventType
    from agentnexus.tui.widgets.message import ReasoningBlock

    runtime, _ = workspace_runtime
    release = threading.Event()

    def execute(*_args, **_kwargs):
        runtime.agent._on_event(ReActEvent(ReActEventType.STREAM_REASONING, {"token": "此任务仍在分析。"}), None, None)
        release.wait(8)
        return SimpleNamespace(answer="完成")

    runtime.agent.run.side_effect = execute
    app = AgentNexusTUI(runtime=runtime)
    async with app.run_test(size=(80, 24)) as pilot:
        app.screen.query_one("#chat-input", Composer).value = "长任务"
        await pilot.press("enter")
        try:
            await settle(pilot, lambda: bool(app.screen.query(ReasoningBlock)))
            app.screen.query_one("#chat-input", Composer).value = "/session new"
            await pilot.press("enter")
            await settle(pilot, lambda: app.screen._chat_session.id != "workspace")
            assert not app.screen.query(ReasoningBlock)
            app.screen.query_one("#chat-input", Composer).value = "/session switch workspace"
            await pilot.press("enter")
            await settle(pilot, lambda: app.screen._chat_session.id == "workspace")
            assert "此任务仍在分析。" in str(app.screen.query_one(".reasoning-content").render())
        finally:
            release.set()
        await settle(pilot, lambda: not runtime.chat.is_processing)


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
        assert app.screen.query_one("#chat-input", Composer).has_focus
        assert app.screen.query_one("#input-area").region.height == 3
        assert app.screen.query_one("#hud").region.y == app.screen.query_one("#composer-hints").region.y


@pytest.mark.asyncio
async def test_first_submission_hides_welcome_and_persists_the_answer(workspace_runtime):
    runtime, _ = workspace_runtime
    app = AgentNexusTUI(runtime=runtime)
    async with app.run_test(size=(80, 24)) as pilot:
        app.screen.query_one("#chat-input", Composer).value = "first task"
        await pilot.press("enter")
        await settle(pilot, lambda: runtime.chat.get_session_recovery("workspace") is not None and not runtime.chat.is_processing)
        assert not app.screen.query_one("#welcome").display
        assert runtime.chat.get_session_recovery("workspace")["answer"] == "Completed the requested task."


@pytest.mark.asyncio
async def test_unavailable_task_store_keeps_the_terminal_usable(workspace_runtime, monkeypatch):
    runtime, _ = workspace_runtime

    def unavailable(_sid):
        raise OSError("任务存储暂时不可用")

    monkeypatch.setattr(runtime.chat, "get_session_recovery", unavailable)
    app = AgentNexusTUI(runtime=runtime)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        assert "任务存储暂时不可用" in str(app.screen.query_one("#task-summary").render())
        await pilot.press("ctrl+p")
        assert app.screen.query_one("#palette-search").has_focus


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
        assert "恢复的任务" in str(app.screen.query_one("#todo-card").render())
        await pilot.press("f1")
        assert "累计输入 1234" in app.screen.query_one("#record-content").text
        assert "累计输出 567" in app.screen.query_one("#record-content").text


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
        editor = app.screen.query_one("#chat-input", TextArea)
        editor.load_text("first line\nsecond line")
        assert app.screen.query_one("#chat-area").display
        await pilot.press("enter")
        assert editor.text == "\nfirst line\nsecond line"
        editor.load_text("first line\nsecond line")
        await pilot.press("ctrl+s")
        await settle(pilot, lambda: runtime.chat.get_session_recovery("workspace") is not None and not runtime.chat.is_processing)
        assert runtime.chat.get_session_recovery("workspace")["question"] == "first line\nsecond line"
        assert agents["workspace"].run.call_count == 1


@pytest.mark.asyncio
async def test_loaded_draft_is_consumed_after_sending(workspace_runtime, tmp_path):
    runtime, agents = workspace_runtime
    draft = tmp_path / "request.txt"
    draft.write_text("first line\nsecond line", encoding="utf-8")
    app = AgentNexusTUI(runtime=runtime)
    async with app.run_test(size=(80, 24)) as pilot:
        composer = app.screen.query_one(Composer)
        composer.value = f'/draft load "{draft}"'
        await pilot.press("ctrl+s")
        await settle(pilot, lambda: composer.text == "first line\nsecond line")
        composer.value = "/draft send"
        await pilot.press("ctrl+s")
        try:
            await settle(pilot, lambda: runtime.chat.get_session_recovery("workspace") is not None and not runtime.chat.is_processing)
        except AssertionError:
            from agentnexus.tui.widgets.message import ChatMessage

            pytest.fail(str({"focus": app.focused, "input": composer.text, "messages": [message.content for message in app.screen.query(ChatMessage)]}))
        await pilot.pause()
        assert composer.text == ""
        await pilot.press("ctrl+s")
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
async def test_thinking_picker_changes_effort_without_replacing_the_draft(workspace_runtime):
    runtime, _ = workspace_runtime
    app = AgentNexusTUI(runtime=runtime)
    async with app.run_test(size=(80, 24)) as pilot:
        app.screen.query_one("#chat-input", Composer).value = "尚未发送的任务"
        await pilot.press("ctrl+p")
        app.screen.query_one("#palette-search", Input).value = "思考强度"
        await pilot.press("enter")
        await settle(pilot, lambda: "选择思考强度" in str(app.screen.query_one("Label").render()))
        app.screen.query_one("#palette-search", Input).value = "high"
        await pilot.press("enter")
        await settle(pilot, lambda: runtime.chat.get_thinking_effort("workspace") == "high")
        assert app.screen.query_one("#chat-input", Composer).value == "尚未发送的任务"


@pytest.mark.asyncio
async def test_composer_actions_fit_a_narrow_terminal_and_open_the_picker(workspace_runtime):
    from textual.widgets import Button

    runtime, _ = workspace_runtime
    runtime.agent.model_id = "provider/very-long-model-identifier-that-should-be-shortened"
    app = AgentNexusTUI(runtime=runtime)
    async with app.run_test(size=(60, 20)) as pilot:
        await pilot.pause()
        for button in app.screen.query("#composer-actions Button"):
            assert button.region.right <= 60
        await pilot.click("#choose-thinking")
        app.screen.query_one("#palette-search", Input).value = "high"
        await pilot.press("enter")
        await settle(pilot, lambda: bool(app.screen.query("#choose-thinking")))
        await settle(pilot, lambda: "high" in str(app.screen.query_one("#choose-thinking", Button).label))


@pytest.mark.asyncio
async def test_attachment_form_keeps_task_text_and_lists_attached_file(workspace_runtime, tmp_path):
    runtime, _ = workspace_runtime
    path = tmp_path / "中文 attachment.txt"
    path.write_text("source material", encoding="utf-8")
    app = AgentNexusTUI(runtime=runtime)
    async with app.run_test(size=(80, 24)) as pilot:
        app.screen.query_one("#chat-input", Composer).value = "阅读此文件"
        await pilot.press("ctrl+p")
        app.screen.query_one("#palette-search", Input).value = "添加附件"
        await pilot.press("enter")
        await settle(pilot, lambda: bool(app.screen.query("#parameter-input")))
        app.screen.query_one("#parameter-input", Input).value = str(path)
        await pilot.press("enter")
        await settle(pilot, lambda: bool(app.screen.query("#chat-input")))
        await settle(pilot, lambda: "attachment.txt" in str(app.screen.query_one("#composer-meta").render()))
        assert app.screen.query_one("#chat-input", Composer).value == "阅读此文件"
        assert runtime.agent.run.call_count == 0


@pytest.mark.asyncio
async def test_multiline_paste_stays_inline_without_sending(workspace_runtime):
    from textual.events import Paste
    from textual.widgets import TextArea

    runtime, agents = workspace_runtime
    app = AgentNexusTUI(runtime=runtime)
    async with app.run_test(size=(80, 24)) as pilot:
        app.screen.query_one("#chat-input", Composer).post_message(Paste("中文任务\n第二行"))
        await pilot.pause()
        assert app.screen.query_one("#chat-input", TextArea).text == "中文任务\n第二行"
        assert app.screen.query_one("#chat-area").display
        await pilot.press("escape")
        assert agents["workspace"].run.call_count == 0
        await pilot.press("f4")
        assert app.screen.query_one("#chat-input", TextArea).text == "中文任务\n第二行"


@pytest.mark.asyncio
async def test_inspector_adapts_to_terminal_width_and_resize(workspace_runtime):
    runtime, _ = workspace_runtime
    app = AgentNexusTUI(runtime=runtime)
    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.press("f3")
        assert app.screen.query_one("#task-detail") is not None
        await pilot.press("escape")
        await pilot.resize_terminal(80, 24)
        assert not app.screen.query_one("#side-panel").display
        assert app.screen.query_one("#chat-area").region.width == 80
        await pilot.press("f3")
        assert app.screen.query_one("#task-detail") is not None
        await pilot.press("escape")
        assert app.screen.query_one("#chat-input", Composer).has_focus


@pytest.mark.asyncio
async def test_task_summary_opens_actual_collaboration_progress(workspace_runtime):
    runtime, _ = workspace_runtime
    child = runtime.chat._subagents.register("workspace", "parent", "检查源码", "explorer", "检查入口")
    runtime.chat._subagents.update(child, status="waiting", latest_progress="找到两个入口", waiting_questions=1, child_run_id="attempt")
    app = AgentNexusTUI(runtime=runtime)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        assert "待答复 1" in str(app.screen.query_one("#task-summary").render())
        await pilot.press("f3")
        assert "检查入口" in app.screen.query_one("#child-detail").text
        assert "检查源码" in app.screen.query_one("#child-detail").text
        assert "找到两个入口" in app.screen.query_one("#child-detail").text


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
    completed = context.started("已完成检查", "explorer", "已经结束的任务")
    runtime.chat._subagents.update(completed, status="completed")
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
            await pilot.press("r")
            app.screen.query_one("#message-editor", TextArea).load_text("是的，使用 Markdown。")
            await pilot.press("end", "space", "r", "i", "x", "l")
            await pilot.press("ctrl+s")
            await settle(pilot, lambda: len(store.list_collaboration_messages("workspace", entry.subagent_id)) == 2)
            reply = store.list_collaboration_messages("workspace", entry.subagent_id)[-1]
            assert reply["question_id"] == question["id"]
            assert reply["child_run_id"] == "attempt"
            assert reply["content"] == "是的，使用 Markdown。 rixl"
            assert reply["delivery"] == "queued"
            assert "待送达" in app.screen.query_one("#child-detail", TextArea).text
            await click_ready(app, pilot, "#child-send")
            app.screen.query_one("#message-editor", TextArea).load_text("尚未发送的补充")
            await pilot.press("escape", "escape", "f3")
            await click_ready(app, pilot, "#child-send")
            assert app.screen.query_one("#message-editor", TextArea).text == "尚未发送的补充"
            await pilot.press("escape")
            for count in (3, 4):
                await settle(pilot, lambda: not app.screen.query_one("#child-send").has_class("-active"))
                await click_ready(app, pilot, "#child-send")
                app.screen.query_one("#message-editor", TextArea).load_text("继续检查")
                if count == 3:
                    await pilot.click("#editor-actions", offset=(16, 0))
                else:
                    await pilot.press("ctrl+s")
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
            await click_ready(app, pilot, "#child-send")
            app.screen.query_one("#message-editor", TextArea).load_text("只投递一次")
            await pilot.press("ctrl+s")
            await settle(pilot, lambda: len(store.list_collaboration_messages("workspace", entry.subagent_id)) == 5)
            await pilot.press("escape", "f3")
            await click_ready(app, pilot, "#child-send")
            assert app.screen.query_one("#message-editor", TextArea).text == "只投递一次"
            await pilot.press("ctrl+s")
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
        app.screen.query_one("#chat-input", Composer).value = f"/theme {theme}"
        await pilot.press("enter")
        assert app.theme == theme
        assert app.screen.query_one("#chat-area").region.width == 60
        assert app.screen.query_one("#chat-input", Composer).region.width > 40
        await pilot.press("f4")
        assert app.screen.query_one("#chat-input").region.height == 1
        assert app.screen.query_one("#chat-area").region.height >= 7


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
        app.screen.query_one("#chat-input", Composer).value = "old session task"
        await pilot.press("enter")
        await settle(pilot, lambda: runtime.chat.get_session_recovery("workspace") is not None and not runtime.chat.is_processing)
        await pilot.pause()
        await pilot.press("f1")
        assert "累计输入 1234" in app.screen.query_one("#record-content").text
        await pilot.press("escape", "f3")
        assert "旧会话任务" in str(app.screen.query_one("#task-todos").render())
        await pilot.press("escape", "f2")
        await settle(pilot, lambda: bool(app.screen.query("#palette-search")))
        await pilot.press("enter")
        await settle(pilot, lambda: hasattr(app.screen, "_chat_session") and app.screen._chat_session.id != "workspace")
        await pilot.pause()
        assert "旧会话任务" not in str(app.screen.query_one("#todo-card").render())
        assert "old session task" not in str(app.screen.query_one("#timeline-card").render())
        text = app.screen.query_one("#hud-text").content
        assert "ctx 0/" in text
        await pilot.press("f1")
        assert "累计输入 0" in app.screen.query_one("#record-content").text
        assert "累计输出 0" in app.screen.query_one("#record-content").text
        assert "思考能力 支持" in app.screen.query_one("#record-content").text
        await pilot.press("escape", "f3")
        assert "旧会话任务" not in str(app.screen.query_one("#task-todos").render())
