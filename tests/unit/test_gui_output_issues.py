"""Tests for GUI output issues.

Issues:
1. Backend should NOT print agent output (thoughts, actions, answers) to stdout
2. Frontend should receive final answer via 'answer' event, not just thinking/tool events
"""
import io
import sys
from unittest.mock import MagicMock

from agentnexus.server.routes.chat import _map_to_gui_event


class TestBackendOutputSuppression:
    """Backend should not print agent output to stdout."""

    def test_send_message_does_not_print_to_stdout(self):
        """ChatService.send_message() should suppress agent _output (print)."""
        from agentnexus.services.chat import ChatService

        agent = MagicMock()
        agent._output = print  # Simulate real agent default
        agent.run.return_value = "final answer"

        service = ChatService(agent=agent)
        session = service.start_session()

        # Capture stdout
        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured

        try:
            service.send_message(session.id, "hello")
        finally:
            sys.stdout = old_stdout

        output = captured.getvalue()
        # Should NOT contain agent output
        assert "思考:" not in output, f"Backend printed thought to stdout: {output}"
        assert "行动:" not in output, f"Backend printed action to stdout: {output}"
        assert "观察:" not in output, f"Backend printed observation to stdout: {output}"
        assert "最终答案:" not in output, f"Backend printed final answer to stdout: {output}"

    def test_send_message_suppresses_output_with_real_agent(self):
        """Agent _output should be suppressed during run, even if default is print."""
        from agentnexus.services.chat import ChatService

        agent = MagicMock()
        agent._output = print  # Simulate real agent default

        def run_that_outputs(_text, memory_manager=None):
            # Simulate agent calling _output during run
            agent._output("思考: some thought")
            agent._output("行动: search(query)")
            agent._output("观察: result")
            agent._output("最终答案: the answer")
            return "the answer"

        agent.run.side_effect = run_that_outputs
        service = ChatService(agent=agent)
        session = service.start_session()

        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured

        try:
            service.send_message(session.id, "hello")
        finally:
            sys.stdout = old_stdout

        output = captured.getvalue()
        assert "思考:" not in output, f"Backend printed thought to stdout: {output}"
        assert "行动:" not in output, f"Backend printed action to stdout: {output}"
        assert "观察:" not in output, f"Backend printed observation to stdout: {output}"
        assert "最终答案:" not in output, f"Backend printed final answer to stdout: {output}"

    def test_send_message_suppresses_output_even_with_on_event(self):
        """Agent _output should be suppressed even when _on_event is set."""
        from agentnexus.agents.react_types import ReActEvent, ReActEventType
        from agentnexus.services.chat import ChatService

        agent = MagicMock()

        def run_with_events(_text, memory_manager=None):
            # Simulate agent emitting events
            if agent._on_event:
                agent._on_event(
                    ReActEvent(ReActEventType.TOOLS_FOUND, {"thought": "I need to search"}),
                    None, None,
                )
            return "answer"

        agent.run.side_effect = run_with_events
        service = ChatService(agent=agent)
        session = service.start_session()

        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured

        try:
            service.send_message(session.id, "hello")
        finally:
            sys.stdout = old_stdout

        output = captured.getvalue()
        assert "思考:" not in output


class TestGuiEventMapping:
    """_map_to_gui_event should produce correct event types for frontend."""

    def _make_event(self, event_type: str, payload: dict = None, run_id: str = "run_test"):
        event = MagicMock()
        event.type = event_type
        event.payload = payload or {}
        event.run_id = run_id
        return event

    def test_run_finished_maps_to_answer(self):
        """run_finished should map to 'answer' type for frontend."""
        chat = MagicMock()
        chat._turns = {}

        event = self._make_event("run_finished", {"answer": "final answer", "status": "finished"})
        result = _map_to_gui_event(event, chat)

        assert result is not None
        assert result["type"] == "answer"
        assert result["content"] == "final answer"

    def test_message_delta_maps_to_token(self):
        """message_delta should map to 'token' type for frontend."""
        chat = MagicMock()
        chat._turns = {}

        event = self._make_event("message_delta", {"text": "some text"})
        result = _map_to_gui_event(event, chat)

        assert result is not None
        assert result["type"] == "token"
        assert result["content"] == "some text"

    def test_turn_journal_tools_found_maps_to_thinking(self):
        """TOOLS_FOUND should map to 'thinking' type."""
        chat = MagicMock()
        turn = MagicMock()
        turn._journal = ["thought: I need to search for something"]
        chat._turns = {"run_test": turn}

        event = self._make_event("turn_journal", {"event": "TOOLS_FOUND"})
        result = _map_to_gui_event(event, chat)

        assert result is not None
        assert result["type"] == "thinking"
        assert "I need to search" in result["content"]

    def test_turn_journal_tool_start_maps_to_tool_call(self):
        """TOOL_START should map to 'tool_call' type."""
        chat = MagicMock()
        turn = MagicMock()
        turn._journal = ["tool start: web_search {'query': 'test'}"]
        chat._turns = {"run_test": turn}

        event = self._make_event("turn_journal", {"event": "TOOL_START"})
        result = _map_to_gui_event(event, chat)

        assert result is not None
        assert result["type"] == "tool_call"
        assert result["tool_name"] == "web_search"

    def test_turn_journal_tool_done_maps_to_tool_result(self):
        """TOOL_DONE should map to 'tool_result' type."""
        chat = MagicMock()
        turn = MagicMock()
        turn._journal = ["tool done: web_search -> search results here"]
        chat._turns = {"run_test": turn}

        event = self._make_event("turn_journal", {"event": "TOOL_DONE"})
        result = _map_to_gui_event(event, chat)

        assert result is not None
        assert result["type"] == "tool_result"
        assert result["tool_name"] == "web_search"

    def test_answer_thought_maps_to_thinking(self):
        """ANSWER_THOUGHT should map to 'thinking' type (final thought before answer)."""
        chat = MagicMock()
        turn = MagicMock()
        turn._journal = ["thought: Based on the results, I can now answer"]
        chat._turns = {"run_test": turn}

        event = self._make_event("turn_journal", {"event": "ANSWER_THOUGHT"})
        result = _map_to_gui_event(event, chat)

        assert result is not None
        assert result["type"] == "thinking"
        assert "Based on the results" in result["content"]

    def test_complete_event_sequence_for_frontend(self):
        """Verify the complete event sequence sent to frontend."""
        from agentnexus.agents.react_types import ReActEvent, ReActEventType
        from agentnexus.services.chat import ChatService

        agent = MagicMock()

        def run_with_full_cycle(_text, memory_manager=None):
            # Simulate full agent cycle
            if agent._on_event:
                agent._on_event(
                    ReActEvent(ReActEventType.TOOLS_FOUND, {"thought": "Thinking..."}),
                    None, None,
                )
                agent._on_event(
                    ReActEvent(ReActEventType.TOOL_START, {"name": "search", "arguments": {}}),
                    None, None,
                )
                agent._on_event(
                    ReActEvent(ReActEventType.TOOL_DONE, {"name": "search", "arguments": {}, "result": "data"}),
                    None, None,
                )
                agent._on_event(
                    ReActEvent(ReActEventType.ANSWER_THOUGHT, {"thought": "Final thought"}),
                    None, None,
                )
            return "final answer"

        agent.run.side_effect = run_with_full_cycle
        service = ChatService(agent=agent)
        session = service.start_session()

        run = service.send_message(session.id, "hello")

        # Map all events to GUI format
        chat_service = service
        gui_events = []
        for event in service.stream_events(run.id):
            gui_event = _map_to_gui_event(event, chat_service)
            if gui_event is not None:
                gui_events.append(gui_event)

        # Should have thinking, tool_call, tool_result, token, answer events
        event_types = [e["type"] for e in gui_events]

        # Must have 'answer' event
        assert "answer" in event_types, f"Missing 'answer' event. Got: {event_types}"

        # Must have 'token' event (message_delta)
        assert "token" in event_types, f"Missing 'token' event. Got: {event_types}"

        # Must have thinking events
        assert "thinking" in event_types, f"Missing 'thinking' event. Got: {event_types}"

        # Must have tool events
        assert "tool_call" in event_types, f"Missing 'tool_call' event. Got: {event_types}"
        assert "tool_result" in event_types, f"Missing 'tool_result' event. Got: {event_types}"

        # answer should come before done (run_persisted)
        answer_idx = event_types.index("answer")
        done_idx = event_types.index("done")
        assert answer_idx < done_idx, f"'answer' should come before 'done'. Got: {event_types}"

        # token should come before answer
        token_idx = event_types.index("token")
        assert token_idx < answer_idx, f"'token' should come before 'answer'. Got: {event_types}"

        # Verify answer content
        answer_event = gui_events[answer_idx]
        assert answer_event["content"] == "final answer"
