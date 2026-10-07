"""Subagent visibility registry — named subagents, live status, per-subagent cancel.

Subagents spawned by the ``subagent_run`` tool execute on dispatcher lane-pool
threads, so they cannot reach the per-run event machinery of ChatService
directly. ChatService publishes a per-run ``SubagentRunContext`` on the shared
registry's ``subagent_bridge`` (same single-slot pattern as ``CancelBridge``);
the subagent tool closure forwards child-agent events through that context
into the parent run's queues, and individual cancellation rides a
``threading.Event`` stored on each entry.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Statuses exposed to the UI (also carried on every subagent_event).
TERMINAL_STATUSES = frozenset({"interrupted", "completed", "failed"})

_NAME_MAX_LEN = 40


def _sanitize_name(hint: object) -> str:
    """Strip, collapse whitespace, cap at 40 chars; empty -> '' (caller generates)."""
    name = " ".join(str(hint or "").split())[:_NAME_MAX_LEN].strip()
    return name


@dataclass
class SubagentEntry:
    subagent_id: str
    session_id: str
    run_id: str
    name: str
    role: str
    task: str
    status: str = "thinking"
    current_tool: str = ""
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    steps_used: int = 0
    error: str = ""
    cancel_event: threading.Event = field(default_factory=threading.Event)


class SubagentRegistry:
    """Thread-safe, in-memory registry of subagent entries."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_id: dict[str, SubagentEntry] = {}
        # (session_id, base) -> last used counter, for auto-generated names.
        self._auto_counts: dict[tuple[str, str], int] = {}
        # session_id -> set of used display names.
        self._names: dict[str, set[str]] = {}

    def register(self, session_id: str, run_id: str, name_hint: object,
                 role: str, task: str) -> SubagentEntry:
        with self._lock:
            subagent_id = f"sub_{uuid.uuid4().hex[:8]}"
            name = _sanitize_name(name_hint)
            if not name:
                key = (session_id, role)
                n = self._auto_counts.get(key, 0) + 1
                self._auto_counts[key] = n
                name = f"{role}-{n}"
            name = self._unique_name(session_id, name)
            entry = SubagentEntry(
                subagent_id=subagent_id, session_id=session_id, run_id=run_id,
                name=name, role=role, task=task,
            )
            self._by_id[subagent_id] = entry
            return entry

    def _unique_name(self, session_id: str, name: str) -> str:
        used = self._names.setdefault(session_id, set())
        if name not in used:
            used.add(name)
            return name
        i = 2
        while f"{name}-{i}" in used:
            i += 1
        unique = f"{name}-{i}"
        used.add(unique)
        return unique

    def get(self, subagent_id: str) -> SubagentEntry | None:
        with self._lock:
            return self._by_id.get(subagent_id)

    def list_for_session(self, session_id: str) -> list[SubagentEntry]:
        with self._lock:
            return [e for e in self._by_id.values() if e.session_id == session_id]

    def update(self, entry: SubagentEntry, **fields) -> None:
        with self._lock:
            for key, value in fields.items():
                if value is None:
                    continue
                setattr(entry, key, value)
            if entry.status in TERMINAL_STATUSES and entry.finished_at is None:
                entry.finished_at = time.time()

    def cancel(self, subagent_id: str) -> bool:
        """Flag a subagent for cancellation. False if unknown or already terminal."""
        with self._lock:
            entry = self._by_id.get(subagent_id)
            if entry is None or entry.status in TERMINAL_STATUSES:
                return False
            entry.cancel_event.set()
            return True

    @staticmethod
    def to_dict(entry: SubagentEntry) -> dict:
        return {
            "subagent_id": entry.subagent_id,
            "name": entry.name,
            "role": entry.role,
            "task": entry.task,
            "status": entry.status,
            "current_tool": entry.current_tool,
            "started_at": entry.started_at,
            "finished_at": entry.finished_at,
            "steps_used": entry.steps_used,
            "error": entry.error,
        }


class SubagentRunContext:
    """Per-run handle handed to the subagent tool closure via SubagentBridge.

    Methods are called from dispatcher lane-pool threads; they only touch the
    thread-safe registry and ChatService._put_event (which serializes via its
    own lock) — never the agent loop.
    """

    def __init__(self, service, session_id: str, run_id: str) -> None:
        self._service = service
        self._session_id = session_id
        self._run_id = run_id

    def started(self, name_hint: object, role: str, task: str) -> SubagentEntry:
        entry = self._service._subagents.register(
            self._session_id, self._run_id, name_hint, role, task,
        )
        self.event(entry, "started", status="thinking", role=role, task=task)
        return entry

    def event(self, entry: SubagentEntry, kind: str, status: str | None = None,
              **fields) -> None:
        try:
            self._service._subagent_emit(entry, kind, status=status, **fields)
        except Exception:
            logger.debug("subagent event emit failed", exc_info=True)

    def finished(self, entry: SubagentEntry, status: str, summary: str = "",
                 error: str = "", steps_used: int = 0) -> None:
        self.event(entry, "finished", status=status, summary=summary,
                   error=error, steps_used=steps_used)
