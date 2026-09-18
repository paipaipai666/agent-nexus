"""Schemas for command hooks (config.yaml / hooks.yaml / plugin manifests).

Kept dependency-free from core.hooks at module level to avoid an import
cycle: event-name validation resolves HookType lazily.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class HookConfig(BaseModel):
    """One command hook definition.

    Loaded from (lowest → highest precedence):
      1. ``config.yaml`` ``hooks:`` list (managed, trusted)
      2. ``~/.agentnexus/hooks.yaml`` (user-global, trusted)
      3. ``<workspace>/.agentnexus/hooks.yaml`` (project, hash trust review)
    """

    event: str
    command: str
    matcher: str | None = Field(
        default=None,
        description="Optional regex applied to payload['name'] (tool events).",
    )
    timeout: float = Field(default=10.0, gt=0, le=600)
    async_: bool = Field(default=False, alias="async")
    enabled: bool = True
    on_failure: Literal["warn", "block"] = "warn"

    model_config = {"populate_by_name": True}

    @field_validator("event")
    @classmethod
    def _validate_event(cls, value: str) -> str:
        from agentnexus.core.hooks import HookType

        valid = {h.value for h in HookType}
        if value not in valid:
            raise ValueError(
                f"unknown hook event {value!r}; expected one of {sorted(valid)}"
            )
        return value

    @field_validator("on_failure")
    @classmethod
    def _validate_on_failure(cls, value: str, info) -> str:
        event = info.data.get("event", "")
        interceptable = event.startswith("before_") or event in (
            "user_prompt_submit",
            "agent_stop",
            "permission_request",
        )
        if value == "block" and not interceptable:
            raise ValueError(
                "on_failure='block' only makes sense for interceptable events "
                "(before_*/user_prompt_submit/agent_stop/permission_request); "
                "observer events cannot block execution"
            )
        return value

    @field_validator("matcher")
    @classmethod
    def _validate_matcher(cls, value: str | None) -> str | None:
        if value is None:
            return None
        import re

        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"invalid matcher regex: {exc}") from exc
        return value

    def matches(self, hook_event: str, payload: dict) -> bool:
        """Decide whether this hook fires for the given event + payload."""
        if not self.enabled or self.event != hook_event:
            return False
        if self.matcher is None:
            return True
        import re

        target = payload.get("name") or ""
        return bool(re.search(self.matcher, str(target)))
