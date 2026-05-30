"""Thread-safe in-process audit log for tool calls."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agentnexus.tools.registry import AuditEntry
else:
    AuditEntry = Any

logger = logging.getLogger(__name__)


class ThreadSafeAuditLog:
    """Small list-like audit buffer guarded by a re-entrant lock.

    Optionally persists entries to a JSONL file for crash resilience.
    """

    def __init__(self, persist_dir: str | None = None):
        self._entries: list[AuditEntry] = []
        self._lock = RLock()
        self._persist_dir = persist_dir
        if persist_dir:
            Path(persist_dir).mkdir(parents=True, exist_ok=True)

    def append(self, entry: AuditEntry) -> None:
        with self._lock:
            self._entries.append(entry)
            if self._persist_dir:
                try:
                    date_str = time.strftime("%Y-%m-%d")
                    path = Path(self._persist_dir) / f"audit_{date_str}.jsonl"
                    with open(path, "a", encoding="utf-8") as f:
                        entry_dict = entry if isinstance(entry, dict) else {"entry": str(entry)}
                        f.write(json.dumps(entry_dict, ensure_ascii=False, default=str) + "\n")
                except OSError as e:
                    logger.debug("Audit log persistence failed (non-fatal): %s", e)

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
