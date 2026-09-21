"""Tests for WS reconnect/resume token semantics (R8).

Verifies the fixes validated by experiments/verify_reconnect_resume.py:
- token buffers/cursors accumulate content tokens ONLY (reasoning resumes via
  seq-based replay and must not pollute the answer snapshot);
- stream_token events carry an absolute per-run tok_seq (queue-position
  independent reconnect skip key);
- TOOL_START advances the snapshot baseline so snapshot content is scoped to
  the CURRENT step;
- token state resets per run and is dropped at run end.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

from agentnexus.services.chat import ChatService


def _ev(event_name: str, **payload):
    """Minimal stand-in for a ReActEvent (bridge reads .type.name and .payload)."""
    return SimpleNamespace(type=SimpleNamespace(name=event_name), payload=payload)


def _make_service() -> tuple[ChatService, MagicMock]:
    agent = MagicMock()
    # Concrete serializable attributes — under a full-suite run an earlier test
    # may leave the trace/exporter pipeline active, which JSON-serializes
    # agent metadata (model/agent_id/...). MagicMock values would crash it.
    agent.llm_client.model = "mock-model"
    agent.llm_client.last_error = ""
    agent.agent_id = "mock-agent"
    agent.max_steps = 5
    agent._total_usage = {}
    agent._step_count = 0
    service = ChatService(
        agent_factory=lambda _sid=None: agent,
        memory_factory_builder=lambda _sid: lambda: MagicMock(),
    )
    return service, agent


def _run_with_events(service: ChatService, agent: MagicMock, session_id: str, events: list):
    """Send a message whose agent.run emits the given bridge events, then drain."""
    emitted = []

    def side_effect(_question, **kwargs):
        on_event = agent._on_event
        for e in events:
            on_event(e, None, None)
        return "mock answer"

    agent.run.side_effect = side_effect
    run = service.send_message(session_id, "hello")
    for e in service.stream_events(run.id):
        if e is not None:
            emitted.append(e)
    return run, emitted


class TestTokenBufferSemantics:
    def test_buffer_and_cursor_count_content_tokens_only(self):
        service, agent = _make_service()
        session = service.start_session()
        _run_with_events(service, agent, session.id, [
            _ev("STREAM_TOKEN", token="T1"),
            _ev("STREAM_REASONING", token="R1"),
            _ev("STREAM_TOKEN", token="T2"),
        ])
        assert service._token_buffers.get(session.id) is None  # popped at run end
        # Replay the same events against a live run to inspect the buffer:
        service2, agent2 = _make_service()
        session2 = service2.start_session()
        captured = {}

        def side_effect(_q, **kwargs):
            on_event = agent2._on_event
            on_event(_ev("STREAM_TOKEN", token="T1"), None, None)
            on_event(_ev("STREAM_REASONING", token="R1"), None, None)
            on_event(_ev("STREAM_TOKEN", token="T2"), None, None)
            captured.update(service2.get_run_token_snapshot(session2.id))
            # Mid-run: buffer holds content tokens only; reasoning excluded.
            assert captured == {"content": "T1T2", "cursor": 2}
            return "answer"

        agent2.run.side_effect = side_effect
        service2.send_message(session2.id, "hi")
        assert captured["content"] == "T1T2"

    def test_reasoning_event_still_emitted_without_tok_seq(self):
        service, agent = _make_service()
        session = service.start_session()
        _, emitted = _run_with_events(service, agent, session.id, [
            _ev("STREAM_REASONING", token="R1"),
            _ev("STREAM_TOKEN", token="T1"),
        ])
        reasoning = [e for e in emitted if e.type == "stream_reasoning"]
        tokens = [e for e in emitted if e.type == "stream_token"]
        assert len(reasoning) == 1 and reasoning[0].tok_seq == 0
        assert len(tokens) == 1 and tokens[0].tok_seq == 1


class TestTokSeq:
    def test_tok_seq_monotonic_within_run(self):
        service, agent = _make_service()
        session = service.start_session()
        _, emitted = _run_with_events(service, agent, session.id, [
            _ev("STREAM_TOKEN", token="a"),
            _ev("STREAM_TOKEN", token="b"),
            _ev("STREAM_TOKEN", token="c"),
        ])
        tokens = [e for e in emitted if e.type == "stream_token"]
        assert [e.tok_seq for e in tokens] == [1, 2, 3]

    def test_tok_seq_resets_per_run(self):
        service, agent = _make_service()
        session = service.start_session()
        _run_with_events(service, agent, session.id, [
            _ev("STREAM_TOKEN", token="a"),
            _ev("STREAM_TOKEN", token="b"),
        ])
        # Second run in the same session: tok_seq restarts at 1.
        agent.run.side_effect = None
        _, emitted = _run_with_events(service, agent, session.id, [
            _ev("STREAM_TOKEN", token="x"),
        ])
        tokens = [e for e in emitted if e.type == "stream_token"]
        assert [e.tok_seq for e in tokens] == [1]


class TestStepScopedSnapshot:
    def test_tool_start_advances_snapshot_baseline(self):
        service, agent = _make_service()
        session = service.start_session()
        captured = {}

        def side_effect(_q, **kwargs):
            on_event = agent._on_event
            on_event(_ev("STREAM_TOKEN", token="AAA"), None, None)
            on_event(_ev("TOOL_START", name="file_read", arguments={}), None, None)
            on_event(_ev("STREAM_TOKEN", token="BB"), None, None)
            captured.update(service.get_run_token_snapshot(session.id))
            return "answer"

        agent.run.side_effect = side_effect
        service.send_message(session.id, "hi")
        # Snapshot carries only the CURRENT step's text ("BB"), not "AAAAAA…".
        assert captured == {"content": "BB", "cursor": 2}

    def test_snapshot_empty_at_step_boundary(self):
        service, agent = _make_service()
        session = service.start_session()
        captured = {}

        def side_effect(_q, **kwargs):
            on_event = agent._on_event
            on_event(_ev("STREAM_TOKEN", token="AAA"), None, None)
            on_event(_ev("TOOL_START", name="file_read", arguments={}), None, None)
            captured.update(service.get_run_token_snapshot(session.id))
            return "answer"

        agent.run.side_effect = side_effect
        service.send_message(session.id, "hi")
        assert captured == {"content": "", "cursor": 1}

    def test_token_state_dropped_at_run_end(self):
        service, agent = _make_service()
        session = service.start_session()
        _run_with_events(service, agent, session.id, [
            _ev("STREAM_TOKEN", token="T1"),
        ])
        assert service.get_run_token_snapshot(session.id) == {"content": "", "cursor": 0}
        assert session.id not in service._token_step_base
        assert session.id not in service._token_buffers
