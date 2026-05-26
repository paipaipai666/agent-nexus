"""Thread-safe in-process audit log for tool calls."""

from __future__ import annotations

from collections.abc import Iterator
from threading import RLock

from agentnexus.tools.registry import AuditEntry


class ThreadSafeAuditLog:
    """Small list-like audit buffer guarded by a re-entrant lock."""

    def __init__(self):
        self._entries: list[AuditEntry] = []
        self._lock = RLock()

    def append(self, entry: AuditEntry) -> None:
        with self._lock:
            self._entries.append(entry)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def copy(self) -> list[AuditEntry]:
        with self._lock:
            return list(self._entries)

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def __iter__(self) -> Iterator[AuditEntry]:
        return iter(self.copy())

    def __getitem__(self, key):
        with self._lock:
            if isinstance(key, slice):
                return list(self._entries[key])
            return self._entries[key]


_global_audit_log = ThreadSafeAuditLog()


def get_audit_log() -> list[AuditEntry]:
    return _global_audit_log.copy()


def append_audit(entry: AuditEntry) -> None:
    _global_audit_log.append(entry)
