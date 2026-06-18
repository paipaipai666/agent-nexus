"""Tests for GUI event mapping — verifies answer reaches frontend."""
import io
import sys
from unittest.mock import MagicMock

from agentnexus.server.routes.chat import _map_to_gui_event
from agentnexus.services.chat import AgentEvent, ChatService


class TestGuiEventMapping:
    """Test _map_to_gui_event maps ChatService events to GUI format correctly."""

    def test_message_delta_returns_none(self):
        """message_delta is now skipped (answer comes from run_finished)."""
        event = AgentEvent(type="message_delta", payload={"text": "hello world"})
        result = _map_to_gui_event(event, MagicMock(), 0)
        assert result is None

    def test_run_finished_maps_to_answer(self):
        event = AgentEvent(type="run_finished", payload={"answer": "final answer", "status": "finished"})
        result = _map_to_gui_event(event, MagicMock(), 0)
        assert result == {"type": "answer", "content": "final answer", "run_id": None, "seq": 0}

    def test_run_finished_with_empty_answer(self):
        event = AgentEvent(type="run_finished", payload={"answer": "", "status": "finished"})
        result = _map_to_gui_event(event, MagicMock(), 0)
        assert result == {"type": "answer", "content": "", "run_id": None, "seq": 0}

    def test_run_finished_with_none_answer(self):
        event = AgentEvent(type="run_finished", payload={"answer": None, "status": "finished"})
        result = _map_to_gui_event(event, MagicMock(), 0)
        assert result == {"type": "answer", "content": None, "run_id": None, "seq": 0}

    def test_run_persisted_maps_to_done(self):
        event = AgentEvent(type="run_persisted", payload={"status": "finished"})
        result = _map_to_gui_event(event, MagicMock(), 0)
        assert result == {"type": "done", "run_id": None, "seq": 0}

    def test_run_failed_maps_to_error(self):
        event = AgentEvent(type="run_failed", payload={"error": "something broke"})
        result = _map_to_gui_event(event, MagicMock(), 0)
        assert result == {"type": "error", "message": "something broke", "run_id": None, "seq": 0}

    def test_run_interrupted_maps_to_error(self):
        event = AgentEvent(type="run_interrupted", payload={"error": "cancelled"})
        result = _map_to_gui_event(event, MagicMock(), 0)
        assert result == {"type": "error", "message": "cancelled", "run_id": None, "seq": 0}

    def test_run_finished_preserves_run_id(self):
        event = AgentEvent(type="run_finished", payload={"answer": "done", "status": "finished"}, run_id="run_456")
        result = _map_to_gui_event(event, MagicMock(), 0)
        assert result["run_id"] == "run_456"
        assert result["seq"] == 0


class TestChatServiceAnswerFlow:
    """Test that answer flows correctly from agent through ChatService events."""

    def test_send_message_emits_answer_event(self):
        agent = MagicMock()
        agent.run.return_value = "the final answer"
        service = ChatService(agent_factory=lambda _sid=None: agent, memory_factory_builder=lambda _sid: lambda: MagicMock())
        session = service.start_session()

        run = service.send_message(session.id, "question")
        events = list(service.stream_events(run.id))

        event_types = [e.type for e in events]
        assert "message_delta" in event_types
        assert "run_finished" in event_types

        finished_event = next(e for e in events if e.type == "run_finished")
        assert finished_event.payload["answer"] == "the final answer"

    def test_send_message_maps_to_gui_format(self):
        agent = MagicMock()
        agent.run.return_value = "gui answer"
        service = ChatService(agent_factory=lambda _sid=None: agent, memory_factory_builder=lambda _sid: lambda: MagicMock())
        session = service.start_session()

        run = service.send_message(session.id, "question")
        events = list(service.stream_events(run.id))

        gui_events = []
        for event in events:
            gui_event = _map_to_gui_event(event, service, 0)
            if gui_event is not None:
                gui_events.append(gui_event)

        gui_types = [e["type"] for e in gui_events]
        assert "answer" in gui_types

        answer_event = next(e for e in gui_events if e["type"] == "answer")
        assert answer_event["content"] == "gui answer"

    def test_full_flow_agent_to_gui_events(self):
        """End-to-end: agent answer -> ChatService events -> GUI events."""
        from agentnexus.agents.react_types import ReActResult

        agent = MagicMock()

        def simulate_agent_run(text, memory_manager=None):
            if hasattr(agent, '_on_event') and agent._on_event:
                from agentnexus.agents.react_types import ReActEvent, ReActEventType
                agent._on_event(ReActEvent(ReActEventType.TOOLS_FOUND, {"thought": "thinking..."}), None, None)
                agent._on_event(ReActEvent(ReActEventType.TOOL_START, {"name": "search"}), None, None)
                agent._on_event(ReActEvent(ReActEventType.TOOL_DONE, {"name": "search", "result": "data"}), None, None)
            return ReActResult(answer="final answer from agent", steps=[])

        agent.run.side_effect = simulate_agent_run
        service = ChatService(agent_factory=lambda _sid=None: agent, memory_factory_builder=lambda _sid: lambda: MagicMock())
        session = service.start_session()

        run = service.send_message(session.id, "test question")
        events = list(service.stream_events(run.id))

        gui_events = []
        for event in events:
            gui_event = _map_to_gui_event(event, service, 0)
            if gui_event is not None:
                gui_events.append(gui_event)

        gui_types = [e["type"] for e in gui_events]

        assert "thinking" in gui_types
        assert "tool_call" in gui_types
        assert "tool_result" in gui_types
        assert "answer" in gui_types
        assert "done" in gui_types

        answer_event = next(e for e in gui_events if e["type"] == "answer")
        assert answer_event["content"] == "final answer from agent"

    def test_turn_journal_maps_to_thinking(self):
        service = MagicMock()
        turn = MagicMock()
        turn._journal = ["thought: I need to search for info"]
        service._turns = {"run_123": turn}

        event = AgentEvent(
            type="turn_journal",
            payload={"event": "ANSWER_THOUGHT"},
            run_id="run_123",
        )
        result = _map_to_gui_event(event, service, 0)
        assert result is not None
        assert result["type"] == "thinking"
        assert result["content"] == "I need to search for info"
        assert result["seq"] == 0

    def test_direct_tool_start_maps_to_tool_call(self):
        """tool_start event carries payload directly — no journal parsing."""
        event = AgentEvent(
            type="tool_start",
            payload={"name": "web_search", "arguments": {"query": "test"}},
            run_id="run_123",
        )
        result = _map_to_gui_event(event, MagicMock(), 5)
        assert result is not None
        assert result["type"] == "tool_call"
        assert result["tool_name"] == "web_search"
        assert result["arguments"] == {"query": "test"}
        assert result["seq"] == 5

    def test_direct_tool_done_maps_to_tool_result(self):
        """tool_done event carries payload directly — no journal parsing."""
        event = AgentEvent(
            type="tool_done",
            payload={"name": "web_search", "result": "search results here"},
            run_id="run_123",
        )
        result = _map_to_gui_event(event, MagicMock(), 6)
        assert result is not None
        assert result["type"] == "tool_result"
        assert result["tool_name"] == "web_search"
        assert result["result"] == "search results here"
        assert result["seq"] == 6

    def test_tool_start_with_space_in_name(self):
        """Tool names with spaces are preserved through direct payload."""
        event = AgentEvent(
            type="tool_start",
            payload={"name": "web search", "arguments": {"query": "test"}},
            run_id="run_123",
        )
        result = _map_to_gui_event(event, MagicMock(), 0)
        assert result is not None
        assert result["tool_name"] == "web search"

    def test_tool_done_matching_name_with_result(self):
        """tool_done name matches tool_start name — frontend can pair them."""
        start = AgentEvent(type="tool_start", payload={"name": "read_file", "arguments": {"path": "/tmp/x"}})
        done = AgentEvent(type="tool_done", payload={"name": "read_file", "result": "file contents"})
        s = _map_to_gui_event(start, MagicMock(), 0)
        d = _map_to_gui_event(done, MagicMock(), 1)
        assert s["tool_name"] == d["tool_name"]


class TestBackendOutputSuppression:
    """Backend should not print agent output to stdout."""

    def test_send_message_does_not_print_to_stdout(self):
        """ChatService.send_message() should suppress agent _output (print)."""
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
