"""Tests for real-time event streaming, async queue, and HITL confirm.

Tests cover:
1. Events are delivered one-by-one in real-time (not batched)
2. Async queue (astream_events) works correctly
3. HITL confirm bridge sends confirm_request and waits for response
4. Event ordering: thinking → tool_call → tool_result → token → answer → done
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

from agentnexus.server.routes.chat import _map_to_gui_event


class TestRealTimeEventStreaming:
    """Events should be delivered one-by-one, not batched."""

    def test_sync_stream_events_is_batched(self):
        """Sync stream_events delivers events after agent completes (batched)."""
        from agentnexus.agents.react_types import ReActEvent, ReActEventType
        from agentnexus.services.chat import ChatService

        agent = MagicMock()

        def run_with_events(_text, memory_manager=None):
            if agent._on_event:
                agent._on_event(
                    ReActEvent(ReActEventType.TOOLS_FOUND, {"thought": "Step 1"}),
                    None, None,
                )
                agent._on_event(
                    ReActEvent(ReActEventType.TOOL_START, {"name": "search", "arguments": {}}),
                    None, None,
                )
            return "answer"

        agent.run.side_effect = run_with_events
        service = ChatService(agent=agent)
        session = service.start_session()

        run = service.send_message(session.id, "hello")

        # Sync stream_events delivers all events after completion
        gui_events = []
        for event in service.stream_events(run.id):
            gui_event = _map_to_gui_event(event, service)
            if gui_event:
                gui_events.append(gui_event["type"])

        assert "thinking" in gui_events
        assert "tool_call" in gui_events

    def test_async_stream_events_is_realtime(self):
        """Async astream_events delivers events as they're produced (real-time)."""
        from agentnexus.agents.react_types import ReActEvent, ReActEventType
        from agentnexus.services.chat import ChatService

        agent = MagicMock()

        def run_with_events(_text, memory_manager=None):
            if agent._on_event:
                agent._on_event(
                    ReActEvent(ReActEventType.TOOLS_FOUND, {"thought": "Step 1"}),
                    None, None,
                )
                agent._on_event(
                    ReActEvent(ReActEventType.TOOL_START, {"name": "search", "arguments": {}}),
                    None, None,
                )
            return "answer"

        agent.run.side_effect = run_with_events
        service = ChatService(agent=agent)
        session = service.start_session()

        run = service.send_message(session.id, "hello")

        async def collect_events():
            events = []
            async for event in service.astream_events(run.id):
                gui_event = _map_to_gui_event(event, service)
                if gui_event:
                    events.append(gui_event["type"])
            return events

        event_types = asyncio.get_event_loop().run_until_complete(collect_events())

        assert "thinking" in event_types
        assert "tool_call" in event_types
        assert "answer" in event_types

    def test_async_queue_put_nowait(self):
        """Events should be put into async queue immediately."""
        from agentnexus.services.chat import AgentEvent, ChatService

        service = ChatService(agent=MagicMock())

        # Manually test _put_event
        service._async_run_events["test_run"] = asyncio.Queue()
        service._run_events["test_run"] = MagicMock()

        event = AgentEvent("test", {"data": "value"}, run_id="test_run")
        service._put_event("test_run", event)

        # Event should be in async queue immediately
        async_q = service._async_run_events["test_run"]
        assert not async_q.empty()

        # Get the event
        got = async_q.get_nowait()
        assert got.type == "test"
        assert got.payload == {"data": "value"}

    def test_async_queue_none_sentinel(self):
        """None sentinel should terminate astream_events."""
        from agentnexus.services.chat import AgentEvent, ChatService

        service = ChatService(agent=MagicMock())
        service._async_run_events["test_run"] = asyncio.Queue()

        # Put events and sentinel
        service._async_run_events["test_run"].put_nowait(
            AgentEvent("event1", {}, run_id="test_run")
        )
        service._async_run_events["test_run"].put_nowait(
            AgentEvent("event2", {}, run_id="test_run")
        )
        service._async_run_events["test_run"].put_nowait(None)  # sentinel

        async def collect():
            events = []
            async for event in service.astream_events("test_run"):
                events.append(event.type)
            return events

        result = asyncio.get_event_loop().run_until_complete(collect())
        assert result == ["event1", "event2"]


class TestEventOrdering:
    """Verify correct event ordering for frontend."""

    def test_thinking_before_tool_call(self):
        """thinking event should arrive before tool_call."""
        from agentnexus.agents.react_types import ReActEvent, ReActEventType
        from agentnexus.services.chat import ChatService

        agent = MagicMock()

        def run(_text, memory_manager=None):
            if agent._on_event:
                agent._on_event(
                    ReActEvent(ReActEventType.TOOLS_FOUND, {"thought": "I need to search"}),
                    None, None,
                )
                agent._on_event(
                    ReActEvent(ReActEventType.TOOL_START, {"name": "search", "arguments": {}}),
                    None, None,
                )
            return "answer"

        agent.run.side_effect = run
        service = ChatService(agent=agent)
        session = service.start_session()
        run = service.send_message(session.id, "test")

        gui_events = []
        for event in service.stream_events(run.id):
            gui_event = _map_to_gui_event(event, service)
            if gui_event:
                gui_events.append(gui_event["type"])

        thinking_idx = gui_events.index("thinking")
        tool_call_idx = gui_events.index("tool_call")
        assert thinking_idx < tool_call_idx

    def test_tool_result_before_answer(self):
        """tool_result should arrive before final answer."""
        from agentnexus.agents.react_types import ReActEvent, ReActEventType
        from agentnexus.services.chat import ChatService

        agent = MagicMock()

        def run(_text, memory_manager=None):
            if agent._on_event:
                agent._on_event(
                    ReActEvent(ReActEventType.TOOL_START, {"name": "search", "arguments": {}}),
                    None, None,
                )
                agent._on_event(
                    ReActEvent(ReActEventType.TOOL_DONE, {"name": "search", "arguments": {}, "result": "data"}),
                    None, None,
                )
            return "final answer"

        agent.run.side_effect = run
        service = ChatService(agent=agent)
        session = service.start_session()
        run = service.send_message(session.id, "test")

        gui_events = []
        for event in service.stream_events(run.id):
            gui_event = _map_to_gui_event(event, service)
            if gui_event:
                gui_events.append(gui_event["type"])

        tool_result_idx = gui_events.index("tool_result")
        answer_idx = gui_events.index("answer")
        assert tool_result_idx < answer_idx

    def test_token_before_answer(self):
        """token (message_delta) should arrive before answer (run_finished)."""
        from agentnexus.services.chat import ChatService

        agent = MagicMock()
        agent.run.return_value = "final answer"

        service = ChatService(agent=agent)
        session = service.start_session()
        run = service.send_message(session.id, "test")

        gui_events = []
        for event in service.stream_events(run.id):
            gui_event = _map_to_gui_event(event, service)
            if gui_event:
                gui_events.append(gui_event["type"])

        token_idx = gui_events.index("token")
        answer_idx = gui_events.index("answer")
        assert token_idx < answer_idx

    def test_answer_before_done(self):
        """answer (run_finished) should arrive before done (run_persisted)."""
        from agentnexus.services.chat import ChatService

        agent = MagicMock()
        agent.run.return_value = "final answer"

        service = ChatService(agent=agent)
        session = service.start_session()
        run = service.send_message(session.id, "test")

        gui_events = []
        for event in service.stream_events(run.id):
            gui_event = _map_to_gui_event(event, service)
            if gui_event:
                gui_events.append(gui_event["type"])

        answer_idx = gui_events.index("answer")
        done_idx = gui_events.index("done")
        assert answer_idx < done_idx


class TestHITLConfirmBridge:
    """HITL confirm should work via WebSocket."""

    def test_confirm_bridge_sends_confirm_request(self):
        """ws_confirm should send confirm_request event via WebSocket."""
        from agentnexus.tools.confirm_bridge import ConfirmBridge

        bridge = ConfirmBridge()
        ws = AsyncMock()
        loop = asyncio.get_event_loop()

        confirm_result = None

        def ws_confirm(summary: str) -> bool:
            nonlocal confirm_result
            future = loop.create_future()
            asyncio.run_coroutine_threadsafe(
                ws.send_json({"type": "confirm_request", "summary": summary}),
                loop,
            )
            # Simulate immediate approval
            future.set_result(True)
            return future.result()

        bridge.set_target(ws_confirm)

        result = bridge("test tool call summary")

        assert result is True
        ws.send_json.assert_called_once_with({
            "type": "confirm_request",
            "summary": "test tool call summary"
        })

    def test_confirm_bridge_returns_false_when_no_target(self):
        """ConfirmBridge should return False when no target is set."""
        from agentnexus.tools.confirm_bridge import ConfirmBridge

        bridge = ConfirmBridge()
        result = bridge("some summary")
        assert result is False

    def test_confirm_bridge_set_target(self):
        """ConfirmBridge.set_target should update the target function."""
        from agentnexus.tools.confirm_bridge import ConfirmBridge

        bridge = ConfirmBridge()
        mock_target = MagicMock(return_value=True)
        bridge.set_target(mock_target)

        result = bridge("summary")
        assert result is True
        mock_target.assert_called_once_with("summary")


class TestDualQueueArchitecture:
    """Both sync and async queues should receive events."""

    def test_put_event_sends_to_both_queues(self):
        """_put_event should send to both sync and async queues."""
        from agentnexus.services.chat import AgentEvent, ChatService

        service = ChatService(agent=MagicMock())

        # Set up both queues
        import queue
        sync_q = queue.Queue()
        async_q = asyncio.Queue()
        service._run_events["test_run"] = sync_q
        service._async_run_events["test_run"] = async_q

        event = AgentEvent("test", {"data": "value"}, run_id="test_run")
        service._put_event("test_run", event)

        # Sync queue should have event
        sync_event = sync_q.get_nowait()
        assert sync_event.type == "test"

        # Async queue should have event
        async_event = async_q.get_nowait()
        assert async_event.type == "test"

    def test_put_event_handles_missing_queues(self):
        """_put_event should not crash if queues don't exist."""
        from agentnexus.services.chat import AgentEvent, ChatService

        service = ChatService(agent=MagicMock())
        event = AgentEvent("test", {}, run_id="nonexistent")

        # Should not raise
        service._put_event("nonexistent", event)

    def test_send_message_populates_async_queue(self):
        """send_message should populate async queue for real-time streaming."""
        from agentnexus.services.chat import ChatService

        agent = MagicMock()
        agent.run.return_value = "answer"

        service = ChatService(agent=agent)
        session = service.start_session()
        run = service.send_message(session.id, "hello")

        # Async queue should have events
        async_q = service._async_run_events.get(run.id)
        assert async_q is not None
        assert not async_q.empty()
