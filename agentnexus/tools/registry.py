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
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


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

    def __init__(self, audit_log: list[AuditEntry] | None = None):
        self._tools: dict[str, tuple[ToolMeta, Callable]] = {}
        self._rate_counters: dict[str, list[float]] = defaultdict(list)
        self._audit_log: list[AuditEntry] = audit_log or []

    # ── registration ──────────────────────────────────────────────

    def register(self, meta: ToolMeta, func: Callable) -> None:
        if meta.name in self._tools:
            logger.warning("Tool '%s' already registered — overwriting", meta.name)
        self._tools[meta.name] = (meta, func)

    # ── call path (the governance gate) ───────────────────────────

    def invoke(self, name: str, params: dict, caller: str = "unknown",
               hitl_approver: Callable[[str], bool] | None = None) -> Any:
        """Execute a tool with full governance checks. Raises on violation."""
        meta, func = self._get_tool(name)

        # 1. RBAC check
        if "*" not in meta.allowed_agents and caller not in meta.allowed_agents:
            raise PermissionError(
                f"Agent '{caller}' is not allowed to call tool '{name}' "
                f"(allowed: {meta.allowed_agents})"
            )

        # 2. Parameter schema validation
        if meta.param_schema:
            self._validate_params(name, params, meta.param_schema)

        # 3. Rate limiting
        if meta.rate_limit_per_min > 0:
            self._check_rate_limit(name, meta.rate_limit_per_min)

        # 4. HITL gate
        hitl_triggered = False
        if meta.require_hitl and hitl_approver:
            hitl_triggered = True
            if not hitl_approver(str(params)[:200]):
                return "[blocked] 用户取消了该工具调用"

        # 5. Execute with timeout enforcement
        start = time.time()
        error = None
        result_str = ""
        try:
            from concurrent.futures import ThreadPoolExecutor
            from concurrent.futures import TimeoutError as FutureTimeout
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(func, **params)
                try:
                    result = future.result(timeout=meta.timeout_sec)
                except FutureTimeout:
                    error = f"Tool '{name}' timed out after {meta.timeout_sec}s"
                    raise TimeoutError(error)
            result_str = str(result)[:500]
            # 6. Output schema validation
            if meta.output_schema:
                self._validate_output(name, result, meta.output_schema)
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
            # 7. Audit
            if meta.audit_enabled:
                self._audit_log.append(AuditEntry(
                    tool_name=name,
                    caller=caller,
                    params=json.dumps(params, ensure_ascii=False, default=str)[:300],
                    result_summary=result_str[:300],
                    duration_ms=round(duration, 1),
                    hitl_triggered=hitl_triggered,
                    error=error,
                ))

    # ── query API (for LLM prompt building) ───────────────────────

    def get_available_tools(self, agent: str = "*") -> str:
        """Return a formatted description of tools available to *agent*."""
        lines = []
        for name, (meta, _) in self._tools.items():
            if "*" in meta.allowed_agents or agent in meta.allowed_agents:
                risk_tag = f"[{meta.risk_level.value}]"
                lines.append(f"- {name}: {meta.description} {risk_tag}")
        return "\n".join(lines) if lines else "(no tools available)"

    def get_tool(self, name: str) -> Callable | None:
        """Get the raw callable for a tool (for backward compat)."""
        entry = self._tools.get(name)
        return entry[1] if entry else None

    def list_tools(self) -> list[str]:
        return list(self._tools.keys())

    def to_openai_tools(self, agent: str = "*") -> list[dict]:
        """Convert registered tools to OpenAI function-calling format."""
        tools = []
        for name, (meta, _) in self._tools.items():
            if "*" not in meta.allowed_agents and agent not in meta.allowed_agents:
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
    def _validate_params(name: str, params: dict, schema: dict) -> None:
        try:
            import jsonschema
            jsonschema.validate(params, schema)
        except ImportError:
            pass  # jsonschema not installed — skip validation with a warning
            logger.debug("jsonschema not available, skipping param validation for '%s'", name)
        except Exception as e:
            raise ValueError(f"Tool '{name}' parameter validation failed: {e}") from e

    @staticmethod
    def _validate_output(name: str, result: Any, schema: dict) -> None:
        try:
            import jsonschema
            if isinstance(result, dict):
                jsonschema.validate(result, schema)
        except ImportError:
            pass
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
