"""Unified hook/event system for AgentNexus lifecycle interception."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)

SLOW_HOOK_THRESHOLD_MS = 100


class HookType(str, Enum):
    """Supported hook points in the agent lifecycle."""

    BEFORE_TOOL_CALL = "before_tool_call"
    AFTER_TOOL_CALL = "after_tool_call"
    ON_TOOL_ERROR = "on_tool_error"
    BEFORE_MODEL_CALL = "before_model_call"
    AFTER_MODEL_CALL = "after_model_call"
    AGENT_START = "agent_start"
    AGENT_END = "agent_end"
    BEFORE_MEMORY_OP = "before_memory_op"
    AFTER_MEMORY_OP = "after_memory_op"


_MUTABLE_HOOKS: frozenset[HookType] = frozenset(
    {
        HookType.BEFORE_TOOL_CALL,
        HookType.BEFORE_MODEL_CALL,
        HookType.AFTER_MODEL_CALL,
    }
)


@dataclass
class HookContext:
    """Context passed to each hook.  Contains payload, abort mechanism, and timing."""

    hook_type: HookType
    payload: dict[str, Any]
    _abort: bool = field(default=False, repr=False)
    _abort_code: str = field(default="", repr=False)
    _abort_reason: str = field(default="", repr=False)
    _abort_details: dict[str, Any] = field(default_factory=dict, repr=False)
    elapsed_ms: float = 0.0

    def abort(
        self,
        reason: str = "",
        *,
        code: str | None = None,
        message: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        """Short-circuit hook chain.

        Two calling conventions:
        - ``ctx.abort("simple reason")`` — backward compatible, code defaults to "BLOCKED"
        - ``ctx.abort(code="PERMISSION_DENIED", message="...", details={...})`` — structured
        """
        self._abort = True
        if code is not None:
            self._abort_code = code
            self._abort_reason = message
            self._abort_details = details or {}
        else:
            self._abort_code = "BLOCKED"
            self._abort_reason = reason
            self._abort_details = {}

    @property
    def aborted(self) -> bool:
        return self._abort

    @property
    def abort_code(self) -> str:
        return self._abort_code

    @property
    def abort_reason(self) -> str:
        return self._abort_reason

    @property
    def abort_details(self) -> dict[str, Any]:
        return self._abort_details


@dataclass
class _HookEntry:
    """Internal: one registered hook."""

    name: str
    hook_type: HookType
    callback: Callable
    priority: int = 200
    enabled: bool = True


class HookManager:
    """Central registry and dispatcher for lifecycle hooks."""

    def __init__(self) -> None:
        self._hooks: dict[str, _HookEntry] = {}
        self._slow_threshold_ms: float = SLOW_HOOK_THRESHOLD_MS

    # ── registration ───────────────────────────────────────────────

    def register(
        self,
        hook_type: HookType,
        callback: Callable,
        *,
        name: str | None = None,
        priority: int = 200,
        enabled: bool = True,
    ) -> str:
        """Register a hook.  Returns the hook name."""
        hook_name = name or callback.__name__
        self._hooks[hook_name] = _HookEntry(
            name=hook_name,
            hook_type=hook_type,
            callback=callback,
            priority=priority,
            enabled=enabled,
        )
        return hook_name

    def unregister(self, name: str) -> None:
        """Remove a hook by name."""
        self._hooks.pop(name, None)

    def enable(self, name: str) -> None:
        """Enable a previously disabled hook."""
        entry = self._hooks.get(name)
        if entry:
            entry.enabled = True

    def disable(self, name: str) -> None:
        """Disable a hook without removing it."""
        entry = self._hooks.get(name)
        if entry:
            entry.enabled = False

    def list_hooks(self) -> list[dict[str, Any]]:
        """Return metadata for all registered hooks."""
        return [
            {
                "name": e.name,
                "hook_type": e.hook_type.value,
                "priority": e.priority,
                "enabled": e.enabled,
                "is_async": asyncio.iscoroutinefunction(e.callback),
            }
            for e in sorted(
                self._hooks.values(), key=lambda e: (e.hook_type.value, e.priority)
            )
        ]

    def clear(self) -> None:
        """Remove all hooks (for testing)."""
        self._hooks.clear()

    # ── dispatch (sync) ────────────────────────────────────────────

    def fire(self, hook_type: HookType, payload: dict[str, Any]) -> HookContext:
        """Fire all hooks for *hook_type* synchronously.  Returns the context."""
        ctx = HookContext(hook_type, dict(payload))
        t0 = time.perf_counter()
        for entry in self._sorted(hook_type):
            if not entry.enabled:
                continue
            try:
                if asyncio.iscoroutinefunction(entry.callback):
                    self._run_async(entry.callback(ctx))
                else:
                    entry.callback(ctx)
            except Exception:
                logger.debug("Hook %r raised (fire)", entry.name, exc_info=True)
            if ctx.aborted:
                break
        ctx.elapsed_ms = (time.perf_counter() - t0) * 1000
        self._check_slow(ctx, hook_type)
        return ctx

    # ── dispatch (async) ───────────────────────────────────────────

    async def afire(self, hook_type: HookType, payload: dict[str, Any]) -> HookContext:
        """Fire all hooks for *hook_type* asynchronously.  Returns the context."""
        ctx = HookContext(hook_type, dict(payload))
        t0 = time.perf_counter()
        for entry in self._sorted(hook_type):
            if not entry.enabled:
                continue
            try:
                if asyncio.iscoroutinefunction(entry.callback):
                    await entry.callback(ctx)
                else:
                    entry.callback(ctx)
            except Exception:
                logger.debug("Hook %r raised (afire)", entry.name, exc_info=True)
            if ctx.aborted:
                break
        ctx.elapsed_ms = (time.perf_counter() - t0) * 1000
        self._check_slow(ctx, hook_type)
        return ctx

    # ── internals ──────────────────────────────────────────────────

    def _sorted(self, hook_type: HookType) -> list[_HookEntry]:
        return sorted(
            [e for e in self._hooks.values() if e.hook_type == hook_type],
            key=lambda e: e.priority,
        )

    def _check_slow(self, ctx: HookContext, hook_type: HookType) -> None:
        if ctx.elapsed_ms > self._slow_threshold_ms:
            logger.warning(
                "Slow hook chain %s took %.1fms (threshold %dms)",
                hook_type.value,
                ctx.elapsed_ms,
                self._slow_threshold_ms,
            )

    @staticmethod
    def _run_async(coro) -> None:
        """Run an async coroutine from sync context."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(asyncio.run, coro).result()
        else:
            asyncio.run(coro)


# ── module-level singleton ─────────────────────────────────────────

_hook_manager: HookManager | None = None


def get_hook_manager() -> HookManager:
    """Return the global HookManager singleton."""
    global _hook_manager
    if _hook_manager is None:
        _hook_manager = HookManager()
    return _hook_manager


def _reset_hook_manager() -> None:
    """Reset singleton (for testing only)."""
    global _hook_manager
    _hook_manager = None


# ── decorator API ──────────────────────────────────────────────────


def on(
    hook_type: HookType,
    *,
    name: str | None = None,
    priority: int = 200,
    _manager: HookManager | None = None,
) -> Callable:
    """Decorator to register a function as a hook.

    Usage::

        @on(HookType.BEFORE_TOOL_CALL)
        def audit(ctx):
            print(f"tool call: {ctx.payload['name']}")
    """

    def decorator(func: Callable) -> Callable:
        mgr = _manager or get_hook_manager()
        mgr.register(hook_type, func, name=name, priority=priority)
        return func

    return decorator
