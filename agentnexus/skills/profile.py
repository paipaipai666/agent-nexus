"""Compile workflow session profiles into prompt and tool visibility inputs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from string import Formatter
from typing import TYPE_CHECKING, Any

from agentnexus.prompts import load_prompt
from agentnexus.skills.workflow import SessionProfile, ToolPolicy

if TYPE_CHECKING:
    from agentnexus.core.config import PersonaConfig

_FRAGMENTS_DIR = Path(__file__).resolve().parents[1] / "prompts" / "fragments"
_RISK_ORDER = {
    "low": 0,
    "medium": 1,
    "high": 2,
}
_CORE_TEMPLATE_KEYS = {"tools", "question", "history", "memory_context", "conversation_context"}

# Platform-level behavioral fragments — always loaded, cannot be disabled.
_CORE_FRAGMENTS = ["stance", "autonomy", "accountability"]

# Priority preamble — injected before all fragments to resolve conflicts.
# NOTE: must NOT contain the exact fragment headers "行为原则", "自主权边界",
# "Accountability" — tests use substring matching to verify fragment ordering.
_PRIORITY_PREAMBLE = (
    "== 准则优先级 ==\n"
    "当以下原则之间存在冲突时，按此顺序裁决：\n"
    "1. 安全约束 > 用户操作边界 > 核心准则 > 问责机制\n"
    "2. 高风险操作的安全约束永远不可被其他原则覆盖\n"
    "3. '直接指出问题'不等于'绕过安全确认直接执行'\n"
    "4. 如果两条原则冲突且无法调和，选择更保守的行动并说明理由"
)


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


def load_core_fragments() -> str:
    """Load platform-level behavioral fragments. Always loaded, cannot be disabled."""
    parts: list[str] = [_PRIORITY_PREAMBLE]
    for name in _CORE_FRAGMENTS:
        fragment_path = _FRAGMENTS_DIR / f"{name}.txt"
        try:
            parts.append(fragment_path.read_text(encoding="utf-8").strip())
        except FileNotFoundError:
            continue
    return "\n\n".join(part for part in parts if part)


def compile_persona_fragment(persona_config: PersonaConfig) -> str:
    """Compile a validated PersonaConfig into a prompt fragment string.

    Uses behavioral language instead of declarative statements to produce
    stronger guidance on how the agent should act, not just who it is.
    """
    if not persona_config:
        return ""

    lines: list[str] = []
    if persona_config.agent_name:
        lines.append(f"你的名字是 {persona_config.agent_name}。在回答中保持这个身份的一致性。")
    if persona_config.identity:
        lines.append(f"你的角色：{persona_config.identity}。以这个角色的专业视角分析问题、给出建议。")
    if persona_config.tone:
        lines.append(f"沟通风格：{persona_config.tone}。在所有回复中贯彻这一风格，包括错误说明和不确定性表达。")
    if persona_config.projects:
        lines.append("当前关注：")
        for project in persona_config.projects:
            lines.append(f"- {project.name}：{project.focus}")
        lines.append("当用户的问题涉及以上领域时，优先利用这些上下文。")
    if not lines:
        return ""
    return "== Persona ==\n" + "\n".join(lines)


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
