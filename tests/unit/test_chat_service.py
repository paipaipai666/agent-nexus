"""Tests for ChatService."""
import queue
from unittest.mock import MagicMock

import pytest

from agentnexus.services.chat import AgentEvent, ChatService, RunHandle, SessionHandle


class TestChatService:
    def test_start_session_creates_handle_with_unique_id(self):
        service = ChatService(agent=MagicMock())
        handle = service.start_session()
        assert isinstance(handle, SessionHandle)
        assert handle.id.startswith("session_")

    def test_start_session_multiple_calls_unique_ids(self):
        service = ChatService(agent=MagicMock())
        h1 = service.start_session()
        h2 = service.start_session()
        assert h1.id != h2.id

    def test_start_session_with_skill(self):
        service = ChatService(agent=MagicMock())
        handle = service.start_session(skill="python")
        assert handle.skill == "python"

    def test_start_session_with_profile(self):
        service = ChatService(agent=MagicMock())
        handle = service.start_session(profile="expert")
        assert handle.profile == "expert"

    def test_start_session_with_skill_and_profile(self):
        service = ChatService(agent=MagicMock())
        handle = service.start_session(skill="python", profile="expert")
        assert handle.skill == "python"
        assert handle.profile == "expert"

    def test_send_message_valid_session_returns_run_handle(self):
        agent = MagicMock()
        agent.run.return_value = "mock answer"
        service = ChatService(agent=agent)
        session = service.start_session()
        run = service.send_message(session.id, "hello")
        assert isinstance(run, RunHandle)
        assert run.id.startswith("run_")
        assert run.session_id == session.id

    def test_send_message_invokes_agent_run(self):
        agent = MagicMock()
        service = ChatService(agent=agent)
        session = service.start_session()
        service.send_message(session.id, "hello")
        agent.run.assert_called_once_with("hello", memory_manager=None)

    def test_send_message_invalid_session_raises_key_error(self):
        service = ChatService(agent=MagicMock())
        with pytest.raises(KeyError, match="Unknown session_id"):
            service.send_message("nonexistent", "hello")

    def test_send_message_emits_events_correctly(self):
        agent = MagicMock()
        agent.run.return_value = "mock answer"
        service = ChatService(agent=agent)
        session = service.start_session()
        run = service.send_message(session.id, "hello")
        events = list(service.stream_events(run.id))
        assert len(events) == 3
        assert events[0].type == "message_started"
        assert events[0].payload["text"] == "hello"
        assert events[0].run_id == run.id
        assert events[0].session_id == session.id
        assert events[1].type == "message_delta"
        assert events[1].payload["text"] == "mock answer"
        assert events[2].type == "run_finished"
        assert events[2].payload["answer"] == "mock answer"

    def test_send_message_agent_returns_object_with_answer_attr(self):
        agent = MagicMock()
        agent.run.return_value = MagicMock(answer="extracted answer")
        service = ChatService(agent=agent)
        session = service.start_session()
        run = service.send_message(session.id, "hello")
        events = list(service.stream_events(run.id))
        assert events[1].payload["text"] == "extracted answer"
        assert events[2].payload["answer"] == "extracted answer"

    def test_send_message_agent_returns_none_answer(self):
        agent = MagicMock()
        agent.run.return_value = MagicMock(answer=None)
        service = ChatService(agent=agent)
        session = service.start_session()
        run = service.send_message(session.id, "hello")
        events = list(service.stream_events(run.id))
        assert events[1].payload["text"] == ""
        assert events[2].payload["answer"] == ""

    def test_stream_events_unknown_run_id_raises_key_error(self):
        service = ChatService(agent=MagicMock())
        with pytest.raises(KeyError, match="Unknown run_id"):
            next(service.stream_events("nonexistent"))

    def test_cancel_run_emits_run_failed_with_cancelled_error(self):
        agent = MagicMock()
        agent.run.return_value = "answer"
        service = ChatService(agent=agent)
        session = service.start_session()
        run = service.send_message(session.id, "hello")
        # Consume events that are already queued
        events_before = list(service.stream_events(run.id))
        assert len(events_before) == 3
        # Cancel and read new events
        service.cancel_run(run.id)
        events_after = list(service.stream_events(run.id))
        assert len(events_after) == 1
        assert events_after[0].type == "run_failed"
        assert events_after[0].payload["error"] == "cancelled"

    def test_cancel_run_unknown_run_id_is_noop(self):
        service = ChatService(agent=MagicMock())
        # Should not raise
        service.cancel_run("nonexistent")

    def test_confirm_tool_call_emits_confirmation_requested_with_true(self):
        agent = MagicMock()
        agent.run.return_value = "answer"
        service = ChatService(agent=agent)
        session = service.start_session()
        run = service.send_message(session.id, "hello")
        # Drain initial events
        self._drain_queue(service, run.id)
        service.confirm_tool_call(run.id, approved=True)
        events = self._drain_queue(service, run.id)
        assert len(events) == 1
        assert events[0].type == "confirmation_requested"
        assert events[0].payload["approved"] is True

    def test_confirm_tool_call_emits_confirmation_requested_with_false(self):
        agent = MagicMock()
        agent.run.return_value = "answer"
        service = ChatService(agent=agent)
        session = service.start_session()
        run = service.send_message(session.id, "hello")
        self._drain_queue(service, run.id)
        service.confirm_tool_call(run.id, approved=False)
        events = self._drain_queue(service, run.id)
        assert len(events) == 1
        assert events[0].type == "confirmation_requested"
        assert events[0].payload["approved"] is False

    def test_confirm_tool_call_unknown_run_id_is_noop(self):
        service = ChatService(agent=MagicMock())
        service.confirm_tool_call("nonexistent", approved=True)

    def test_get_session_snapshot_returns_correct_state(self):
        memory = MagicMock()
        version = MagicMock()
        agent = MagicMock()
        service = ChatService(agent=agent, memory_manager=memory, version_manager=version)
        session = service.start_session(skill="python", profile="expert")
        snapshot = service.get_session_snapshot(session.id)
        assert snapshot["session"] is session
        assert snapshot["memory"] is memory
        assert snapshot["version"] is version

    def test_get_session_snapshot_unknown_session_raises_key_error(self):
        service = ChatService(agent=MagicMock())
        with pytest.raises(KeyError, match="Unknown session_id"):
            service.get_session_snapshot("nonexistent")

    def test_get_session_snapshot_memory_and_version_can_be_none(self):
        agent = MagicMock()
        service = ChatService(agent=agent)
        session = service.start_session()
        snapshot = service.get_session_snapshot(session.id)
        assert snapshot["memory"] is None
        assert snapshot["version"] is None

    def test_agent_exception_emits_run_failed_then_none(self):
        agent = MagicMock()
        agent.run.side_effect = RuntimeError("something broke")
        service = ChatService(agent=agent)
        session = service.start_session()
        with pytest.raises(RuntimeError, match="something broke"):
            service.send_message(session.id, "hello")
        run_id = next(iter(service._run_events))
        events = list(service.stream_events(run_id))
        assert len(events) == 2
        assert events[0].type == "message_started"
        assert events[1].type == "run_failed"
        assert events[1].payload["error"] == "something broke"

    def test_multiple_sessions_independent(self):
        agent = MagicMock()
        agent.run.return_value = "answer"
        service = ChatService(agent=agent)
        s1 = service.start_session(skill="python")
        s2 = service.start_session(skill="rust")
        r1 = service.send_message(s1.id, "hello")
        r2 = service.send_message(s2.id, "world")
        assert r1.session_id == s1.id
        assert r2.session_id == s2.id
        assert r1.id != r2.id

    def test_session_handle_frozen(self):
        handle = SessionHandle(id="session_test")
        with pytest.raises(Exception):
            handle.id = "changed"  # type: ignore

    def test_run_handle_frozen(self):
        handle = RunHandle(id="run_test", session_id="session_test")
        with pytest.raises(Exception):
            handle.id = "changed"  # type: ignore

    def test_agent_event_defaults(self):
        event = AgentEvent(type="test")
        assert event.payload == {}
        assert event.run_id is None
        assert event.session_id is None

    def test_agent_event_frozen(self):
        event = AgentEvent(type="test")
        with pytest.raises(Exception):
            event.type = "changed"  # type: ignore

    def test_send_message_passes_memory_manager_to_agent(self):
        memory = MagicMock()
        agent = MagicMock()
        agent.run.return_value = "answer"
        service = ChatService(agent=agent, memory_manager=memory)
        session = service.start_session()
        service.send_message(session.id, "hello")
        agent.run.assert_called_once_with("hello", memory_manager=memory)

    def test_send_message_applies_skill_runtime_events(self):
        from agentnexus.services.skill import SkillService
        from agentnexus.skills.registry import SkillEntry, SkillRegistry
        from agentnexus.skills.workflow import Workflow

        workflow = Workflow.model_validate({
            "id": "review",
            "version": "1",
            "display_name": "Review",
            "prompt_profile": {"system": "react"},
            "tool_policy": {"max_risk": "low"},
            "steps": [{"type": "prompt", "id": "inspect", "prompt": "Inspect."}],
            "success_criteria": ["Done."],
        })
        entry = SkillEntry("review", "review", "Review", "", MagicMock(), workflow)
        registry = SkillRegistry([])
        registry._entries = [entry]
        agent = MagicMock()
        agent.run.return_value = "answer"
        skill = SkillService(registry, agent=agent)
        skill.current = entry
        service = ChatService(agent=agent, skill_service=skill)
        session = service.start_session()

        run = service.send_message(session.id, "hello")

        events = list(service.stream_events(run.id))
        assert [event.type for event in events] == [
            "message_started",
            "workflow_step",
            "message_delta",
            "run_finished",
        ]
        workflow_event = events[1]
        assert workflow_event.payload["status"] == "ok"
        assert workflow_event.payload["summary"]
        assert skill.snapshot().last_run_status == "completed"
        sent_text = agent.run.call_args[0][0]
        assert "Workflow Runtime Context" in sent_text
        assert "Inspect." in sent_text

    def test_send_message_applies_session_skill(self):
        from agentnexus.services.skill import SkillService
        from agentnexus.skills.registry import SkillEntry, SkillRegistry
        from agentnexus.skills.workflow import Workflow

        workflow = Workflow.model_validate({
            "id": "review",
            "version": "1",
            "display_name": "Review",
            "prompt_profile": {"system": "react"},
            "tool_policy": {"max_risk": "low"},
            "steps": [{"type": "prompt", "id": "inspect", "prompt": "Inspect session skill."}],
            "success_criteria": ["Done."],
        })
        entry = SkillEntry("review", "review", "Review", "", MagicMock(), workflow)
        registry = SkillRegistry([])
        registry._entries = [entry]
        agent = MagicMock()
        agent.run.return_value = "answer"
        skill = SkillService(registry, agent=agent)
        service = ChatService(agent=agent, skill_service=skill)
        session = service.start_session(skill="review/review")

        service.send_message(session.id, "hello")

        assert skill.current == entry
        sent_text = agent.run.call_args[0][0]
        assert "Inspect session skill." in sent_text

    def test_send_message_auto_selects_skill_when_session_has_none(self):
        from agentnexus.services.skill import SkillService
        from agentnexus.skills.registry import SkillEntry, SkillRegistry
        from agentnexus.skills.workflow import Workflow

        workflow = Workflow.model_validate({
            "id": "draft-writer",
            "version": "1",
            "display_name": "Draft Writer",
            "description": "Write concise product release notes and drafts.",
            "prompt_profile": {"system": "react"},
            "tool_policy": {"max_risk": "low"},
            "steps": [{"type": "prompt", "id": "draft", "prompt": "Draft concise release notes."}],
            "success_criteria": ["Done."],
        })
        entry = SkillEntry("default", "draft-writer", "Draft Writer", workflow.description, MagicMock(), workflow)
        registry = SkillRegistry([])
        registry._entries = [entry]
        agent = MagicMock()
        agent.run.return_value = "answer"
        skill = SkillService(registry, agent=agent)
        service = ChatService(agent=agent, skill_service=skill)
        session = service.start_session()

        run = service.send_message(session.id, "Please write concise release notes.")

        assert skill.current == entry
        events = list(service.stream_events(run.id))
        event_types = [event.type for event in events]
        assert "skill_auto_selected" in event_types
        auto_event = next(event for event in events if event.type == "skill_auto_selected")
        assert auto_event.payload["source"] == "deterministic"
        sent_text = agent.run.call_args[0][0]
        assert "Draft concise release notes." in sent_text

    @staticmethod
    def _drain_queue(service, run_id):
        """Drain all currently available events from a run queue without blocking."""
        q = service._run_events[run_id]
        items = []
        while True:
            try:
                items.append(q.get_nowait())
            except queue.Empty:
                break
        return items
