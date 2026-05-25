"""Compile workflow session profiles into prompt and tool visibility inputs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from string import Formatter
from typing import Any

from agentnexus.prompts import load_prompt
from agentnexus.skills.workflow import SessionProfile, ToolPolicy

_FRAGMENTS_DIR = Path(__file__).resolve().parents[1] / "prompts" / "fragments"
_RISK_ORDER = {
    "low": 0,
    "medium": 1,
    "high": 2,
}
_CORE_TEMPLATE_KEYS = {"tools", "question", "history", "memory_context", "conversation_context"}


@dataclass(frozen=True)
class CompiledSessionProfile:
    """Prepared profile data used by ReActAgent during a run."""

    profile: SessionProfile
    prompt_template: str
    fragments_text: str
    workflow_guidance: str

    @property
    def tool_policy(self) -> ToolPolicy:
        return self.profile.tool_policy


def validate_session_profile(profile: SessionProfile) -> CompiledSessionProfile:
    """Load prompt assets and build the static guidance block for a profile."""
    system_name = profile.prompt_profile.system or "react"
    try:
        prompt_template = load_prompt(system_name)
    except FileNotFoundError as exc:
        raise ValueError(f"Prompt template not found: {system_name}") from exc

    fragments: list[str] = []
    for fragment in profile.prompt_profile.fragments:
        fragment_path = _FRAGMENTS_DIR / f"{fragment}.txt"
        try:
            fragments.append(fragment_path.read_text(encoding="utf-8").strip())
        except FileNotFoundError as exc:
            raise ValueError(f"Prompt fragment not found: {fragment}") from exc

    guidance = build_workflow_guidance(profile)
    return CompiledSessionProfile(
        profile=profile,
        prompt_template=prompt_template,
        fragments_text="\n\n".join(part for part in fragments if part),
        workflow_guidance=guidance,
    )


def build_workflow_guidance(profile: SessionProfile) -> str:
    """Render workflow metadata as advisory context, not an execution script."""
    variables = dict(profile.prompt_profile.variables or {})
    lines = [
        "== Skill Workflow ==",
        f"Workflow: {profile.workflow_id}",
    ]
    if profile.display_name:
        lines.append(f"Name: {_format_with_variables(profile.display_name, variables)}")
    if profile.description:
        lines.append(f"Description: {_format_with_variables(profile.description, variables)}")
    if profile.prompt_profile.fragments:
        lines.append("Prompt fragments: " + ", ".join(profile.prompt_profile.fragments))
    if profile.resources:
        lines.append("Bundled resources:")
        by_type: dict[str, list[str]] = {"script": [], "reference": [], "asset": []}
        for resource in profile.resources:
            location = resource.absolute_path or resource.path
            by_type.setdefault(resource.type, []).append(f"{resource.path} => {location}")
        for resource_type in ("script", "reference", "asset"):
            paths = by_type.get(resource_type, [])
            if not paths:
                continue
            shown = ", ".join(paths[:12])
            suffix = f", ... +{len(paths) - 12}" if len(paths) > 12 else ""
            lines.append(f"- {resource_type}s: {shown}{suffix}")
        lines.append(
            "Use references only when needed, use assets as output resources, "
            "and do not execute bundled scripts unless the user request requires it."
        )
    if profile.steps:
        lines.append("Suggested steps:")
        for step in profile.steps:
            label = step.id or step.type
            detail = step.prompt or step.tool or ""
            suffix = f" - {_format_with_variables(detail, variables)}" if detail else ""
            lines.append(f"- {label} ({step.type}){suffix}")
    if profile.success_criteria:
        lines.append("Success criteria:")
        for item in profile.success_criteria:
            lines.append(f"- {_format_with_variables(item, variables)}")
    lines.append("Follow this workflow as guidance while preserving the ReAct loop.")
    return "\n".join(lines)


def filter_tool_meta(name: str, meta: Any, tool_policy: ToolPolicy | None) -> bool:
    """Return True when a tool should be visible to the model for this profile."""
    if tool_policy is None:
        return True
    allow = set(tool_policy.allow or [])
    deny = set(tool_policy.deny or [])
    if allow and name not in allow:
        return False
    if name in deny:
        return False
    if not tool_policy.allow_subagents and name == "subagent_run":
        return False
    max_risk = _RISK_ORDER.get(str(tool_policy.max_risk or "high").lower(), _RISK_ORDER["high"])
    risk_value = getattr(meta.risk_level, "value", str(meta.risk_level))
    if _RISK_ORDER.get(str(risk_value).lower(), _RISK_ORDER["high"]) > max_risk:
        return False
    return True


def format_tool_policy_summary(tool_policy: ToolPolicy | None) -> str:
    if tool_policy is None:
        return "default"
    parts = [f"risk<={tool_policy.max_risk}"]
    if tool_policy.allow:
        parts.append(f"allow={len(tool_policy.allow)}")
    if tool_policy.deny:
        parts.append(f"deny={len(tool_policy.deny)}")
    if not tool_policy.allow_subagents:
        parts.append("no-subagents")
    return ", ".join(parts)


def _format_with_variables(text: str, variables: dict[str, Any]) -> str:
    if not text or not variables:
        return text
    allowed = {
        field_name for _, field_name, _, _ in Formatter().parse(text)
        if field_name and field_name not in _CORE_TEMPLATE_KEYS
    }
    safe_vars = {key: value for key, value in variables.items() if key in allowed}
    try:
        return text.format(**safe_vars)
    except (KeyError, ValueError):
        return text
