"""Chat service facade over ReActAgent.

The first implementation intentionally stays thin: it exposes stable handles
and event types for future GUI/Web adapters while preserving the existing TUI
path that still consumes the raw ReActAgent directly.
"""

from __future__ import annotations

import asyncio
import queue
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterator

from agentnexus.services.turn import TurnRecord, TurnRuntime


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

    def __init__(
        self,
        agent: Any,
        memory_manager: Any = None,
        version_manager: Any = None,
        skill_service: Any = None,
        tool_executor: Any = None,
        capability_runtime: Any = None,
    ):
        self._agent = agent
        self._memory = memory_manager
        self._version = version_manager
        self._skill_service = skill_service
        self._tool_executor = tool_executor or getattr(agent, "tool_executor", None)
        self._capability_runtime = capability_runtime
        self._sessions: dict[str, SessionHandle] = {}
        self._run_events: dict[str, queue.Queue[AgentEvent | None]] = {}
        self._async_run_events: dict[str, asyncio.Queue[AgentEvent | None]] = {}
        self._turns: dict[str, TurnRuntime] = {}
        self._run_snapshots: dict[str, TurnRecord] = {}
        self._message_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self._is_processing = False

    def start_session(self, skill: str | None = None, profile: str | None = None) -> SessionHandle:
        handle = SessionHandle(id=f"session_{uuid.uuid4().hex[:12]}", skill=skill, profile=profile)
        self._sessions[handle.id] = handle
        return handle

    # ── Message Queue ──────────────────────────────────────────────

    @property
    def is_processing(self) -> bool:
        return self._is_processing

    @property
    def queue_size(self) -> int:
        return self._message_queue.qsize()

    def enqueue_message(self, session_id: str, text: str) -> int:
        """Enqueue a message for later processing. Returns queue position."""
        self._message_queue.put((session_id, text))
        return self._message_queue.qsize()

    def dequeue_message(self) -> tuple[str, str] | None:
        """Dequeue the next message. Returns (session_id, text) or None."""
        try:
            return self._message_queue.get_nowait()
        except queue.Empty:
            return None

    def mark_processing(self, processing: bool) -> None:
        """Mark whether the agent is currently processing a message."""
        self._is_processing = processing

    def _put_event(self, run_id: str, event: AgentEvent) -> None:
        """Put event into both sync and async queues."""
        sync_q = self._run_events.get(run_id)
        if sync_q is not None:
            sync_q.put(event)
        async_q = self._async_run_events.get(run_id)
        if async_q is not None:
            try:
                async_q.put_nowait(event)
            except Exception:
                pass

    def send_message(self, session_id: str, text: str) -> RunHandle:
        if session_id not in self._sessions:
            raise KeyError(f"Unknown session_id: {session_id}")
        run, events, turn = self.begin_turn(session_id, text)
        old_on_event = getattr(self._agent, "_on_event", None)
        old_output = getattr(self._agent, "_output", None)
        try:
            if self._capability_runtime is not None:
                self._capability_runtime.refresh_if_stale()
            if hasattr(self._agent, "set_cancel_checker"):
                self._agent.set_cancel_checker(turn.cancel_checker)
            agent_text = self._prepare_message(text, events, run.id, session_id)
            self._install_agent_event_bridge(turn, events, run.id, session_id, old_on_event)
            # Suppress agent _output (print) — events are sent via WebSocket
            try:
                self._agent._output = lambda _msg: None
            except Exception:
                pass
            result = self._agent.run(agent_text, memory_manager=self._memory)
            answer = getattr(result, "answer", result)
            record = turn.finish(answer or "")
            self._run_snapshots[run.id] = record
            self._put_event(run.id, AgentEvent(
                "message_delta", {"text": answer or ""},
                run_id=run.id, session_id=session_id,
            ))
            self._put_event(run.id, AgentEvent(
                "run_finished",
                {"answer": answer or "", "status": record.status},
                run_id=run.id,
                session_id=session_id,
            ))
            self._put_event(run.id, AgentEvent(
                "run_persisted", {"status": record.status},
                run_id=run.id, session_id=session_id,
            ))
        except Exception as exc:
            if turn.cancel_checker() or str(exc) == "cancelled":
                record = turn.cancel("cancelled")
                event_type = "run_interrupted"
            else:
                record = turn.fail("Agent 执行错误", str(exc))
                event_type = "run_failed"
            self._run_snapshots[run.id] = record
            payload = {
                "error": str(exc),
                "status": record.status,
                "answer": record.answer,
                "reason": record.reason,
            }
            self._put_event(run.id, AgentEvent(
                event_type, payload,
                run_id=run.id, session_id=session_id,
            ))
            self._put_event(run.id, AgentEvent(
                "run_persisted", {"status": record.status},
                run_id=run.id, session_id=session_id,
            ))
            raise
        finally:
            if hasattr(self._agent, "set_cancel_checker"):
                self._agent.set_cancel_checker(None)
            try:
                self._agent._on_event = old_on_event
            except Exception:
                pass
            try:
                self._agent._output = old_output
            except Exception:
                pass
            self._put_event(run.id, None)
        return run

    def begin_turn(self, session_id: str, text: str) -> tuple[RunHandle, queue.Queue[AgentEvent | None], TurnRuntime]:
        if session_id not in self._sessions:
            raise KeyError(f"Unknown session_id: {session_id}")
        run = RunHandle(id=f"run_{uuid.uuid4().hex[:12]}", session_id=session_id)
        events: queue.Queue[AgentEvent | None] = queue.Queue()
        async_events: asyncio.Queue[AgentEvent | None] = asyncio.Queue()
        self._run_events[run.id] = events
        self._async_run_events[run.id] = async_events
        turn = TurnRuntime(
            run_id=run.id,
            session_id=session_id,
            question=text,
            memory_manager=self._memory,
            version_manager=self._version,
        )
        self._turns[run.id] = turn
        events.put(AgentEvent("message_started", {"text": text}, run_id=run.id, session_id=session_id))
        return run, events, turn

    def record_agent_event(self, run_id: str, event) -> None:
        turn = self._turns.get(run_id)
        if turn is not None:
            self._record_agent_event(turn, event)

    def record_workflow_event(self, run_id: str, event) -> None:
        turn = self._turns.get(run_id)
        if turn is None:
            return
        summary = f"{event.step_type}:{event.step_id} {event.status}"
        if getattr(event, "summary", ""):
            summary = f"{summary} - {event.summary}"
        turn.record("workflow", summary)

    def _prepare_message(
        self,
        text: str,
        events: queue.Queue[AgentEvent | None],
        run_id: str,
        session_id: str,
    ) -> str:
        service = self._skill_service
        if service is None:
            return text
        session = self._sessions[session_id]
        if session.skill:
            service.use(session.skill)

        # Get router recommendations (fast, deterministic, ~45ms)
        recommendations = service.get_recommendations(text)

        # Inject skill context WITH recommendations into agent prompt
        if hasattr(self._agent, "set_available_skill_context"):
            self._agent.set_available_skill_context(
                service.available_skill_context(recommendations=recommendations),
            )

        # Let the agent decide — it has conversation history + LTM context
        # If agent decides to use a skill, it will call /<skill-id> or
        # the maybe_auto_select will activate it
        if not session.skill:
            service.maybe_auto_select(text)

        result = service.prepare_message(
            text,
            tool_executor=self._tool_executor,
            memory_manager=self._memory,
        )
        snapshot = service.snapshot()
        if snapshot.auto_route_reason:
            events.put(AgentEvent(
                "skill_auto_selected",
                {
                    "skill": snapshot.current,
                    "score": snapshot.auto_route_score,
                    "source": snapshot.auto_route_source,
                    "reason": snapshot.auto_route_reason,
                },
                run_id=run_id,
                session_id=session_id,
            ))
        for event in result.events:
            self.record_workflow_event(run_id, event)
            events.put(AgentEvent(
                "workflow_step",
                {
                    "step_id": event.step_id,
                    "step_type": event.step_type,
                    "status": event.status,
                    "summary": event.summary,
                },
                run_id=run_id,
                session_id=session_id,
            ))
        return result.enhanced_question

    def _install_agent_event_bridge(
        self,
        turn: TurnRuntime,
        events: queue.Queue[AgentEvent | None],
        run_id: str,
        session_id: str,
        previous,
    ) -> None:
        def _on_event(event, from_state, to_state):
            event_type = getattr(getattr(event, "type", None), "name", str(getattr(event, "type", "")))
            payload = getattr(event, "payload", {}) or {}

            # STREAM_TOKEN events are sent directly as token events for real-time streaming
            if event_type in ("STREAM_TOKEN", "STREAM_REASONING"):
                token = payload.get("token", "")
                if token:
                    evt_type = "stream_reasoning" if event_type == "STREAM_REASONING" else "stream_token"
                    token_event = AgentEvent(
                        evt_type,
                        {"token": token},
                        run_id=run_id,
                        session_id=session_id,
                    )
                    self._put_event(run_id, token_event)
                return

            self._record_agent_event(turn, event)
            agent_event = AgentEvent(
                "turn_journal",
                {"event": event_type},
                run_id=run_id,
                session_id=session_id,
            )
            self._put_event(run_id, agent_event)
            if previous is not None:
                previous(event, from_state, to_state)

        try:
            self._agent._on_event = _on_event
        except Exception:
            pass

    @staticmethod
    def _record_agent_event(turn: TurnRuntime, event) -> None:
        event_type = getattr(getattr(event, "type", None), "name", "")
        payload = getattr(event, "payload", {}) or {}
        if event_type in {"TOOLS_FOUND", "ANSWER_THOUGHT"}:
            thought = payload.get("thought")
            if thought:
                turn.record("thought", thought)
        elif event_type == "TOOL_START":
            turn.record("tool start", f"{payload.get('name', '')} {payload.get('arguments', {})}")
        elif event_type == "TOOL_DONE":
            result = _plain_summary(payload.get("result", ""), 300)
            turn.record("tool done", f"{payload.get('name', '')} -> {result}")
        elif event_type == "THOUGHT_MISSING":
            turn.record("retry", "model thought missing; requested retry")
        elif event_type == "RETRIES_LEFT":
            turn.record("retry", payload.get("reason", ""))
        elif event_type == "DEGRADED":
            turn.record("degraded", payload.get("strategy", ""))

    def stream_events(self, run_id: str) -> Iterator[AgentEvent]:
        events = self._run_events.get(run_id)
        if events is None:
            raise KeyError(f"Unknown run_id: {run_id}")
        while True:
            event = events.get()
            if event is None:
                break
            yield event

    async def astream_events(self, run_id: str):
        """Async generator that yields events in real-time."""
        events = self._async_run_events.get(run_id)
        if events is None:
            raise KeyError(f"Unknown run_id: {run_id}")
        while True:
            event = await events.get()
            if event is None:
                break
            yield event

    def cancel_run(self, run_id: str, reason: str = "cancelled") -> None:
        events = self._run_events.get(run_id)
        turn = self._turns.get(run_id)
        if events is not None and turn is not None:
            record = turn.cancel(reason)
            self._run_snapshots[run_id] = record
            events.put(AgentEvent(
                "run_interrupted",
                {"error": reason, "status": record.status, "answer": record.answer, "reason": record.reason},
                run_id=run_id,
                session_id=record.session_id,
            ))
            events.put(AgentEvent(
                "run_persisted",
                {"status": record.status},
                run_id=run_id,
                session_id=record.session_id,
            ))
            events.put(None)
        elif events is not None:
            events.put(AgentEvent("run_interrupted", {"error": reason}, run_id=run_id))
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

    def get_run_snapshot(self, run_id: str) -> TurnRecord | None:
        turn = self._turns.get(run_id)
        if turn is not None:
            return turn.record_snapshot
        return self._run_snapshots.get(run_id)


def _plain_summary(text: str, limit: int) -> str:
    clean = " ".join(str(text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 1)] + "…"
