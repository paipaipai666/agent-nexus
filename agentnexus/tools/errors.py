"""Structured tool error types.

Provides ``ToolError`` — a machine-readable error envelope returned by
tools and the governance gate.  Models use ``error_code`` and
``recoverable`` to decide whether to retry, escalate, or abort.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ToolErrorCode(str, Enum):
    """Machine-readable error codes for tool failures."""

    RATE_LIMITED = "RATE_LIMITED"
    TIMEOUT = "TIMEOUT"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    CANCELLED = "CANCELLED"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    HITL_BLOCKED = "HITL_BLOCKED"
    TOOL_NOT_FOUND = "TOOL_NOT_FOUND"
    SCHEMA_VALIDATION_FAILED = "SCHEMA_VALIDATION_FAILED"


@dataclass
class ToolError:
    """Structured error returned by tools and the governance gate.

    Attributes:
        error_code: Machine-readable constant from ``ToolErrorCode``.
        message: Human/model-readable description of what went wrong.
        recoverable: Whether the agent may retry this call.
        suggested_action: Guidance for the agent on what to do next.
    """

    error_code: str
    message: str
    recoverable: bool
    suggested_action: str

    def __str__(self) -> str:
        return (
            f"[{self.error_code}] {self.message} "
            f"(suggested: {self.suggested_action})"
        )
