"""Tests for GUI event mapping — verifies answer reaches frontend."""
from unittest.mock import MagicMock

from agentnexus.server.routes.chat import _map_to_gui_event
from agentnexus.services.chat import AgentEvent, ChatService


class TestGuiEventMapping:
    """Test _map_to_gui_event maps ChatService events to GUI format correctly."""

    def test_message_delta_maps_to_token(self):
        event = AgentEvent(type="message_delta", payload={"text": "hello world"})
        result = _map_to_gui_event(event, MagicMock())
        assert result == {"type": "token", "content": "hello world", "run_id": None}

    def test_run_finished_maps_to_answer(self):
        event = AgentEvent(type="run_finished", payload={"answer": "final answer", "status": "finished"})
        result = _map_to_gui_event(event, MagicMock())
        assert result == {"type": "answer", "content": "final answer", "run_id": None}

    def test_run_finished_with_empty_answer(self):
        event = AgentEvent(type="run_finished", payload={"answer": "", "status": "finished"})
        result = _map_to_gui_event(event, MagicMock())
        assert result == {"type": "answer", "content": "", "run_id": None}

    def test_run_finished_with_none_answer(self):
        event = AgentEvent(type="run_finished", payload={"answer": None, "status": "finished"})
        result = _map_to_gui_event(event, MagicMock())
        assert result == {"type": "answer", "content": None, "run_id": None}

    def test_run_persisted_maps_to_done(self):
        event = AgentEvent(type="run_persisted", payload={"status": "finished"})
        result = _map_to_gui_event(event, MagicMock())
        assert result == {"type": "done", "run_id": None}

    def test_run_failed_maps_to_error(self):
        event = AgentEvent(type="run_failed", payload={"error": "something broke"})
        result = _map_to_gui_event(event, MagicMock())
        assert result == {"type": "error", "message": "something broke", "run_id": None}

    def test_run_interrupted_maps_to_error(self):
        event = AgentEvent(type="run_interrupted", payload={"error": "cancelled"})
        result = _map_to_gui_event(event, MagicMock())
        assert result == {"type": "error", "message": "cancelled", "run_id": None}

    def test_message_delta_preserves_run_id(self):
        event = AgentEvent(type="message_delta", payload={"text": "hi"}, run_id="run_123")
        result = _map_to_gui_event(event, MagicMock())
        assert result["run_id"] == "run_123"

    def test_run_finished_preserves_run_id(self):
        event = AgentEvent(type="run_finished", payload={"answer": "done", "status": "finished"}, run_id="run_456")
        result = _map_to_gui_event(event, MagicMock())
        assert result["run_id"] == "run_456"


class TestChatServiceAnswerFlow:
    """Test that answer flows correctly from agent through ChatService events."""

    def test_send_message_emits_token_and_answer_events(self):
        agent = MagicMock()
        agent.run.return_value = "the final answer"
        service = ChatService(agent=agent)
        session = service.start_session()

        run = service.send_message(session.id, "question")
        events = list(service.stream_events(run.id))

        event_types = [e.type for e in events]
        assert "message_delta" in event_types
        assert "run_finished" in event_types

        delta_event = next(e for e in events if e.type == "message_delta")
        finished_event = next(e for e in events if e.type == "run_finished")

        assert delta_event.payload["text"] == "the final answer"
        assert finished_event.payload["answer"] == "the final answer"

    def test_send_message_maps_to_gui_format(self):
        agent = MagicMock()
        agent.run.return_value = "gui answer"
        service = ChatService(agent=agent)
        session = service.start_session()

        run = service.send_message(session.id, "question")
        events = list(service.stream_events(run.id))

        gui_events = []
        for event in events:
            gui_event = _map_to_gui_event(event, service)
            if gui_event is not None:
                gui_events.append(gui_event)

        gui_types = [e["type"] for e in gui_events]
        assert "token" in gui_types
        assert "answer" in gui_types

        token_event = next(e for e in gui_events if e["type"] == "token")
        answer_event = next(e for e in gui_events if e["type"] == "answer")

        assert token_event["content"] == "gui answer"
        assert answer_event["content"] == "gui answer"

    def test_send_message_with_react_result_object(self):
        from agentnexus.agents.react_types import ReActResult

        agent = MagicMock()
        agent.run.return_value = ReActResult(answer="react answer", steps=[])
        service = ChatService(agent=agent)
        session = service.start_session()

        run = service.send_message(session.id, "question")
        events = list(service.stream_events(run.id))

        delta_event = next(e for e in events if e.type == "message_delta")
        finished_event = next(e for e in events if e.type == "run_finished")

        assert delta_event.payload["text"] == "react answer"
        assert finished_event.payload["answer"] == "react answer"

    def test_full_flow_agent_to_gui_events(self):
        """End-to-end: agent answer -> ChatService events -> GUI events."""
        from agentnexus.agents.react_types import ReActResult

        agent = MagicMock()

        def simulate_agent_run(text, memory_manager=None):
            # Simulate agent setting _on_event
            if hasattr(agent, '_on_event') and agent._on_event:
                from agentnexus.agents.react_types import ReActEvent, ReActEventType
                agent._on_event(ReActEvent(ReActEventType.TOOLS_FOUND, {"thought": "thinking..."}), None, None)
                agent._on_event(ReActEvent(ReActEventType.TOOL_START, {"name": "search"}), None, None)
                agent._on_event(ReActEvent(ReActEventType.TOOL_DONE, {"name": "search", "result": "data"}), None, None)
            return ReActResult(answer="final answer from agent", steps=[])

        agent.run.side_effect = simulate_agent_run
        service = ChatService(agent=agent)
        session = service.start_session()

        run = service.send_message(session.id, "test question")
        events = list(service.stream_events(run.id))

        gui_events = []
        for event in events:
            gui_event = _map_to_gui_event(event, service)
            if gui_event is not None:
                gui_events.append(gui_event)

        gui_types = [e["type"] for e in gui_events]

        # Should have thinking, tool_call, tool_result, token, answer, done
        assert "thinking" in gui_types
        assert "tool_call" in gui_types
        assert "tool_result" in gui_types
        assert "token" in gui_types
        assert "answer" in gui_types
        assert "done" in gui_types

        # Verify answer content
        token_event = next(e for e in gui_events if e["type"] == "token")
        answer_event = next(e for e in gui_events if e["type"] == "answer")

        assert token_event["content"] == "final answer from agent"
        assert answer_event["content"] == "final answer from agent"

    def test_turn_journal_maps_to_thinking(self):
        """Test that turn_journal events with ANSWER_THOUGHT map to thinking."""
        service = MagicMock()
        turn = MagicMock()
        turn._journal = ["thought: I need to search for info"]
        service._turns = {"run_123": turn}

        event = AgentEvent(
            type="turn_journal",
            payload={"event": "ANSWER_THOUGHT"},
            run_id="run_123",
        )
        result = _map_to_gui_event(event, service)
        assert result is not None
        assert result["type"] == "thinking"
        assert result["content"] == "I need to search for info"

    def test_turn_journal_tool_start_maps_to_tool_call(self):
        """Test that turn_journal TOOL_START maps to tool_call."""
        service = MagicMock()
        turn = MagicMock()
        turn._journal = ["tool start: web_search {\"query\": \"test\"}"]
        service._turns = {"run_123": turn}

        event = AgentEvent(
            type="turn_journal",
            payload={"event": "TOOL_START"},
            run_id="run_123",
        )
        result = _map_to_gui_event(event, service)
        assert result is not None
        assert result["type"] == "tool_call"
        assert result["tool_name"] == "web_search"
