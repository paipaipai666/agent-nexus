"""Unified hook/event system for AgentNexus lifecycle interception."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

SLOW_HOOK_THRESHOLD_MS = 100

_JOURNAL_LOCK = None  # lazy threading.Lock


def _journal_lock():
    global _JOURNAL_LOCK
    if _JOURNAL_LOCK is None:
        import threading

        _JOURNAL_LOCK = threading.Lock()
    return _JOURNAL_LOCK


_TIMEOUT_POOL = None


def _hook_timeout_pool():
    global _TIMEOUT_POOL
    if _TIMEOUT_POOL is None or _TIMEOUT_POOL._shutdown:
        import concurrent.futures

        _TIMEOUT_POOL = concurrent.futures.ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="hook-timeout",
        )
    return _TIMEOUT_POOL


def write_hook_journal(records: list[dict]) -> None:
    """Append per-hook outcome lines to {traces_dir}/hooks.jsonl.

    Gated by config ``hooks_journal`` (default on). Failures never propagate —
    the journal is observability, not control flow.
    """
    if not records:
        return
    try:
        from agentnexus.core.config import get_settings

        settings = get_settings()
        if not getattr(settings, "hooks_journal", True):
            return
        traces_dir = Path(getattr(settings, "traces_dir", "") or "")
        if not str(traces_dir):
            return
        import time as _time

        path = Path(traces_dir)
        path.mkdir(parents=True, exist_ok=True)
        line_ts = _time.time()
        with _journal_lock():
            with (path / "hooks.jsonl").open("a", encoding="utf-8") as handle:
                for record in records:
                    record.setdefault("ts", line_ts)
                    handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except Exception:
        logger.debug("hook journal write failed", exc_info=True)


def read_hook_journal(limit: int = 100) -> list[dict]:
    """Tail the hook journal (most recent first). Missing/invalid lines skipped."""
    try:
        from agentnexus.core.config import get_settings

        traces_dir = Path(getattr(get_settings(), "traces_dir", "") or "")
        if not str(traces_dir):
            return []
        path = Path(traces_dir) / "hooks.jsonl"
        if not path.is_file():
            return []
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
        records: list[dict] = []
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return records
    except Exception:
        return []


class HookType(str, Enum):
    """Supported hook points in the agent lifecycle."""

    # ── Tier 0: user-interaction lifecycle ────────────────────────
    USER_PROMPT_SUBMIT = "user_prompt_submit"     # payload["prompt"] rewritable; abort = refuse
    AGENT_STOP = "agent_stop"                     # veto final answer → agent continues
    PERMISSION_REQUEST = "permission_request"     # Tool Gateway HITL decision input
    NOTIFICATION = "notification"                 # fire-and-forget (HITL waiting, prompts)

    # ── agent-level tool/model lifecycle ──────────────────────────
    BEFORE_TOOL_CALL = "before_tool_call"
    AFTER_TOOL_CALL = "after_tool_call"
    ON_TOOL_ERROR = "on_tool_error"
    BEFORE_MODEL_CALL = "before_model_call"
    AFTER_MODEL_CALL = "after_model_call"
    AGENT_START = "agent_start"
    AGENT_END = "agent_end"
    BEFORE_MEMORY_OP = "before_memory_op"
    AFTER_MEMORY_OP = "after_memory_op"

    # ── Tier 1: core governance paths ────────────────────────────
    BEFORE_LLM_CALL = "before_llm_call"
    AFTER_LLM_CALL = "after_llm_call"
    BEFORE_LTM_SAVE = "before_ltm_save"
    AFTER_LTM_SAVE = "after_ltm_save"
    BEFORE_LTM_SEARCH = "before_ltm_search"
    AFTER_LTM_SEARCH = "after_ltm_search"
    BEFORE_SHELL_EXEC = "before_shell_exec"
    AFTER_SHELL_EXEC = "after_shell_exec"
    BEFORE_REGISTRY_INVOKE = "before_registry_invoke"
    AFTER_REGISTRY_INVOKE = "after_registry_invoke"

    # ── Tier 2: operational lifecycle ────────────────────────────
    BEFORE_MCP_CONNECT = "before_mcp_connect"
    AFTER_MCP_CONNECT = "after_mcp_connect"
    BEFORE_MCP_CALL_TOOL = "before_mcp_call_tool"
    AFTER_MCP_CALL_TOOL = "after_mcp_call_tool"
    # Subagent hooks fire on dispatcher lane-pool worker threads when
    # subagents run in parallel — callbacks must be thread-safe and must
    # never touch UI directly (TUI: use call_from_thread).
    BEFORE_SUBAGENT_RUN = "before_subagent_run"
    AFTER_SUBAGENT_RUN = "after_subagent_run"
    BEFORE_RAG_SEARCH = "before_rag_search"
    AFTER_RAG_SEARCH = "after_rag_search"
    BEFORE_KB_INGEST = "before_kb_ingest"
    AFTER_KB_INGEST = "after_kb_ingest"
    BEFORE_CHECKPOINT = "before_checkpoint"
    AFTER_CHECKPOINT = "after_checkpoint"

    # ── Tier 3: infrastructure lifecycle ─────────────────────────
    BEFORE_PLUGIN_LOAD = "before_plugin_load"
    AFTER_PLUGIN_LOAD = "after_plugin_load"
    BEFORE_APP_BUILD = "before_app_build"
    AFTER_APP_BUILD = "after_app_build"
    BEFORE_COMPACT = "before_compact"
    AFTER_COMPACT = "after_compact"
    BEFORE_WORKFLOW_STEP = "before_workflow_step"
    AFTER_WORKFLOW_STEP = "after_workflow_step"
    BEFORE_EVAL_RUN = "before_eval_run"
    AFTER_EVAL_RUN = "after_eval_run"


_MUTABLE_HOOKS: frozenset[HookType] = frozenset(
    {
        HookType.USER_PROMPT_SUBMIT,
        HookType.PERMISSION_REQUEST,
        HookType.BEFORE_TOOL_CALL,
        HookType.BEFORE_MODEL_CALL,
        HookType.AFTER_MODEL_CALL,
        HookType.BEFORE_LLM_CALL,
        HookType.BEFORE_SHELL_EXEC,
        HookType.BEFORE_MCP_CALL_TOOL,
        HookType.BEFORE_RAG_SEARCH,
    }
)

# Expected payload keys per event. fire() validates present keys against
# these types when hook_schema_check is enabled (default) — catches drift
# between fire sites and hook authors instead of silent misbehavior.
# Extra keys are allowed (forward compatibility).
_STR = str
_DICT = dict
_LIST = list
_INT = int
_FLOAT = float
_BOOL = bool
_ANY = object

PAYLOAD_SCHEMAS: dict[HookType, dict[str, Any]] = {
    HookType.USER_PROMPT_SUBMIT: {"prompt": _STR, "agent_id": _STR},
    HookType.AGENT_STOP: {"answer": _STR, "steps": _INT, "question": _STR, "agent_id": _STR},
    HookType.PERMISSION_REQUEST: {"name": _STR, "params": _DICT, "caller": _STR, "risk_level": _STR},
    HookType.NOTIFICATION: {"kind": _STR},
    HookType.BEFORE_TOOL_CALL: {"name": _STR, "params": _DICT, "caller": _STR},
    HookType.AFTER_TOOL_CALL: {"name": _STR, "params": _DICT, "result": _ANY},
    HookType.ON_TOOL_ERROR: {"name": _STR, "params": _DICT, "error": _ANY},
    HookType.BEFORE_MODEL_CALL: {"messages": _LIST, "tools": _LIST, "strategy": _STR},
    HookType.AFTER_MODEL_CALL: {"messages": _LIST, "response_text": _STR},
    HookType.AGENT_START: {"question": _STR, "agent_id": _STR},
    HookType.AGENT_END: {"answer": _ANY, "steps": _INT, "agent_id": _STR},
    HookType.BEFORE_MEMORY_OP: {"op": _STR, "role": _STR, "content": _STR},
    HookType.AFTER_MEMORY_OP: {"op": _STR, "role": _STR, "content": _STR},
    HookType.BEFORE_LLM_CALL: {"messages": _LIST, "model": _STR, "tools": _ANY},
    HookType.AFTER_LLM_CALL: {"messages": _LIST, "model": _STR, "response_text": _STR},
    HookType.BEFORE_LTM_SAVE: {"session_id": _STR, "content": _STR, "category": _STR, "importance": _INT},
    HookType.AFTER_LTM_SAVE: {"session_id": _STR, "content": _STR, "category": _STR, "importance": _INT},
    HookType.BEFORE_LTM_SEARCH: {"category": _ANY, "limit": _INT, "min_similarity": _FLOAT},
    HookType.AFTER_LTM_SEARCH: {"category": _ANY, "limit": _INT, "result_count": _INT},
    HookType.BEFORE_SHELL_EXEC: {"command": _STR, "cwd": _ANY, "timeout": _ANY},
    HookType.AFTER_SHELL_EXEC: {"command": _STR, "cwd": _ANY, "timeout": _ANY, "result": _STR, "backend": _STR},
    HookType.BEFORE_REGISTRY_INVOKE: {"name": _STR, "params": _DICT, "caller": _STR},
    HookType.AFTER_REGISTRY_INVOKE: {"name": _STR, "params": _DICT, "caller": _STR,
                                      "duration_ms": _FLOAT, "error": _ANY},
    HookType.BEFORE_MCP_CONNECT: {"server_name": _STR},
    HookType.AFTER_MCP_CONNECT: {"server_name": _STR, "success": _BOOL, "state": _STR},
    HookType.BEFORE_MCP_CALL_TOOL: {"local_name": _STR, "params": _DICT},
    HookType.AFTER_MCP_CALL_TOOL: {"local_name": _STR, "server_name": _STR},
    HookType.BEFORE_SUBAGENT_RUN: {"task": _STR, "role": _STR, "tool_names": _LIST,
                                    "max_steps": _INT, "retry_reason": _ANY},
    HookType.AFTER_SUBAGENT_RUN: {"task": _STR, "role": _STR, "tool_names": _LIST,
                                   "max_steps": _INT, "retry_reason": _ANY,
                                   "success": _BOOL, "error": _ANY},
    HookType.BEFORE_RAG_SEARCH: {"query": _STR, "namespace": _STR},
    HookType.AFTER_RAG_SEARCH: {"query": _STR, "namespace": _STR, "result_count": _INT},
    HookType.BEFORE_KB_INGEST: {"filepath": _STR, "namespace": _STR, "enable_contextual": _BOOL},
    HookType.AFTER_KB_INGEST: {"filepath": _STR, "namespace": _STR,
                                "written_chunks": _INT, "replaced_chunks": _INT},
    HookType.BEFORE_CHECKPOINT: {"session_id": _STR, "question": _STR},
    HookType.AFTER_CHECKPOINT: {"session_id": _STR, "question": _STR, "cp_id": _ANY, "parent_id": _ANY},
    HookType.BEFORE_PLUGIN_LOAD: {},
    HookType.AFTER_PLUGIN_LOAD: {"loaded_count": _INT, "disabled_count": _INT,
                                  "failed_count": _INT, "loaded_names": _LIST},
    HookType.BEFORE_APP_BUILD: {"profile": _ANY, "session_id": _ANY, "restore_session": _ANY},
    HookType.AFTER_APP_BUILD: {"profile": _ANY, "session_id": _ANY, "mcp_enabled": _BOOL},
    HookType.BEFORE_COMPACT: {"is_auto": _BOOL, "threshold": _ANY},
    HookType.AFTER_COMPACT: {"is_auto": _BOOL, "tokens_saved": _INT,
                              "tokens_before": _INT, "tokens_after": _INT},
    HookType.BEFORE_WORKFLOW_STEP: {"method": _STR},
    HookType.AFTER_WORKFLOW_STEP: {"method": _STR},
    HookType.BEFORE_EVAL_RUN: {"traces_dir": _STR, "days": _INT},
    HookType.AFTER_EVAL_RUN: {"traces_dir": _STR, "days": _INT,
                               "trace_count": _INT, "record_count": _INT},
}


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
            # Positional reason doubles as the structured message so
            # ctx.abort("why", code="X") doesn't silently drop the reason.
            self._abort_reason = message or reason
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

    def to_feedback(self) -> str:
        """Standardized, model-visible text for an aborted hook chain.

        Every abort path must surface this to the model (as observation or
        error text) so it can adjust instead of blind-retrying.
        """
        text = f"[hook blocked] {self.abort_code}"
        if self._abort_reason:
            text += f": {self._abort_reason}"
        if self._abort_details:
            details = json.dumps(self._abort_details, ensure_ascii=False, default=str)
            text += f" | details: {details[:500]}"
        return text


class AbortCode:
    """Structured abort codes (free strings are discouraged)."""

    BLOCKED = "BLOCKED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    POLICY_VIOLATION = "POLICY_VIOLATION"
    RATE_LIMITED = "RATE_LIMITED"


@dataclass
class _HookEntry:
    """Internal: one registered hook."""

    name: str
    hook_type: HookType
    callback: Callable
    priority: int = 200
    enabled: bool = True
    fail_count: int = 0      # consecutive exceptions (warned once, then debug)
    timeout: float | None = None  # seconds; sync callbacks only, None = no cap


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
        timeout: float | None = None,
    ) -> str:
        """Register a hook.  Returns the hook name.

        ``timeout`` caps sync callback duration (seconds); on expiry the
        loop continues without the hook's result — the stray thread is left
        to finish (same caveat as registry SEC-008: Python cannot kill
        threads). Async callbacks ignore ``timeout``.
        """
        hook_name = name or callback.__name__
        self._hooks[hook_name] = _HookEntry(
            name=hook_name,
            hook_type=hook_type,
            callback=callback,
            priority=priority,
            enabled=enabled,
            timeout=timeout,
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
        from agentnexus.observability.tracer import get_trace_manager

        trace_mgr = get_trace_manager()
        ctx = HookContext(hook_type, dict(payload))
        self._validate_payload(hook_type, ctx.payload)
        mutable = hook_type in _MUTABLE_HOOKS
        records: list[dict] = []
        t0 = time.perf_counter()
        with trace_mgr.span("hook_fire", {"hook_type": hook_type.name}):
            for entry in self._sorted(hook_type):
                if not entry.enabled:
                    continue
                record = {"event": hook_type.value, "hook": entry.name,
                          "kind": "inprocess", "outcome": "ok", "duration_ms": 0.0}
                before = dict(ctx.payload) if not mutable else None
                started = time.perf_counter()
                exc: Exception | None = None
                with trace_mgr.span("hook_call", {"hook": entry.name}):
                    try:
                        if entry.timeout is not None \
                                and not asyncio.iscoroutinefunction(entry.callback):
                            record["outcome"] = self._run_with_timeout(entry, ctx)
                        elif asyncio.iscoroutinefunction(entry.callback):
                            self._run_async(entry.callback(ctx))
                        else:
                            entry.callback(ctx)
                    except Exception as err:  # noqa: BLE001 — isolation by design
                        exc = err
                record["duration_ms"] = round((time.perf_counter() - started) * 1000, 1)
                if exc is not None:
                    entry.fail_count += 1
                    record["outcome"] = "raised"
                    if entry.fail_count == 1:
                        logger.warning("Hook %r raised (suppressed)", entry.name, exc_info=True)
                    else:
                        logger.debug("Hook %r raised again (x%d)", entry.name, entry.fail_count)
                if before is not None and ctx.payload != before:
                    changed = sorted(
                        k for k in set(before) | set(ctx.payload)
                        if before.get(k) != ctx.payload.get(k)
                    )
                    record["changed_keys"] = changed
                    logger.warning(
                        "Hook %r mutated read-only %s payload (changed keys: %s)",
                        entry.name, hook_type.value, changed,
                    )
                records.append(record)
                if ctx.aborted:
                    break
            if not ctx.aborted:
                records.extend(self._run_command_hooks(ctx))
            elif ctx.aborted:
                records.append({"event": hook_type.value, "hook": "(chain)",
                                "kind": "chain", "outcome": "aborted",
                                "abort_code": ctx.abort_code, "duration_ms": 0.0})
        ctx.elapsed_ms = (time.perf_counter() - t0) * 1000
        write_hook_journal(records)
        self._check_slow(ctx, hook_type)
        return ctx

    # ── internals ──────────────────────────────────────────────────

    def _sorted(self, hook_type: HookType) -> list[_HookEntry]:
        return sorted(
            [e for e in self._hooks.values() if e.hook_type == hook_type],
            key=lambda e: e.priority,
        )

    @staticmethod
    def _validate_payload(hook_type: HookType, payload: dict[str, Any]) -> None:
        """Log an error when a fire site drifts from PAYLOAD_SCHEMAS.

        Gated by config ``hook_schema_check`` (default on). Never raises —
        validation exists to catch framework bugs, not to break the loop.
        """
        try:
            from agentnexus.core.config import get_settings

            if not getattr(get_settings(), "hook_schema_check", True):
                return
        except Exception:
            return
        schema = PAYLOAD_SCHEMAS.get(hook_type)
        if schema is None:
            return
        for key, expected in schema.items():
            if key not in payload:
                logger.error(
                    "hook %s payload missing key %r (fire-site drift)",
                    hook_type.value, key,
                )
                continue
            value = payload[key]
            if expected is object:
                continue
            if not isinstance(value, expected):
                logger.error(
                    "hook %s payload key %r expected %s, got %s",
                    hook_type.value, key, expected.__name__, type(value).__name__,
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
    def _run_command_hooks(ctx: HookContext) -> list[dict]:
        """Execute declared command hooks after the in-process chain.

        Lazy import keeps subprocess machinery off the hot import path;
        no-op when no command hooks are declared. Returns journal records.
        """
        try:
            from agentnexus.core.hook_executor import run_command_hooks_for

            results = run_command_hooks_for(ctx)
        except Exception:
            # Command hooks must never break the agent loop.
            logger.warning("command hook execution failed", exc_info=True)
            return []
        records: list[dict] = []
        for result in results:
            if result.blocked:
                outcome = "blocked"
            elif result.timed_out:
                outcome = "timeout"
            elif result.exit_code == 0:
                outcome = "ok"
            else:
                outcome = "error"
            records.append({
                "event": ctx.hook_type.value,
                "hook": result.config.command[:80],
                "kind": "command",
                "outcome": outcome,
                "duration_ms": round(result.duration_ms, 1),
            })
        return records

    @staticmethod
    def _run_with_timeout(entry: _HookEntry, ctx: HookContext) -> str:
        """Run a sync hook callback on a worker thread with a deadline.

        Returns "ok" or "timeout". On timeout the loop continues without the
        hook's contribution; the stray thread runs to completion (Python
        cannot kill threads — same caveat as registry SEC-008).
        """
        import concurrent.futures

        pool = _hook_timeout_pool()
        future = pool.submit(entry.callback, ctx)
        try:
            future.result(timeout=entry.timeout)
            return "ok"
        except concurrent.futures.TimeoutError:
            logger.warning(
                "Hook %r exceeded timeout %.1fs — skipped", entry.name, entry.timeout,
            )
            return "timeout"
        except Exception:
            raise  # handled by the caller's isolation path

    @staticmethod
    def _run_async(coro):
        """Run an async coroutine from a sync context."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # We're inside a running event loop — schedule on it
            future = asyncio.run_coroutine_threadsafe(coro, loop)
            try:
                return future.result(timeout=30)
            except TimeoutError:
                future.cancel()
                logger.warning("Async hook timed out after 30s")
                return None
        else:
            # No running loop — safe to create one
            return asyncio.run(coro)


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
    timeout: float | None = None,
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
        mgr.register(hook_type, func, name=name, priority=priority, timeout=timeout)
        return func

    return decorator
