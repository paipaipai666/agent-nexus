"""Chat service facade over ReActAgent.

The first implementation intentionally stays thin: it exposes stable handles
and event types for future GUI/Web adapters while preserving the existing TUI
path that still consumes the raw ReActAgent directly.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Iterator

from agentnexus.core.text_utils import collapse_and_truncate
from agentnexus.services.turn import TurnRecord, TurnRuntime

if TYPE_CHECKING:
    from agentnexus.capabilities.runtime import CapabilityRuntime
    from agentnexus.memory.versioned import ConversationVersionManager
    from agentnexus.skills import SkillRegistry
    from agentnexus.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


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
        agent_factory: Callable[[str | None], Any],
        memory_factory_builder: Callable[[str], Callable[[], Any]],
        version_manager: "ConversationVersionManager | None" = None,
        skill_service: "SkillRegistry | None" = None,
        tool_executor: "ToolRegistry | None" = None,
        capability_runtime: "CapabilityRuntime | None" = None,
    ):
        # Factories for per-session agent/memory creation (Phase 1: multi-session)
        self._agent_factory = agent_factory
        self._memory_factory_builder = memory_factory_builder
        # Legacy references
        self._version = version_manager  # Used by _get_version_manager for workspace path
        self._tool_executor = tool_executor
        self._capability_runtime = capability_runtime
        self._skill_service = skill_service
        self._sessions: dict[str, SessionHandle] = {}
        self._run_events: dict[str, queue.Queue[AgentEvent | None]] = {}
        self._async_run_events: dict[str, asyncio.Queue[AgentEvent | None]] = {}
        self._turns: dict[str, TurnRuntime] = {}
        self._run_snapshots: dict[str, TurnRecord] = {}
        self._message_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        # Per-session locking (R0 resolved: threading, R1, R2)
        self._session_locks: dict[str, threading.Lock] = {}
        self._locks_lock = threading.Lock()
        self._processing_lock = threading.Lock()
        self._processing_sessions: set[str] = set()
        # Per-session agent and memory instances (R1: isolation)
        self._agents: dict[str, Any] = {}
        self._memory_managers: dict[str, Any] = {}
        # Per-session token buffers for WS reconnect snapshot (R8)
        self._token_buffers: dict[str, str] = {}
        self._token_cursors: dict[str, int] = {}
        # Per-session version managers — each session gets its own journal + checkpoints
        self._version_managers: dict[str, Any] = {}
        # Per-session short-term memories — each session gets its own STM deque
        # (R7: migrated into MemoryManager via closure factory)
        self._stms: dict[str, Any] = {}

    def start_session(self, skill: str | None = None, profile: str | None = None) -> SessionHandle:
        handle = SessionHandle(id=f"session_{uuid.uuid4().hex[:12]}", skill=skill, profile=profile)
        self._sessions[handle.id] = handle
        return handle

    # ── Per-Session Lock & Instance Management (Phase 1) ──────────

    def _get_session_lock(self, session_id: str) -> threading.Lock:
        """Get or create a per-session lock. Thread-safe creation via meta-lock."""
        with self._locks_lock:
            if session_id not in self._session_locks:
                self._session_locks[session_id] = threading.Lock()
            return self._session_locks[session_id]

    def _get_or_create_agent(self, session_id: str) -> Any:
        """Get or create a per-session ReActAgent instance."""
        lock = self._get_session_lock(session_id)
        with lock:
            if session_id not in self._agents:
                self._agents[session_id] = self._agent_factory(session_id)
            return self._agents[session_id]

    def _get_or_create_memory(self, session_id: str) -> Any:
        """Get or create a per-session MemoryManager.
        R7: factory_builder returns a closure that absorbs STM on first call."""
        lock = self._get_session_lock(session_id)
        with lock:
            if session_id not in self._memory_managers:
                factory = self._memory_factory_builder(session_id)
                self._memory_managers[session_id] = factory()
            return self._memory_managers[session_id]

    def delete_session(self, session_id: str) -> None:
        """Explicit cleanup for all per-session state (R3). Lock ordering: acquire session lock first."""
        session_lock = self._get_session_lock(session_id)
        with session_lock:
            self._agents.pop(session_id, None)
            self._memory_managers.pop(session_id, None)
            self._token_buffers.pop(session_id, None)  # R8
            self._token_cursors.pop(session_id, None)  # R8
        # Session lock is idle now, safe to remove
        with self._locks_lock:
            self._session_locks.pop(session_id, None)
        with self._processing_lock:
            self._processing_sessions.discard(session_id)

    def is_session_processing(self, session_id: str) -> bool:
        """Check if a specific session is currently processing."""
        with self._processing_lock:
            return session_id in self._processing_sessions

    # ── Message Queue ──────────────────────────────────────────────

    @property
    def is_processing(self) -> bool:
        """Check if ANY session is currently processing."""
        with self._processing_lock:
            return len(self._processing_sessions) > 0

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

    def mark_processing(self, processing: bool, session_id: str | None = None) -> None:
        """Mark whether a session is currently processing. Backward-compatible: if no session_id, affects all."""
        with self._processing_lock:
            if session_id is not None:
                if processing:
                    self._processing_sessions.add(session_id)
                else:
                    self._processing_sessions.discard(session_id)
            else:
                # Legacy fallback: clear all processing state
                if not processing:
                    self._processing_sessions.clear()

    def _put_event(self, run_id: str, event: AgentEvent) -> None:
        """Put event into both sync and async queues."""
        sync_q = self._run_events.get(run_id)
        if sync_q is not None:
            sync_q.put(event)
        async_q = self._async_run_events.get(run_id)
        if async_q is not None:
            try:
                async_q.put_nowait(event)
            except Exception as e:
                logger.debug("Async event queue put_nowait failed: %s", e)

    def send_message(
        self,
        session_id: str,
        text: str,
        on_run_started: Callable[[RunHandle], None] | None = None,
    ) -> RunHandle:
        if session_id not in self._sessions:
            raise KeyError(f"Unknown session_id: {session_id}")
        # Per-session agent and memory — no shared lock needed (R1)
        agent = self._get_or_create_agent(session_id)
        memory = self._get_or_create_memory(session_id)
        # Reset token buffers for new run (R8)
        with self._get_session_lock(session_id):
            self._token_buffers[session_id] = ""
            self._token_cursors[session_id] = 0
        # Mark this session as processing
        self.mark_processing(True, session_id=session_id)
        run, events, turn = self.begin_turn(session_id, text, memory_manager=memory)
        if on_run_started is not None:
            on_run_started(run)
        # Persist user question immediately so it survives even if the run
        # is interrupted (e.g. WebSocket disconnect, page navigation).
        try:
            version_mgr = self._get_version_manager(session_id)
            existing = version_mgr.get_messages(limit=0)
            if not existing or existing[-1].get("content") != text:
                version_mgr.commit_with_messages(
                    messages=[{"role": "user", "content": text}],
                    question=text, answer="",
                )
        except Exception as e:
            logger.debug("Failed to persist user question immediately: %s", e)
        old_on_event = getattr(agent, "_on_event", None)
        old_output = getattr(agent, "_output", None)
        try:
            if self._capability_runtime is not None:
                self._capability_runtime.refresh_if_stale()
            if hasattr(agent, "set_cancel_checker"):
                agent.set_cancel_checker(turn.cancel_checker)
            agent_text = self._prepare_message(text, events, run.id, session_id, agent=agent, memory_manager=memory)
            self._install_agent_event_bridge(turn, events, run.id, session_id, old_on_event, agent=agent)
            # Suppress agent _output (print) — events are sent via WebSocket
            try:
                agent._output = lambda _msg: None
            except Exception as e:
                logger.debug("Failed to suppress agent output: %s", e)
            # 启动 trace，记录任务级元数据
            from agentnexus.observability.tracer import trace_manager as _tm
            _tm.start_trace(agent_text, metadata={
                "user_goal": text,
                "model_version": agent.llm_client.model,
                "agent_id": agent.agent_id,
                "max_steps": agent.max_steps,
                "session_id": session_id,
            })
            try:
                result = agent.run(agent_text, memory_manager=memory)
            finally:
                _tm.end_trace()
            answer = getattr(result, "answer", result)
            record = turn.finish(answer or "")
            # Persist cumulative token usage and step count to DB
            try:
                usage = getattr(agent, "_total_usage", {}) or {}
                version_mgr.update_session_stats(
                    input_tokens=usage.get("input_tokens", 0),
                    output_tokens=usage.get("output_tokens", 0),
                    step_count=getattr(agent, "_step_count", 0),
                )
            except Exception as e:
                logger.warning("Failed to persist session stats: %s", e)
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
            self.mark_processing(False, session_id=session_id)
            if hasattr(agent, "set_cancel_checker"):
                agent.set_cancel_checker(None)
            try:
                agent._on_event = old_on_event
            except Exception as e:
                logger.debug("Failed to restore agent _on_event: %s", e)
            try:
                agent._output = old_output
            except Exception as e:
                logger.debug("Failed to restore agent _output: %s", e)
            self._put_event(run.id, None)
        return run

    def _get_version_manager(self, session_id: str):
        """Return a per-session ConversationVersionManager, creating one if needed."""
        if session_id not in self._version_managers:
            from agentnexus.core.config import get_settings
            from agentnexus.memory.versioned import ConversationVersionManager
            settings = get_settings()
            workspace = ""
            if self._version is not None:
                workspace = getattr(self._version, "_workspace_path", "")
            self._version_managers[session_id] = ConversationVersionManager(
                session_id,
                settings.memory_db_path,
                workspace_path=workspace,
            )
        return self._version_managers[session_id]

    def _get_or_create_stm(self, session_id: str, snapshot: str | None = None) -> Any:
        """Return the per-session STM, creating one from snapshot if needed."""
        if session_id not in self._stms:
            if snapshot:
                from agentnexus.memory.short_term import ShortTermMemory
                self._stms[session_id] = ShortTermMemory.from_json(snapshot)
            else:
                from agentnexus.memory.short_term import ShortTermMemory
                self._stms[session_id] = ShortTermMemory()
        return self._stms[session_id]

    def set_session_stm_snapshot(self, session_id: str, snapshot: str) -> None:
        """Store a per-session STM from a checkpoint snapshot (used by restore_session)."""
        from agentnexus.memory.short_term import ShortTermMemory
        self._stms[session_id] = ShortTermMemory.from_json(snapshot)

    def begin_turn(self, session_id: str, text: str, memory_manager: Any = None) -> tuple[RunHandle, queue.Queue[AgentEvent | None], TurnRuntime]:
        if session_id not in self._sessions:
            raise KeyError(f"Unknown session_id: {session_id}")
        run = RunHandle(id=f"run_{uuid.uuid4().hex[:12]}", session_id=session_id)
        events: queue.Queue[AgentEvent | None] = queue.Queue()
        async_events: asyncio.Queue[AgentEvent | None] = asyncio.Queue()
        self._run_events[run.id] = events
        self._async_run_events[run.id] = async_events
        version_mgr = self._get_version_manager(session_id)
        turn = TurnRuntime(
            run_id=run.id,
            session_id=session_id,
            question=text,
            memory_manager=memory_manager,
            version_manager=version_mgr,
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
        agent: Any = None,
        memory_manager: Any = None,
    ) -> str:
        service = self._skill_service
        agent = agent or getattr(self, "_agent", None)
        memory_manager = memory_manager or getattr(self, "_memory", None)
        if service is None:
            return text
        session = self._sessions[session_id]
        if session.skill:
            service.use(session.skill)
        # Set session profile on per-session agent (replaces SkillService.agent reference)
        if agent is not None and session.skill and hasattr(agent, "set_session_profile"):
            agent.set_session_profile(session.skill)

        # Get router recommendations (fast, deterministic, ~45ms)
        recommendations = service.get_recommendations(text)

        # Inject skill context WITH recommendations into agent prompt
        if agent is not None and hasattr(agent, "set_available_skill_context"):
            agent.set_available_skill_context(
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
            memory_manager=memory_manager,
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
        # Pass workflow context to agent as a separate system message
        # (not embedded in the user question — that buryies the actual question)
        workflow_ctx = getattr(result, "workflow_context", None)
        if workflow_ctx and agent is not None and hasattr(agent, "set_workflow_context"):
            agent.set_workflow_context(workflow_ctx)
        return result.enhanced_question

    def _install_agent_event_bridge(
        self,
        turn: TurnRuntime,
        events: queue.Queue[AgentEvent | None],
        run_id: str,
        session_id: str,
        previous,
        agent: Any = None,
    ) -> None:
        agent = agent or getattr(self, "_agent", None)
        has_reasoning = False

        def _on_event(event, from_state, to_state):
            nonlocal has_reasoning
            event_type = getattr(getattr(event, "type", None), "name", str(getattr(event, "type", "")))
            payload = getattr(event, "payload", {}) or {}

            # STREAM_TOKEN events are sent directly as token events for real-time streaming
            if event_type in ("STREAM_TOKEN", "STREAM_REASONING"):
                token = payload.get("token", "")
                if token:
                    if event_type == "STREAM_REASONING":
                        has_reasoning = True
                    evt_type = "stream_reasoning" if event_type == "STREAM_REASONING" else "stream_token"
                    token_event = AgentEvent(
                        evt_type,
                        {"token": token},
                        run_id=run_id,
                        session_id=session_id,
                    )
                    self._put_event(run_id, token_event)
                    # Update token buffer + cursor atomically (R8)
                    # Same lock as snapshot read — ensures content/cursor consistency
                    with self._get_session_lock(session_id):
                        self._token_buffers[session_id] = \
                            self._token_buffers.get(session_id, "") + token
                        self._token_cursors[session_id] = \
                            self._token_cursors.get(session_id, 0) + 1
                return

            self._record_agent_event(turn, event)

            # Skip thought events when reasoning is available
            if event_type in ("TOOLS_FOUND", "ANSWER_THOUGHT") and has_reasoning:
                has_reasoning = False
                return

            # TOOL_START/TOOL_DONE: carry payload directly to avoid journal-parsing bugs
            if event_type == "TOOL_START":
                self._put_event(run_id, AgentEvent(
                    "tool_start",
                    {"name": payload.get("name", ""), "arguments": payload.get("arguments", {})},
                    run_id=run_id,
                    session_id=session_id,
                ))
                return
            if event_type == "TOOL_DONE":
                self._put_event(run_id, AgentEvent(
                    "tool_done",
                    {
                        "name": payload.get("name", ""),
                        "arguments": payload.get("arguments", {}),
                        "result": collapse_and_truncate(payload.get("result", ""), 300),
                    },
                    run_id=run_id,
                    session_id=session_id,
                ))
                return

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
            if agent is not None:
                agent._on_event = _on_event
        except Exception as e:
            logger.debug("Failed to install agent event bridge: %s", e)

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
            result = collapse_and_truncate(payload.get("result", ""), 300)
            turn.record("tool done", f"{payload.get('name', '')} -> {result}")
        elif event_type == "THOUGHT_MISSING":
            turn.record("retry", "model thought missing; requested retry")
        elif event_type == "RETRIES_LEFT":
            turn.record("retry", payload.get("reason", ""))
        elif event_type == "DEGRADED":
            turn.record("degraded", payload.get("strategy", ""))

    def stream_events(self, run_id: str, timeout: float = 30.0) -> Iterator[AgentEvent]:
        events = self._run_events.get(run_id)
        if events is None:
            raise KeyError(f"Unknown run_id: {run_id}")
        while True:
            try:
                event = events.get(timeout=timeout)
            except queue.Empty:
                logger.warning("stream_events timed out for run_id=%s", run_id)
                break
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
        turn = self._turns.get(run_id)
        if turn is not None:
            record = turn.cancel(reason)
            self._run_snapshots[run_id] = record
            self._put_event(run_id, AgentEvent(
                "run_interrupted",
                {"error": reason, "status": record.status, "answer": record.answer, "reason": record.reason},
                run_id=run_id,
                session_id=record.session_id,
            ))
            self._put_event(run_id, AgentEvent(
                "run_persisted",
                {"status": record.status},
                run_id=run_id,
                session_id=record.session_id,
            ))
        else:
            self._put_event(run_id, AgentEvent("run_interrupted", {"error": reason}, run_id=run_id))
        # 同步和异步队列都需要 None 哨兵来终止 stream
        sync_q = self._run_events.get(run_id)
        if sync_q is not None:
            sync_q.put(None)
        async_q = self._async_run_events.get(run_id)
        if async_q is not None:
            try:
                async_q.put_nowait(None)
            except Exception:
                pass

    def confirm_tool_call(self, run_id: str, approved: bool) -> None:
        events = self._run_events.get(run_id)
        if events is not None:
            events.put(AgentEvent("confirmation_requested", {"approved": approved}, run_id=run_id))

    def get_session_snapshot(self, session_id: str) -> dict[str, Any]:
        if session_id not in self._sessions:
            raise KeyError(f"Unknown session_id: {session_id}")
        return {
            "session": self._sessions[session_id],
            "memory": self._memory_managers.get(session_id),
            "version": self._version_managers.get(session_id),
        }

    def get_run_snapshot(self, run_id: str) -> TurnRecord | None:
        turn = self._turns.get(run_id)
        if turn is not None:
            return turn.record_snapshot
        return self._run_snapshots.get(run_id)
