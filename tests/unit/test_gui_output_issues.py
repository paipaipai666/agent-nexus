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
        agent._output = print
        agent.run.return_value = "final answer"

        service = ChatService(agent_factory=lambda _sid=None: agent, memory_factory_builder=lambda _sid: lambda: MagicMock())
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
        assert "行动:" not in output
        assert "观察:" not in output
        assert "最终答案:" not in output

    def test_send_message_suppresses_output_with_real_agent(self):
        from agentnexus.services.chat import ChatService

        agent = MagicMock()
        agent._output = print

        def run_that_outputs(_text, memory_manager=None):
            agent._output("思考: some thought")
            agent._output("行动: search(query)")
            agent._output("观察: result")
            agent._output("最终答案: the answer")
            return "the answer"

        agent.run.side_effect = run_that_outputs
        service = ChatService(agent_factory=lambda _sid=None: agent, memory_factory_builder=lambda _sid: lambda: MagicMock())
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
        assert "行动:" not in output
        assert "观察:" not in output
        assert "最终答案:" not in output

    def test_send_message_suppresses_output_even_with_on_event(self):
        from agentnexus.agents.react_types import ReActEvent, ReActEventType
        from agentnexus.services.chat import ChatService

        agent = MagicMock()

        def run_with_events(_text, memory_manager=None):
            if agent._on_event:
                agent._on_event(
                    ReActEvent(ReActEventType.TOOLS_FOUND, {"thought": "I need to search"}),
                    None, None,
                )
            return "answer"

        agent.run.side_effect = run_with_events
        service = ChatService(agent_factory=lambda _sid=None: agent, memory_factory_builder=lambda _sid: lambda: MagicMock())
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
        chat = MagicMock()
        chat._turns = {}

        event = self._make_event("run_finished", {"answer": "final answer", "status": "finished"})
        result = _map_to_gui_event(event, chat, 0)

        assert result is not None
        assert result["type"] == "answer"
        assert result["content"] == "final answer"
        assert result["seq"] == 0

    def test_message_delta_returns_none(self):
        """message_delta is now skipped (answer comes from run_finished)."""
        chat = MagicMock()
        chat._turns = {}

        event = self._make_event("message_delta", {"text": "some text"})
        result = _map_to_gui_event(event, chat, 0)

        assert result is None

    def test_turn_journal_tools_found_maps_to_thinking(self):
        chat = MagicMock()
        turn = MagicMock()
        turn._journal = ["thought: I need to search for something"]
        chat._turns = {"run_test": turn}

        event = self._make_event("turn_journal", {"event": "TOOLS_FOUND"})
        result = _map_to_gui_event(event, chat, 0)

        assert result is not None
        assert result["type"] == "thinking"
        assert "I need to search" in result["content"]

    def test_turn_journal_tool_start_maps_to_tool_call(self):
        chat = MagicMock()
        turn = MagicMock()
        turn._journal = ["tool start: web_search {'query': 'test'}"]
        chat._turns = {"run_test": turn}

        event = self._make_event("turn_journal", {"event": "TOOL_START"})
        result = _map_to_gui_event(event, chat, 0)

        assert result is not None
        assert result["type"] == "tool_call"
        assert result["tool_name"] == "web_search"

    def test_turn_journal_tool_done_maps_to_tool_result(self):
        chat = MagicMock()
        turn = MagicMock()
        turn._journal = ["tool done: web_search -> search results here"]
        chat._turns = {"run_test": turn}

        event = self._make_event("turn_journal", {"event": "TOOL_DONE"})
        result = _map_to_gui_event(event, chat, 0)

        assert result is not None
        assert result["type"] == "tool_result"
        assert result["tool_name"] == "web_search"

    def test_answer_thought_maps_to_thinking(self):
        chat = MagicMock()
        turn = MagicMock()
        turn._journal = ["thought: Based on the results, I can now answer"]
        chat._turns = {"run_test": turn}

        event = self._make_event("turn_journal", {"event": "ANSWER_THOUGHT"})
        result = _map_to_gui_event(event, chat, 0)

        assert result is not None
        assert result["type"] == "thinking"
        assert "Based on the results" in result["content"]

    def test_complete_event_sequence_for_frontend(self):
        """Verify the complete event sequence sent to frontend."""
        from agentnexus.agents.react_types import ReActEvent, ReActEventType
        from agentnexus.services.chat import ChatService

        agent = MagicMock()

        def run_with_full_cycle(_text, memory_manager=None):
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
        service = ChatService(agent_factory=lambda _sid=None: agent, memory_factory_builder=lambda _sid: lambda: MagicMock())
        session = service.start_session()

        run = service.send_message(session.id, "hello")

        gui_events = []
        for event in service.stream_events(run.id):
            gui_event = _map_to_gui_event(event, service, 0)
            if gui_event is not None:
                gui_events.append(gui_event)

        event_types = [e["type"] for e in gui_events]

        assert "answer" in event_types, f"Missing 'answer' event. Got: {event_types}"
        assert "thinking" in event_types, f"Missing 'thinking' event. Got: {event_types}"
        assert "tool_call" in event_types, f"Missing 'tool_call' event. Got: {event_types}"
        assert "tool_result" in event_types, f"Missing 'tool_result' event. Got: {event_types}"

        answer_idx = event_types.index("answer")
        done_idx = event_types.index("done")
        assert answer_idx < done_idx

        answer_event = gui_events[answer_idx]
        assert answer_event["content"] == "final answer"
