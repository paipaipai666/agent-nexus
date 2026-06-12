"""Tests for WebSocket agent event streaming."""
import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import WebSocketDisconnect

from agentnexus.server.routes.chat import ws_agent
from agentnexus.services.chat import ChatService


@pytest.fixture(autouse=True)
def _mock_trace_manager():
    """Prevent trace_manager from trying to serialize MagicMock objects."""
    mock_tm = MagicMock()
    with patch("agentnexus.observability.tracer.trace_manager", mock_tm):
        yield


class TestWebSocketAgentStream:
    """Test ws_agent WebSocket endpoint streams events correctly."""

    @pytest.fixture
    def mock_runtime(self):
        from agentnexus.tools.confirm_bridge import ConfirmBridge

        agent = MagicMock()
        agent.run.return_value = "test answer"
        chat = ChatService(agent=agent)
        session = chat.start_session()

        runtime = MagicMock()
        runtime.services.chat = chat
        runtime.subagent_confirm = ConfirmBridge()
        return runtime, chat, session

    @staticmethod
    def _task_coro_name(task: asyncio.Task) -> str:
        coro = task.get_coro()
        code = getattr(coro, "cr_code", None)
        return getattr(code, "co_name", getattr(coro, "__name__", ""))

    @staticmethod
    def _ws_until_event(event_type: str, content: str = "hello"):
        ws = AsyncMock()
        ws.accept = AsyncMock()
        sent_events: list[dict] = []
        target_sent = asyncio.Event()
        received_message = False

        async def receive_json():
            nonlocal received_message
            if not received_message:
                received_message = True
                return {"type": "send_message", "content": content}
            await asyncio.wait_for(target_sent.wait(), timeout=1)
            raise WebSocketDisconnect()

        async def send_json(payload: dict):
            sent_events.append(payload)
            if payload.get("type") == event_type:
                target_sent.set()

        ws.receive_json = AsyncMock(side_effect=receive_json)
        ws.send_json = AsyncMock(side_effect=send_json)
        return ws, sent_events

    @pytest.mark.asyncio
    async def test_ws_streams_answer_event(self, mock_runtime):
        """Verify answer and done events are sent through WebSocket."""
        runtime, _chat, session = mock_runtime

        with patch("agentnexus.server.app._get_runtime", return_value=runtime):
            ws, sent_events = self._ws_until_event("done")

            await ws_agent(ws, session.id)

            event_types = [event.get("type") for event in sent_events]
            assert "answer" in event_types, f"Expected 'answer' in {event_types}"
            assert "done" in event_types, f"Expected 'done' in {event_types}"

            answer_event = next(event for event in sent_events if event.get("type") == "answer")
            assert answer_event["content"] == "test answer"

    @pytest.mark.asyncio
    async def test_ws_streams_thinking_then_answer(self, mock_runtime):
        """Verify thinking events appear before answer."""
        from agentnexus.agents.react_types import ReActEvent, ReActEventType

        runtime, chat, session = mock_runtime

        def simulate_run(_text, memory_manager=None):
            if hasattr(chat._agent, "_on_event") and chat._agent._on_event:
                chat._agent._on_event(
                    ReActEvent(ReActEventType.TOOLS_FOUND, {"thought": "analyzing..."}),
                    None,
                    None,
                )
            return "final"

        chat._agent.run.side_effect = simulate_run

        with patch("agentnexus.server.app._get_runtime", return_value=runtime):
            ws, sent_events = self._ws_until_event("done")

            await ws_agent(ws, session.id)

            event_types = [event.get("type") for event in sent_events]
            thinking_idx = event_types.index("thinking")
            answer_idx = event_types.index("answer")
            assert thinking_idx < answer_idx, "thinking should come before answer"

    @pytest.mark.asyncio
    async def test_ws_sends_done_after_answer(self, mock_runtime):
        """Verify 'done' event is sent after answer."""
        runtime, _chat, session = mock_runtime

        with patch("agentnexus.server.app._get_runtime", return_value=runtime):
            ws, sent_events = self._ws_until_event("done")

            await ws_agent(ws, session.id)

            event_types = [event.get("type") for event in sent_events]
            assert "answer" in event_types, f"Expected 'answer' in {event_types}"
            assert "done" in event_types, f"Expected 'done' in {event_types}"
            answer_idx = event_types.index("answer")
            done_idx = event_types.index("done")
            assert done_idx > answer_idx, "done should come after answer"

    @pytest.mark.asyncio
    async def test_ws_no_duplicate_answer(self, mock_runtime):
        """Verify answer event is sent exactly once."""
        runtime, _chat, session = mock_runtime

        with patch("agentnexus.server.app._get_runtime", return_value=runtime):
            ws, sent_events = self._ws_until_event("done")

            await ws_agent(ws, session.id)

            answer_events = [event for event in sent_events if event.get("type") == "answer"]
            assert len(answer_events) == 1, f"Expected 1 answer event, got {len(answer_events)}"

    @pytest.mark.asyncio
    async def test_ws_event_order_complete(self, mock_runtime):
        """Verify complete event ordering: thinking* tool* token answer done."""
        from agentnexus.agents.react_types import ReActEvent, ReActEventType

        runtime, chat, session = mock_runtime

        def simulate_run(_text, memory_manager=None):
            if hasattr(chat._agent, "_on_event") and chat._agent._on_event:
                chat._agent._on_event(
                    ReActEvent(ReActEventType.TOOLS_FOUND, {"thought": "thinking..."}),
                    None,
                    None,
                )
                chat._agent._on_event(
                    ReActEvent(ReActEventType.TOOL_START, {"name": "search", "arguments": {}}),
                    None,
                    None,
                )
                chat._agent._on_event(
                    ReActEvent(ReActEventType.TOOL_DONE, {"name": "search", "result": "ok"}),
                    None,
                    None,
                )
                chat._agent._on_event(
                    ReActEvent(ReActEventType.STREAM_TOKEN, {"token": "hello"}),
                    None,
                    None,
                )
            return "done"

        chat._agent.run.side_effect = simulate_run

        with patch("agentnexus.server.app._get_runtime", return_value=runtime):
            ws, sent_events = self._ws_until_event("done")

            await ws_agent(ws, session.id)

            event_types = [event.get("type") for event in sent_events]
            relevant = [
                event_type
                for event_type in event_types
                if event_type in ("thinking", "tool_call", "tool_result", "token", "answer", "done")
            ]

            assert relevant[0] == "thinking"
            assert "tool_call" in relevant
            assert "tool_result" in relevant
            assert "token" in relevant
            assert relevant[-2] == "answer"
            assert relevant[-1] == "done"

    @pytest.mark.asyncio
    async def test_ws_disconnect_cancels_stream_task(self, mock_runtime):
        """Disconnecting the socket should not leave stream_events pending."""
        runtime, chat, session = mock_runtime
        release_agent = threading.Event()
        created_tasks: list[asyncio.Task] = []
        real_create_task = asyncio.create_task

        def slow_run(_text, memory_manager=None):
            release_agent.wait(timeout=1)
            return "late answer"

        def track_task(coro, *args, **kwargs):
            task = real_create_task(coro, *args, **kwargs)
            created_tasks.append(task)
            return task

        chat._agent.run.side_effect = slow_run
        ws = AsyncMock()
        ws.accept = AsyncMock()
        ws.receive_json = AsyncMock(side_effect=[
            {"type": "send_message", "content": "hello"},
            WebSocketDisconnect(),
        ])
        ws.send_json = AsyncMock()

        with (
            patch("agentnexus.server.app._get_runtime", return_value=runtime),
            patch("agentnexus.server.routes.chat.asyncio.create_task", side_effect=track_task),
        ):
            try:
                await ws_agent(ws, session.id)
                stream_tasks = [task for task in created_tasks if self._task_coro_name(task) == "stream_events"]
                assert stream_tasks, "Expected ws_agent to create a stream_events task"
                assert all(task.done() for task in stream_tasks), "stream_events task was left pending after disconnect"
            finally:
                release_agent.set()
                if created_tasks:
                    await asyncio.gather(*created_tasks, return_exceptions=True)

    @pytest.mark.asyncio
    async def test_ws_stream_task_does_not_send_error_after_socket_close(self, mock_runtime):
        """A closed socket send should end streaming without a second error send."""
        runtime, _chat, session = mock_runtime
        sent_events: list[dict] = []
        received_message = False
        send_failed = asyncio.Event()
        closed_error = RuntimeError(
            "Unexpected ASGI message 'websocket.send', after sending 'websocket.close' or response already completed."
        )

        ws = AsyncMock()
        ws.accept = AsyncMock()

        async def receive_json():
            nonlocal received_message
            if not received_message:
                received_message = True
                return {"type": "send_message", "content": "hello"}
            await asyncio.wait_for(send_failed.wait(), timeout=1)
            raise WebSocketDisconnect()

        async def send_json(payload: dict):
            sent_events.append(payload)
            if payload.get("type") == "run_started":
                return
            send_failed.set()
            raise closed_error

        ws.receive_json = AsyncMock(side_effect=receive_json)
        ws.send_json = AsyncMock(side_effect=send_json)

        with patch("agentnexus.server.app._get_runtime", return_value=runtime):
            await ws_agent(ws, session.id)

        error_events = [event for event in sent_events if event.get("type") == "error"]
        assert error_events == []

    @pytest.mark.asyncio
    async def test_ws_run_started_uses_current_session_run_id(self):
        """The websocket should not subscribe to another session's concurrent run."""
        agent = MagicMock()
        chat = ChatService(agent=agent)
        target_session = chat.start_session()
        other_session = chat.start_session()
        other_run, _events, _turn = chat.begin_turn(other_session.id, "other")
        runtime = MagicMock()
        runtime.services.chat = chat
        sent_events: list[dict] = []
        ws = AsyncMock()
        ws.accept = AsyncMock()
        ws.receive_json = AsyncMock(side_effect=[
            {"type": "send_message", "content": "hello"},
            WebSocketDisconnect(),
        ])

        def run(_text, memory_manager=None):
            return "target answer"

        async def send_json(payload: dict):
            sent_events.append(payload)

        agent.run.side_effect = run
        ws.send_json = AsyncMock(side_effect=send_json)

        with patch("agentnexus.server.app._get_runtime", return_value=runtime):
            await ws_agent(ws, target_session.id)

        run_started = next(event for event in sent_events if event.get("type") == "run_started")
        assert run_started["run_id"] != other_run.id
        assert chat.get_run_snapshot(run_started["run_id"]).session_id == target_session.id

    @pytest.mark.asyncio
    async def test_ws_disconnect_unblocks_pending_confirm(self, mock_runtime):
        """Disconnecting while a HITL prompt is open should fail closed."""
        runtime, chat, session = mock_runtime
        confirm_started = threading.Event()
        confirm_finished = threading.Event()
        confirm_result: list[bool] = []
        sent_events: list[dict] = []
        ws = AsyncMock()
        ws.accept = AsyncMock()

        def run(_text, memory_manager=None):
            try:
                confirm_started.set()
                confirm_result.append(runtime.subagent_confirm("approve tool"))
                return "after confirm"
            finally:
                confirm_finished.set()

        async def receive_json():
            if not confirm_started.is_set():
                return {"type": "send_message", "content": "hello"}
            raise WebSocketDisconnect()

        async def send_json(payload: dict):
            sent_events.append(payload)

        chat._agent.run.side_effect = run
        ws.receive_json = AsyncMock(side_effect=receive_json)
        ws.send_json = AsyncMock(side_effect=send_json)

        with patch("agentnexus.server.app._get_runtime", return_value=runtime):
            await ws_agent(ws, session.id)

        assert any(event.get("type") == "confirm_request" for event in sent_events)
        assert confirm_finished.wait(timeout=1)
        assert confirm_result == [False]
