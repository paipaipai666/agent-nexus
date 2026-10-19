"""TUI shares application sessions and execution with the desktop runtime."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agentnexus.services.chat import ChatService, SessionHandle
from agentnexus.tui.app import AgentNexusTUI
from agentnexus.tui.widgets.input_bar import Composer


@pytest.fixture
def interactive_runtime(tmp_path):
    from agentnexus.core.config import get_settings
    from agentnexus.memory.short_term import ShortTermMemory
    from agentnexus.memory.versioned import ConversationVersionManager

    agents = {}

    def make_agent(sid):
        agent = MagicMock()
        agent.model_id = "test-model"
        agent.agent_id = "test-agent"
        agent.max_steps = 5
        agent.llm_client.model = "test-model"
        agent.llm_client.base_url = ""
        agent.llm_client.capabilities.max_context_tokens = 200000
        agent._on_event = None
        agent._todo_list = None
        agent._total_usage = {}
        agent._step_count = 0
        agent.run.return_value = SimpleNamespace(answer=f"answer for {sid}")
        agents[sid] = agent
        return agent

    def make_memory(_sid):
        return lambda: SimpleNamespace(short_term=ShortTermMemory(), estimate_stm_tokens=lambda: 0)

    settings = get_settings()
    version = ConversationVersionManager("initial", settings.memory_db_path, workspace_path=str(tmp_path))
    agent, memory = make_agent("initial"), make_memory("initial")()
    chat = ChatService(make_agent, make_memory, version_manager=version)
    chat.attach_session(SessionHandle("initial", workspace=str(tmp_path)), agent=agent, memory=memory, version=version)
    runtime = SimpleNamespace(agent=agent, memory_manager=memory, version_manager=version, chat=chat,
                              session_id="initial", mcp_manager=None, skill=None, capability_runtime=None, llm=agent.llm_client)
    return runtime, agents


async def submit_command(app, pilot, text):

    app.screen.query_one("#chat-input", Composer).value = text
    await pilot.press("enter")


async def wait_until(pilot, predicate):
    for _ in range(150):
        if predicate():
            return
        await pilot.pause(0.02)
    assert predicate()


def test_launcher_passes_the_assembled_runtime():
    from agentnexus.cli.tui_cmd import launch_tui

    runtime = MagicMock()
    with patch("agentnexus.app.AppRuntime.build", return_value=runtime), patch(
        "agentnexus.tui.app.AgentNexusTUI"
    ) as app:
        launch_tui()
    assert app.call_args.kwargs["runtime"] is runtime
    runtime.close.assert_called_once()


@pytest.mark.asyncio
async def test_screen_uses_runtime_chat_and_initial_session(tmp_path):
    from agentnexus.memory.versioned import ConversationVersionManager

    agent = MagicMock()
    agent.model_id = "test-model"
    agent.llm_client.capabilities.max_context_tokens = 200000
    version = ConversationVersionManager("tui_original", str(tmp_path / "memory.db"), workspace_path=str(tmp_path))
    memory = MagicMock()
    memory.short_term.get_all.return_value = []
    chat = ChatService(lambda _sid: agent, lambda _sid: lambda: memory, version_manager=version)
    chat.attach_session(SessionHandle("tui_original", profile="tui", workspace=str(tmp_path)), agent=agent, memory=memory, version=version)
    runtime = SimpleNamespace(agent=agent, memory_manager=memory, version_manager=version, chat=chat,
                              session_id="tui_original", mcp_manager=None, skill=None, capability_runtime=None)
    app = AgentNexusTUI(runtime=runtime)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        assert app.screen._chat_service is chat
        assert app.screen._chat_session.id == "tui_original"
        assert chat.session_memory("tui_original") is memory
        assert chat.session_version("tui_original") is version


@pytest.mark.asyncio
async def test_submitted_message_uses_shared_execution_and_saved_recovery(tmp_path):
    from agentnexus.memory.short_term import ShortTermMemory
    from agentnexus.memory.versioned import ConversationVersionManager

    memory = MagicMock()
    memory.short_term = ShortTermMemory()
    memory.estimate_stm_tokens.return_value = 0
    agent = MagicMock()
    agent.agent_id = "test-agent"
    agent.max_steps = 5
    agent.model_id = "test-model"
    agent.llm_client.model = "test-model"
    agent.llm_client.base_url = ""
    agent.llm_client.capabilities.max_context_tokens = 200000
    agent._on_event = None
    agent._todo_list = None
    agent._total_usage = {}
    agent._step_count = 0
    agent.run.return_value = SimpleNamespace(answer="shared answer")
    version = ConversationVersionManager("tui_send", str(tmp_path / "memory.db"), workspace_path=str(tmp_path))
    chat = ChatService(lambda _sid: agent, lambda _sid: lambda: memory, version_manager=version)
    chat.attach_session(SessionHandle("tui_send", workspace=str(tmp_path)), agent=agent, memory=memory, version=version)
    runtime = SimpleNamespace(agent=agent, memory_manager=memory, version_manager=version, chat=chat,
                              session_id="tui_send", mcp_manager=None, skill=None, capability_runtime=None)
    app = AgentNexusTUI(runtime=runtime)
    with patch.object(chat, "send_message", wraps=chat.send_message) as send:
        async with app.run_test(size=(100, 30)) as pilot:

            app.screen.query_one("#chat-input", Composer).value = "hello"
            await pilot.press("enter")
            for _ in range(100):
                await pilot.pause(0.02)
                recovery = chat.get_session_recovery("tui_send")
                if recovery and recovery["status"] != "running" and not chat.is_processing:
                    break
            assert send.call_count == 1
            assert recovery["answer"] == "shared answer"
            assert recovery["persisted"] is True
            assert version.get_messages_with_answers()[-1]["content"] == "[最终答案] shared answer"


@pytest.mark.asyncio
async def test_switching_sessions_replaces_memory_agent_and_version_together(interactive_runtime):
    runtime, agents = interactive_runtime
    app = AgentNexusTUI(runtime=runtime)
    async with app.run_test(size=(100, 30)) as pilot:
        await submit_command(app, pilot, "/session new")
        await wait_until(pilot, lambda: app.screen._chat_session.id != "initial")
        other = app.screen._chat_session.id
        assert app.screen._memory is not runtime.memory_manager
        assert app.screen._agent is agents[other]
        assert app.screen._version.session_id == other
        await submit_command(app, pilot, "/switch initial")
        await pilot.pause()
        assert app.screen._chat_session.id == "initial"
        assert app.screen._memory is runtime.memory_manager
        assert app.screen._version is runtime.version_manager


@pytest.mark.asyncio
async def test_background_completion_does_not_write_into_the_selected_chat(interactive_runtime):
    import threading

    from agentnexus.tui.widgets.message import ChatMessage

    runtime, agents = interactive_runtime
    entered, release = threading.Event(), threading.Event()

    def first_run(*args, **kwargs):
        entered.set()
        release.wait(5)
        return SimpleNamespace(answer="answer from previous session")

    agents["initial"].run.side_effect = first_run
    app = AgentNexusTUI(runtime=runtime)
    try:
        async with app.run_test(size=(100, 30)) as pilot:
            await submit_command(app, pilot, "first message")
            await wait_until(pilot, entered.is_set)
            await submit_command(app, pilot, "/session new")
            await wait_until(pilot, lambda: app.screen._chat_session.id != "initial")
            other = app.screen._chat_session.id
            await submit_command(app, pilot, "second message")
            await wait_until(pilot, lambda: runtime.chat.get_session_recovery(other) is not None and not runtime.chat.is_session_processing(other))
            release.set()
            await wait_until(pilot, lambda: not runtime.chat.is_processing)
            contents = [widget.content for widget in app.screen.query(ChatMessage)]
            assert f"answer for {other}" in contents
            assert "answer from previous session" not in contents
            assert runtime.chat.get_session_recovery("initial")["answer"] == "answer from previous session"
    finally:
        release.set()


@pytest.mark.asyncio
async def test_stop_keeps_processing_until_the_worker_releases_resources(interactive_runtime):
    import threading

    runtime, agents = interactive_runtime
    entered, release = threading.Event(), threading.Event()

    def delayed_run(*args, **kwargs):
        entered.set()
        release.wait(5)
        return SimpleNamespace(answer="late result")

    agents["initial"].run.side_effect = delayed_run
    app = AgentNexusTUI(runtime=runtime)
    try:
        async with app.run_test(size=(100, 30)) as pilot:
            await submit_command(app, pilot, "slow request")
            await wait_until(pilot, entered.is_set)
            await submit_command(app, pilot, "/stop")
            await wait_until(pilot, lambda: runtime.chat.get_session_recovery("initial")["status"] == "interrupted")
            assert runtime.chat.is_session_processing("initial") is True
            release.set()
            await wait_until(pilot, lambda: not runtime.chat.is_session_processing("initial"))
            assert runtime.chat.get_session_recovery("initial")["status"] == "interrupted"
    finally:
        release.set()


def test_continue_mode_restores_each_sessions_own_context(tmp_path):
    from agentnexus.app import AppRuntime
    from agentnexus.core.config import get_settings
    from agentnexus.memory.short_term import ShortTermMemory
    from agentnexus.memory.versioned import ConversationVersionManager
    from agentnexus.skills import SkillRegistry

    settings = get_settings()
    for sid, text in (("first", "first context"), ("second", "second context")):
        memory = ShortTermMemory()
        memory.append("user", text)
        version = ConversationVersionManager(sid, settings.memory_db_path, workspace_path=str(tmp_path))
        version.commit(stm_snapshot=memory.to_json(), question=text)

    def memory_manager(*args, **kwargs):
        return SimpleNamespace(short_term=ShortTermMemory())

    with patch("agentnexus.core.llm.AgentLLM"), patch("agentnexus.tools.registry.ToolRegistry"), patch(
        "agentnexus.tools.register_all_tools"
    ), patch("agentnexus.tools.mcp.adapter.create_mcp_manager_from_settings", return_value=None), patch(
        "agentnexus.memory.manager.MemoryManager", side_effect=memory_manager
    ), patch("agentnexus.agents.re_act_agent.ReActAgent"), patch(
        "agentnexus.skills.SkillRegistry.from_settings", return_value=SkillRegistry([])
    ):
        runtime = AppRuntime.build(profile="tui", session_id="first", workspace_path=str(tmp_path), restore_session=True)
        assert runtime.chat.ensure_session_restored("second") is True
        assert runtime.chat.session_memory("second").short_term.get_all()[0]["content"] == "second context"
        assert runtime.chat.session_memory("first").short_term.get_all()[0]["content"] == "first context"


@pytest.mark.asyncio
async def test_child_stream_tokens_do_not_flood_chat_and_questions_are_visible(interactive_runtime):
    from agentnexus.services.chat import AgentEvent
    from agentnexus.tui.widgets.message import ChatMessage

    runtime, _ = interactive_runtime
    app = AgentNexusTUI(runtime=runtime)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        screen = app.screen
        before = len(screen.query(ChatMessage))
        screen._apply_service_event(AgentEvent("subagent_event", {"kind": "token", "subagent_id": "child", "token": "partial token"}, session_id="initial"))
        await pilot.pause()
        assert len(screen.query(ChatMessage)) == before
        screen._apply_service_event(AgentEvent("subagent_event", {"kind": "communication", "subagent_id": "child", "messages": [{"id": 7, "kind": "question", "sender": "child", "content": "Which branch should I use?", "delivery_state": "queued"}]}, session_id="initial"))
        await pilot.pause()
        assert any("Which branch should I use?" in widget.content for widget in screen.query(ChatMessage))


@pytest.mark.asyncio
async def test_execution_keeps_token_totals_available_from_the_status_help(interactive_runtime):
    runtime, agents = interactive_runtime
    agents["initial"].total_usage = {"input_tokens": 1200, "output_tokens": 300}
    app = AgentNexusTUI(runtime=runtime)
    async with app.run_test(size=(100, 30)) as pilot:
        await submit_command(app, pilot, "usage request")
        await wait_until(pilot, lambda: runtime.chat.get_session_recovery("initial") is not None and not runtime.chat.is_processing)
        await pilot.pause()
        await pilot.press("f1")
        text = app.screen.query_one("#record-content").text
        assert "累计输入 1200" in text
        assert "累计输出 300" in text


@pytest.mark.asyncio
async def test_background_compaction_cannot_change_the_selected_chat(interactive_runtime):
    import asyncio

    from agentnexus.tui.widgets.message import ChatMessage

    runtime, _ = interactive_runtime
    app = AgentNexusTUI(runtime=runtime)
    async with app.run_test(size=(100, 30)) as pilot:
        initial_callback = runtime.memory_manager._on_compact
        await submit_command(app, pilot, "/session new")
        await wait_until(pilot, lambda: app.screen._chat_session.id != "initial")
        await asyncio.to_thread(initial_callback, {"event": "start", "tokens_before": 90000})
        await pilot.pause()
        assert app.screen._hud._compacting is False
        assert not any("上下文压缩" in widget.content for widget in app.screen.query(ChatMessage))
        await submit_command(app, pilot, "/switch initial")
        await pilot.pause()
        assert app.screen._hud._compacting is True
        await asyncio.to_thread(initial_callback, {"event": "complete", "tokens_before": 90000, "tokens_after": 10000})
        await pilot.pause()
        assert app.screen._hud._compacting is False


@pytest.mark.asyncio
async def test_quiet_execution_keeps_listening_for_the_final_answer(interactive_runtime, monkeypatch):
    import threading
    import time

    from agentnexus.tui.widgets.message import ChatMessage

    runtime, agents = interactive_runtime
    stream = runtime.chat.stream_events
    entered = threading.Event()

    def quiet_run(*args, **kwargs):
        entered.set()
        time.sleep(0.12)
        return SimpleNamespace(answer="answer after a quiet period")

    def short_timeout(run_id, timeout=30):
        yield from stream(run_id, timeout=None if timeout is None else 0.02)

    agents["initial"].run.side_effect = quiet_run
    monkeypatch.setattr(runtime.chat, "stream_events", short_timeout)
    app = AgentNexusTUI(runtime=runtime)
    async with app.run_test(size=(100, 30)) as pilot:
        await submit_command(app, pilot, "wait for answer")
        await wait_until(pilot, entered.is_set)
        await wait_until(pilot, lambda: not app.screen._running_sessions)
        await pilot.pause()
        contents = [widget.content for widget in app.screen.query(ChatMessage)]
        assert "answer after a quiet period" in contents, (contents, runtime.chat.get_session_recovery("initial"))


@pytest.mark.asyncio
async def test_dynamic_skill_waits_in_the_same_session_queue(interactive_runtime):
    import threading

    from agentnexus.services.skill import SkillService
    from agentnexus.skills import SkillEntry, SkillRegistry, Workflow

    runtime, agents = interactive_runtime
    workflow = Workflow.model_validate({"id": "review", "version": "1", "display_name": "Review", "description": "Review changes", "prompt_profile": {"system": "react"}, "tool_policy": {"max_risk": "low"}, "steps": [{"type": "prompt", "id": "review", "prompt": "Review changes"}], "success_criteria": ["Done"]})
    entry = SkillEntry("default", "review", "Review", workflow.description, MagicMock(), workflow)
    registry = SkillRegistry([])
    registry._entries = [entry]
    skill = SkillService(registry, agent=agents["initial"])
    runtime.chat._skill_service = skill
    runtime.chat._session_skill_services["initial"] = skill
    entered, release = threading.Event(), threading.Event()

    def run(text, **kwargs):
        if text == "first request":
            entered.set()
            release.wait(5)
        return SimpleNamespace(answer="answer to " + text)

    agents["initial"].run.side_effect = run
    app = AgentNexusTUI(runtime=runtime)
    try:
        async with app.run_test(size=(100, 30)) as pilot:
            await submit_command(app, pilot, "first request")
            await wait_until(pilot, entered.is_set)
            await submit_command(app, pilot, "/review inspect changes")
            await pilot.pause()
            assert agents["initial"].run.call_count == 1
            assert skill.current is None
            assert app.screen._queued_messages["initial"][0]["text"] == "inspect changes"
            release.set()
            await wait_until(pilot, lambda: agents["initial"].run.call_count == 2 and not app.screen._running_sessions)
            agents["initial"].set_session_profile.assert_any_call(workflow.to_session_profile())
            assert runtime.chat.get_session_recovery("initial")["question"] == "inspect changes"
    finally:
        release.set()


@pytest.mark.parametrize("argument", ["search review", "show default/review"])
def test_skill_search_and_detail_show_the_workflow(argument):
    from agentnexus.tui.screens.chat import ChatScreen

    workflow = MagicMock()
    workflow.model_dump.return_value = {"description": "review changes carefully", "steps": [{"prompt": "check behavior"}]}
    entry = SimpleNamespace(qualified_id="default/review", display_name="Review", description="review changes carefully", workflow=workflow, path="skills/review/SKILL.md")
    registry = MagicMock()
    registry.get.return_value = entry
    registry.list.return_value = [entry]
    screen = ChatScreen(agent=None, memory=None, version=None)
    screen._skill_registry = registry
    screen._chat_area = MagicMock()
    screen._handle_skill_command(argument)
    rendered = screen._chat_area.add_message.call_args
    assert rendered is not None
    assert "review changes carefully" in rendered.args[1]


@pytest.mark.asyncio
async def test_background_command_errors_stay_with_their_source_session(interactive_runtime):
    import threading

    from agentnexus.tui.widgets.message import ChatMessage

    runtime, _ = interactive_runtime
    entered, release = threading.Event(), threading.Event()
    app = AgentNexusTUI(runtime=runtime)
    try:
        async with app.run_test(size=(100, 30)) as pilot:
            original = app.screen._terminal.run

            def command(name, args, sid):
                if name == "/plugin":
                    entered.set()
                    release.wait(5)
                    raise ValueError("plugin inspection failed")
                return original(name, args, sid)

            app.screen._terminal.run = command
            await submit_command(app, pilot, "/plugin")
            await wait_until(pilot, entered.is_set)
            await submit_command(app, pilot, "/session new")
            await wait_until(pilot, lambda: app.screen._chat_session.id != "initial")
            release.set()
            await pilot.pause(0.1)
            assert not any("plugin inspection failed" in widget.content for widget in app.screen.query(ChatMessage))
            await submit_command(app, pilot, "/switch initial")
            await pilot.pause()
            assert any("plugin inspection failed" in widget.content for widget in app.screen.query(ChatMessage))
    finally:
        release.set()


@pytest.mark.asyncio
async def test_queue_drain_preserves_a_new_unsent_draft(interactive_runtime):
    import threading

    from agentnexus.tui.commands import TerminalCommands

    runtime, agents = interactive_runtime
    entered, release = threading.Event(), threading.Event()

    def run(text, **kwargs):
        if text == "first":
            entered.set()
            release.wait(5)
        return SimpleNamespace(answer="answer to " + text)

    agents["initial"].run.side_effect = run
    app = AgentNexusTUI(runtime=runtime)
    try:
        async with app.run_test(size=(100, 30)) as pilot:
            await submit_command(app, pilot, "first")
            await wait_until(pilot, entered.is_set)
            await submit_command(app, pilot, "queued")
            app.screen.query_one("#chat-input", Composer).value = "not submitted yet"
            await pilot.pause()
            release.set()
            await wait_until(pilot, lambda: agents["initial"].run.call_count == 2 and not app.screen._running_sessions)
            restored = TerminalCommands(runtime)
            restored.load_draft("initial")
            assert restored.inputs["initial"] == "not submitted yet"
    finally:
        release.set()


@pytest.mark.asyncio
async def test_prepared_background_draft_is_submitted_to_its_original_session(interactive_runtime):
    import threading

    from agentnexus.tui.commands import CommandResult

    runtime, agents = interactive_runtime
    entered, release = threading.Event(), threading.Event()
    app = AgentNexusTUI(runtime=runtime)
    try:
        async with app.run_test(size=(100, 30)) as pilot:
            original = app.screen._terminal.run

            def command(name, args, sid):
                if name == "/draft":
                    entered.set()
                    release.wait(5)
                    return CommandResult({"status": "ready"}, submission={"text": "background draft", "attachments": None})
                return original(name, args, sid)

            app.screen._terminal.run = command
            await submit_command(app, pilot, "/draft send")
            await wait_until(pilot, entered.is_set)
            await submit_command(app, pilot, "/session new")
            await wait_until(pilot, lambda: app.screen._chat_session.id != "initial")
            release.set()
            await wait_until(pilot, lambda: agents["initial"].run.call_count == 1)
            await wait_until(pilot, lambda: not runtime.chat.is_processing)
            assert runtime.chat.get_session_recovery("initial")["question"] == "background draft"
    finally:
        release.set()
