"""Tool Registry — unified governance layer for all agent tools.

Every tool must be registered with ToolMeta before any agent can call it.
The registry enforces: RBAC, parameter schema validation, rate limiting,
risk-level HITL gates, timeout control, and audit logging.
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from agentnexus.observability.tracer import trace_manager
from agentnexus.skills.profile import filter_tool_meta
from agentnexus.tools.result_format import summarize_tool_result

try:
    import jsonschema
except ImportError:
    jsonschema = None

logger = logging.getLogger(__name__)

_SENSITIVE_PARAM_TOKENS = ("api_key", "apikey", "token", "secret", "password", "authorization")
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


def _serialize_params(params: dict, limit: int) -> str:
    redacted = _redact_sensitive_params(params)
    return json.dumps(redacted, ensure_ascii=False, default=str)[:limit]


class RiskLevel(str, Enum):
    LOW = "low"          # read-only queries (search, file read)
    MEDIUM = "medium"    # write ops, network requests
    HIGH = "high"        # code execution, database writes, external payments


@dataclass
class ToolMeta:
    """Each tool must declare these 9 metadata fields before registration."""

    name: str                            # unique identifier
    description: str                     # human-readable, shown to LLM
    param_schema: dict                   # JSON Schema for input validation
    allowed_agents: list[str] = field(default_factory=lambda: ["*"])
    risk_level: RiskLevel = RiskLevel.LOW
    require_hitl: bool = False           # human-in-the-loop confirmation required
    timeout_sec: int = 30
    rate_limit_per_min: int = 0          # 0 = unlimited
    output_schema: dict | None = None    # JSON Schema for output validation
    audit_enabled: bool = True
    source_type: str = "unknown"
    source_id: str = "unknown"
    enabled: bool = True
    generation: int = 0


@dataclass
class AuditEntry:
    tool_name: str
    caller: str
    params: str          # truncated / redacted
    result_summary: str  # truncated
    duration_ms: float
    hitl_triggered: bool
    error: str | None
    timestamp: float = field(default_factory=time.time)


class ToolRegistry:
    """Unified tool governance — all tool calls go through this."""

    def __init__(self, audit_log: Any | None = None):
        self._tools: dict[str, tuple[ToolMeta, Callable]] = {}
        self._rate_counters: dict[str, list[float]] = defaultdict(list)
        self._audit_log = audit_log if audit_log is not None else []
        self._param_validators: dict[str, Any] = {}
        self._output_validators: dict[str, Any] = {}
        self._executor = ThreadPoolExecutor(max_workers=4)

    # ── registration ──────────────────────────────────────────────

    def register_tool(
        self,
        name: str,
        description: str,
        func: Callable,
        param_schema: dict | None = None,
        allowed_agents: list[str] | None = None,
        risk_level: str = "low",
        require_hitl: bool = False,
        timeout_sec: int = 30,
        rate_limit_per_min: int = 0,
        output_schema: dict | None = None,
        audit_enabled: bool = True,
        source_type: str = "unknown",
        source_id: str = "unknown",
        enabled: bool = True,
        generation: int = 0,
    ) -> None:
        """Register a tool with flat parameters (convenience wrapper)."""
        risk = getattr(RiskLevel, risk_level.upper(), RiskLevel.LOW)
        meta = ToolMeta(
            name=name,
            description=description,
            param_schema=param_schema or {"type": "object", "properties": {}},
            allowed_agents=allowed_agents or ["*"],
            risk_level=risk,
            require_hitl=require_hitl,
            timeout_sec=timeout_sec,
            rate_limit_per_min=rate_limit_per_min,
            output_schema=output_schema,
            audit_enabled=audit_enabled,
            source_type=source_type,
            source_id=source_id,
            enabled=enabled,
            generation=generation,
        )
        self.register(meta, func)

    def register(self, meta: ToolMeta, func: Callable) -> None:
        if meta.name in self._tools:
            existing_meta, _ = self._tools[meta.name]
            existing_source = f"{existing_meta.source_type}:{existing_meta.source_id}"
            incoming_source = f"{meta.source_type}:{meta.source_id}"
            if existing_source != incoming_source:
                raise ValueError(
                    f"Tool '{meta.name}' already registered by {existing_source}; "
                    f"cannot replace from {incoming_source}"
                )
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
        return existed

    def unregister_source(self, source_id: str, source_type: str | None = None) -> list[str]:
        removed: list[str] = []
        for name, (meta, _) in list(self._tools.items()):
            if meta.source_id != source_id:
                continue
            if source_type is not None and meta.source_type != source_type:
                continue
            if self.unregister(name):
                removed.append(name)
        return removed

    def unregister_source_prefix(self, source_prefix: str, source_type: str | None = None) -> list[str]:
        removed: list[str] = []
        for name, (meta, _) in list(self._tools.items()):
            if not meta.source_id.startswith(source_prefix):
                continue
            if source_type is not None and meta.source_type != source_type:
                continue
            if self.unregister(name):
                removed.append(name)
        return removed

    def unregister_source_type(self, source_type: str) -> list[str]:
        removed: list[str] = []
        for name, (meta, _) in list(self._tools.items()):
            if meta.source_type != source_type:
                continue
            if self.unregister(name):
                removed.append(name)
        return removed

    # ── call path (the governance gate) ───────────────────────────

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

        # ── before registry invoke hook ────────────────────────
        hook_mgr.fire(HookType.BEFORE_REGISTRY_INVOKE, {
            "name": name, "params": params, "caller": caller,
        })

        meta, func = self._get_tool(name)
        start = time.time()
        hitl_triggered = False
        error = None
        result_str = ""

        # 1. RBAC check
        try:
            if "*" not in meta.allowed_agents and caller not in meta.allowed_agents:
                raise PermissionError(
                    f"Agent '{caller}' is not allowed to call tool '{name}' "
                    f"(allowed: {meta.allowed_agents})"
                )
            if not meta.enabled:
                raise PermissionError(f"Tool '{name}' is disabled")

            # 2. Skill tool policy hard gate
            if not filter_tool_meta(name, meta, tool_policy):
                raise PermissionError(
                    f"Tool '{name}' is not visible under current skill tool_policy"
                )

            # 3. Parameter schema validation
            if meta.param_schema:
                self._validate_params(name, params, self._param_validators.get(name))

            # 4. Rate limiting
            if meta.rate_limit_per_min > 0:
                self._check_rate_limit(name, meta.rate_limit_per_min)

            redacted_params = _redact_sensitive_params(params)

            # 5. HITL gate
            if meta.require_hitl:
                hitl_triggered = True
                if hitl_approver is None:
                    return "[blocked] 该工具需要人工确认，但当前没有可用的确认通道"
                confirm_summary = (
                    f"调用者: {caller}\n"
                    f"工具: {name}\n"
                    f"风险: {meta.risk_level.value}\n"
                    f"参数: {json.dumps(redacted_params, ensure_ascii=False, default=str)[:500]}"
                )
                if not hitl_approver(confirm_summary):
                    return "[blocked] 用户取消了该工具调用"

            # 6. Execute with timeout enforcement
            span_input = {
                "tool_name": name,
                "caller": caller,
                "params": redacted_params,
                "risk_level": meta.risk_level.value,
            }
            with trace_manager.span("tool", span_input) as span:
                future = self._executor.submit(func, **params)
                try:
                    result = future.result(timeout=meta.timeout_sec)
                except FutureTimeout:
                    error = f"Tool '{name}' timed out after {meta.timeout_sec}s"
                    raise TimeoutError(error)
                result_str = summarize_tool_result(result)[:500]
                # 7. Output schema validation
                if meta.output_schema:
                    self._validate_output(name, result, self._output_validators.get(name))
                span.output = {"result_summary": result_str}
                span.metadata = {"status": "ok", "caller": caller}
                return result
        except TimeoutError:
            raise
        except Exception as e:
            if not error:
                error = str(e)
            result_str = f"[error] {error}"
            raise
        finally:
            duration = (time.time() - start) * 1000

            # ── after registry invoke hook ─────────────────────
            hook_mgr.fire(HookType.AFTER_REGISTRY_INVOKE, {
                "name": name, "params": params, "caller": caller,
                "duration_ms": round(duration, 1), "error": error,
            })

            # 7. Audit
            if meta.audit_enabled:
                self._audit_log.append(AuditEntry(
                    tool_name=name,
                    caller=caller,
                    params=_serialize_params(params, 300),
                    result_summary=result_str[:300],
                    duration_ms=round(duration, 1),
                    hitl_triggered=hitl_triggered,
                    error=error,
                ))

    # ── query API (for LLM prompt building) ───────────────────────

    def get_available_tools(self, agent: str = "*", tool_policy: Any = None) -> str:
        """Return a formatted description of tools available to *agent*."""
        lines = []
        for name, (meta, _) in self._tools.items():
            allowed = "*" in meta.allowed_agents or agent in meta.allowed_agents
            if meta.enabled and allowed and filter_tool_meta(name, meta, tool_policy):
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
            if "*" not in meta.allowed_agents and agent not in meta.allowed_agents:
                continue
            if not filter_tool_meta(name, meta, tool_policy):
                continue
            # Strip default values from properties — OpenAI doesn't use them
            schema = {"type": "object", "properties": {}, "required": meta.param_schema.get("required", [])}
            props = meta.param_schema.get("properties", {})
            if props:
                for prop_name, prop_schema in props.items():
                    cleaned = {k: v for k, v in prop_schema.items() if k != "default"}
                    schema["properties"][prop_name] = cleaned
            else:
                # No declared properties → empty schema (tool takes no args)
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

    def get_audit_log(self) -> list[AuditEntry]:
        return list(self._audit_log)

    # ── internals ─────────────────────────────────────────────────

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
        now = time.time()
        window = self._rate_counters[name]
        # Remove entries older than 60s
        cutoff = now - 60
        window[:] = [t for t in window if t > cutoff]
        if len(window) >= limit:
            raise RuntimeError(
                f"Rate limit exceeded for tool '{name}' ({limit}/min)"
            )
        window.append(now)
