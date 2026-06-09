"""ComputerUseManager — singleton manager for desktop automation.

Follows the same design pattern as BrowserManager:
- Singleton + classmethod instance()
- Per-task state isolation
- TTL auto-cleanup
- HITL rule checking
- Background event loop for async operations
"""

from __future__ import annotations

import asyncio
import logging
import re
from time import monotonic
from typing import Any

from agentnexus.core.config import get_settings
from agentnexus.tools.computer_use.backends.base import ComputerUseBackend
from agentnexus.tools.computer_use.element import DesktopElement

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Error/warning formatting helpers (same pattern as browser.py)
# ---------------------------------------------------------------------------


def _error(msg: str, detail: str = "", hint: str = "") -> str:
    """Format a unified error response."""
    parts = [f"ERROR: {msg}"]
    if detail:
        parts.append(f"DETAIL: {detail}")
    if hint:
        parts.append(f"HINT: {hint}")
    return "\n".join(parts)


def _warning(msg: str, detail: str = "") -> str:
    """Format a unified warning response."""
    parts = [f"WARNING: {msg}"]
    if detail:
        parts.append(f"DETAIL: {detail}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# HITL rules check
# ---------------------------------------------------------------------------


def _check_hitl_rules(action: str, role: str | None, name: str | None) -> bool:
    """Check if an action matches any HITL rule requiring human confirmation.

    Returns True if the action should be blocked for HITL review.
    """
    settings = get_settings()
    rules = settings.computer_use_hitl_rules
    if not rules:
        return False

    for rule in rules:
        if rule.get("action") and rule["action"] != action:
            continue
        if rule.get("role") and rule.get("role") != (role or ""):
            continue
        if rule.get("name_pattern"):
            if not re.search(rule["name_pattern"], name or "", re.IGNORECASE):
                continue
        return True  # Rule matched — HITL required
    return False


# ---------------------------------------------------------------------------
# App blocklist check
# ---------------------------------------------------------------------------


def _is_blocked_app(app_name: str) -> bool:
    """Check if an application is in the blocked list."""
    settings = get_settings()
    blocked = settings.computer_use_blocked_apps
    if not blocked:
        return False
    app_lower = app_name.lower()
    return any(b.lower() in app_lower for b in blocked)


def _is_allowed_app(app_name: str) -> bool:
    """Check if an application is in the allowed list (empty = all allowed)."""
    settings = get_settings()
    allowed = settings.computer_use_allowed_apps
    if not allowed:
        return True  # Empty list = all allowed
    app_lower = app_name.lower()
    return any(a.lower() in app_lower for a in allowed)


# ---------------------------------------------------------------------------
# ComputerUseManager — singleton
# ---------------------------------------------------------------------------


class ComputerUseManager:
    """Singleton manager for desktop automation with per-task isolation.

    Follows the same pattern as BrowserManager:
    - Singleton via classmethod instance()
    - Per-task state tracking (focused window, last access time)
    - TTL auto-cleanup for idle tasks
    - HITL rule checking
    """

    _instance: ComputerUseManager | None = None

    def __init__(self) -> None:
        self._backend: ComputerUseBackend | None = None
        self._backend_platform: str = ""
        self._lock = asyncio.Lock()
        # Per-task state
        self._focused_window: dict[str, dict[str, Any]] = {}  # task_id -> window info
        self._last_access: dict[str, float] = {}  # task_id -> monotonic timestamp
        self._active_tasks: set[str] = set()
        self._ttl_task: Any = None  # asyncio.Task for TTL cleanup
        self._ttl_enabled: bool = True
        self._evicted_snapshots: dict[str, dict] = {}  # task_id -> snapshot data

    @classmethod
    def instance(cls) -> ComputerUseManager:
        """Get or create the singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def ensure_backend(self) -> ComputerUseBackend:
        """Lazily initialize the platform backend."""
        if self._backend is not None:
            return self._backend

        async with self._lock:
            if self._backend is not None:
                return self._backend
            return await self._init_backend()

    async def _init_backend(self) -> ComputerUseBackend:
        """Initialize the backend. Caller must hold self._lock."""
        settings = get_settings()
        preferred = settings.computer_use_backend

        from agentnexus.tools.computer_use.backends import _detect_platform, get_backend

        platform = preferred if preferred != "auto" else _detect_platform()
        self._backend = get_backend(preferred)
        self._backend_platform = platform

        logger.info("ComputerUse backend initialized: %s", platform)

        # Start TTL cleanup
        if self._ttl_enabled and self._ttl_task is None:
            self._ttl_task = asyncio.create_task(self._ttl_cleanup_loop())

        return self._backend

    async def _ttl_cleanup_loop(self) -> None:
        """Background task to clean up idle task resources."""
        try:
            while True:
                await asyncio.sleep(60)
                settings = get_settings()
                ttl = settings.computer_use_snapshot_max_nodes  # reuse as TTL hint
                now = monotonic()
                idle_tasks = [
                    tid for tid, ts in self._last_access.items()
                    if now - ts > 600  # 10 min default TTL
                ]
                for tid in idle_tasks:
                    logger.info("TTL cleanup: evicting idle task %s", tid)
                    self._active_tasks.discard(tid)
                    self._focused_window.pop(tid, None)
                    self._last_access.pop(tid, None)
        except asyncio.CancelledError:
            pass

    async def get_focused_window(self, task_id: str) -> dict[str, Any] | None:
        """Get the focused window for a task."""
        self._last_access[task_id] = monotonic()
        self._active_tasks.add(task_id)
        return self._focused_window.get(task_id)

    async def set_focused_window(self, task_id: str, window: dict[str, Any]) -> None:
        """Set the focused window for a task."""
        self._focused_window[task_id] = window
        self._last_access[task_id] = monotonic()
        self._active_tasks.add(task_id)

    async def list_windows(self, task_id: str) -> list[dict[str, Any]]:
        """List all visible windows."""
        self._last_access[task_id] = monotonic()
        self._active_tasks.add(task_id)
        backend = await self.ensure_backend()
        return await backend.list_windows()

    async def get_snapshot(
        self,
        task_id: str,
        app_name: str | None = None,
        window_title: str | None = None,
    ) -> list[DesktopElement]:
        """Get accessibility tree for a window."""
        self._last_access[task_id] = monotonic()
        self._active_tasks.add(task_id)
        backend = await self.ensure_backend()

        # If no window specified, use focused window
        if not app_name and not window_title:
            focused = self._focused_window.get(task_id)
            if focused:
                app_name = focused.get("app_name")
                window_title = focused.get("title")

        return await backend.get_snapshot(app_name, window_title)

    async def click(
        self,
        task_id: str,
        element_id: str,
        button: str = "left",
        clicks: int = 1,
    ) -> None:
        """Click an element."""
        self._last_access[task_id] = monotonic()
        backend = await self.ensure_backend()
        await backend.click(element_id, button, clicks)

    async def type_text(
        self,
        task_id: str,
        element_id: str,
        text: str,
        clear: bool = True,
    ) -> None:
        """Type text into an element."""
        self._last_access[task_id] = monotonic()
        backend = await self.ensure_backend()
        await backend.type_text(element_id, text, clear)

    async def press_key(self, task_id: str, keys: str) -> None:
        """Press a key combination."""
        self._last_access[task_id] = monotonic()
        backend = await self.ensure_backend()
        await backend.press_key(keys)

    async def scroll(
        self,
        task_id: str,
        element_id: str | None = None,
        direction: str = "down",
        amount: int = 3,
    ) -> None:
        """Scroll an element or the screen."""
        self._last_access[task_id] = monotonic()
        backend = await self.ensure_backend()
        await backend.scroll(element_id, direction, amount)

    async def select(
        self,
        task_id: str,
        element_id: str,
        value: str,
    ) -> None:
        """Select a value in a combobox."""
        self._last_access[task_id] = monotonic()
        backend = await self.ensure_backend()
        await backend.select(element_id, value)

    async def toggle(
        self,
        task_id: str,
        element_id: str,
        checked: bool | None = None,
    ) -> None:
        """Toggle a checkbox."""
        self._last_access[task_id] = monotonic()
        backend = await self.ensure_backend()
        await backend.toggle(element_id, checked)

    async def launch_app(
        self,
        task_id: str,
        app_path: str,
        args: list[str] | None = None,
    ) -> dict[str, Any]:
        """Launch an application."""
        self._last_access[task_id] = monotonic()
        backend = await self.ensure_backend()

        # Check blocklist
        if _is_blocked_app(app_path):
            raise ValueError(f"应用 {app_path} 在黑名单中，禁止启动。")

        # Check allowlist
        if not _is_allowed_app(app_path):
            raise ValueError(f"应用 {app_path} 不在白名单中，禁止启动。")

        result = await backend.launch_app(app_path, args)

        # Auto-focus the launched app
        if result.get("title"):
            self._focused_window[task_id] = result
        self._active_tasks.add(task_id)

        return result

    async def get_clipboard(self, task_id: str) -> str:
        """Read clipboard content."""
        self._last_access[task_id] = monotonic()
        backend = await self.ensure_backend()
        return await backend.get_clipboard()

    async def set_clipboard(self, task_id: str, text: str) -> None:
        """Write to clipboard."""
        self._last_access[task_id] = monotonic()
        backend = await self.ensure_backend()
        await backend.set_clipboard(text)

    def close_task(self, task_id: str) -> None:
        """Clean up per-task state."""
        self._focused_window.pop(task_id, None)
        self._last_access.pop(task_id, None)
        self._active_tasks.discard(task_id)

    async def close_all(self) -> None:
        """Shut down the manager and release all resources."""
        if self._ttl_task:
            self._ttl_task.cancel()
            try:
                await self._ttl_task
            except asyncio.CancelledError:
                pass
            self._ttl_task = None

        if self._backend:
            try:
                await self._backend.close()
            except Exception:
                pass
            self._backend = None

        self._focused_window.clear()
        self._last_access.clear()
        self._active_tasks.clear()
        self._evicted_snapshots.clear()
        ComputerUseManager._instance = None


# ---------------------------------------------------------------------------
# Persistent background event loop (same pattern as browser.py)
# ---------------------------------------------------------------------------

_bg_loop: asyncio.AbstractEventLoop | None = None
_bg_thread: Any = None


def _get_bg_loop() -> asyncio.AbstractEventLoop:
    """Get or create a persistent background event loop running in a daemon thread."""
    global _bg_loop, _bg_thread
    if _bg_loop is not None and _bg_loop.is_running():
        return _bg_loop

    import threading

    _bg_loop = asyncio.new_event_loop()

    def _run_loop():
        asyncio.set_event_loop(_bg_loop)
        _bg_loop.run_forever()

    _bg_thread = threading.Thread(target=_run_loop, daemon=True, name="computer-use-bg-loop")
    _bg_thread.start()
    return _bg_loop


def _run_async(coro: Any) -> Any:
    """Run an async coroutine on the persistent background event loop."""
    loop = _get_bg_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout=120)
