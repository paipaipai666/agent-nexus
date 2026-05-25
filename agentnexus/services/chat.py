"""Chat service facade over ReActAgent.

The first implementation intentionally stays thin: it exposes stable handles
and event types for future GUI/Web adapters while preserving the existing TUI
path that still consumes the raw ReActAgent directly.
"""

from __future__ import annotations

import queue
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterator


@dataclass(frozen=True)
class SessionHandle:
    id: str
    skill: str | None = None
    profile: str | None = None


@dataclass(frozen=True)
class RunHandle:
    id: str
    session_id: str


@dataclass(frozen=True)
class AgentEvent:
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    run_id: str | None = None
    session_id: str | None = None


class ChatService:
    """UI-neutral interaction facade for chat sessions."""

    def __init__(self, agent: Any, memory_manager: Any = None, version_manager: Any = None):
        self._agent = agent
        self._memory = memory_manager
        self._version = version_manager
        self._sessions: dict[str, SessionHandle] = {}
        self._run_events: dict[str, queue.Queue[AgentEvent | None]] = {}

    def start_session(self, skill: str | None = None, profile: str | None = None) -> SessionHandle:
        handle = SessionHandle(id=f"session_{uuid.uuid4().hex[:12]}", skill=skill, profile=profile)
        self._sessions[handle.id] = handle
        return handle

    def send_message(self, session_id: str, text: str) -> RunHandle:
        if session_id not in self._sessions:
            raise KeyError(f"Unknown session_id: {session_id}")
        run = RunHandle(id=f"run_{uuid.uuid4().hex[:12]}", session_id=session_id)
        events: queue.Queue[AgentEvent | None] = queue.Queue()
        self._run_events[run.id] = events
        events.put(AgentEvent("message_started", {"text": text}, run_id=run.id, session_id=session_id))
        try:
            result = self._agent.run(text, memory_manager=self._memory)
            answer = getattr(result, "answer", result)
            events.put(AgentEvent("message_delta", {"text": answer or ""}, run_id=run.id, session_id=session_id))
            events.put(AgentEvent("run_finished", {"answer": answer or ""}, run_id=run.id, session_id=session_id))
        except Exception as exc:
            events.put(AgentEvent("run_failed", {"error": str(exc)}, run_id=run.id, session_id=session_id))
            raise
        finally:
            events.put(None)
        return run

    def stream_events(self, run_id: str) -> Iterator[AgentEvent]:
        events = self._run_events.get(run_id)
        if events is None:
            raise KeyError(f"Unknown run_id: {run_id}")
        while True:
            event = events.get()
            if event is None:
                break
            yield event

    def cancel_run(self, run_id: str) -> None:
        events = self._run_events.get(run_id)
        if events is not None:
            events.put(AgentEvent("run_failed", {"error": "cancelled"}, run_id=run_id))
            events.put(None)

    def confirm_tool_call(self, run_id: str, approved: bool) -> None:
        events = self._run_events.get(run_id)
        if events is not None:
            events.put(AgentEvent("confirmation_requested", {"approved": approved}, run_id=run_id))

    def get_session_snapshot(self, session_id: str) -> dict[str, Any]:
        if session_id not in self._sessions:
            raise KeyError(f"Unknown session_id: {session_id}")
        return {
            "session": self._sessions[session_id],
            "memory": self._memory,
            "version": self._version,
        }

