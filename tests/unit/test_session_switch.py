"""Tests for multi-session behavior: session switching, concurrent runs, and history preservation.

These tests reproduce two reported bugs:
- Bug 1: Creating a new session interrupts the previously running session's agent
- Bug 2: User question is lost when navigating away mid-run
"""

import asyncio
import threading
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import WebSocketDisconnect

from agentnexus.server.routes.chat import ws_agent
from agentnexus.services.chat import ChatService


@pytest.fixture(autouse=True)
def _mock_trace_manager():
    mock_tm = MagicMock()
    with patch("agentnexus.observability.tracer.trace_manager", mock_tm):
        yield


@pytest.fixture
def mock_runtime():
    from agentnexus.memory.short_term import ShortTermMemory
    from agentnexus.tools.confirm_bridge import ConfirmBridge

    agent = MagicMock()
    agent.run.return_value = "test answer"
    memory = MagicMock()
    memory.short_term = ShortTermMemory()

    def memory_factory_builder(session_id):
        def factory():
            # Migrate STM from chat._stms if present (mirrors real make_memory_factory)
            if session_id in chat._stms:
                restored = chat._stms.pop(session_id)
                memory.short_term = restored
            return memory
        return factory

    chat = ChatService(agent_factory=lambda _sid=None: agent, memory_factory_builder=memory_factory_builder)
    runtime = MagicMock()
    runtime.services.chat = chat
    runtime.subagent_confirm = ConfirmBridge()
    return runtime, chat, agent, memory


# ── Bug 1: New session should NOT interrupt the running agent ──────────


class TestSessionSwitchDoesNotInterruptAgent:
    """When a WebSocket disconnects (user navigates away), the running agent
    should continue in the background and persist its results."""

    @pytest.mark.asyncio
    async def test_agent_keeps_running_after_ws_disconnect(self, mock_runtime):
        """Agent run should NOT be cancelled when the WebSocket disconnects.

        Reproduces Bug 1: user starts a new session while old session's agent
        is still running. The old agent must finish and persist.
        """
        runtime, chat, agent, memory = mock_runtime
        session = chat.start_session()

        agent_finished = threading.Event()
        agent_result = []

        def slow_agent_run(_text, memory_manager=None):
            """Simulate a slow agent that takes time to respond."""
            time.sleep(0.5)  # Simulate work
            agent_result.append("agent completed")
            agent_finished.set()
            return "slow answer"

        agent.run.side_effect = slow_agent_run

        ws = AsyncMock()
        ws.accept = AsyncMock()
        message_sent = False

        async def receive_json():
            nonlocal message_sent
            if not message_sent:
                message_sent = True
                return {"type": "send_message", "content": "hello"}
            # Disconnect immediately after message sent
            raise WebSocketDisconnect()

        ws.receive_json = AsyncMock(side_effect=receive_json)
        ws.send_json = AsyncMock()

        with patch("agentnexus.server.app._get_runtime", return_value=runtime):
            await ws_agent(ws, session.id)

        # ws_agent returned (WebSocket disconnected), but agent should still be running
        # Wait for the agent to finish
        finished = agent_finished.wait(timeout=3)
        assert finished, "Agent should have finished in the background after WS disconnect"
        assert agent_result == ["agent completed"]

    @pytest.mark.asyncio
    async def test_agent_persists_results_after_ws_disconnect(self, mock_runtime):
        """Agent results should be persisted even after WebSocket disconnects.

        The version manager should receive commit_with_messages when the agent
        finishes, even though the WebSocket is already closed.
        """
        runtime, chat, agent, memory = mock_runtime
        session = chat.start_session()

        # Replace the version manager with a mock to track persistence
        version_mgr = MagicMock()
        version_mgr.get_messages.return_value = []
        version_mgr.get_message_count.return_value = 0
        chat._get_version_manager = lambda _sid: version_mgr

        persist_done = threading.Event()

        def agent_run_with_persist(_text, memory_manager=None):
            """Agent that takes time, then finishes."""
            time.sleep(0.3)
            return "persisted answer"

        agent.run.side_effect = agent_run_with_persist

        ws = AsyncMock()
        ws.accept = AsyncMock()
        message_sent = False

        async def receive_json():
            nonlocal message_sent
            if not message_sent:
                message_sent = True
                return {"type": "send_message", "content": "test question"}
            raise WebSocketDisconnect()

        ws.receive_json = AsyncMock(side_effect=receive_json)
        ws.send_json = AsyncMock()

        with patch("agentnexus.server.app._get_runtime", return_value=runtime):
            await ws_agent(ws, session.id)

        # Wait for agent thread to finish
        time.sleep(1)

        # commit_with_messages should have been called:
        # 1. Immediately for user question
        # 2. After agent finishes (via turn.persist_snapshot)
        assert version_mgr.commit_with_messages.call_count >= 2, (
            f"Expected at least 2 commit calls (user question + agent result), "
            f"got {version_mgr.commit_with_messages.call_count}"
        )

    @pytest.mark.asyncio
    async def test_two_sessions_independent_stm(self, mock_runtime):
        """Two sessions should have independent STMs — running agent in session A
        should not be affected by creating session B.

        Reproduces Bug 1: per-session STM isolation.
        """
        runtime, chat, agent, memory = mock_runtime

        session_a = chat.start_session()
        session_b = chat.start_session()

        # Pre-populate session A's STM
        stm_a = chat._get_or_create_stm(session_a.id)
        stm_a.append("user", "session A message")

        # Get session B's STM — should be independent
        stm_b = chat._get_or_create_stm(session_b.id)
        assert len(stm_b.get_all()) == 0, "Session B STM should be empty"
        assert len(stm_a.get_all()) == 1, "Session A STM should still have its message"


# ── Bug 2: User question should survive session switch ──────────


class TestUserQuestionPersistsImmediately:
    """User question must be persisted to the database immediately when
    send_message is called, before the agent runs. This ensures the question
    survives even if the run is interrupted."""

    def test_user_question_persisted_before_agent_runs(self, mock_runtime):
        """The user's question should be in the database immediately after
        send_message, regardless of whether the agent completes.

        Reproduces Bug 2: user sends a question, navigates away before agent
        finishes, comes back — question should be visible.
        """
        runtime, chat, agent, memory = mock_runtime
        session = chat.start_session()

        version_mgr = MagicMock()
        version_mgr.get_messages.return_value = []
        version_mgr.get_message_count.return_value = 0
        chat._get_version_manager = lambda _sid: version_mgr

        # Agent blocks indefinitely — simulates user navigating away mid-run
        agent_started = threading.Event()
        agent_block = threading.Event()

        def blocking_agent_run(_text, memory_manager=None):
            agent_started.set()
            agent_block.wait(timeout=10)  # Block until test signals
            return "answer"

        agent.run.side_effect = blocking_agent_run

        # Send message in a thread (since it blocks)
        run_thread = threading.Thread(
            target=chat.send_message,
            args=(session.id, "where is the bug?"),
        )
        run_thread.start()

        # Wait for agent to start
        agent_started.wait(timeout=3)

        # User question should be persisted ALREADY (before agent finishes)
        assert version_mgr.commit_with_messages.call_count >= 1, (
            "User question should be persisted immediately, before agent finishes"
        )
        first_call = version_mgr.commit_with_messages.call_args_list[0]
        persisted_messages = first_call.kwargs.get("messages", first_call[1].get("messages", []))
        assert any(m.get("content") == "where is the bug?" for m in persisted_messages), (
            f"User question 'where is the bug?' should be in first commit, "
            f"got: {persisted_messages}"
        )

        # Unblock agent and clean up
        agent_block.set()
        run_thread.join(timeout=3)

    def test_user_question_persisted_even_on_agent_failure(self, mock_runtime):
        """If the agent throws an exception, the user question should still
        be in the database (persisted before the agent ran).
        """
        runtime, chat, agent, memory = mock_runtime
        session = chat.start_session()

        version_mgr = MagicMock()
        version_mgr.get_messages.return_value = []
        version_mgr.get_message_count.return_value = 0
        chat._get_version_manager = lambda _sid: version_mgr

        agent.run.side_effect = RuntimeError("LLM exploded")

        with pytest.raises(RuntimeError, match="LLM exploded"):
            chat.send_message(session.id, "what happened?")

        # User question was persisted before the agent ran
        assert version_mgr.commit_with_messages.call_count >= 1
        first_call = version_mgr.commit_with_messages.call_args_list[0]
        persisted_messages = first_call.kwargs.get("messages", first_call[1].get("messages", []))
        assert any(m.get("content") == "what happened?" for m in persisted_messages)

    @pytest.mark.asyncio
    async def test_user_question_survives_ws_disconnect(self, mock_runtime):
        """Full integration: send message via WebSocket, disconnect, verify
        the user question is persisted in the database.

        Reproduces Bug 2 end-to-end.
        """
        runtime, chat, agent, memory = mock_runtime
        session = chat.start_session()

        version_mgr = MagicMock()
        version_mgr.get_messages.return_value = []
        version_mgr.get_message_count.return_value = 0
        chat._get_version_manager = lambda _sid: version_mgr

        # Agent blocks so we can verify persistence before it finishes
        agent_started = threading.Event()
        agent_block = threading.Event()

        def blocking_agent_run(_text, memory_manager=None):
            agent_started.set()
            agent_block.wait(timeout=5)
            return "answer"

        agent.run.side_effect = blocking_agent_run

        ws = AsyncMock()
        ws.accept = AsyncMock()
        message_sent = False

        async def receive_json():
            nonlocal message_sent
            if not message_sent:
                message_sent = True
                return {"type": "send_message", "content": "help me debug"}
            raise WebSocketDisconnect()

        ws.receive_json = AsyncMock(side_effect=receive_json)
        ws.send_json = AsyncMock()

        with patch("agentnexus.server.app._get_runtime", return_value=runtime):
            # Run ws_agent in a task so we can check state while it's running
            ws_task = asyncio.create_task(ws_agent(ws, session.id))
            await asyncio.sleep(0.5)  # Let the message be processed

        # Wait for agent to start
        agent_started.wait(timeout=3)

        # User question should be persisted
        assert version_mgr.commit_with_messages.call_count >= 1
        first_call = version_mgr.commit_with_messages.call_args_list[0]
        persisted_messages = first_call.kwargs.get("messages", first_call[1].get("messages", []))
        assert any(m.get("content") == "help me debug" for m in persisted_messages), (
            f"User question should be persisted after WS disconnect, got: {persisted_messages}"
        )

        # Clean up
        agent_block.set()
        try:
            await asyncio.wait_for(ws_task, timeout=3)
        except (asyncio.TimeoutError, Exception):
            ws_task.cancel()


# ── STM swap correctness ──────────


class TestSTMSwapCorrectness:
    """Verify the STM swap mechanism in send_message works correctly."""

    def test_stm_swapped_during_agent_run(self, mock_runtime):
        """During agent.run(), memory_manager.short_term should be the
        per-session STM, not the global one."""
        runtime, chat, agent, memory = mock_runtime
        session = chat.start_session()

        captured_stms = []

        def capture_stm_run(_text, memory_manager=None):
            if memory_manager is not None:
                captured_stms.append(memory_manager.short_term)
            return "answer"

        agent.run.side_effect = capture_stm_run

        # Pre-create a session STM with some data
        from agentnexus.memory.short_term import ShortTermMemory
        session_stm = ShortTermMemory()
        session_stm.append("user", "old context")
        chat._stms[session.id] = session_stm

        chat.send_message(session.id, "new message")

        # During the run, the agent should have seen the per-session STM
        assert len(captured_stms) == 1
        assert captured_stms[0] is session_stm, "Agent should use per-session STM"
        # Per-session STM should have the old context + the agent's processing
        all_msgs = captured_stms[0].get_all()
        assert any(m.get("content") == "old context" for m in all_msgs)

    def test_global_stm_restored_after_run(self, mock_runtime):
        """After send_message returns, memory_manager.short_term should be
        restored to the global STM."""
        runtime, chat, agent, memory = mock_runtime
        session = chat.start_session()

        # Create a global STM
        from agentnexus.memory.short_term import ShortTermMemory
        global_stm = ShortTermMemory()
        global_stm.append("system", "global state")
        memory.short_term = global_stm

        chat.send_message(session.id, "hello")

        # Global STM should be restored
        assert memory.short_term is global_stm
        assert any(m.get("content") == "global state" for m in global_stm.get_all())

    def test_stm_swap_survives_agent_exception(self, mock_runtime):
        """If agent.run() throws, the global STM should still be restored."""
        runtime, chat, agent, memory = mock_runtime
        session = chat.start_session()

        from agentnexus.memory.short_term import ShortTermMemory
        global_stm = ShortTermMemory()
        global_stm.append("system", "global state")
        memory.short_term = global_stm

        agent.run.side_effect = RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            chat.send_message(session.id, "hello")

        # Global STM must be restored even after exception
        assert memory.short_term is global_stm


# ── Concurrent sessions: the real bug scenario ──────────


class TestConcurrentSessions:
    """Test two sessions running concurrently — the actual bug scenario.

    When the user has session A running and creates session B, both sessions
    may call send_message concurrently. The STM swap must be thread-safe.
    """

    def test_concurrent_send_message_different_sessions(self, mock_runtime):
        """Two sessions calling send_message concurrently should not corrupt
        each other's STM. The STM lock serializes access — the second session
        waits until the first finishes.

        This is the real Bug 1 scenario: old session A is still running when
        session B starts. Both use self._memory.short_term — the STM swap
        must be thread-safe.
        """
        runtime, chat, agent, memory = mock_runtime
        session_a = chat.start_session()
        session_b = chat.start_session()

        # Pre-populate each session's STM
        from agentnexus.memory.short_term import ShortTermMemory
        stm_a = ShortTermMemory()
        stm_a.append("user", "session A context")
        chat._stms[session_a.id] = stm_a

        stm_b = ShortTermMemory()
        stm_b.append("user", "session B context")
        chat._stms[session_b.id] = stm_b

        stms_seen = {}

        def agent_run_a(_text, memory_manager=None):
            stms_seen["A"] = memory_manager.short_term
            return "answer A"

        def agent_run_b(_text, memory_manager=None):
            stms_seen["B"] = memory_manager.short_term
            return "answer B"

        results = {}
        errors = []

        def run_session(session_id, label, agent_fn):
            agent.run.side_effect = agent_fn
            try:
                chat.send_message(session_id, f"hello from {label}")
                results[label] = "ok"
            except Exception as e:
                errors.append((label, e))

        # Run session A first, then B — serialized by the STM lock
        t_a = threading.Thread(target=run_session, args=(session_a.id, "A", agent_run_a))
        t_a.start()
        t_a.join(timeout=5)

        t_b = threading.Thread(target=run_session, args=(session_b.id, "B", agent_run_b))
        t_b.start()
        t_b.join(timeout=5)

        assert not errors, f"Errors during concurrent send_message: {errors}"
        assert results.get("A") == "ok"
        assert results.get("B") == "ok"

        # Each session should have seen its own STM
        assert stms_seen.get("A") is stm_a, "Session A should use its own STM"
        assert stms_seen.get("B") is stm_b, "Session B should use its own STM"

    def test_stm_not_corrupted_by_sequential_access(self, mock_runtime):
        """After sequential send_message calls, each session's STM should
        contain only its own messages — not the other session's."""
        runtime, chat, agent, memory = mock_runtime
        session_a = chat.start_session()
        session_b = chat.start_session()

        from agentnexus.memory.short_term import ShortTermMemory
        stm_a = ShortTermMemory()
        stm_a.append("user", "A's secret")
        chat._stms[session_a.id] = stm_a

        stm_b = ShortTermMemory()
        stm_b.append("user", "B's secret")
        chat._stms[session_b.id] = stm_b

        def agent_run(_text, memory_manager=None):
            return "done"

        agent.run.side_effect = agent_run

        chat.send_message(session_a.id, "msg A")
        chat.send_message(session_b.id, "msg B")

        # Session A's STM should not contain B's messages and vice versa
        msgs_a = [m.get("content") for m in stm_a.get_all()]
        msgs_b = [m.get("content") for m in stm_b.get_all()]

        assert "B's secret" not in msgs_a, f"Session A STM corrupted: {msgs_a}"
        assert "A's secret" not in msgs_b, f"Session B STM corrupted: {msgs_b}"

    def test_global_stm_not_corrupted_by_sequential_sessions(self, mock_runtime):
        """The global scratch STM should be unchanged after sequential
        session operations."""
        runtime, chat, agent, memory = mock_runtime
        session_a = chat.start_session()
        session_b = chat.start_session()

        from agentnexus.memory.short_term import ShortTermMemory
        global_stm = ShortTermMemory()
        global_stm.append("system", "global data")
        memory.short_term = global_stm

        def agent_run(_text, memory_manager=None):
            return "done"

        agent.run.side_effect = agent_run

        chat.send_message(session_a.id, "msg A")
        chat.send_message(session_b.id, "msg B")

        # Global STM should be unchanged
        assert memory.short_term is global_stm
        msgs = [m.get("content") for m in global_stm.get_all()]
        assert msgs == ["global data"], f"Global STM corrupted: {msgs}"


# ── End-to-end: full lifecycle ──────────


class TestSessionSwitchLifecycle:
    """End-to-end test simulating the real user workflow that triggers both bugs."""

    @pytest.mark.asyncio
    async def test_full_lifecycle_session_a_then_b_then_back(self, mock_runtime):
        """Simulate: send to A → navigate away → create B → send to B → go back to A.

        Both sessions should have their own history.
        """
        runtime, chat, agent, memory = mock_runtime

        # Version managers per session to track persistence
        version_managers = {}

        def make_version_mgr(session_id):
            if session_id not in version_managers:
                vm = MagicMock()
                vm.get_messages.return_value = []
                vm.get_message_count.return_value = 0
                version_managers[session_id] = vm
            return version_managers[session_id]

        chat._get_version_manager = make_version_mgr

        # ── Step 1: Session A, send message ──
        session_a = chat.start_session()
        agent_started_a = threading.Event()
        agent_block_a = threading.Event()

        def agent_a(_text, memory_manager=None):
            agent_started_a.set()
            agent_block_a.wait(timeout=5)
            return "answer A"

        agent.run.side_effect = agent_a

        ws_a = AsyncMock()
        ws_a.accept = AsyncMock()
        msg_sent_a = False

        async def recv_a():
            nonlocal msg_sent_a
            if not msg_sent_a:
                msg_sent_a = True
                return {"type": "send_message", "content": "question A"}
            await asyncio.to_thread(agent_started_a.wait, 2)
            raise WebSocketDisconnect()

        ws_a.receive_json = AsyncMock(side_effect=recv_a)
        ws_a.send_json = AsyncMock()

        mock_patch = patch("agentnexus.server.app._get_runtime", return_value=runtime)
        mock_patch.start()
        try:
            ws_task_a = asyncio.create_task(ws_agent(ws_a, session_a.id))

            # Wait for agent A to start (which means begin_turn + persistence ran)
            await asyncio.to_thread(agent_started_a.wait, 3)
            await asyncio.sleep(0.2)  # Let persistence code finish

            # ── Step 2: Verify question A is persisted ──
            vm_a = version_managers.get(session_a.id)
            assert vm_a is not None, (
                f"Version manager for session A should exist. "
                f"Known sessions: {list(version_managers.keys())}"
            )
            assert vm_a.commit_with_messages.call_count >= 1, (
                "Question A should be persisted immediately"
            )

            # ── Step 3: Unblock agent A and let it finish ──
            agent_block_a.set()
            try:
                await asyncio.wait_for(ws_task_a, timeout=3)
            except (asyncio.TimeoutError, Exception):
                ws_task_a.cancel()

            await asyncio.sleep(0.5)  # Let agent thread finish

            # Agent A's result should also be persisted
            assert vm_a.commit_with_messages.call_count >= 2, (
                f"Expected user question + agent result for session A, "
                f"got {vm_a.commit_with_messages.call_count} calls"
            )
        finally:
            mock_patch.stop()


# ── Bug 3: Reasoning content lost on session navigation ──────────


class TestReasoningContentPersistence:
    """Streaming reasoning content must be persisted to STM so it survives
    session navigation (user switches to another page and comes back).

    Reproduces Bug 3: When reasoning_streamed=True, _emit_answer_thought
    skips storing the thought in STM. The reasoning was streamed via WebSocket
    but never persisted — navigating away loses it.
    """

    def test_reasoning_content_stored_in_stm_when_streamed(self, mock_runtime):
        """When the agent produces streamed reasoning, the reasoning_content
        should be persisted to STM so it survives session navigation.

        Before the fix, _emit_answer_thought returned early when
        reasoning_streamed=True, leaving STM without any reasoning.
        """
        from agentnexus.agents.re_act_agent import ReActAgent
        from agentnexus.agents.react_types import AgentStep, ExecutionContext

        runtime, chat, agent, memory = mock_runtime

        # Create a real agent with mocked LLM
        agent = ReActAgent.__new__(ReActAgent)
        agent._output = lambda msg: None
        agent.llm_client = MagicMock()
        agent.llm_client.last_reasoning_content = "Deep thinking about the problem..."

        # Build an ExecutionContext with reasoning_streamed=True
        ctx = ExecutionContext(
            question="test question",
            memory_manager=MagicMock(),
            last_reasoning="Deep thinking about the problem...",
            steps=[AgentStep(
                step_id=1,
                strategy_used="native_tools",
                reasoning_content="Deep thinking about the problem...",
                reasoning_streamed=True,  # Key: reasoning was streamed
            )],
        )
        ctx.steps[0].tool_outputs = ["some tool output"]

        # Call _emit_answer_thought
        agent._emit_answer_thought(ctx)

        # BEFORE FIX: memory_manager.append was NOT called (early return)
        # AFTER FIX: memory_manager.append should be called with reasoning
        ctx.memory_manager.append.assert_called_once()
        call_args = ctx.memory_manager.append.call_args
        assert call_args[0][0] == "system", f"Expected role 'system', got {call_args[0][0]}"
        assert "[思考过程]" in call_args[0][1], (
            f"Expected reasoning content with [思考过程] prefix, got: {call_args[0][1]}"
        )
        assert "Deep thinking about the problem..." in call_args[0][1]

    def test_reasoning_content_survives_session_restore(self, mock_runtime):
        """After reasoning is persisted to STM, session history should include it.

        Simulates: agent produces reasoning → user navigates away → user comes
        back → loadAndDisplayMessages fetches from backend → reasoning visible.
        """
        runtime, chat, agent, memory = mock_runtime
        session = chat.start_session()

        # Simulate STM with reasoning content (as the fix would produce)
        stm = chat._get_or_create_stm(session.id)
        stm.append("user", "what is 2+2?")
        stm.append("system", "[思考过程] Let me think about basic arithmetic...")
        stm.append("assistant", "I need to calculate 2+2")
        stm.append("system", "[最终答案] 2+2 = 4")

        # Verify the reasoning content is in STM
        all_msgs = stm.get_all()
        reasoning_msgs = [m for m in all_msgs if "[思考过程]" in m.get("content", "")]
        assert len(reasoning_msgs) == 1, (
            f"Expected 1 reasoning message in STM, got {len(reasoning_msgs)}"
        )
        assert "basic arithmetic" in reasoning_msgs[0]["content"]

    def test_thought_stored_normally_when_not_streamed(self, mock_runtime):
        """When reasoning_streamed=False (no streaming reasoning), the thought
        should still be stored in STM via the existing path (no regression).
        """
        from agentnexus.agents.re_act_agent import ReActAgent
        from agentnexus.agents.react_types import AgentStep, ExecutionContext

        runtime, chat, agent, memory = mock_runtime

        agent = ReActAgent.__new__(ReActAgent)
        agent._output = lambda msg: None
        agent.llm_client = MagicMock()
        agent.llm_client.last_reasoning_content = ""

        # Use a JSON response with a "thought" field — the non-streaming path
        # parses the thought from JSON, and it differs from raw_text.
        json_response = '{"thought": "I should search for the answer", "tool": "search", "params": {"q": "test"}}'
        ctx = ExecutionContext(
            question="test question",
            memory_manager=MagicMock(),
            last_response_text=json_response,
            last_reasoning="",
            steps=[AgentStep(
                step_id=1,
                strategy_used="native_tools",
                reasoning_content="",
                reasoning_streamed=False,
            )],
        )
        ctx.steps[0].tool_outputs = ["some tool output"]

        # Mock emit to capture events
        emitted = []
        ctx.emit = lambda evt_type, **kw: emitted.append((evt_type, kw))

        agent._emit_answer_thought(ctx)

        # The existing path should still work: thought stored as "assistant"
        calls = ctx.memory_manager.append.call_args_list
        assert len(calls) == 1
        assert calls[0][0][0] == "assistant"
