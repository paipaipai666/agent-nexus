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

from agentnexus.agents.exceptions import AgentCancelled
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
    workspace: str | None = None  # normalized; None = inherit the server default cwd


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
    seq: int = 0  # per-run monotonic sequence for reconnect resume
    tok_seq: int = 0  # per-run monotonic index over stream_token events only;
                      # absolute (queue-position independent) reconnect skip key


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
        self._run_event_seq: dict[str, int] = {}
        self._session_last_run: dict[str, str] = {}
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
        # Char offset into _token_buffers where the CURRENT step started
        # (advanced on TOOL_START). Snapshot content is buffer[base:] so a
        # mid-run reconnect never replays earlier steps' text into the
        # current step's draft.
        self._token_step_base: dict[str, int] = {}
        # Per-run monotonic index over stream_token events (reconnect skip key)
        self._run_token_seq: dict[str, int] = {}
        # Per-session version managers — each session gets its own journal + checkpoints
        self._version_managers: dict[str, Any] = {}
        # Per-session short-term memories — each session gets its own STM deque
        # (R7: migrated into MemoryManager via closure factory)
        self._stms: dict[str, Any] = {}

    def start_session(
        self,
        skill: str | None = None,
        profile: str | None = None,
        workspace: str | None = None,
    ) -> SessionHandle:
        if workspace:
            from agentnexus.memory.versioned import ConversationVersionManager
            workspace = ConversationVersionManager.normalize_workspace_path(workspace)
        handle = SessionHandle(
            id=f"session_{uuid.uuid4().hex[:12]}",
            skill=skill, profile=profile, workspace=workspace or None,
        )
        self._sessions[handle.id] = handle
        return handle

    def get_session_workspace(self, session_id: str) -> str | None:
        """Return the session's explicit workspace, or None for the default."""
        handle = self._sessions.get(session_id)
        return handle.workspace if handle else None

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
        if event is not None:
            seq = self._run_event_seq.get(run_id, 0) + 1
            self._run_event_seq[run_id] = seq
            # frozen dataclass — assign via object.__setattr__
            object.__setattr__(event, "seq", seq)
        sync_q = self._run_events.get(run_id)
        if sync_q is not None:
            sync_q.put(event)
        async_q = self._async_run_events.get(run_id)
        if async_q is not None:
            try:
                async_q.put_nowait(event)
            except Exception as e:
                logger.debug("Async event queue put_nowait failed: %s", e)

    def _evaluate_run_alerts(self) -> None:
        """Feed recent run metrics into the alert pipeline (non-fatal)."""
        try:
            from agentnexus.core.config import get_settings
            from agentnexus.observability.alerting import evaluate_stats
            from agentnexus.observability.stats import compute_stats

            traces_dir = get_settings().traces_dir
            if not traces_dir:
                return
            evaluate_stats(compute_stats(traces_dir, days=1))
        except Exception as e:
            logger.debug("Post-run alert evaluation failed: %s", e)

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
        # Tools resolve relative paths against this session's workspace folder.
        from agentnexus.tools.workspace import current_workspace
        _ws_token = current_workspace.set(self._sessions[session_id].workspace)
        # Reset token buffers for new run (R8)
        with self._get_session_lock(session_id):
            self._token_buffers[session_id] = ""
            self._token_cursors[session_id] = 0
            self._token_step_base[session_id] = 0
        # Mark this session as processing
        self.mark_processing(True, session_id=session_id)
        # Persist user question BEFORE the run starts so it is durable the
        # moment on_run_started fires (run_started ⇒ user message already
        # on disk — a disconnect/cancel from the event-loop thread can no
        # longer win the first commit slot).
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
        run, events, turn = self.begin_turn(session_id, text, memory_manager=memory)
        if on_run_started is not None:
            on_run_started(run)
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
                self._evaluate_run_alerts()
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
            # Surface the LLM error when the run produced no answer — a silent
            # empty reply reads as "agent ignored me" (e.g. rate-limited model).
            llm_error = ""
            llm_client = getattr(agent, "llm_client", None)
            if not answer and llm_client is not None:
                llm_error = getattr(llm_client, "last_error", "") or ""
            self._put_event(run.id, AgentEvent(
                "message_delta", {"text": answer or ""},
                run_id=run.id, session_id=session_id,
            ))
            self._put_event(run.id, AgentEvent(
                "run_finished",
                {"answer": answer or "", "status": record.status, "error": llm_error},
                run_id=run.id,
                session_id=session_id,
            ))
            self._put_event(run.id, AgentEvent(
                "run_persisted", {"status": record.status},
                run_id=run.id, session_id=session_id,
            ))
        except Exception as exc:
            # If the turn was already settled externally (cancel_run from the
            # WS lifecycle / cancel endpoint), its terminal events were
            # emitted there — re-emitting would duplicate them for the client.
            already_settled = turn.record_snapshot.status != "running"
            if turn.cancel_checker() or isinstance(exc, AgentCancelled):
                record = turn.cancel("cancelled")
                event_type = "run_interrupted"
            else:
                record = turn.fail("Agent 执行错误", str(exc))
                event_type = "run_failed"
            self._run_snapshots[run.id] = record
            if not already_settled:
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
            current_workspace.reset(_ws_token)
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
            # Run ended — the token snapshot is only meaningful mid-run. Left
            # populated, a post-run reconnect would overwrite the finalized
            # answer with raw streamed tokens (reconnect_snapshot).
            with self._get_session_lock(session_id):
                self._token_buffers.pop(session_id, None)
                self._token_cursors.pop(session_id, None)
                self._token_step_base.pop(session_id, None)
            self._put_event(run.id, None)
        return run

    def _get_version_manager(self, session_id: str):
        """Return a per-session ConversationVersionManager, creating one if needed."""
        if session_id not in self._version_managers:
            from agentnexus.core.config import get_settings
            from agentnexus.memory.versioned import ConversationVersionManager
            settings = get_settings()
            # Per-session workspace wins; fall back to the server default.
            handle = self._sessions.get(session_id)
            workspace = (handle.workspace if handle else None) or ""
            if not workspace and self._version is not None:
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
        # Drop the previous run's queues/state (reconnect resume only ever
        # targets the latest run) — otherwise these dicts grow unboundedly.
        prev_run = self._session_last_run.get(session_id)
        if prev_run:
            self._run_events.pop(prev_run, None)
            self._async_run_events.pop(prev_run, None)
            self._turns.pop(prev_run, None)
            self._run_event_seq.pop(prev_run, None)
            self._run_token_seq.pop(prev_run, None)
            self._run_snapshots.pop(prev_run, None)
        run = RunHandle(id=f"run_{uuid.uuid4().hex[:12]}", session_id=session_id)
        events: queue.Queue[AgentEvent | None] = queue.Queue()
        async_events: asyncio.Queue[AgentEvent | None] = asyncio.Queue()
        self._run_events[run.id] = events
        self._async_run_events[run.id] = async_events
        self._run_event_seq[run.id] = 0
        self._run_token_seq[run.id] = 0
        self._session_last_run[session_id] = run.id
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

            # STREAM_TOKEN feeds the reconnect snapshot (content only) and gets
            # an absolute per-run tok_seq (reconnect skip key). STREAM_REASONING
            # is resumed via seq-based replay — it must NOT pollute the answer
            # buffer, the token cursor, or the token skip counter: mixing the
            # two channels desynced client/server cursors and rendered
            # reasoning text as answer content (see experiments/verify_reconnect_resume.py).
            if event_type in ("STREAM_TOKEN", "STREAM_REASONING"):
                token = payload.get("token", "")
                if token:
                    if event_type == "STREAM_TOKEN":
                        tok_seq = self._run_token_seq.get(run_id, 0) + 1
                        self._run_token_seq[run_id] = tok_seq
                        token_event = AgentEvent(
                            "stream_token",
                            {"token": token},
                            run_id=run_id,
                            session_id=session_id,
                            tok_seq=tok_seq,
                        )
                        self._put_event(run_id, token_event)
                        # Update token buffer + cursor atomically (R8)
                        # Same lock as snapshot read — ensures content/cursor consistency
                        with self._get_session_lock(session_id):
                            self._token_buffers[session_id] = \
                                self._token_buffers.get(session_id, "") + token
                            self._token_cursors[session_id] = \
                                self._token_cursors.get(session_id, 0) + 1
                    else:
                        has_reasoning = True
                        self._put_event(run_id, AgentEvent(
                            "stream_reasoning",
                            {"token": token},
                            run_id=run_id,
                            session_id=session_id,
                        ))
                return

            self._record_agent_event(turn, event)

            # Skip thought events when reasoning is available
            if event_type in ("TOOLS_FOUND", "ANSWER_THOUGHT") and has_reasoning:
                has_reasoning = False
                return

            # TOOL_START/TOOL_DONE: carry payload directly to avoid journal-parsing bugs
            if event_type == "TOOL_START":
                # The current LLM step ends here: everything buffered so far
                # belongs to PREVIOUS steps. Advance the snapshot baseline so
                # reconnect_snapshot only ever carries the CURRENT step's text.
                with self._get_session_lock(session_id):
                    self._token_step_base[session_id] = \
                        len(self._token_buffers.get(session_id, ""))
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

    def get_run_token_snapshot(self, session_id: str) -> dict[str, Any]:
        """Current-step token text + absolute token cursor for WS reconnect (R8).

        content is scoped to the CURRENT step (buffer[_token_step_base:]) so a
        mid-run reconnect never injects earlier steps' raw text into the
        in-flight answer draft; cursor counts content tokens only (reasoning
        resumes via seq-based replay, not the snapshot).
        """
        lock = self._get_session_lock(session_id)
        with lock:
            buffer = self._token_buffers.get(session_id, "")
            base = self._token_step_base.get(session_id, 0)
            return {
                "content": buffer[base:],
                "cursor": self._token_cursors.get(session_id, 0),
            }

    def is_run_active(self, run_id: str) -> bool:
        """True while the run is still executing (not finished/failed/cancelled).

        Used by connection-lifecycle code to cancel only live runs — calling
        cancel_run on a completed run would re-emit terminal events.
        """
        turn = self._turns.get(run_id)
        return turn is not None and turn.record_snapshot.status == "running"

    def cancel_all_runs(self, reason: str = "server_shutdown") -> None:
        """Cancel every still-active run (graceful shutdown / process exit).

        Each cancellation persists results and queues terminal events, so
        nothing is lost when the server goes down.
        """
        for run_id in list(self._turns.keys()):
            try:
                if self.is_run_active(run_id):
                    self.cancel_run(run_id, reason=reason)
            except Exception:
                logger.exception("Failed to cancel run %s during shutdown", run_id)

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
