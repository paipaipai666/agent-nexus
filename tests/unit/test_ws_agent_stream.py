"""Tests for WebSocket agent event streaming."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentnexus.server.routes.chat import ws_agent
from agentnexus.services.chat import ChatService


class TestWebSocketAgentStream:
    """Test ws_agent WebSocket endpoint streams events correctly."""

    @pytest.fixture
    def mock_runtime(self):
        agent = MagicMock()
        agent.run.return_value = "test answer"
        chat = ChatService(agent=agent)
        session = chat.start_session()

        runtime = MagicMock()
        runtime.services.chat = chat
        return runtime, chat, session

    @pytest.mark.asyncio
    async def test_ws_streams_answer_event(self, mock_runtime):
        """Verify answer event is sent through WebSocket."""
        runtime, chat, session = mock_runtime

        with patch("agentnexus.server.app._get_runtime", return_value=runtime):
            ws = AsyncMock()
            ws.accept = AsyncMock()
            ws.receive_json = AsyncMock(side_effect=[
                {"type": "send_message", "content": "hello"},
                asyncio.CancelledError(),
            ])
            ws.send_json = AsyncMock()

            with pytest.raises((asyncio.CancelledError, Exception)):
                await ws_agent(ws, session.id)

            sent_events = [call.args[0] for call in ws.send_json.call_args_list]
            event_types = [e.get("type") for e in sent_events]

            assert "token" in event_types, f"Expected 'token' in {event_types}"
            assert "answer" in event_types, f"Expected 'answer' in {event_types}"

            token_event = next(e for e in sent_events if e.get("type") == "token")
            answer_event = next(e for e in sent_events if e.get("type") == "answer")

            assert token_event["content"] == "test answer"
            assert answer_event["content"] == "test answer"

    @pytest.mark.asyncio
    async def test_ws_streams_thinking_then_answer(self, mock_runtime):
        """Verify thinking events appear before answer."""
        from agentnexus.agents.react_types import ReActEvent, ReActEventType, ReActResult

        runtime, chat, session = mock_runtime

        def simulate_run(text, memory_manager=None):
            # Simulate agent events via _on_event bridge
            if hasattr(chat._agent, '_on_event') and chat._agent._on_event:
                chat._agent._on_event(
                    ReActEvent(ReActEventType.TOOLS_FOUND, {"thought": "analyzing..."}),
                    None, None,
                )
            return ReActResult(answer="final", steps=[])

        chat._agent.run.side_effect = simulate_run

        with patch("agentnexus.server.app._get_runtime", return_value=runtime):
            ws = AsyncMock()
            ws.accept = AsyncMock()
            ws.receive_json = AsyncMock(side_effect=[
                {"type": "send_message", "content": "hello"},
                asyncio.CancelledError(),
            ])
            ws.send_json = AsyncMock()

            with pytest.raises((asyncio.CancelledError, Exception)):
                await ws_agent(ws, session.id)

            sent_events = [call.args[0] for call in ws.send_json.call_args_list]
            event_types = [e.get("type") for e in sent_events]

            thinking_idx = event_types.index("thinking")
            answer_idx = event_types.index("answer")
            assert thinking_idx < answer_idx, "thinking should come before answer"

    @pytest.mark.asyncio
    async def test_ws_sends_done_after_answer(self, mock_runtime):
        """Verify 'done' event is sent after answer."""
        runtime, chat, session = mock_runtime

        with patch("agentnexus.server.app._get_runtime", return_value=runtime):
            ws = AsyncMock()
            ws.accept = AsyncMock()
            ws.receive_json = AsyncMock(side_effect=[
                {"type": "send_message", "content": "hello"},
                asyncio.CancelledError(),
            ])
            ws.send_json = AsyncMock()

            with pytest.raises((asyncio.CancelledError, Exception)):
                await ws_agent(ws, session.id)

            sent_events = [call.args[0] for call in ws.send_json.call_args_list]
            event_types = [e.get("type") for e in sent_events]

            answer_idx = event_types.index("answer")
            done_idx = event_types.index("done")
            assert done_idx > answer_idx, "done should come after answer"

    @pytest.mark.asyncio
    async def test_ws_no_duplicate_answer(self, mock_runtime):
        """Verify answer event is sent exactly once."""
        runtime, chat, session = mock_runtime

        with patch("agentnexus.server.app._get_runtime", return_value=runtime):
            ws = AsyncMock()
            ws.accept = AsyncMock()
            ws.receive_json = AsyncMock(side_effect=[
                {"type": "send_message", "content": "hello"},
                asyncio.CancelledError(),
            ])
            ws.send_json = AsyncMock()

            with pytest.raises((asyncio.CancelledError, Exception)):
                await ws_agent(ws, session.id)

            sent_events = [call.args[0] for call in ws.send_json.call_args_list]
            answer_events = [e for e in sent_events if e.get("type") == "answer"]

            assert len(answer_events) == 1, f"Expected 1 answer event, got {len(answer_events)}"

    @pytest.mark.asyncio
    async def test_ws_event_order_complete(self, mock_runtime):
        """Verify complete event ordering: thinking* tool* token answer done."""
        from agentnexus.agents.react_types import ReActEvent, ReActEventType, ReActResult

        runtime, chat, session = mock_runtime

        def simulate_run(text, memory_manager=None):
            if hasattr(chat._agent, '_on_event') and chat._agent._on_event:
                chat._agent._on_event(
                    ReActEvent(ReActEventType.TOOLS_FOUND, {"thought": "thinking..."}),
                    None, None,
                )
                chat._agent._on_event(
                    ReActEvent(ReActEventType.TOOL_START, {"name": "search", "arguments": {}}),
                    None, None,
                )
                chat._agent._on_event(
                    ReActEvent(ReActEventType.TOOL_DONE, {"name": "search", "result": "ok"}),
                    None, None,
                )
            return ReActResult(answer="done", steps=[])

        chat._agent.run.side_effect = simulate_run

        with patch("agentnexus.server.app._get_runtime", return_value=runtime):
            ws = AsyncMock()
            ws.accept = AsyncMock()
            ws.receive_json = AsyncMock(side_effect=[
                {"type": "send_message", "content": "hello"},
                asyncio.CancelledError(),
            ])
            ws.send_json = AsyncMock()

            with pytest.raises((asyncio.CancelledError, Exception)):
                await ws_agent(ws, session.id)

            sent_events = [call.args[0] for call in ws.send_json.call_args_list]
            event_types = [e.get("type") for e in sent_events]

            # Filter to relevant types
            relevant = [
                t for t in event_types
                if t in ("thinking", "tool_call", "tool_result", "token", "answer", "done")
            ]

            assert relevant[0] == "thinking"
            assert "tool_call" in relevant
            assert "tool_result" in relevant
            assert relevant[-2] == "answer"
            assert relevant[-1] == "done"
