"""Tool Registry — unified governance layer for all agent tools.

Every tool must be registered with ToolMeta before any agent can call it.
The registry enforces: RBAC, parameter schema validation, rate limiting,
risk-level HITL gates, timeout control, and audit logging.
"""

from __future__ import annotations

import contextvars
import json
import logging
import threading
import time
from collections import defaultdict
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from agentnexus.observability.audit_log import ThreadSafeAuditLog
from agentnexus.observability.tracer import trace_manager
from agentnexus.skills.profile import filter_tool_meta
from agentnexus.tools.result_format import summarize_tool_result

try:
    import jsonschema
except ImportError:
    jsonschema = None

logger = logging.getLogger(__name__)

_SENSITIVE_PARAM_TOKENS = (
    "api_key", "apikey", "token", "secret", "password", "authorization",
    "credential", "auth", "private", "access_key", "session_key", "signing_key",
)
_VALIDATOR_CACHE: dict[str, Any] = {}


def _is_sensitive_key(key: str) -> bool:
    normalized = str(key).lower().replace("-", "").replace("_", "")
    return any(token.replace("_", "") in normalized for token in _SENSITIVE_PARAM_TOKENS)


def _redact_sensitive_params(value: Any, key: str | None = None) -> Any:
    if key is not None and _is_sensitive_key(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {k: _redact_sensitive_params(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_sensitive_params(item) for item in value]
    return value


def _hitl_hooks_may_approve() -> bool:
    """Whether PERMISSION_REQUEST hooks may auto-allow an HITL tool call.

    Explicit opt-in only (config ``hitl_hooks_may_approve``): by default
    hooks can deny but never approve.
    """
    try:
        from agentnexus.core.config import get_settings

        return bool(getattr(get_settings(), "hitl_hooks_may_approve", False))
    except Exception:
        return False


def _serialize_params(params: dict, limit: int) -> str:
    redacted = _redact_sensitive_params(params)
    return json.dumps(redacted, ensure_ascii=False, default=str)[:limit]


class RiskLevel(str, Enum):
    """Static risk classification for tools.

    SEC-011: Risk level is currently metadata-only, used for HITL triggering
    and tool_policy filtering. A future enhancement could add risk-aware
    behaviors: tighter rate limits for high-risk tools, automatic HITL
    escalation after N calls, or risk-weighted audit detail levels.
    """
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class ToolMeta:
    """Each tool must declare these metadata fields before registration."""

    name: str
    description: str
    param_schema: dict
    allowed_agents: list[str] = field(default_factory=lambda: ["*"])
    risk_level: RiskLevel = RiskLevel.LOW
    require_hitl: bool = False
    timeout_sec: int = 30
    rate_limit_per_min: int = 0
    output_schema: dict | None = None
    audit_enabled: bool = True
    source_type: str = "unknown"
    source_id: str = "unknown"
    enabled: bool = True
    generation: int = 0
    recoverable: bool = False
    max_retention: int = 5
    concurrency_safe: bool = False
    lane: str = ""
    read_only: bool = False
    requires_subagent_context: bool = False


@dataclass
class AuditEntry:
    tool_name: str
    caller: str
    params: str
    result_summary: str
    duration_ms: float
    hitl_triggered: bool
    error: str | None
    timestamp: float = field(default_factory=time.time)
    risk_level: str = "low"
    schema_validation: str = ""
    retry_count: int = 0
    tool_selection_reason: str = ""
    hitl_decision: str = ""


class ToolRegistry:
    """Unified tool governance — all tool calls go through this."""

    def __init__(self, audit_log: Any | None = None):
        self._tools: dict[str, tuple[ToolMeta, Callable]] = {}
        self._rate_counters: dict[str, list[float]] = defaultdict(list)
        self._audit_log = audit_log if audit_log is not None else ThreadSafeAuditLog()
        self._param_validators: dict[str, Any] = {}
        self._output_validators: dict[str, Any] = {}
        self._executor = ThreadPoolExecutor(max_workers=4)
        self._rate_lock = threading.Lock()
        self._lane_pools: dict[str, ThreadPoolExecutor] = {}
        self._lane_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending_futures: set[Future] = set()
        from agentnexus.tools.confirm_bridge import CancelBridge, SubagentBridge
        self.cancel_bridge = CancelBridge()
        self.subagent_bridge = SubagentBridge()
        if jsonschema is None:
            logger.warning("jsonschema package not installed — tool parameter validation is disabled")

    def register(self, meta: ToolMeta, func: Callable) -> None:
        if meta.name in self._tools:
            existing_meta, _ = self._tools[meta.name]
            existing_source = f"{existing_meta.source_type}:{existing_meta.source_id}"
            incoming_source = f"{meta.source_type}:{meta.source_id}"
            if existing_source != incoming_source and existing_source != "unknown:unknown":
                raise ValueError(
                    f"Tool '{meta.name}' already registered by {existing_source}; "
                    f"cannot replace from {incoming_source}"
                )
            if existing_source == incoming_source:
                logger.warning("Tool '%s' already registered — overwriting", meta.name)
        self._tools[meta.name] = (meta, func)
        self._param_validators[meta.name] = self._build_validator(meta.param_schema)
        self._output_validators[meta.name] = self._build_validator(meta.output_schema)

    def unregister(self, name: str) -> bool:
        existed = name in self._tools
        self._tools.pop(name, None)
        self._param_validators.pop(name, None)
        self._output_validators.pop(name, None)
        self._rate_counters.pop(name, None)
        mcp_sigs = getattr(self, "_mcp_registered_signatures", None)
        if mcp_sigs is not None:
            mcp_sigs.pop(name, None)
        return existed

    def close(self, wait: bool = False) -> None:
        """Shut down this registry's thread pools.

        Only for short-lived registries (e.g. per-attempt subagent children):
        on Python < 3.13 an un-shutdown ThreadPoolExecutor's idle workers
        never exit. The session-scoped registry lives for the process and
        must NOT be closed.
        """
        self._executor.shutdown(wait=wait)
        for pool in self._lane_pools.values():
            pool.shutdown(wait=wait)

    def has_pending_work(self) -> bool:
        """Include timed-out tools whose underlying worker has not exited."""
        with self._pending_lock:
            return any(not future.done() for future in self._pending_futures)

    def _forget_future(self, future: Future) -> None:
        with self._pending_lock:
            self._pending_futures.discard(future)

    def get_meta(self, name: str) -> ToolMeta | None:
        """Return ToolMeta for a registered tool, or None if not found."""
        entry = self._tools.get(name)
        return entry[0] if entry else None
    def get_lane_pool(self, lane: str) -> ThreadPoolExecutor:
        """Return the session-scoped pool for a named execution lane.

        The "subagent" lane is sized by settings.subagent_max_concurrent
        (default 3, so local-LLM users can dial it down to 1); other lanes
        default to 2 workers. Pool size IS the concurrency cap — excess
        submissions queue instead of erroring.
        """
        with self._lane_lock:
            pool = self._lane_pools.get(lane)
            if pool is None:
                if lane == "subagent":
                    from agentnexus.core.config import get_settings
                    workers = max(1, get_settings().subagent_max_concurrent)
                else:
                    workers = 2
                pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"lane-{lane}")
                self._lane_pools[lane] = pool
            return pool

    def unregister_where(self, predicate: Callable[[ToolMeta], bool]) -> list[str]:
        """Unregister every tool whose meta satisfies predicate."""
        removed: list[str] = []
        for name, (meta, _) in list(self._tools.items()):
            if predicate(meta) and self.unregister(name):
                removed.append(name)
        return removed

    def unregister_source(self, source_id: str, source_type: str | None = None) -> list[str]:
        return self.unregister_where(
            lambda m: m.source_id == source_id
            and (source_type is None or m.source_type == source_type)
        )

    def unregister_source_prefix(self, source_prefix: str, source_type: str | None = None) -> list[str]:
        return self.unregister_where(
            lambda m: m.source_id.startswith(source_prefix)
            and (source_type is None or m.source_type == source_type)
        )

    def unregister_source_type(self, source_type: str) -> list[str]:
        return self.unregister_where(lambda m: m.source_type == source_type)

    def invoke(
        self,
        name: str,
        params: dict,
        caller: str = "unknown",
        hitl_approver: Callable[[str], bool] | None = None,
        tool_policy: Any = None,
    ) -> Any:
        """Execute a tool with full governance checks. Raises on violation."""
        from agentnexus.core.hooks import HookType, get_hook_manager

        hook_mgr = get_hook_manager()

        hook_mgr.fire(HookType.BEFORE_REGISTRY_INVOKE, {
            "name": name, "params": params, "caller": caller,
        })

        meta, func = self._get_tool(name)
        start = time.time()
        hitl_triggered = False
        hitl_decision = ""
        error = None
        result_str = ""

        try:
            if "*" not in meta.allowed_agents and caller not in meta.allowed_agents:
                raise PermissionError(
                    f"Agent '{caller}' is not allowed to call tool '{name}' "
                    f"(allowed: {meta.allowed_agents})"
                )
            if not meta.enabled:
                raise PermissionError(f"Tool '{name}' is disabled")
            if meta.requires_subagent_context and not self._has_collaboration():
                raise PermissionError("当前入口不支持异步子代理协作")

            if not filter_tool_meta(name, meta, tool_policy):
                raise PermissionError(
                    f"Tool '{name}' is not visible under current skill tool_policy"
                )

            if meta.param_schema:
                self._validate_params(name, params, self._param_validators.get(name))

            if meta.rate_limit_per_min > 0:
                self._check_rate_limit(name, meta.rate_limit_per_min)

            redacted_params = _redact_sensitive_params(params)

            if meta.require_hitl:
                hitl_triggered = True
                hook_mgr.fire(HookType.NOTIFICATION, {
                    "kind": "hitl_request",
                    "tool": name,
                    "caller": caller,
                    "risk_level": meta.risk_level.value,
                })
                perm_ctx = hook_mgr.fire(HookType.PERMISSION_REQUEST, {
                    "name": name,
                    "params": redacted_params,
                    "caller": caller,
                    "risk_level": meta.risk_level.value,
                })
                if perm_ctx.aborted:
                    hitl_decision = f"hook_denied:{perm_ctx.abort_code}"
                    return perm_ctx.to_feedback()
                if (
                    perm_ctx.payload.get("decision") == "allow"
                    and _hitl_hooks_may_approve()
                ):
                    hitl_decision = "hook_allowed"
                else:
                    if hitl_approver is None:
                        hitl_decision = "no_channel"
                        return "[blocked] 该工具需要人工确认，但当前没有可用的确认通道"
                    confirm_summary = (
                        f"调用者: {caller}\n"
                        f"工具: {name}\n"
                        f"风险: {meta.risk_level.value}\n"
                        f"参数: {json.dumps(redacted_params, ensure_ascii=False, default=str)[:500]}"
                    )
                    if not hitl_approver(confirm_summary):
                        hitl_decision = "user_denied"
                        return "[blocked] 用户取消了该工具调用"
                    hitl_decision = "user_allowed"

            span_input = {
                "tool_name": name,
                "caller": caller,
                "params": redacted_params,
                "risk_level": meta.risk_level.value,
            }
            with trace_manager.span("tool", span_input) as span:
                future = self._executor.submit(contextvars.copy_context().run, func, **params)
                with self._pending_lock:
                    self._pending_futures.add(future)
                future.add_done_callback(self._forget_future)
                try:
                    result = future.result(timeout=meta.timeout_sec)
                except FutureTimeout:
                    error = f"Tool '{name}' timed out after {meta.timeout_sec}s"
                    raise TimeoutError(error)
                result_str = summarize_tool_result(result)[:500]
                if meta.output_schema:
                    self._validate_output(name, result, self._output_validators.get(name))
                span.output = {"result_summary": result_str}
                span.metadata = {
                    "status": "ok",
                    "caller": caller,
                    "risk_level": meta.risk_level.value,
                    "schema_validation": "passed",
                }
                return result
        except TimeoutError:
            raise
        except Exception as e:
            if not error:
                error = str(e)
            result_str = f"[error] {error}"
            if "span" in locals() and span is not None:
                is_validation = "validation" in error.lower() or "schema" in error.lower()
                span.metadata = {
                    "status": "error",
                    "error": error,
                    "caller": caller,
                    "risk_level": meta.risk_level.value,
                    "schema_validation": "failed" if is_validation else "passed",
                }
            raise
        finally:
            duration = (time.time() - start) * 1000

            hook_mgr.fire(HookType.AFTER_REGISTRY_INVOKE, {
                "name": name, "params": params, "caller": caller,
                "duration_ms": round(duration, 1), "error": error,
            })

            if meta.audit_enabled:
                self._audit_log.append(AuditEntry(
                    tool_name=name,
                    caller=caller,
                    params=_serialize_params(params, 300),
                    result_summary=result_str[:300],
                    duration_ms=round(duration, 1),
                    hitl_triggered=hitl_triggered,
                    error=error,
                    risk_level=meta.risk_level.value,
                    schema_validation="passed" if not error else "failed",
                    hitl_decision=hitl_decision,
                ))

    def get_available_tools(self, agent: str = "*", tool_policy: Any = None) -> str:
        """Return a formatted description of tools available to *agent*."""
        lines = []
        for name, (meta, _) in self._tools.items():
            allowed = "*" in meta.allowed_agents or agent in meta.allowed_agents
            if meta.enabled and allowed and (not meta.requires_subagent_context or self._has_collaboration()) and filter_tool_meta(name, meta, tool_policy):
                risk_tag = f"[{meta.risk_level.value}]"
                lines.append(f"- {name}: {meta.description} {risk_tag}")
        return "\n".join(lines) if lines else "(no tools available)"

    def get_tool(self, name: str) -> Callable | None:
        """Get the raw callable for a tool (for backward compat)."""
        entry = self._tools.get(name)
        return entry[1] if entry else None

    def list_tools(self) -> list[str]:
        return list(self._tools.keys())

    def list_tools_with_meta(self) -> list[ToolMeta]:
        return [meta for meta, _ in self._tools.values()]

    def to_openai_tools(self, agent: str = "*", tool_policy: Any = None) -> list[dict]:
        """Convert registered tools to OpenAI function-calling format."""
        tools = []
        for name, (meta, _) in self._tools.items():
            if not meta.enabled:
                continue
            if meta.requires_subagent_context and not self._has_collaboration():
                continue
            if "*" not in meta.allowed_agents and agent not in meta.allowed_agents:
                continue
            if not filter_tool_meta(name, meta, tool_policy):
                continue
            schema = {"type": "object", "properties": {}, "required": meta.param_schema.get("required", [])}
            props = meta.param_schema.get("properties", {})
            if props:
                for prop_name, prop_schema in props.items():
                    cleaned = {k: v for k, v in prop_schema.items() if k != "default"}
                    schema["properties"][prop_name] = cleaned
            else:
                schema["properties"] = {}
                schema.pop("required", None)

            tools.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": meta.description,
                    "parameters": schema,
                },
            })
        return tools

    def _has_collaboration(self) -> bool:
        context = self.subagent_bridge.get_context()
        return context is not None and context.collaboration is not None

    def get_audit_log(self) -> list[AuditEntry]:
        return list(self._audit_log)

    def _get_tool(self, name: str) -> tuple[ToolMeta, Callable]:
        entry = self._tools.get(name)
        if entry is None:
            raise KeyError(f"Tool '{name}' not found in registry")
        return entry

    @staticmethod
    def _build_validator(schema: dict | None):
        if not schema or jsonschema is None:
            return None
        cache_key = json.dumps(schema, ensure_ascii=False, sort_keys=True, default=str)
        if cache_key in _VALIDATOR_CACHE:
            return _VALIDATOR_CACHE[cache_key]
        try:
            validator_cls = jsonschema.validators.validator_for(schema)
            validator_cls.check_schema(schema)
            validator = validator_cls(schema)
            _VALIDATOR_CACHE[cache_key] = validator
            return validator
        except Exception as e:
            logger.warning("Failed to compile schema validator: %s", e)
            return None

    @staticmethod
    def _validate_params(name: str, params: dict, validator: Any) -> None:
        if validator is None:
            if jsonschema is None:
                logger.debug("jsonschema not available, skipping param validation for '%s'", name)
            return
        try:
            validator.validate(params)
        except Exception as e:
            raise ValueError(f"Tool '{name}' parameter validation failed: {e}") from e

    @staticmethod
    def _validate_output(name: str, result: Any, validator: Any) -> None:
        if validator is None or not isinstance(result, dict):
            return
        try:
            validator.validate(result)
        except Exception as e:
            logger.warning("Tool '%s' output validation failed: %s", name, e)

    def _check_rate_limit(self, name: str, limit: int) -> None:
        with self._rate_lock:
            now = time.time()
            window = self._rate_counters[name]
            cutoff = now - 60
            window[:] = [t for t in window if t > cutoff]
            if len(window) >= limit:
                raise RuntimeError(
                    f"Rate limit exceeded for tool '{name}' ({limit}/min)"
                )
            window.append(now)
