"""Command hook executor — runs declarative shell-command hooks.

Contract (converged with Codex / Claude Code so community recipes port over):

- stdin  receives JSON: {"hook_event", "session_id", "workspace",
  "payload", "abort_supported"}
- exit 0  → success; stdout JSON may carry {"update": {payload merge},
  "context": "text shown in logs"}
- exit 2  → block: stderr is the reason, surfaced via HookContext.abort
- other non-zero / spawn error / timeout → handled per config.on_failure
  ("warn" logs and continues, "block" aborts — interceptable events only)

Security: hooks run with a scrubbed environment (AGENTNEXUS_* plus OS
basics like PATH — LLM API keys and other secrets are never passed down).
Commands run with the effective workspace as cwd.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from agentnexus.core.hook_schemas import HookConfig
from agentnexus.core.hooks import AbortCode
from agentnexus.tools.workspace import get_effective_workspace

logger = logging.getLogger(__name__)

# Environment variables always passed through so shells/interpreters work.
_ENV_ALLOWLIST = {
    "PATH", "SystemRoot", "COMSPEC", "WINDIR", "HOME", "USERPROFILE",
    "HOMEDRIVE", "HOMEPATH", "TEMP", "TMP", "PATHEXT", "OS", "NUMBER_OF_PROCESSORS",
    "LANG", "LC_ALL", "TERM",
}

_OBSERVER_POOL: ThreadPoolExecutor | None = None
_warned_untrusted: set[str] = set()


@dataclass
class CommandResult:
    """Outcome of one command hook execution."""

    config: HookConfig
    exit_code: int | None
    stdout: str = ""
    stderr: str = ""
    duration_ms: float = 0.0
    timed_out: bool = False
    error: str | None = None
    update: dict[str, Any] = field(default_factory=dict)
    context_text: str = ""

    @property
    def blocked(self) -> bool:
        return self.exit_code == 2


def _observer_pool() -> ThreadPoolExecutor:
    global _OBSERVER_POOL
    if _OBSERVER_POOL is None or _OBSERVER_POOL._shutdown:
        _OBSERVER_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="hook-obs")
    return _OBSERVER_POOL


def _build_subprocess_env() -> dict[str, str]:
    """Scrubbed env: AGENTNEXUS_* plus OS basics; secrets never propagate."""
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        if key.startswith("AGENTNEXUS_") or key in _ENV_ALLOWLIST:
            env[key] = value
    return env


def _parse_stdout(stdout: str) -> tuple[dict[str, Any], str]:
    """Extract {"update": {...}, "context": str} from hook stdout, tolerantly."""
    text = (stdout or "").strip()
    if not text:
        return {}, ""
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return {}, ""
    if not isinstance(parsed, dict):
        return {}, ""
    update = parsed.get("update")
    context = parsed.get("context")
    return (
        update if isinstance(update, dict) else {},
        context if isinstance(context, str) else "",
    )


def execute_command_hook(
    config: HookConfig,
    *,
    hook_event: str,
    payload: dict[str, Any],
    abort_supported: bool,
) -> CommandResult:
    """Run one hook command synchronously and classify the outcome."""
    workspace = get_effective_workspace()
    stdin_payload = {
        "hook_event": hook_event,
        "session_id": payload.get("session_id") or "",
        "workspace": str(workspace),
        "payload": payload,
        "abort_supported": abort_supported,
    }
    t0 = time.perf_counter()
    try:
        process = subprocess.Popen(
            config.command,
            shell=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(workspace),
            env=_build_subprocess_env(),
            start_new_session=(sys.platform != "win32"),
        )
        try:
            stdout, stderr = process.communicate(
                input=json.dumps(stdin_payload, ensure_ascii=False),
                timeout=config.timeout,
            )
        except subprocess.TimeoutExpired:
            _kill_process_tree(process)
            stdout, stderr = process.communicate()
            duration_ms = (time.perf_counter() - t0) * 1000
            logger.warning(
                "Command hook %r timed out after %.1fs (killed)",
                config.command[:80], config.timeout,
            )
            return CommandResult(
                config=config, exit_code=None, timed_out=True,
                stderr=(stderr or "")[:4000],
                duration_ms=duration_ms,
                error=f"timeout after {config.timeout}s",
            )
        duration_ms = (time.perf_counter() - t0) * 1000
        update, context_text = _parse_stdout(stdout or "")
        return CommandResult(
            config=config,
            exit_code=process.returncode,
            stdout=(stdout or "")[:4000],
            stderr=(stderr or "")[:4000],
            duration_ms=duration_ms,
            update=update,
            context_text=context_text,
        )
    except OSError as exc:
        duration_ms = (time.perf_counter() - t0) * 1000
        return CommandResult(
            config=config, exit_code=None, duration_ms=duration_ms, error=str(exc),
        )


def _kill_process_tree(process: subprocess.Popen) -> None:
    """Kill a timed-out hook and its children.

    On Windows, killing the cmd.exe wrapper leaves grandchildren holding
    inherited pipe handles, which blocks communicate() until they exit —
    so the kill must target the whole tree (taskkill /F /T).
    """
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                capture_output=True, timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        # POSIX: grandchildren (shell wrappers that didn't exec) inherit the
        # pipe handles and would block communicate() until they exit — kill
        # the whole process group (spawned with start_new_session).
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
    try:
        process.kill()
    except OSError:
        pass


def _is_interceptable(hook_event: str) -> bool:
    return hook_event.startswith("before_")


def _apply_result(ctx, result: CommandResult, *, can_block: bool) -> None:
    """Fold one command result into the hook context."""
    cfg = result.config
    if result.blocked and can_block:
        reason = result.stderr.strip() or f"command hook blocked ({cfg.command[:60]})"
        ctx.abort(reason, code=AbortCode.BLOCKED)
        return
    if result.exit_code == 0:
        if result.update:
            ctx.payload.update(result.update)
        if result.context_text:
            logger.info("hook context [%s]: %s", cfg.event, result.context_text[:300])
        return
    if result.timed_out or result.error or (result.exit_code not in (0, 2)):
        failure_reason = result.error or result.stderr.strip() or f"exit {result.exit_code}"
        if cfg.on_failure == "block" and can_block:
            ctx.abort(f"command hook failed: {failure_reason}", code=AbortCode.BLOCKED)
        else:
            logger.warning(
                "Command hook %r failed (%s) — continuing",
                cfg.command[:80], failure_reason,
            )


def _run_async_observer(config: HookConfig, hook_event: str, payload: dict) -> None:
    def _job() -> None:
        result = execute_command_hook(
            config, hook_event=hook_event, payload=payload, abort_supported=False,
        )
        if result.blocked or result.exit_code not in (0, None):
            logger.info(
                "Async hook %r finished: exit=%s (%sms)",
                config.command[:60], result.exit_code, round(result.duration_ms),
            )

    _observer_pool().submit(_job)


def run_command_hooks_for(ctx) -> list[CommandResult]:
    """Execute declared command hooks for an already-fired HookContext.

    Called by HookManager after the in-process hook chain. In-process
    aborts skip command hooks entirely (chain already short-circuited).
    """
    from agentnexus.core.hook_sources import discover_hook_configs

    hook_event = ctx.hook_type.value
    configs = discover_hook_configs()
    matched = [c for c in configs if c.config.matches(hook_event, ctx.payload)]
    if not matched:
        return []

    results: list[CommandResult] = []
    if _is_interceptable(hook_event):
        # Sequential, can block; abort short-circuits the rest.
        for loaded in matched:
            cfg = loaded.config
            if cfg.async_:
                _run_async_observer(cfg, hook_event, dict(ctx.payload))
                continue
            result = execute_command_hook(
                cfg, hook_event=hook_event, payload=dict(ctx.payload),
                abort_supported=True,
            )
            results.append(result)
            _apply_result(ctx, result, can_block=True)
            if ctx.aborted:
                break
    else:
        # Observer events: concurrent, never block, results only logged.
        futures = []
        max_timeout = 0.0
        for loaded in matched:
            cfg = loaded.config
            if cfg.async_:
                _run_async_observer(cfg, hook_event, dict(ctx.payload))
                continue
            max_timeout = max(max_timeout, cfg.timeout)
            futures.append(
                _observer_pool().submit(
                    execute_command_hook, cfg,
                    hook_event=hook_event, payload=dict(ctx.payload),
                    abort_supported=False,
                )
            )
        for future in futures:
            try:
                result = future.result(timeout=max(60.0, max_timeout + 5))
            except Exception as exc:  # defensive: observer hooks never break the loop
                logger.debug("observer command hook failed to complete: %s", exc)
                continue
            results.append(result)
            _apply_result(ctx, result, can_block=False)
    return results
